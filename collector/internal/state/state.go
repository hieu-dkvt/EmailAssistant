// Package state lưu vị trí đã đồng bộ của từng folder mbox (state.json cục bộ).
//
// Spec: docs/superpowers/specs/2026-10-17-incremental-sync-design.md mục 3. Đổi server hoặc
// profile thì state cũ không còn đúng (mail chưa có trên server mới) và bị bỏ.
package state

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"

	"pmmail/collector/internal/fsutil"
)

const Version = 1

type Folder struct {
	Offset int64 `json:"offset"` // đã gửi xong tới vị trí này
	// Tail: SHA-256 (hex) của tối đa 4 KiB ngay trước Offset. Lần sau chỉ đọc tiếp từ Offset nếu
	// đoạn này không đổi (compact làm dịch nội dung); thiếu hoặc khác → đọc lại từ đầu.
	Tail       string    `json:"tail,omitempty"`
	Size       int64     `json:"size"` // kích thước file lúc đọc xong lần cuối
	MTime      time.Time `json:"mtime"`
	LastSyncAt time.Time `json:"last_sync_at"`
}

type State struct {
	StateVersion int               `json:"state_version"`
	ServerURL    string            `json:"server_url"`
	ProfilePath  string            `json:"profile_path"`
	Folders      map[string]Folder `json:"folders"`
}

// DefaultPath trả <LocalAppData>\PMMailAssistant\collector\state.json.
func DefaultPath(localAppData string) string {
	return filepath.Join(localAppData, "PMMailAssistant", "collector", "state.json")
}

func New(serverURL, profilePath string) *State {
	return &State{
		StateVersion: Version,
		ServerURL:    serverURL,
		ProfilePath:  profilePath,
		Folders:      map[string]Folder{},
	}
}

// Load đọc state. Thiếu file → state rỗng. File hỏng, khác phiên bản, khác server hoặc
// profile → state rỗng và reset = true.
func Load(path, serverURL, profilePath string) (s *State, reset bool) {
	data, err := os.ReadFile(path)
	if err != nil {
		return New(serverURL, profilePath), !os.IsNotExist(err)
	}
	var loaded State
	if json.Unmarshal(data, &loaded) != nil || loaded.StateVersion != Version ||
		!sameURL(loaded.ServerURL, serverURL) || !samePath(loaded.ProfilePath, profilePath) {
		return New(serverURL, profilePath), true
	}
	if loaded.Folders == nil {
		loaded.Folders = map[string]Folder{}
	}
	return &loaded, false
}

func (s *State) Save(path string) error {
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	return fsutil.WriteFileAtomic(path, append(data, '\n'), 0o600)
}

// ResetFolder quên vị trí của folder: lần đồng bộ sau đọc lại từ đầu.
func (s *State) ResetFolder(relPath string) { delete(s.Folders, relPath) }

func sameURL(a, b string) bool {
	return strings.EqualFold(strings.TrimRight(a, "/"), strings.TrimRight(b, "/"))
}

func samePath(a, b string) bool {
	return strings.EqualFold(filepath.Clean(a), filepath.Clean(b))
}
