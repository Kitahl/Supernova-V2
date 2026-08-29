package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

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
		BuildLockSHA256: hashes[0],
		ExecutorSHA256:  hashes[1],
		LlamaCLISHA256:  hashes[2],
		ModelSHA256:     hashes[3],
		Schema:          componentsSchema,
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
