//go:build windows

package mbox

import (
	"os"
	"path/filepath"
	"testing"
)

// Thunderbird compact: ghi nstmp rồi đổi tên đè lên mbox. Collector đang đọc không được chặn việc đó.
func TestOpenAllowsRenameAndDelete(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "INBOX")
	if err := os.WriteFile(p, []byte(msg1), 0o644); err != nil {
		t.Fatal(err)
	}
	f, err := Open(p)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer f.Close()

	if err := os.Rename(p, p+".renamed"); err != nil {
		t.Fatalf("rename while open: %v", err)
	}
	if err := os.Remove(p + ".renamed"); err != nil {
		t.Fatalf("delete while open: %v", err)
	}
	buf := make([]byte, 4)
	if _, err := f.Read(buf); err != nil || string(buf) != "From" {
		t.Errorf("read after rename: %q %v", buf, err)
	}
}
