package config

import (
	"fmt"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"pmmail/collector/internal/profile"
)

// FolderState là trạng thái hiển thị của một folder trên màn hình setting.
type FolderState struct {
	Selected bool
	IsNew    bool // chưa có trong cấu hình đã lưu của cùng profile
}

// SameProfile so sánh đường dẫn profile, không phân biệt hoa thường (Windows).
func SameProfile(a, b string) bool {
	return strings.EqualFold(filepath.Clean(a), filepath.Clean(b))
}

// Resolve tính trạng thái chọn cho mọi folder có mbox, khóa theo Folder.Path.
// Chưa có cấu hình cho profile này: dùng mặc định (bỏ tick Trash, Junk, Drafts…).
// Có cấu hình: theo quy tắc của decide.
func Resolve(cfg *Config, profileDir string, accounts []profile.Account) map[string]FolderState {
	out := map[string]FolderState{}
	if cfg == nil || !SameProfile(cfg.ProfilePath, profileDir) {
		walk(accounts, func(_ profile.Account, f *profile.Folder) {
			out[f.Path] = FolderState{Selected: !profile.ExcludedByDefault(f.Path)}
		})
		return out
	}
	known := indexDecisions(cfg.Folders)
	walk(accounts, func(_ profile.Account, f *profile.Folder) {
		out[f.Path] = cfg.decide(known, f.Path)
	})
	return out
}

// Build tạo cấu hình từ cây folder hiện tại và quyết định của người dùng
// cho các folder đã hiển thị (path → chọn/bỏ). Folder không có trong decisions
// (người dùng chưa thấy) không được ghi, để lần sau vẫn là folder mới.
// Path trong decisions mà không phải folder có mbox trong cây thì trả lỗi.
func Build(profileDir string, autoInclude bool, accounts []profile.Account, decisions map[string]bool, now time.Time) (*Config, error) {
	c := &Config{
		ConfigVersion:         CurrentVersion,
		ProfilePath:           profileDir,
		AutoIncludeNewFolders: autoInclude,
		Folders:               []FolderChoice{},
		SavedAt:               now,
	}
	seen := map[string]bool{}
	walk(accounts, func(a profile.Account, f *profile.Folder) {
		seen[f.Path] = true
		if sel, ok := decisions[f.Path]; ok {
			c.Folders = append(c.Folders, FolderChoice{Path: f.Path, Account: a.Name, Kind: a.Kind, Selected: sel})
		}
	})
	var unknown []string
	for p := range decisions {
		if !seen[p] {
			unknown = append(unknown, p)
		}
	}
	if len(unknown) > 0 {
		sort.Strings(unknown)
		return nil, &UnknownFolderError{Path: unknown[0]}
	}
	return c, nil
}

// UnknownFolderError: path được gửi lên không phải folder có mbox trong cây hiện tại.
type UnknownFolderError struct {
	Path string
}

func (e *UnknownFolderError) Error() string {
	return fmt.Sprintf("folder không tồn tại: %s", e.Path)
}

// ShouldScan cho bước quét biết có được đọc folder relPath hay không.
func (c *Config) ShouldScan(relPath string) bool {
	return c.decide(indexDecisions(c.Folders), relPath).Selected
}

// savedDecisions: quyết định đã lưu, khóa là path viết thường
// (tên file trên Windows không phân biệt hoa thường).
type savedDecisions map[string]bool

func indexDecisions(folders []FolderChoice) savedDecisions {
	m := make(savedDecisions, len(folders))
	for _, f := range folders {
		m[strings.ToLower(f.Path)] = f.Selected
	}
	return m
}

// decide áp quy tắc cho một folder:
//  1. Đã có quyết định → giữ nguyên.
//  2. Folder mới thuộc danh sách mặc định bỏ tick → bỏ tick.
//  3. Folder mới mà folder cha gần nhất có quyết định đã bị bỏ tick → bỏ tick
//     (người dùng loại "Cá nhân" thì "Cá nhân/Ngân hàng" tạo sau cũng bị loại).
//  4. Còn lại → theo AutoIncludeNewFolders.
func (c *Config) decide(known savedDecisions, relPath string) FolderState {
	if sel, ok := known[strings.ToLower(relPath)]; ok {
		return FolderState{Selected: sel}
	}
	if profile.ExcludedByDefault(relPath) {
		return FolderState{IsNew: true}
	}
	for _, anc := range ancestors(relPath) {
		if sel, ok := known[strings.ToLower(anc)]; ok {
			if !sel {
				return FolderState{IsNew: true}
			}
			break
		}
	}
	return FolderState{IsNew: true, Selected: c.AutoIncludeNewFolders}
}

// ancestors trả path các folder cha, gần nhất trước:
// "ImapMail/a/X.sbd/Y.sbd/Z" → "ImapMail/a/X.sbd/Y", "ImapMail/a/X".
func ancestors(relPath string) []string {
	segs := strings.Split(relPath, "/")
	var out []string
	for i := len(segs) - 1; i >= 3; i-- {
		parent := append([]string{}, segs[:i]...)
		parent[i-1] = strings.TrimSuffix(parent[i-1], ".sbd")
		out = append(out, strings.Join(parent, "/"))
	}
	return out
}

// walk gọi fn cho mọi folder có mbox, cha trước con.
func walk(accounts []profile.Account, fn func(profile.Account, *profile.Folder)) {
	var rec func(a profile.Account, fs []*profile.Folder)
	rec = func(a profile.Account, fs []*profile.Folder) {
		for _, f := range fs {
			if f.HasMbox {
				fn(a, f)
			}
			rec(a, f.Children)
		}
	}
	for _, a := range accounts {
		rec(a, a.Folders)
	}
}
