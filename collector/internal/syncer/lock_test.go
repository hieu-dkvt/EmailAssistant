package syncer

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func TestLockExclusive(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sync.lock")
	release, err := AcquireLock(path)
	if err != nil {
		t.Fatalf("first acquire: %v", err)
	}
	if _, err := AcquireLock(path); !errors.Is(err, ErrAlreadyRunning) {
		t.Errorf("second acquire err = %v", err)
	}
	release()
	release2, err := AcquireLock(path)
	if err != nil {
		t.Fatalf("acquire after release: %v", err)
	}
	release2()
}

func TestStaleLockReplaced(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sync.lock")
	if err := os.WriteFile(path, []byte("old"), 0o600); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-7 * time.Hour)
	if err := os.Chtimes(path, old, old); err != nil {
		t.Fatal(err)
	}
	release, err := AcquireLock(path)
	if err != nil {
		t.Fatalf("stale lock not replaced: %v", err)
	}
	release()
}

// sync.lock bị thay bằng symlink trỏ tới file khác (vd. một mbox): khóa không được xóa file đó.
func TestLockDoesNotFollowSymlink(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "INBOX")
	if err := os.WriteFile(target, []byte("From - x\n\nmail\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dir, "sync.lock")
	if err := os.Symlink(target, link); err != nil {
		t.Skipf("không tạo được symlink (cần quyền hoặc Developer Mode): %v", err)
	}
	release, err := AcquireLock(link)
	if err != nil {
		t.Fatalf("acquire: %v", err)
	}
	release()
	if b, err := os.ReadFile(target); err != nil || string(b) != "From - x\n\nmail\n" {
		t.Errorf("symlink target damaged: %q, %v", b, err)
	}
}

// Tiến trình giữ khóa chết (bị kill, mất điện) thì khóa phải tự nhả, không chặn đồng bộ.
func TestLockReleasedWhenProcessDies(t *testing.T) {
	if p := os.Getenv("PMMAIL_LOCK_HELPER"); p != "" {
		if _, err := AcquireLock(p); err != nil {
			os.Exit(2)
		}
		os.Exit(0) // thoát mà không gọi release
	}
	path := filepath.Join(t.TempDir(), "sync.lock")
	cmd := exec.Command(os.Args[0], "-test.run=^TestLockReleasedWhenProcessDies$")
	cmd.Env = append(os.Environ(), "PMMAIL_LOCK_HELPER="+path)
	if err := cmd.Run(); err != nil {
		t.Fatalf("helper: %v", err)
	}
	release, err := AcquireLock(path)
	if err != nil {
		t.Fatalf("lock left by dead process blocks sync: %v", err)
	}
	release()
}
