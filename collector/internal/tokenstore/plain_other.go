//go:build !windows

package tokenstore

// Ngoài Windows (chỉ dùng khi dev): lưu nguyên văn trong file quyền 0600.
func protect(data []byte) ([]byte, error)   { return data, nil }
func unprotect(data []byte) ([]byte, error) { return data, nil }
