// Package tokenstore lưu token xác thực của collector, mã hóa bằng DPAPI trên Windows.
//
// Spec: docs/superpowers/specs/2026-10-17-sync-ui-design.md mục 2. Token không bao giờ được
// trả về trình duyệt hay ghi vào log.
package tokenstore

import (
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"pmmail/collector/internal/fsutil"
)

// Entry: token và server (origin, dạng scheme://host:port) mà token được nhập cho. Collector
// chỉ gửi token tới đúng origin này.
type Entry struct {
	Origin string `json:"origin"`
	Token  string `json:"token"`
}

// DefaultPath trả <LocalAppData>\PMMailAssistant\collector\token.dat.
func DefaultPath(localAppData string) string {
	return filepath.Join(localAppData, "PMMailAssistant", "collector", "token.dat")
}

func Save(path string, e Entry) error {
	plain, err := json.Marshal(e)
	if err != nil {
		return err
	}
	data, err := protect(plain)
	if err != nil {
		return err
	}
	return fsutil.WriteFileAtomic(path, data, 0o600)
}

// Load trả Entry rỗng khi chưa lưu token. File của bản cũ (chỉ có token) → Origin rỗng.
func Load(path string) (Entry, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return Entry{}, nil
	}
	if err != nil {
		return Entry{}, err
	}
	plain, err := unprotect(data)
	if err != nil {
		return Entry{}, err
	}
	var e Entry
	if strings.HasPrefix(string(plain), "{") && json.Unmarshal(plain, &e) == nil {
		return e, nil
	}
	return Entry{Token: string(plain)}, nil
}
