//go:build windows

package syncer

import (
	"errors"
	"os"
	"syscall"
)

const (
	accessDelete          = 0x00010000
	fileFlagDeleteOnClose = 0x04000000
	// Mở chính file khóa, không đi theo symlink: nếu sync.lock bị thay bằng symlink trỏ tới một
	// mbox thì không khóa và không xóa mbox đó khi đóng.
	fileFlagOpenReparsePoint = 0x00200000
	errorSharingViolation    = syscall.Errno(32)
)

// lockFile mở file không chia sẻ (share mode 0): handle thứ hai, kể cả trong cùng tiến trình,
// bị ERROR_SHARING_VIOLATION. File tự xóa khi handle đóng, kể cả khi tiến trình bị kill.
func lockFile(path string) (func(), error) {
	p, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return nil, &os.PathError{Op: "lock", Path: path, Err: err}
	}
	h, err := syscall.CreateFile(p, syscall.GENERIC_READ|syscall.GENERIC_WRITE|accessDelete, 0, nil,
		syscall.OPEN_ALWAYS, syscall.FILE_ATTRIBUTE_NORMAL|fileFlagDeleteOnClose|fileFlagOpenReparsePoint, 0)
	if errors.Is(err, errorSharingViolation) {
		return nil, ErrAlreadyRunning
	}
	if err != nil {
		return nil, &os.PathError{Op: "lock", Path: path, Err: err}
	}
	return func() { _ = syscall.CloseHandle(h) }, nil
}
