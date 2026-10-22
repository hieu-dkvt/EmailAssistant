//go:build windows

package mbox

import (
	"os"
	"syscall"
)

const fileFlagSequentialScan = 0x08000000

// Open mở mbox chỉ đọc mà không khóa: cho phép Thunderbird ghi, đổi tên, xóa file trong lúc
// collector đang đọc (os.Open của Go thiếu FILE_SHARE_DELETE, làm hỏng thao tác compact).
func Open(path string) (*os.File, error) {
	p, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return nil, &os.PathError{Op: "open", Path: path, Err: err}
	}
	h, err := syscall.CreateFile(
		p,
		syscall.GENERIC_READ,
		syscall.FILE_SHARE_READ|syscall.FILE_SHARE_WRITE|syscall.FILE_SHARE_DELETE,
		nil,
		syscall.OPEN_EXISTING,
		syscall.FILE_ATTRIBUTE_NORMAL|fileFlagSequentialScan,
		0,
	)
	if err != nil {
		return nil, &os.PathError{Op: "open", Path: path, Err: err}
	}
	return os.NewFile(uintptr(h), path), nil
}
