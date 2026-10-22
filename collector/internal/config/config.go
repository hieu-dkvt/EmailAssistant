// Package config đọc/ghi file cấu hình cục bộ của collector
// (profile Thunderbird và các folder được chọn để đồng bộ).
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"time"

	"pmmail/collector/internal/fsutil"
	"pmmail/collector/internal/profile"
)

const CurrentVersion = 1

// FolderChoice lưu quyết định cho một folder. Lưu cả folder bỏ tick
// để lần sau phân biệt folder đã biết với folder mới xuất hiện.
type FolderChoice struct {
	Path     string              `json:"path"`
	Account  string              `json:"account"`
	Kind     profile.AccountKind `json:"kind"`
	Selected bool                `json:"selected"`
}

type Config struct {
	ConfigVersion         int            `json:"config_version"`
	ProfilePath           string         `json:"profile_path"`
	AutoIncludeNewFolders bool           `json:"auto_include_new_folders"`
	ServerURL             string         `json:"server_url,omitempty"` // token lưu riêng (tokenstore)
	Folders               []FolderChoice `json:"folders"`
	SavedAt               time.Time      `json:"saved_at"`
}

// DefaultPath trả <userConfigDir>\PMMailAssistant\collector\config.json.
func DefaultPath(userConfigDir string) string {
	return filepath.Join(userConfigDir, "PMMailAssistant", "collector", "config.json")
}

// Load đọc cấu hình. Thiếu file: (nil, false, nil). File hỏng: (nil, true, err).
func Load(path string) (*Config, bool, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, false, nil
	}
	if err != nil {
		return nil, true, err
	}
	var c Config
	if err := json.Unmarshal(data, &c); err != nil {
		return nil, true, fmt.Errorf("parse %s: %w", path, err)
	}
	return &c, true, nil
}

// Save ghi cấu hình ra file tạm cùng thư mục rồi đổi tên, để không bao giờ để lại file hỏng.
func Save(path string, c *Config) error {
	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	return fsutil.WriteFileAtomic(path, append(data, '\n'), 0o600)
}
