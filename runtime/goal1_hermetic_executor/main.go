package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

const (
	maxRequestBytes           = 1 << 20
	maxServerResponseBytes    = 1 << 20
	maxServerDiagnosticBytes  = 4096
	preflightRequestSchema    = "supernova.hermetic-preflight-request.v1"
	preflightResponseSchema   = "supernova.hermetic-preflight-response.v1"
	generationResponseSchema  = "supernova.hermetic-generation-response.v1"
	componentsSchema          = "supernova.hermetic-executor-components.v3"
	modelPath                 = "/opt/supernova/model.gguf"
	componentsPath            = "/opt/supernova/components.json"
	buildLockPath             = "/opt/supernova/BUILD_LOCK.json"
	llamaServerPath           = "/app/llama-server"
	llamaServerHost           = "127.0.0.1"
	llamaServerPort           = 8080
	llamaServerHealthPath     = "/health"
	llamaServerCompletionPath = "/v1/chat/completions"
	llamaServerModelAlias     = "supernova-kimina-prover-1.5b-q4_k_m"
	llamaServerSystemPrompt   = "You are an expert in mathematics and Lean 4. Return only the requested Lean artifact."
	llamaServerReadyTimeout   = 120 * time.Second
	llamaServerRequestTimeout = 300 * time.Second
	llamaServerProbeTimeout   = 2 * time.Second
	llamaServerProbeInterval  = 100 * time.Millisecond
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

type generationResponse struct {
	CompletionUTF8 string `json:"completion_utf8"`
	Schema         string `json:"schema"`
	Status         string `json:"status"`
}

type componentManifest struct {
	BuildLockSHA256   string `json:"build_lock_sha256"`
	ExecutorSHA256    string `json:"executor_sha256"`
	LlamaServerSHA256 string `json:"llama_server_sha256"`
	ModelSHA256       string `json:"model_sha256"`
	Schema            string `json:"schema"`
}

type chatMessage struct {
	Content string `json:"content"`
	Role    string `json:"role"`
}

type completionRequest struct {
	MaxTokens   int           `json:"max_tokens"`
	Messages    []chatMessage `json:"messages"`
	Model       string        `json:"model"`
	Seed        int           `json:"seed"`
	Stream      bool          `json:"stream"`
	Temperature float64       `json:"temperature"`
	TopK        int           `json:"top_k"`
	TopP        float64       `json:"top_p"`
}

type managedLlamaServer interface {
	waitHealthy() error
	complete([]byte) (string, error)
	stop() error
	diagnostics() string
}

type boundedCapture struct {
	mu        sync.Mutex
	data      []byte
	limit     int
	truncated bool
}

func (capture *boundedCapture) Write(value []byte) (int, error) {
	capture.mu.Lock()
	defer capture.mu.Unlock()
	remaining := capture.limit - len(capture.data)
	if remaining > 0 {
		kept := len(value)
		if kept > remaining {
			kept = remaining
		}
		capture.data = append(capture.data, value[:kept]...)
	}
	if len(value) > remaining {
		capture.truncated = true
	}
	return len(value), nil
}

func (capture *boundedCapture) String() string {
	capture.mu.Lock()
	defer capture.mu.Unlock()
	value := string(capture.data)
	if capture.truncated {
		value += "\n[diagnostics truncated]"
	}
	return value
}

type llamaServerProcess struct {
	cmd     *exec.Cmd
	client  *http.Client
	stdout  *boundedCapture
	stderr  *boundedCapture
	stopped bool
}

var startManagedLlamaServer = func() (managedLlamaServer, error) {
	return startLlamaServerProcess()
}

func startLlamaServerProcess() (*llamaServerProcess, error) {
	stdout := &boundedCapture{limit: maxServerDiagnosticBytes}
	stderr := &boundedCapture{limit: maxServerDiagnosticBytes}
	cmd := exec.Command(
		llamaServerPath,
		"-m", modelPath,
		"--host", llamaServerHost,
		"--port", strconv.Itoa(llamaServerPort),
		"--no-webui",
		"--jinja",
		"--alias", llamaServerModelAlias,
		"--device", "none",
		"--threads", "2",
		"--ctx-size", "8192",
		"--batch-size", "512",
		"--reasoning-format", "deepseek",
	)
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("start llama-server: %w", err)
	}
	return &llamaServerProcess{
		cmd:     cmd,
		client:  newLoopbackHTTPClient(),
		stdout:  stdout,
		stderr:  stderr,
		stopped: false,
	}, nil
}

func newLoopbackHTTPClient() *http.Client {
	expectedAddress := net.JoinHostPort(llamaServerHost, strconv.Itoa(llamaServerPort))
	dialer := &net.Dialer{}
	transport := &http.Transport{
		Proxy: nil,
		DialContext: func(ctx context.Context, _, address string) (net.Conn, error) {
			if address != expectedAddress {
				return nil, fmt.Errorf("llama-server HTTP target is not the frozen loopback address")
			}
			return dialer.DialContext(ctx, "tcp4", expectedAddress)
		},
		DisableKeepAlives: true,
	}
	return &http.Client{
		Transport: transport,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return errors.New("llama-server redirects are forbidden")
		},
	}
}

func llamaServerURL(path string) string {
	return fmt.Sprintf("http://%s:%d%s", llamaServerHost, llamaServerPort, path)
}

func readBoundedResponse(response *http.Response) ([]byte, error) {
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, maxServerResponseBytes+1))
	if err != nil {
		return nil, fmt.Errorf("read llama-server response: %w", err)
	}
	if len(raw) > maxServerResponseBytes {
		return nil, errors.New("llama-server response exceeded the byte limit")
	}
	return raw, nil
}

func decodeJSONObject(raw []byte, field string) (map[string]json.RawMessage, error) {
	var value map[string]json.RawMessage
	decoder := json.NewDecoder(bytes.NewReader(raw))
	if err := decoder.Decode(&value); err != nil {
		return nil, fmt.Errorf("decode %s: %w", field, err)
	}
	if value == nil || decoder.Decode(&struct{}{}) != io.EOF {
		return nil, fmt.Errorf("%s is not one JSON object", field)
	}
	return value, nil
}

func probeHealth(ctx context.Context, client *http.Client, endpoint string) (bool, error) {
	probeContext, cancel := context.WithTimeout(ctx, llamaServerProbeTimeout)
	defer cancel()
	request, err := http.NewRequestWithContext(probeContext, http.MethodGet, endpoint, nil)
	if err != nil {
		return false, fmt.Errorf("construct llama-server health request: %w", err)
	}
	response, err := client.Do(request)
	if err != nil {
		return false, err
	}
	raw, err := readBoundedResponse(response)
	if err != nil {
		return false, err
	}
	if response.StatusCode == http.StatusServiceUnavailable {
		return false, nil
	}
	if response.StatusCode != http.StatusOK {
		return false, fmt.Errorf("llama-server health returned HTTP %d", response.StatusCode)
	}
	object, err := decodeJSONObject(raw, "llama-server health response")
	if err != nil {
		return false, err
	}
	var status string
	if rawStatus, ok := object["status"]; !ok || json.Unmarshal(rawStatus, &status) != nil || status != "ok" {
		return false, errors.New("llama-server health response did not report status ok")
	}
	return true, nil
}

func (server *llamaServerProcess) waitHealthy() error {
	ctx, cancel := context.WithTimeout(context.Background(), llamaServerReadyTimeout)
	defer cancel()
	endpoint := llamaServerURL(llamaServerHealthPath)
	var lastErr error
	for {
		ready, err := probeHealth(ctx, server.client, endpoint)
		if ready {
			return nil
		}
		if err != nil {
			lastErr = err
		}
		timer := time.NewTimer(llamaServerProbeInterval)
		select {
		case <-ctx.Done():
			timer.Stop()
			if lastErr != nil {
				return fmt.Errorf("llama-server did not become healthy: %w", lastErr)
			}
			return errors.New("llama-server did not become healthy before timeout")
		case <-timer.C:
		}
	}
}

func canonicalCompletionRequest(prompt []byte) ([]byte, error) {
	if !utf8.Valid(prompt) {
		return nil, errors.New("generation request must be UTF-8")
	}
	return json.Marshal(completionRequest{
		MaxTokens: 4096,
		Messages: []chatMessage{
			{Role: "system", Content: llamaServerSystemPrompt},
			{Role: "user", Content: string(prompt)},
		},
		Model:       llamaServerModelAlias,
		Seed:        -1,
		Stream:      false,
		Temperature: 0.80,
		TopK:        40,
		TopP:        0.95,
	})
}

func completionContent(raw []byte) (string, error) {
	object, err := decodeJSONObject(raw, "llama-server completion response")
	if err != nil {
		return "", err
	}
	rawChoices, ok := object["choices"]
	if !ok {
		return "", errors.New("llama-server completion response omitted choices")
	}
	var choices []json.RawMessage
	if err := json.Unmarshal(rawChoices, &choices); err != nil || len(choices) != 1 {
		return "", errors.New("llama-server completion response must contain one choice")
	}
	choice, err := decodeJSONObject(choices[0], "llama-server completion choice")
	if err != nil {
		return "", err
	}
	rawMessage, ok := choice["message"]
	if !ok {
		return "", errors.New("llama-server completion choice omitted message")
	}
	message, err := decodeJSONObject(rawMessage, "llama-server completion message")
	if err != nil {
		return "", err
	}
	rawFinishReason, ok := choice["finish_reason"]
	if !ok {
		return "", errors.New("llama-server completion choice omitted finish_reason")
	}
	var finishReason string
	if err := json.Unmarshal(rawFinishReason, &finishReason); err != nil {
		return "", errors.New("llama-server completion finish_reason is not a string")
	}
	if finishReason != "stop" {
		return "", fmt.Errorf(
			"llama-server completion was not complete: finish_reason=%q",
			finishReason,
		)
	}
	rawContent, ok := message["content"]
	if !ok {
		return "", errors.New("llama-server completion message omitted content")
	}
	var content string
	if err := json.Unmarshal(rawContent, &content); err != nil {
		return "", errors.New("llama-server completion content is not a string")
	}
	if !utf8.ValidString(content) {
		return "", errors.New("llama-server completion content is not UTF-8")
	}
	return content, nil
}

func requestCompletion(
	ctx context.Context,
	client *http.Client,
	endpoint string,
	prompt []byte,
) (string, error) {
	payload, err := canonicalCompletionRequest(prompt)
	if err != nil {
		return "", err
	}
	request, err := http.NewRequestWithContext(
		ctx, http.MethodPost, endpoint, bytes.NewReader(payload),
	)
	if err != nil {
		return "", fmt.Errorf("construct llama-server completion request: %w", err)
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := client.Do(request)
	if err != nil {
		return "", fmt.Errorf("call llama-server completion: %w", err)
	}
	raw, err := readBoundedResponse(response)
	if err != nil {
		return "", err
	}
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("llama-server completion returned HTTP %d", response.StatusCode)
	}
	return completionContent(raw)
}

func (server *llamaServerProcess) complete(prompt []byte) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), llamaServerRequestTimeout)
	defer cancel()
	return requestCompletion(
		ctx,
		server.client,
		llamaServerURL(llamaServerCompletionPath),
		prompt,
	)
}

func (server *llamaServerProcess) stop() error {
	if server.stopped {
		return nil
	}
	server.stopped = true
	if transport, ok := server.client.Transport.(*http.Transport); ok {
		transport.CloseIdleConnections()
	}
	killErr := server.cmd.Process.Kill()
	waitErr := server.cmd.Wait()
	if killErr != nil && !errors.Is(killErr, os.ErrProcessDone) {
		return fmt.Errorf("terminate llama-server: %w", killErr)
	}
	if waitErr != nil {
		var exitError *exec.ExitError
		if !errors.As(waitErr, &exitError) {
			return fmt.Errorf("reap llama-server: %w", waitErr)
		}
	}
	return nil
}

func (server *llamaServerProcess) diagnostics() string {
	parts := make([]string, 0, 2)
	if value := strings.TrimSpace(server.stdout.String()); value != "" {
		parts = append(parts, "llama-server stdout: "+value)
	}
	if value := strings.TrimSpace(server.stderr.String()); value != "" {
		parts = append(parts, "llama-server stderr: "+value)
	}
	return bounded(strings.Join(parts, "\n"), maxServerDiagnosticBytes)
}

func runLlamaServerSession(prompt []byte, generate bool) (string, string, error) {
	server, err := startManagedLlamaServer()
	if err != nil {
		return "", "", err
	}
	var content string
	operationErr := server.waitHealthy()
	if operationErr == nil && generate {
		content, operationErr = server.complete(prompt)
	}
	stopErr := server.stop()
	diagnostics := server.diagnostics()
	if operationErr != nil {
		return "", diagnostics, operationErr
	}
	if stopErr != nil {
		return "", diagnostics, stopErr
	}
	return content, diagnostics, nil
}

func writeDiagnostics(value string) error {
	value = strings.TrimSpace(value)
	if value == "" {
		return nil
	}
	if _, err := fmt.Fprintln(os.Stderr, bounded(value, maxServerDiagnosticBytes)); err != nil {
		return fmt.Errorf("write llama-server diagnostics: %w", err)
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
	if err := validateComponents(componentsPath, buildLockPath, executable, llamaServerPath, modelPath); err != nil {
		return err
	}
	_, diagnostics, err := runLlamaServerSession(nil, false)
	if diagnosticErr := writeDiagnostics(diagnostics); diagnosticErr != nil {
		return diagnosticErr
	}
	if err != nil {
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
	content, diagnostics, runErr := runLlamaServerSession(prompt, true)
	if diagnosticErr := writeDiagnostics(diagnostics); diagnosticErr != nil {
		return diagnosticErr
	}
	if runErr != nil {
		return fmt.Errorf("generation failed: %w", runErr)
	}
	stdout := []byte(content)
	if !utf8.Valid(stdout) {
		return errors.New("generation completion must be UTF-8")
	}
	if len(stdout) > 0 && bytes.Contains(stdout, prompt) {
		return errors.New("generation output echoed the request")
	}
	trimmed := bytes.TrimSpace(stdout)
	for _, marker := range [][]byte{
		[]byte("Loading model"),
		[]byte("available commands:"),
		[]byte("model : /opt/supernova/model.gguf"),
		[]byte("Exiting..."),
	} {
		if bytes.Contains(trimmed, marker) {
			return errors.New("generation output contained a runtime control transcript")
		}
	}
	status := "ANSWERED"
	if len(trimmed) == 0 {
		status = "NO_ANSWER"
		stdout = nil
	}
	response := generationResponse{
		CompletionUTF8: string(stdout),
		Schema:         generationResponseSchema,
		Status:         status,
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(response); err != nil {
		return fmt.Errorf("write generation response: %w", err)
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
		{llamaPath, manifest.LlamaServerSHA256, "llama-server"},
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
