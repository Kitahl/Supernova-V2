package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type fakeManagedServer struct {
	completion     string
	diagnosticText string
	waitErr        error
	completeErr    error
	stopErr        error
	waited         bool
	completed      bool
	stopped        bool
}

func (server *fakeManagedServer) waitHealthy() error {
	server.waited = true
	return server.waitErr
}

func (server *fakeManagedServer) complete(_ []byte) (string, error) {
	server.completed = true
	return server.completion, server.completeErr
}

func (server *fakeManagedServer) stop() error {
	server.stopped = true
	return server.stopErr
}

func (server *fakeManagedServer) diagnostics() string {
	return server.diagnosticText
}

func installFakeManagedServer(t *testing.T, server *fakeManagedServer) {
	t.Helper()
	original := startManagedLlamaServer
	startManagedLlamaServer = func() (managedLlamaServer, error) {
		return server, nil
	}
	t.Cleanup(func() { startManagedLlamaServer = original })
}

func TestParsePreflightExact(t *testing.T) {
	hashA := string(bytes.Repeat([]byte("a"), 64))
	hashB := string(bytes.Repeat([]byte("b"), 64))
	raw := []byte(`{"context":[],"executor_artifact_sha256":"` + hashA + `","model_identity_sha256":"` + hashB + `","operation":"PREFLIGHT","schema":"supernova.hermetic-preflight-request.v1"}`)
	request, err := parsePreflight(raw)
	if err != nil {
		t.Fatal(err)
	}
	if request.ExecutorArtifactSHA256 != hashA || request.ModelIdentitySHA256 != hashB {
		t.Fatal("challenge hashes changed")
	}
}

func TestParsePreflightRejectsExtraField(t *testing.T) {
	hashA := string(bytes.Repeat([]byte("a"), 64))
	raw := []byte(`{"context":[],"executor_artifact_sha256":"` + hashA + `","model_identity_sha256":"` + hashA + `","operation":"PREFLIGHT","schema":"supernova.hermetic-preflight-request.v1","status":"READY"}`)
	if _, err := parsePreflight(raw); err == nil {
		t.Fatal("extra field was accepted")
	}
}

func TestValidateComponents(t *testing.T) {
	dir := t.TempDir()
	paths := []string{filepath.Join(dir, "lock"), filepath.Join(dir, "executor"), filepath.Join(dir, "llama"), filepath.Join(dir, "model")}
	hashes := make([]string, len(paths))
	for index, path := range paths {
		data := []byte{byte(index + 1)}
		if err := os.WriteFile(path, data, 0600); err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(data)
		hashes[index] = hex.EncodeToString(sum[:])
	}
	manifest := componentManifest{
		BuildLockSHA256:   hashes[0],
		ExecutorSHA256:    hashes[1],
		LlamaServerSHA256: hashes[2],
		ModelSHA256:       hashes[3],
		Schema:            componentsSchema,
	}
	raw, _ := json.Marshal(manifest)
	manifestPath := filepath.Join(dir, "components.json")
	if err := os.WriteFile(manifestPath, append(raw, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	if err := validateComponents(manifestPath, paths[0], paths[1], paths[2], paths[3]); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(paths[3], []byte("changed"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := validateComponents(manifestPath, paths[0], paths[1], paths[2], paths[3]); err == nil {
		t.Fatal("changed model was accepted")
	}
}

func TestHandleGenerationFramesOnlyCompletionAndSeparatesDiagnostics(t *testing.T) {
	server := &fakeManagedServer{
		completion:     "exact h\n",
		diagnosticText: "runtime diagnostic",
	}
	installFakeManagedServer(t, server)

	oldStdout, oldStderr := os.Stdout, os.Stderr
	stdoutFile, err := os.CreateTemp(t.TempDir(), "stdout")
	if err != nil {
		t.Fatal(err)
	}
	stderrFile, err := os.CreateTemp(t.TempDir(), "stderr")
	if err != nil {
		t.Fatal(err)
	}
	os.Stdout, os.Stderr = stdoutFile, stderrFile
	t.Cleanup(func() { os.Stdout, os.Stderr = oldStdout, oldStderr })

	if err := handleGeneration([]byte("frozen prompt")); err != nil {
		t.Fatal(err)
	}
	if err := stdoutFile.Close(); err != nil {
		t.Fatal(err)
	}
	if err := stderrFile.Close(); err != nil {
		t.Fatal(err)
	}
	stdoutRaw, err := os.ReadFile(stdoutFile.Name())
	if err != nil {
		t.Fatal(err)
	}
	stderrRaw, err := os.ReadFile(stderrFile.Name())
	if err != nil {
		t.Fatal(err)
	}
	var response generationResponse
	decoder := json.NewDecoder(bytes.NewReader(stdoutRaw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&response); err != nil {
		t.Fatal(err)
	}
	if response.Schema != generationResponseSchema || response.Status != "ANSWERED" || response.CompletionUTF8 != "exact h\n" {
		t.Fatalf("unexpected framed response: %#v", response)
	}
	if strings.Contains(string(stdoutRaw), "runtime diagnostic") {
		t.Fatal("runtime diagnostics leaked into the framed completion")
	}
	if !strings.Contains(string(stderrRaw), "runtime diagnostic") {
		t.Fatal("runtime diagnostics were not kept on stderr")
	}
	if !server.waited || !server.completed || !server.stopped {
		t.Fatal("llama-server lifecycle was incomplete")
	}
}

func TestHandleGenerationRejectsCapturedInteractiveTranscript(t *testing.T) {
	prompt := []byte("FROZEN_THEOREM_NAME=aime_1983_p1")
	transcript := []byte("Loading model...\nllama-cli\navailable commands:\n" + string(prompt) + "\n\\boxed{3}\nExiting...\n")
	server := &fakeManagedServer{completion: string(transcript)}
	installFakeManagedServer(t, server)
	if err := handleGeneration(prompt); err == nil || !strings.Contains(err.Error(), "echoed the request") {
		t.Fatalf("interactive transcript was not rejected: %v", err)
	}
	if !server.stopped {
		t.Fatal("llama-server was not reaped after rejection")
	}
}

func TestCompletionContentReadsOnlyOneChatMessage(t *testing.T) {
	raw := []byte(`{"choices":[{"finish_reason":"stop","message":{"content":"exact tactic","role":"assistant"}}],"created":1}`)
	content, err := completionContent(raw)
	if err != nil {
		t.Fatal(err)
	}
	if content != "exact tactic" {
		t.Fatalf("unexpected completion: %q", content)
	}
}

func TestCompletionContentRejectsAmbiguousChoices(t *testing.T) {
	raw := []byte(`{"choices":[{"message":{"content":"left"}},{"message":{"content":"right"}}]}`)
	if _, err := completionContent(raw); err == nil || !strings.Contains(err.Error(), "one choice") {
		t.Fatalf("ambiguous choices were not rejected: %v", err)
	}
}
