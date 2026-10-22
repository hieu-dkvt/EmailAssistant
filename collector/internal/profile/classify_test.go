package profile

import (
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestClassify(t *testing.T) {
	tests := []struct {
		name    string
		entries []string // kết thúc bằng "/" là thư mục, còn lại là file
		want    DirKind
	}{
		{"imap only", []string{"ImapMail/"}, DirProfile},
		{"mail only", []string{"Mail/"}, DirProfile},
		{"profiles root", []string{"profiles.ini"}, DirProfilesRoot},
		{"profile with profiles.ini", []string{"ImapMail/", "Mail/", "profiles.ini"}, DirProfile},
		{"empty", nil, DirNotProfile},
		{"mail is a file", []string{"Mail"}, DirNotProfile},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dir := t.TempDir()
			for _, e := range tt.entries {
				p := filepath.Join(dir, filepath.FromSlash(strings.TrimSuffix(e, "/")))
				if strings.HasSuffix(e, "/") {
					if err := os.MkdirAll(p, 0o755); err != nil {
						t.Fatal(err)
					}
				} else {
					writeFile(t, p, "")
				}
			}
			got, err := Classify(dir)
			if err != nil {
				t.Fatalf("Classify: %v", err)
			}
			if got != tt.want {
				t.Errorf("got %v, want %v", got, tt.want)
			}
		})
	}
}

func TestClassify_File(t *testing.T) {
	f := filepath.Join(t.TempDir(), "file")
	writeFile(t, f, "x")
	got, err := Classify(f)
	if err != nil || got != DirNotProfile {
		t.Errorf("got %v, %v; want DirNotProfile, nil", got, err)
	}
}

func TestClassify_Missing(t *testing.T) {
	_, err := Classify(filepath.Join(t.TempDir(), "nope"))
	if !errors.Is(err, fs.ErrNotExist) {
		t.Errorf("want fs.ErrNotExist, got %v", err)
	}
}
