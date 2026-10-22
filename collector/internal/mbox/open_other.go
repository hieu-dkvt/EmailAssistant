//go:build !windows

package mbox

import "os"

// Open mở mbox chỉ đọc (trên Unix, mở file không chặn việc đổi tên/xóa).
func Open(path string) (*os.File, error) { return os.Open(path) }
