package settingsui

import (
	"encoding/json"
	"errors"
	"io/fs"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"pmmail/collector/internal/config"
	"pmmail/collector/internal/profile"
)

// Thông điệp lỗi hiển thị cho người dùng.
const (
	msgNeedAbsPath   = "Cần đường dẫn đầy đủ"
	msgNotFound      = "Không tìm thấy thư mục"
	msgNotProfile    = "Không phải profile Thunderbird"
	msgNotDir        = "Không phải thư mục"
	msgReadFailed    = "Không đọc được thư mục"
	msgBadRequest    = "Dữ liệu gửi lên không hợp lệ"
	msgSaveFailed    = "Không lưu được cấu hình"
	msgUnknownFolder = "Folder không tồn tại: "
	msgCorruptConfig = "File cấu hình bị hỏng, đang dùng lựa chọn mặc định. Bấm Lưu sẽ ghi đè."
)

type profileDTO struct {
	Name      string `json:"name"`
	Path      string `json:"path"`
	IsDefault bool   `json:"is_default"`
}

type folderDTO struct {
	Path            string      `json:"path"`
	Name            string      `json:"name"`
	HasMbox         bool        `json:"has_mbox"`
	Size            int64       `json:"size"`
	ModTime         time.Time   `json:"mod_time"`
	Selected        bool        `json:"selected"`
	IsNew           bool        `json:"is_new"`
	DefaultExcluded bool        `json:"default_excluded"`
	Children        []folderDTO `json:"children"`
}

type accountDTO struct {
	Name        string              `json:"name"`
	Kind        profile.AccountKind `json:"kind"`
	Unsupported string              `json:"unsupported"`
	Folders     []folderDTO         `json:"folders"`
}

type profileFoldersResp struct {
	Kind                  string       `json:"kind"` // "profile"
	ProfilePath           string       `json:"profile_path"`
	AutoIncludeNewFolders bool         `json:"auto_include_new_folders"`
	ConfigWarning         string       `json:"config_warning,omitempty"`
	Accounts              []accountDTO `json:"accounts"`
}

type profilesRootResp struct {
	Kind     string       `json:"kind"` // "profiles_root"
	Profiles []profileDTO `json:"profiles"`
}

type browseEntry struct {
	Name string `json:"name"`
	Path string `json:"path"`
}

type browseResp struct {
	Path   string        `json:"path"`
	Parent string        `json:"parent"`
	Dirs   []browseEntry `json:"dirs"`
}

// saveReq.Folders chứa quyết định cho mọi folder trang đã hiển thị (path → chọn).
// Folder trang chưa hiển thị không có trong map và vẫn là "mới" ở lần sau.
type saveReq struct {
	ProfilePath           string          `json:"profile_path"`
	AutoIncludeNewFolders bool            `json:"auto_include_new_folders"`
	Folders               map[string]bool `json:"folders"`
}

func (s *Server) apiRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /api/profiles", s.handleProfiles)
	mux.HandleFunc("GET /api/browse", s.handleBrowse)
	mux.HandleFunc("GET /api/folders", s.handleFolders)
	mux.HandleFunc("POST /api/config", s.handleSaveConfig)
}

func (s *Server) handleProfiles(w http.ResponseWriter, _ *http.Request) {
	ps, err := profile.ProfilesInRoot(filepath.Join(s.opts.AppData, "Thunderbird"))
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		slog.Warn("cannot read profiles.ini", "err", err)
	}
	resp := struct {
		Detected         []profileDTO `json:"detected"`
		SavedProfilePath string       `json:"saved_profile_path"`
	}{Detected: toProfileDTOs(ps)}
	if cfg, _, err := config.Load(s.opts.ConfigPath); err == nil && cfg != nil {
		resp.SavedProfilePath = cfg.ProfilePath
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) handleFolders(w http.ResponseWriter, r *http.Request) {
	p := cleanUserPath(r.URL.Query().Get("profile"))
	kind, ok := classifyPath(w, p)
	if !ok {
		return
	}
	switch kind {
	case profile.DirNotProfile:
		writeError(w, http.StatusBadRequest, msgNotProfile)
		return
	case profile.DirProfilesRoot:
		ps, err := profile.ProfilesInRoot(p)
		if err != nil {
			writeError(w, http.StatusBadRequest, msgNotProfile)
			return
		}
		writeJSON(w, http.StatusOK, profilesRootResp{Kind: "profiles_root", Profiles: toProfileDTOs(ps)})
		return
	}

	accounts, err := profile.ScanAccounts(p)
	if err != nil {
		slog.Error("scan profile", "path", p, "err", err)
		writeError(w, http.StatusInternalServerError, msgReadFailed)
		return
	}
	resp := profileFoldersResp{Kind: "profile", ProfilePath: p, Accounts: []accountDTO{}}
	cfg, _, err := config.Load(s.opts.ConfigPath)
	if err != nil {
		slog.Warn("config file unreadable, using defaults", "path", s.opts.ConfigPath, "err", err)
		resp.ConfigWarning = msgCorruptConfig
		cfg = nil
	}
	if cfg != nil && config.SameProfile(cfg.ProfilePath, p) {
		resp.AutoIncludeNewFolders = cfg.AutoIncludeNewFolders
	}
	states := config.Resolve(cfg, p, accounts)
	for _, a := range accounts {
		resp.Accounts = append(resp.Accounts, accountDTO{
			Name: a.Name, Kind: a.Kind, Unsupported: a.Unsupported,
			Folders: toFolderDTOs(a.Folders, states),
		})
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) handleSaveConfig(w http.ResponseWriter, r *http.Request) {
	var req saveReq
	r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, msgBadRequest)
		return
	}
	if s.sync.running() {
		writeError(w, http.StatusConflict, msgSyncing)
		return
	}
	p := cleanUserPath(req.ProfilePath)
	kind, ok := classifyPath(w, p)
	if !ok {
		return
	}
	if kind != profile.DirProfile {
		writeError(w, http.StatusBadRequest, msgNotProfile)
		return
	}
	accounts, err := profile.ScanAccounts(p)
	if err != nil {
		slog.Error("scan profile", "path", p, "err", err)
		writeError(w, http.StatusInternalServerError, msgReadFailed)
		return
	}
	cfg, err := config.Build(p, req.AutoIncludeNewFolders, accounts, req.Folders, time.Now())
	var unknown *config.UnknownFolderError
	if errors.As(err, &unknown) {
		writeError(w, http.StatusBadRequest, msgUnknownFolder+unknown.Path)
		return
	}
	if err != nil {
		writeError(w, http.StatusBadRequest, msgBadRequest)
		return
	}
	// Giữ kết nối server đã lưu (trang chỉ gửi profile và folder).
	if old, found, err := config.Load(s.opts.ConfigPath); err == nil && found {
		cfg.ServerURL = old.ServerURL
	}
	if err := config.Save(s.opts.ConfigPath, cfg); err != nil {
		slog.Error("save config", "path", s.opts.ConfigPath, "err", err)
		writeError(w, http.StatusInternalServerError, msgSaveFailed)
		return
	}
	n := 0
	for _, f := range cfg.Folders {
		if f.Selected {
			n++
		}
	}
	slog.Info("config saved", "path", s.opts.ConfigPath, "folders", len(cfg.Folders), "selected", n)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "saved_at": cfg.SavedAt})
}

func (s *Server) handleBrowse(w http.ResponseWriter, r *http.Request) {
	p := cleanUserPath(r.URL.Query().Get("path"))
	resp := browseResp{Dirs: []browseEntry{}}
	if p == "" {
		resp.Dirs = append(resp.Dirs, listRoots()...)
		writeJSON(w, http.StatusOK, resp)
		return
	}
	if !filepath.IsAbs(p) {
		writeError(w, http.StatusBadRequest, msgNeedAbsPath)
		return
	}
	// Stat trước: trên Windows ReadDir mở handle tới path kể cả khi đó là file
	// (vd. mbox), làm Thunderbird không đổi tên/xóa được file trong lúc đó.
	fi, err := os.Stat(p)
	if errors.Is(err, fs.ErrNotExist) {
		writeError(w, http.StatusBadRequest, msgNotFound)
		return
	}
	if err != nil {
		writeError(w, http.StatusBadRequest, msgReadFailed)
		return
	}
	if !fi.IsDir() {
		writeError(w, http.StatusBadRequest, msgNotDir)
		return
	}
	entries, err := os.ReadDir(p)
	if err != nil {
		writeError(w, http.StatusBadRequest, msgReadFailed)
		return
	}
	for _, e := range entries {
		if e.IsDir() {
			resp.Dirs = append(resp.Dirs, browseEntry{Name: e.Name(), Path: filepath.Join(p, e.Name())})
		}
	}
	sort.SliceStable(resp.Dirs, func(i, j int) bool {
		return strings.ToLower(resp.Dirs[i].Name) < strings.ToLower(resp.Dirs[j].Name)
	})
	resp.Path = p
	if parent := filepath.Dir(p); parent != p {
		resp.Parent = parent
	}
	writeJSON(w, http.StatusOK, resp)
}

// classifyPath kiểm tra đường dẫn tuyệt đối và tồn tại; lỗi thì ghi phản hồi và trả false.
func classifyPath(w http.ResponseWriter, p string) (profile.DirKind, bool) {
	if !filepath.IsAbs(p) {
		writeError(w, http.StatusBadRequest, msgNeedAbsPath)
		return 0, false
	}
	kind, err := profile.Classify(p)
	if errors.Is(err, fs.ErrNotExist) {
		writeError(w, http.StatusBadRequest, msgNotFound)
		return 0, false
	}
	if err != nil {
		writeError(w, http.StatusBadRequest, msgReadFailed)
		return 0, false
	}
	return kind, true
}

// cleanUserPath nhận đường dẫn người dùng dán vào, kể cả kiểu "Copy as path"
// của Explorer (có ngoặc kép, dấu \ cuối, khoảng trắng thừa).
func cleanUserPath(s string) string {
	s = strings.TrimSpace(s)
	s = strings.Trim(s, `"`)
	s = strings.TrimSpace(s)
	if s == "" {
		return ""
	}
	return filepath.Clean(s)
}

func toProfileDTOs(ps []profile.Profile) []profileDTO {
	out := make([]profileDTO, 0, len(ps))
	for _, p := range ps {
		out = append(out, profileDTO{Name: p.Name, Path: p.Path, IsDefault: p.IsDefault})
	}
	return out
}

func toFolderDTOs(fs []*profile.Folder, states map[string]config.FolderState) []folderDTO {
	out := make([]folderDTO, 0, len(fs))
	for _, f := range fs {
		st := states[f.Path]
		out = append(out, folderDTO{
			Path: f.Path, Name: f.Name, HasMbox: f.HasMbox, Size: f.Size, ModTime: f.ModTime,
			Selected: st.Selected, IsNew: st.IsNew, DefaultExcluded: profile.ExcludedByDefault(f.Path),
			Children: toFolderDTOs(f.Children, states),
		})
	}
	return out
}
