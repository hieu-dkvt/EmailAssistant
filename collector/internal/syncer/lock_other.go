//go:build !windows

package syncer

import (
	"errors"
	"os"
	"syscall"
)

// lockFile dùng flock: khóa gắn với file đang mở, tự nhả khi tiến trình chết. Chỉ dùng khi dev.
func lockFile(path string) (func(), error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		f.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return nil, ErrAlreadyRunning
		}
		return nil, err
	}
	return func() {
		_ = os.Remove(path)
		_ = f.Close()
	}, nil
}
