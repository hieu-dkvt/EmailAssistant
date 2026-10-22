//go:build windows

package tokenstore

import (
	"bytes"
	"unsafe"

	"golang.org/x/sys/windows"
)

// DPAPI phạm vi người dùng hiện tại: người dùng khác hoặc máy khác không giải mã được file.
// entropy chỉ để tránh nhầm với dữ liệu DPAPI của ứng dụng khác; nó nằm sẵn trong file .exe nên
// KHÔNG chống được chương trình độc hại chạy dưới chính tài khoản người dùng.
var entropy = []byte("PMMailAssistant/collector/token/v1")

func blob(b []byte) *windows.DataBlob {
	if len(b) == 0 {
		return &windows.DataBlob{}
	}
	return &windows.DataBlob{Size: uint32(len(b)), Data: &b[0]}
}

func take(out *windows.DataBlob) []byte {
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	return bytes.Clone(unsafe.Slice(out.Data, out.Size))
}

func protect(data []byte) ([]byte, error) {
	var out windows.DataBlob
	err := windows.CryptProtectData(blob(data), nil, blob(entropy), 0, nil,
		windows.CRYPTPROTECT_UI_FORBIDDEN, &out)
	if err != nil {
		return nil, err
	}
	return take(&out), nil
}

func unprotect(data []byte) ([]byte, error) {
	var out windows.DataBlob
	err := windows.CryptUnprotectData(blob(data), nil, blob(entropy), 0, nil,
		windows.CRYPTPROTECT_UI_FORBIDDEN, &out)
	if err != nil {
		return nil, err
	}
	return take(&out), nil
}
