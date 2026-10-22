package state

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestDefaultPath(t *testing.T) {
	got := DefaultPath(filepath.Join("L", "AppData", "Local"))
	want := filepath.Join("L", "AppData", "Local", "PMMailAssistant", "collector", "state.json")
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestSaveLoadRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sub", "state.json")
	s := New("http://127.0.0.1:8000", `C:\p`)
	mt := time.Date(2026, 10, 17, 9, 0, 0, 0, time.UTC)
	s.Folders["ImapMail/a/INBOX"] = Folder{Offset: 120, Size: 500, MTime: mt}
	if err := s.Save(path); err != nil {
		t.Fatalf("Save: %v", err)
	}
	got, reset := Load(path, "http://127.0.0.1:8000", `c:\P`)
	if reset {
		t.Fatal("unexpected reset")
	}
	f := got.Folders["ImapMail/a/INBOX"]
	if f.Offset != 120 || f.Size != 500 || !f.MTime.Equal(mt) {
		t.Errorf("folder = %+v", f)
	}
	entries, _ := os.ReadDir(filepath.Dir(path))
	if len(entries) != 1 {
		t.Errorf("leftover temp files: %v", entries)
	}
}

func TestLoadResetsOnServerOrProfileChange(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	s := New("http://127.0.0.1:8000", `C:\p`)
	s.Folders["x"] = Folder{Offset: 10}
	if err := s.Save(path); err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct{ server, profile string }{
		{"http://other:8000", `C:\p`},
		{"http://127.0.0.1:8000", `C:\q`},
	} {
		got, reset := Load(path, c.server, c.profile)
		if !reset || len(got.Folders) != 0 || got.ServerURL != c.server || got.ProfilePath != c.profile {
			t.Errorf("%+v: reset=%v state=%+v", c, reset, got)
		}
	}
}

func TestLoadMissingOrCorrupt(t *testing.T) {
	dir := t.TempDir()
	if got, _ := Load(filepath.Join(dir, "none.json"), "s", "p"); got == nil || len(got.Folders) != 0 {
		t.Errorf("missing: %+v", got)
	}
	bad := filepath.Join(dir, "bad.json")
	if err := os.WriteFile(bad, []byte("{oops"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got, reset := Load(bad, "s", "p"); !reset || len(got.Folders) != 0 {
		t.Errorf("corrupt: reset=%v %+v", reset, got)
	}
}

func TestResetFolder(t *testing.T) {
	s := New("s", "p")
	s.Folders["a"] = Folder{Offset: 1}
	s.Folders["b"] = Folder{Offset: 2}
	s.ResetFolder("a")
	if _, ok := s.Folders["a"]; ok || s.Folders["b"].Offset != 2 {
		t.Errorf("folders = %+v", s.Folders)
	}
}
