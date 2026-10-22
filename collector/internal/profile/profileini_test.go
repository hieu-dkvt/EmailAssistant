package profile

import (
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// bom là UTF-8 BOM mà Notepad hay ghi vào đầu file .ini.
var bom = string([]byte{0xEF, 0xBB, 0xBF})

func TestParseProfilesINI_RelativeAbsoluteDefault(t *testing.T) {
	base := t.TempDir()
	abs := filepath.Join(t.TempDir(), "old")
	// BOM đứng ngay trước [Profile1]: không cắt BOM thì mất profile "work".
	ini := bom + "[Profile1]\r\nName=work\r\nIsRelative=1\r\nPath=Profiles/x8k2.default-release\r\n\r\n" +
		"[General]\r\nStartWithLastProfile=1\r\n\r\n" +
		"[Profile0]\r\nName=old\r\nIsRelative=0\r\nPath=" + abs + "\r\nDefault=1\r\n"

	got, err := ParseProfilesINI(strings.NewReader(ini), base)
	if err != nil {
		t.Fatalf("ParseProfilesINI: %v", err)
	}
	want := []Profile{
		{Name: "work", Path: filepath.Join(base, "Profiles", "x8k2.default-release"), IsDefault: false},
		{Name: "old", Path: abs, IsDefault: true},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %+v\nwant %+v", got, want)
	}
}

func TestParseProfilesINI_InstallSectionOverridesDefault(t *testing.T) {
	base := t.TempDir()
	ini := "[Profile0]\nName=a\nIsRelative=1\nPath=Profiles/a\nDefault=1\n\n" +
		"[Profile1]\nName=b\nIsRelative=1\nPath=Profiles/b\n\n" +
		"[Install4F96D1932A9F858E]\nDefault=Profiles/b\nLocked=1\n"

	got, err := ParseProfilesINI(strings.NewReader(ini), base)
	if err != nil {
		t.Fatalf("ParseProfilesINI: %v", err)
	}
	if len(got) != 2 || got[0].IsDefault || !got[1].IsDefault {
		t.Errorf("want only b default, got %+v", got)
	}
}

func TestProfilesInRoot_InstallsINI(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "profiles.ini"),
		"[Profile0]\nName=a\nIsRelative=1\nPath=Profiles/a\nDefault=1\n\n"+
			"[Profile1]\nName=b\nIsRelative=1\nPath=Profiles/b\n")
	writeFile(t, filepath.Join(root, "installs.ini"), "[ABC]\nDefault=Profiles/b\nLocked=1\n")

	got, err := ProfilesInRoot(root)
	if err != nil {
		t.Fatalf("ProfilesInRoot: %v", err)
	}
	want := []Profile{
		{Name: "a", Path: filepath.Join(root, "Profiles", "a"), IsDefault: false},
		{Name: "b", Path: filepath.Join(root, "Profiles", "b"), IsDefault: true},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %+v\nwant %+v", got, want)
	}
}

func TestProfilesInRoot_Missing(t *testing.T) {
	_, err := ProfilesInRoot(t.TempDir())
	if !errors.Is(err, fs.ErrNotExist) {
		t.Errorf("want fs.ErrNotExist, got %v", err)
	}
}

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}
