package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"unicode/utf8"
)

const (
	maxRequestBytes         = 1 << 20
	preflightRequestSchema  = "supernova.hermetic-preflight-request.v1"
	preflightResponseSchema = "supernova.hermetic-preflight-response.v1"
	componentsSchema        = "supernova.hermetic-executor-components.v1"
	modelPath               = "/opt/supernova/model.gguf"
	componentsPath          = "/opt/supernova/components.json"
	buildLockPath           = "/opt/supernova/BUILD_LOCK.json"
	llamaCLIPath            = "/app/llama-cli"
)

type preflightRequest struct {
	Context                []json.RawMessage `json:"context"`
	ExecutorArtifactSHA256 string            `json:"executor_artifact_sha256"`
	ModelIdentitySHA256    string            `json:"model_identity_sha256"`
	Operation              string            `json:"operation"`
	Schema                 string            `json:"schema"`
}

type preflightResponse struct {
	ExecutorArtifactSHA256 string `json:"executor_artifact_sha256"`
	ModelIdentitySHA256    string `json:"model_identity_sha256"`
	Schema                 string `json:"schema"`
	Status                 string `json:"status"`
}

type componentManifest struct {
	BuildLockSHA256 string `json:"build_lock_sha256"`
	ExecutorSHA256  string `json:"executor_sha256"`
	LlamaCLISHA256  string `json:"llama_cli_sha256"`
	ModelSHA256     string `json:"model_sha256"`
	Schema          string `json:"schema"`
}

var runCommand = func(path string, args ...string) error {
	cmd := exec.Command(path, args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("llama runtime failed: %w: %s", err, bounded(stderr.String(), 2048))
	}
	return nil
}

func main() {
	if len(os.Args) != 2 || os.Args[1] != "--stdio" {
		fail(errors.New("usage: executor --stdio"))
	}
	request, err := io.ReadAll(io.LimitReader(os.Stdin, maxRequestBytes+1))
	if err != nil {
		fail(fmt.Errorf("read request: %w", err))
	}
	if len(request) == 0 || len(request) > maxRequestBytes {
		fail(errors.New("request must contain 1..1048576 bytes"))
	}
	if isPreflight(request) {
		if err := handlePreflight(request); err != nil {
			fail(err)
		}
		return
	}
	if err := handleGeneration(request); err != nil {
		fail(err)
	}
}

func isPreflight(raw []byte) bool {
	var probe map[string]json.RawMessage
	if json.Unmarshal(raw, &probe) != nil {
		return false
	}
	for _, key := range []string{"schema", "operation"} {
		value, ok := probe[key]
		if !ok {
			continue
		}
		var text string
		if json.Unmarshal(value, &text) == nil &&
			(text == preflightRequestSchema || text == "PREFLIGHT") {
			return true
		}
	}
	return false
}

func parsePreflight(raw []byte) (preflightRequest, error) {
	var request preflightRequest
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		return request, fmt.Errorf("invalid preflight request: %w", err)
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		return request, errors.New("preflight request contains trailing JSON")
	}
	if request.Schema != preflightRequestSchema || request.Operation != "PREFLIGHT" {
		return request, errors.New("preflight schema or operation differs")
	}
	if request.Context == nil || len(request.Context) != 0 {
		return request, errors.New("preflight context must be the exact empty array")
	}
	if !isSHA256(request.ExecutorArtifactSHA256) ||
		!isSHA256(request.ModelIdentitySHA256) {
		return request, errors.New("preflight challenge hashes must be lowercase SHA-256")
	}
	return request, nil
}

func handlePreflight(raw []byte) error {
	request, err := parsePreflight(raw)
	if err != nil {
		return err
	}
	executable, err := os.Executable()
	if err != nil {
		return fmt.Errorf("resolve executor path: %w", err)
	}
	if err := validateComponents(componentsPath, buildLockPath, executable, llamaCLIPath, modelPath); err != nil {
		return err
	}
	if err := runCommand(
		llamaCLIPath,
		"-m", modelPath,
		"-n", "0",
		"--device", "none",
		"--threads", "1",
		"--ctx-size", "256",
		"--batch-size", "128",
		"--seed", "0",
		"--simple-io",
		"--no-display-prompt",
		"--no-show-timings",
		"--log-disable",
		"--prompt", "",
	); err != nil {
		return fmt.Errorf("empty-context model load: %w", err)
	}
	response := preflightResponse{
		ExecutorArtifactSHA256: request.ExecutorArtifactSHA256,
		ModelIdentitySHA256:    request.ModelIdentitySHA256,
		Schema:                 preflightResponseSchema,
		Status:                 "READY",
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(response); err != nil {
		return fmt.Errorf("write response: %w", err)
	}
	return nil
}

func handleGeneration(prompt []byte) error {
	if !utf8.Valid(prompt) {
		return errors.New("generation request must be UTF-8")
	}
	file, err := os.CreateTemp("/tmp", "supernova-prompt-*")
	if err != nil {
		return fmt.Errorf("create prompt file: %w", err)
	}
	name := file.Name()
	defer os.Remove(name)
	if err := file.Chmod(0600); err != nil {
		file.Close()
		return fmt.Errorf("protect prompt file: %w", err)
	}
	if _, err := file.Write(prompt); err != nil {
		file.Close()
		return fmt.Errorf("write prompt file: %w", err)
	}
	if err := file.Close(); err != nil {
		return fmt.Errorf("close prompt file: %w", err)
	}
	cmd := exec.Command(
		llamaCLIPath,
		"-m", modelPath,
		"--file", name,
		"-n", "1024",
		"--device", "none",
		"--threads", "1",
		"--ctx-size", "4096",
		"--batch-size", "512",
		"--seed", "-1",
		"--temp", "0.80",
		"--top-k", "40",
		"--top-p", "0.95",
		"--simple-io",
		"--no-display-prompt",
		"--no-show-timings",
		"--log-disable",
	)
	cmd.Stdout = os.Stdout
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("generation failed: %w: %s", err, bounded(stderr.String(), 2048))
	}
	return nil
}

func validateComponents(manifestPath, lockPath, executorPath, llamaPath, weightsPath string) error {
	raw, err := os.ReadFile(manifestPath)
	if err != nil {
		return fmt.Errorf("read component manifest: %w", err)
	}
	var manifest componentManifest
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&manifest); err != nil {
		return fmt.Errorf("decode component manifest: %w", err)
	}
	if decoder.Decode(&struct{}{}) != io.EOF || manifest.Schema != componentsSchema {
		return errors.New("component manifest is not the exact supported schema")
	}
	checks := []struct{ path, expected, name string }{
		{lockPath, manifest.BuildLockSHA256, "build lock"},
		{executorPath, manifest.ExecutorSHA256, "executor"},
		{llamaPath, manifest.LlamaCLISHA256, "llama-cli"},
		{weightsPath, manifest.ModelSHA256, "model"},
	}
	for _, check := range checks {
		if !isSHA256(check.expected) {
			return fmt.Errorf("%s manifest hash is invalid", check.name)
		}
		actual, err := fileSHA256(check.path)
		if err != nil {
			return fmt.Errorf("hash %s: %w", check.name, err)
		}
		if actual != check.expected {
			return fmt.Errorf("%s component hash differs", check.name)
		}
	}
	return nil
}

func fileSHA256(path string) (string, error) {
	file, err := os.Open(filepath.Clean(path))
	if err != nil {
		return "", err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

func isSHA256(value string) bool {
	if len(value) != 64 || value != strings.ToLower(value) {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}

func bounded(value string, limit int) string {
	value = strings.TrimSpace(value)
	if len(value) <= limit {
		return value
	}
	return value[:limit]
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, bounded(err.Error(), 4096))
	os.Exit(2)
}
