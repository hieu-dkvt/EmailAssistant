package tokenstore

import (
	"bytes"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestSaveLoad(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sub", "token.dat")
	want := Entry{Origin: "https://mail.company.example", Token: "pmm_SECRET-TOKEN-1234567890"}
	if err := Save(path, want); err != nil {
		t.Fatalf("Save: %v", err)
	}
	got, err := Load(path)
	if err != nil || got != want {
		t.Fatalf("Load = %+v, %v", got, err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS == "windows" && bytes.Contains(raw, []byte("SECRET-TOKEN")) {
		t.Error("token stored in plain text (DPAPI expected on Windows)")
	}
}

// File của bản cũ chỉ chứa token: đọc được, nhưng không gắn với server nào.
func TestLoadLegacyTokenHasNoOrigin(t *testing.T) {
	path := filepath.Join(t.TempDir(), "token.dat")
	data, err := protect([]byte("pmm_old"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := Load(path)
	if err != nil || got != (Entry{Token: "pmm_old"}) {
		t.Errorf("got %+v, %v", got, err)
	}
}

func TestLoadMissing(t *testing.T) {
	got, err := Load(filepath.Join(t.TempDir(), "none.dat"))
	if got != (Entry{}) || err != nil {
		t.Errorf("got %+v, %v", got, err)
	}
}

func TestLoadTamperedFails(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("DPAPI chỉ có trên Windows")
	}
	path := filepath.Join(t.TempDir(), "token.dat")
	if err := os.WriteFile(path, []byte("not dpapi data"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil {
		t.Error("expected error for tampered token file")
	}
}
