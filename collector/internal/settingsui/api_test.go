package settingsui

import (
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"pmmail/collector/internal/config"
)

// Kiểu giải mã riêng cho test, khóa JSON viết tay theo hợp đồng API
// để bắt lỗi đặt sai tên khóa ở phía server.
type tFolder struct {
	Path            string    `json:"path"`
	Name            string    `json:"name"`
	HasMbox         bool      `json:"has_mbox"`
	Size            int64     `json:"size"`
	ModTime         time.Time `json:"mod_time"`
	Selected        bool      `json:"selected"`
	IsNew           bool      `json:"is_new"`
	DefaultExcluded bool      `json:"default_excluded"`
	Children        []tFolder `json:"children"`
}

type tAccount struct {
	Name        string    `json:"name"`
	Kind        string    `json:"kind"`
	Unsupported string    `json:"unsupported"`
	Folders     []tFolder `json:"folders"`
}

type tProfile struct {
	Name      string `json:"name"`
	Path      string `json:"path"`
	IsDefault bool   `json:"is_default"`
}

type tFolders struct {
	Kind          string     `json:"kind"`
	ProfilePath   string     `json:"profile_path"`
	AutoInclude   bool       `json:"auto_include_new_folders"`
	ConfigWarning string     `json:"config_warning"`
	Accounts      []tAccount `json:"accounts"`
	Profiles      []tProfile `json:"profiles"`
}

func touch(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

// mkProfile dựng profile: imap.example.com {INBOX (ProjectA), Trash}, Local Folders {Inbox}.
func mkProfile(t *testing.T, dir string) {
	t.Helper()
	for _, p := range []string{
		"ImapMail/imap.example.com/INBOX",
		"ImapMail/imap.example.com/INBOX.sbd/ProjectA",
		"ImapMail/imap.example.com/Trash",
		"Mail/Local Folders/Inbox",
	} {
		touch(t, filepath.Join(dir, filepath.FromSlash(p)), "x")
	}
}

func doReq(s *Server, method, target, body string) *httptest.ResponseRecorder {
	var rd io.Reader
	if body != "" {
		rd = strings.NewReader(body)
	}
	return rawReq(s, method, target, testAddr, s.Token(), testOrigin, rd)
}

func decode(t *testing.T, w *httptest.ResponseRecorder, v any) {
	t.Helper()
	if err := json.Unmarshal(w.Body.Bytes(), v); err != nil {
		t.Fatalf("decode %q: %v", w.Body.String(), err)
	}
}

func wantError(t *testing.T, w *httptest.ResponseRecorder, status int, msg string) {
	t.Helper()
	var e struct {
		Error string `json:"error"`
	}
	decode(t, w, &e)
	if w.Code != status || e.Error != msg {
		t.Errorf("got %d %q, want %d %q", w.Code, e.Error, status, msg)
	}
}

func getFolders(t *testing.T, s *Server, profilePath string) tFolders {
	t.Helper()
	w := doReq(s, "GET", "/api/folders?profile="+url.QueryEscape(profilePath), "")
	if w.Code != 200 {
		t.Fatalf("status = %d body = %s", w.Code, w.Body.String())
	}
	var r tFolders
	decode(t, w, &r)
	return r
}

func folderByName(fs []tFolder, name string) tFolder {
	for _, f := range fs {
		if f.Name == name {
			return f
		}
	}
	return tFolder{}
}

func TestAPIProfiles(t *testing.T) {
	appData := t.TempDir()
	touch(t, filepath.Join(appData, "Thunderbird", "profiles.ini"),
		"[Profile0]\nName=work\nIsRelative=1\nPath=Profiles/w\nDefault=1\n")
	cfgPath := filepath.Join(t.TempDir(), "config.json")
	saved := filepath.Join(t.TempDir(), "saved-profile")
	if err := config.Save(cfgPath, &config.Config{ConfigVersion: 1, ProfilePath: saved}); err != nil {
		t.Fatal(err)
	}
	s := newTestServerWith(t, Options{AppData: appData, ConfigPath: cfgPath})

	w := doReq(s, "GET", "/api/profiles", "")
	var r struct {
		Detected         []tProfile `json:"detected"`
		SavedProfilePath string     `json:"saved_profile_path"`
	}
	decode(t, w, &r)
	want := []tProfile{{Name: "work", Path: filepath.Join(appData, "Thunderbird", "Profiles", "w"), IsDefault: true}}
	if w.Code != 200 || !reflect.DeepEqual(r.Detected, want) || r.SavedProfilePath != saved {
		t.Errorf("got %d %+v", w.Code, r)
	}

	empty := newTestServer(t)
	w = doReq(empty, "GET", "/api/profiles", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"detected":[]`) {
		t.Errorf("no Thunderbird: got %d %s", w.Code, w.Body.String())
	}
}

func TestAPIFolders_Profile(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "Hồ sơ #1 & 日本")
	mkProfile(t, dir)
	s := newTestServer(t)

	r := getFolders(t, s, dir)
	if r.Kind != "profile" || r.ProfilePath != dir || r.ConfigWarning != "" || len(r.Accounts) != 2 {
		t.Fatalf("got %+v", r)
	}
	imap := r.Accounts[0]
	if imap.Name != "imap.example.com" || imap.Kind != "imap_cache" {
		t.Errorf("account 0 = %s %s", imap.Name, imap.Kind)
	}
	inbox := folderByName(imap.Folders, "INBOX")
	if !inbox.HasMbox || !inbox.Selected || inbox.IsNew || inbox.DefaultExcluded || inbox.Size != 1 || inbox.ModTime.IsZero() {
		t.Errorf("INBOX = %+v", inbox)
	}
	if len(inbox.Children) != 1 || inbox.Children[0].Name != "ProjectA" || !inbox.Children[0].Selected ||
		inbox.Children[0].Path != "ImapMail/imap.example.com/INBOX.sbd/ProjectA" {
		t.Errorf("INBOX children = %+v", inbox.Children)
	}
	if trash := folderByName(imap.Folders, "Trash"); trash.Selected || !trash.DefaultExcluded || trash.IsNew {
		t.Errorf("Trash = %+v", trash)
	}
	if r.Accounts[1].Name != "Local Folders" || r.Accounts[1].Kind != "local" {
		t.Errorf("account 1 = %+v", r.Accounts[1])
	}
}

func TestAPIFolders_QuotedPath(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "p")
	mkProfile(t, dir)
	s := newTestServer(t)
	r := getFolders(t, s, `  "`+dir+string(filepath.Separator)+`"  `)
	if r.Kind != "profile" || r.ProfilePath != dir {
		t.Errorf("got kind %q path %q, want profile %q", r.Kind, r.ProfilePath, dir)
	}
}

func TestAPIFolders_ProfilesRoot(t *testing.T) {
	root := t.TempDir()
	touch(t, filepath.Join(root, "profiles.ini"), "[Profile0]\nName=a\nIsRelative=1\nPath=Profiles/a\n")
	s := newTestServer(t)
	r := getFolders(t, s, root)
	if r.Kind != "profiles_root" || len(r.Profiles) != 1 || r.Profiles[0].Name != "a" {
		t.Errorf("got %+v", r)
	}
}

func TestAPIFolders_Errors(t *testing.T) {
	s := newTestServer(t)
	tests := []struct {
		profile string
		msg     string
	}{
		{t.TempDir(), "Không phải profile Thunderbird"},
		{"relative/path", "Cần đường dẫn đầy đủ"},
		{"", "Cần đường dẫn đầy đủ"},
		{filepath.Join(t.TempDir(), "nope"), "Không tìm thấy thư mục"},
	}
	for _, tt := range tests {
		w := doReq(s, "GET", "/api/folders?profile="+url.QueryEscape(tt.profile), "")
		wantError(t, w, 400, tt.msg)
	}
}

func TestAPIFolders_CorruptConfig(t *testing.T) {
	dir := t.TempDir()
	mkProfile(t, dir)
	cfgPath := filepath.Join(t.TempDir(), "config.json")
	touch(t, cfgPath, "{bad")
	s := newTestServerWith(t, Options{ConfigPath: cfgPath})

	r := getFolders(t, s, dir)
	if r.ConfigWarning == "" || !folderByName(r.Accounts[0].Folders, "INBOX").Selected {
		t.Errorf("got %+v", r)
	}
}

func TestAPIConfigSave(t *testing.T) {
	dir := t.TempDir()
	mkProfile(t, dir)
	cfgPath := filepath.Join(t.TempDir(), "config.json")
	touch(t, cfgPath, "{bad") // file hỏng phải bị ghi đè
	s := newTestServerWith(t, Options{ConfigPath: cfgPath})

	body, _ := json.Marshal(map[string]any{
		"profile_path":             dir,
		"auto_include_new_folders": true,
		"folders": map[string]bool{
			"ImapMail/imap.example.com/INBOX":              true,
			"ImapMail/imap.example.com/INBOX.sbd/ProjectA": false,
			"ImapMail/imap.example.com/Trash":              false,
			"Mail/Local Folders/Inbox":                     false,
		},
	})
	w := doReq(s, "POST", "/api/config", string(body))
	var resp struct {
		OK      bool      `json:"ok"`
		SavedAt time.Time `json:"saved_at"`
	}
	decode(t, w, &resp)
	if w.Code != 200 || !resp.OK || resp.SavedAt.IsZero() {
		t.Fatalf("got %d %s", w.Code, w.Body.String())
	}

	cfg, _, err := config.Load(cfgPath)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	sel := map[string]bool{}
	for _, f := range cfg.Folders {
		sel[f.Path] = f.Selected
	}
	wantSel := map[string]bool{
		"ImapMail/imap.example.com/INBOX":              true,
		"ImapMail/imap.example.com/INBOX.sbd/ProjectA": false,
		"ImapMail/imap.example.com/Trash":              false,
		"Mail/Local Folders/Inbox":                     false,
	}
	if !cfg.AutoIncludeNewFolders || cfg.ProfilePath != dir || !reflect.DeepEqual(sel, wantSel) {
		t.Errorf("saved config = %+v", cfg)
	}

	r := getFolders(t, s, dir)
	inbox := folderByName(r.Accounts[0].Folders, "INBOX")
	if !r.AutoInclude || r.ConfigWarning != "" || !inbox.Selected || inbox.IsNew || inbox.Children[0].Selected {
		t.Errorf("after save: %+v", r)
	}
}

// Folder xuất hiện sau khi trang đã mở (người dùng chưa thấy) không bị ghi là "bỏ tick".
func TestAPIConfigSave_UndisplayedFolderStaysNew(t *testing.T) {
	dir := t.TempDir()
	mkProfile(t, dir)
	s := newTestServerWith(t, Options{ConfigPath: filepath.Join(t.TempDir(), "config.json")})

	body, _ := json.Marshal(map[string]any{
		"profile_path":             dir,
		"auto_include_new_folders": true,
		"folders": map[string]bool{
			"ImapMail/imap.example.com/INBOX": true,
			"ImapMail/imap.example.com/Trash": false,
			"Mail/Local Folders/Inbox":        true,
		},
	})
	if w := doReq(s, "POST", "/api/config", string(body)); w.Code != 200 {
		t.Fatalf("save: %d %s", w.Code, w.Body.String())
	}
	r := getFolders(t, s, dir)
	pa := folderByName(r.Accounts[0].Folders, "INBOX").Children[0]
	if pa.Name != "ProjectA" || !pa.IsNew || !pa.Selected {
		t.Errorf("ProjectA = %+v, want new and selected (parent selected, auto on)", pa)
	}
}

func TestAPIConfigSave_Rejects(t *testing.T) {
	dir := t.TempDir()
	mkProfile(t, dir)
	cfgPath := filepath.Join(t.TempDir(), "config.json")
	s := newTestServerWith(t, Options{ConfigPath: cfgPath})

	body, _ := json.Marshal(map[string]any{"profile_path": dir, "folders": map[string]bool{"ImapMail/imap.example.com/Nope": true}})
	wantError(t, doReq(s, "POST", "/api/config", string(body)), 400, "Folder không tồn tại: ImapMail/imap.example.com/Nope")

	body, _ = json.Marshal(map[string]any{"profile_path": t.TempDir(), "folders": map[string]bool{}})
	wantError(t, doReq(s, "POST", "/api/config", string(body)), 400, "Không phải profile Thunderbird")

	if _, err := os.Stat(cfgPath); !errors.Is(err, fs.ErrNotExist) {
		t.Errorf("config file must not be written, stat err = %v", err)
	}
}

func TestAPIBrowse(t *testing.T) {
	root := t.TempDir()
	for _, d := range []string{"b", "A"} {
		if err := os.Mkdir(filepath.Join(root, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	touch(t, filepath.Join(root, "f.txt"), "x")
	s := newTestServer(t)

	type entry struct {
		Name string `json:"name"`
		Path string `json:"path"`
	}
	var r struct {
		Path   string  `json:"path"`
		Parent string  `json:"parent"`
		Dirs   []entry `json:"dirs"`
	}
	w := doReq(s, "GET", "/api/browse?path="+url.QueryEscape(root), "")
	decode(t, w, &r)
	want := []entry{{"A", filepath.Join(root, "A")}, {"b", filepath.Join(root, "b")}}
	if w.Code != 200 || r.Path != root || r.Parent != filepath.Dir(root) || !reflect.DeepEqual(r.Dirs, want) {
		t.Errorf("got %d %+v", w.Code, r)
	}

	r.Dirs, r.Parent = nil, "x"
	w = doReq(s, "GET", "/api/browse?path=", "")
	decode(t, w, &r)
	if w.Code != 200 || len(r.Dirs) == 0 || r.Parent != "" {
		t.Errorf("roots: got %d %+v", w.Code, r)
	}

	wantError(t, doReq(s, "GET", "/api/browse?path=relative", ""), 400, "Cần đường dẫn đầy đủ")
	wantError(t, doReq(s, "GET", "/api/browse?path="+url.QueryEscape(filepath.Join(root, "f.txt")), ""), 400, "Không phải thư mục")
}
