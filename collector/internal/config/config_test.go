package config

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"pmmail/collector/internal/profile"
)

func sampleConfig() *Config {
	return &Config{
		ConfigVersion:         1,
		ProfilePath:           `C:\p`,
		AutoIncludeNewFolders: true,
		Folders: []FolderChoice{
			{Path: "ImapMail/a/INBOX", Account: "a", Kind: profile.KindIMAPCache, Selected: true},
		},
		SavedAt: time.Date(2026, 10, 16, 10, 0, 0, 0, time.FixedZone("ICT", 7*3600)),
	}
}

func TestDefaultPath(t *testing.T) {
	got := DefaultPath(filepath.Join("X", "AppData"))
	want := filepath.Join("X", "AppData", "PMMailAssistant", "collector", "config.json")
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestSaveLoadRoundTrip(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "sub")
	path := filepath.Join(dir, "config.json")
	in := sampleConfig()

	if err := Save(path, in); err != nil {
		t.Fatalf("Save: %v", err)
	}
	out, found, err := Load(path)
	if err != nil || !found {
		t.Fatalf("Load: found=%v err=%v", found, err)
	}
	if !out.SavedAt.Equal(in.SavedAt) {
		t.Errorf("SavedAt = %v, want %v", out.SavedAt, in.SavedAt)
	}
	out.SavedAt, in.SavedAt = time.Time{}, time.Time{}
	if !reflect.DeepEqual(out, in) {
		t.Errorf("got %+v\nwant %+v", out, in)
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name() != "config.json" {
		t.Errorf("dir entries = %v, want only config.json", entries)
	}
}

func TestLoadMissing(t *testing.T) {
	cfg, found, err := Load(filepath.Join(t.TempDir(), "none.json"))
	if cfg != nil || found || err != nil {
		t.Errorf("got %v, %v, %v; want nil, false, nil", cfg, found, err)
	}
}

func TestLoadCorrupt(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, []byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, found, err := Load(path)
	if cfg != nil || !found || err == nil {
		t.Fatalf("got %v, %v, %v; want nil, true, error", cfg, found, err)
	}

	if err := Save(path, sampleConfig()); err != nil {
		t.Fatalf("Save over corrupt file: %v", err)
	}
	if _, _, err := Load(path); err != nil {
		t.Errorf("Load after overwrite: %v", err)
	}
}
