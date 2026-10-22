# Màn hình setting của collector: kế hoạch triển khai

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lệnh `collector.exe settings` mở trang cục bộ để chọn profile Thunderbird, hiện cây folder Server/Local và lưu lựa chọn folder đồng bộ vào `config.json`.

**Architecture:** Ba gói nội bộ, mỗi gói một trách nhiệm. `internal/profile` dò profile và cây folder, chỉ dùng stat/readdir. `internal/config` đọc/ghi cấu hình và quyết định trạng thái chọn. `internal/settingsui` là HTTP server cục bộ có token, phục vụ API JSON và trang HTML/JS thuần nhúng bằng `go:embed`. `cmd/collector` chỉ ghép các gói lại.

**Tech Stack:** Go (toolchain 1.27 trên máy), chỉ thư viện chuẩn (`net/http`, `embed`, `encoding/json`, `log/slog`), HTML/CSS/JS thuần.

**Spec:** [docs/superpowers/specs/2026-10-16-collector-settings-design.md](../specs/2026-10-16-collector-settings-design.md)

## Global Constraints

- Module `pmmail/collector` trong thư mục `collector/`. Tính năng này chỉ dùng thư viện chuẩn, không thêm dependency, build được với `CGO_ENABLED=0`.
- Profile chỉ được `os.Stat`, `os.Lstat`, `os.ReadDir` và đọc `profiles.ini` / `installs.ini`. **Không bao giờ mở file mbox hay `.msf`. Không ghi gì vào profile.**
- Server chỉ bind `127.0.0.1`.
- Log dùng `log/slog` ra stderr, chỉ ghi đường dẫn, số lượng, thời gian, lỗi.
- Chữ trên giao diện và thông điệp lỗi trả về người dùng bằng tiếng Việt. Tên định danh và khóa JSON bằng tiếng Anh.
- File cấu hình: `<UserConfigDir>\PMMailAssistant\collector\config.json` (trên Windows `UserConfigDir` = `%APPDATA%`), `config_version` = `1`.
- Danh sách mặc định bỏ tick (so khớp `strings.EqualFold`): `Trash`, `Deleted Items`, `Deleted Messages`, `Junk`, `Junk E-mail`, `Spam`, `Drafts`, `Templates`, `Unsent Messages`, `Outbox`, `ゴミ箱`, `削除済みアイテム`, `迷惑メール`, `下書き`, `送信トレイ`, `Thùng rác`, `Thư rác`, `Thư nháp`.
- Đuôi file chắc chắn không phải mbox: `.msf`, `.dat`, `.html`, `.json`, `.sqlite`, `.js`, `.ini`, `.log`, `.mab`, `.bak`, `.tmp`. Tên file bỏ qua: `nstmp` (file tạm khi Thunderbird compact).
- Test chỉ dựng dữ liệu bằng `t.TempDir()` và file rỗng hoặc vài byte vô nghĩa. Test không đọc `data/` hay `samples/`.
- Khi Claude tự chạy binary để kiểm tra: đặt `$env:APPDATA` thành một thư mục tạm trong scratchpad để không dò ra profile thật, dùng `-config <file tạm>`, và chỉ trỏ tới `data/thunderbird-profile` (dữ liệu tự sinh). Không chụp màn hình hay đọc giao diện khi đang mở profile thật.
- Lệnh chạy từ gốc `Email Assistant`: `go test -C collector ./...`, `go vet -C collector ./...`, `gofmt -l collector`. `golangci-lint` chưa được cài, tạm bỏ qua và ghi chú trong `collector/CLAUDE.md`.

## Review Focus

1. File `profiles.ini` thật trên Windows có BOM UTF-8 và xuống dòng CRLF → vẫn đọc đúng. Test nằm ở Task 1 (`TestParseProfilesINI_RelativeAbsoluteDefault`).
2. Người dùng dán đường dẫn bằng "Copy as path" của Explorer (có ngoặc kép, có `\` cuối, có khoảng trắng thừa) → vẫn nhận. Test nằm ở Task 5 (`TestAPIFolders_QuotedPath`).
3. Đường dẫn profile và tên folder có khoảng trắng, dấu tiếng Việt, chữ Nhật, `#`, `&` → hiện đúng, không vỡ query string, lưu đúng. Test nằm ở Task 2 (fixture) và Task 5 (`TestAPIFolders_Profile`, dùng thư mục `Hồ sơ #1 & 日本`).
4. Thunderbird đang compact và để lại file `nstmp` trong thư mục folder → `nstmp` không hiện thành folder. Test nằm ở Task 2 (fixture).
5. `config.json` hỏng (sửa tay sai, ghi dở) → trang vẫn mở với lựa chọn mặc định, kèm cảnh báo, và bấm Lưu thì ghi đè file hỏng. Test nằm ở Task 3 (`TestLoadCorrupt`) và Task 5 (`TestAPIFolders_CorruptConfig`).

---

### Task 1: Khởi tạo module, đọc `profiles.ini`, nhận diện thư mục

**Files:**
- Create: `collector/go.mod`
- Create: `collector/internal/profile/ini.go` (bộ đọc INI tối giản, nội bộ)
- Create: `collector/internal/profile/profileini.go`
- Create: `collector/internal/profile/classify.go`
- Test: `collector/internal/profile/profileini_test.go`, `collector/internal/profile/classify_test.go`
- Modify: `.gitignore` (thêm `.superpowers/`)

**Interfaces:**
- Produces:
  - `type Profile struct { Name string; Path string /* tuyệt đối */; IsDefault bool }`
  - `func ParseProfilesINI(r io.Reader, baseDir string) ([]Profile, error)`: trả các mục `[Profile*]` theo thứ tự trong file. `IsRelative=1` thì `Path = filepath.Join(baseDir, filepath.FromSlash(Path))`. `IsDefault` lấy từ `Default=1`. Nếu có mục `[Install*]` có `Default=<p>` thì `IsDefault` chỉ đúng với profile có đường dẫn trùng `<p>` (sau khi resolve như trên).
  - `func ProfilesInRoot(root string) ([]Profile, error)`: đọc `root/profiles.ini`, rồi nếu có `root/installs.ini` với `Default=<p>` ở mục bất kỳ thì ghi đè `IsDefault` như trên. Thiếu `profiles.ini` thì trả lỗi thỏa `errors.Is(err, fs.ErrNotExist)`.
  - `type DirKind int` với `DirNotProfile`, `DirProfile`, `DirProfilesRoot`
  - `func Classify(dir string) (DirKind, error)`: có thư mục con `ImapMail` hoặc `Mail` → `DirProfile`; nếu không, có file `profiles.ini` → `DirProfilesRoot`; còn lại (kể cả `dir` là file) → `DirNotProfile`. `dir` không tồn tại → lỗi thỏa `errors.Is(err, fs.ErrNotExist)`.

- [ ] **Step 1: Khởi tạo**

Nếu `Email Assistant` chưa là git repo thì chạy `git init` tại đó. Thêm dòng `.superpowers/` vào `.gitignore`. Chạy `go mod init pmmail/collector` trong `collector/`.

- [ ] **Step 2: Viết test thất bại**

```go
// profileini_test.go
func TestParseProfilesINI_RelativeAbsoluteDefault(t *testing.T) {
	base := t.TempDir()
	abs := filepath.Join(t.TempDir(), "old")
	ini := "\ufeff[General]\r\nStartWithLastProfile=1\r\n\r\n" +
		"[Profile1]\r\nName=work\r\nIsRelative=1\r\nPath=Profiles/x8k2.default-release\r\n\r\n" +
		"[Profile0]\r\nName=old\r\nIsRelative=0\r\nPath=" + abs + "\r\nDefault=1\r\n"
	got, err := ParseProfilesINI(strings.NewReader(ini), base)
	// err == nil
	// got == []Profile{
	//   {Name: "work", Path: filepath.Join(base, "Profiles", "x8k2.default-release"), IsDefault: false},
	//   {Name: "old", Path: abs, IsDefault: true},
	// }
}

func TestParseProfilesINI_InstallSectionOverridesDefault(t *testing.T) {
	ini := "[Profile0]\nName=a\nIsRelative=1\nPath=Profiles/a\nDefault=1\n\n" +
		"[Profile1]\nName=b\nIsRelative=1\nPath=Profiles/b\n\n" +
		"[Install4F96D1932A9F858E]\nDefault=Profiles/b\nLocked=1\n"
	// got[0].IsDefault == false, got[1].IsDefault == true
}

func TestProfilesInRoot_InstallsINI(t *testing.T) {
	// root/profiles.ini: Profile0 a (Default=1), Profile1 b
	// root/installs.ini: "[ABC]\nDefault=Profiles/b\nLocked=1\n"
	// → a.IsDefault == false, b.IsDefault == true; b.Path == filepath.Join(root, "Profiles", "b")
}

func TestProfilesInRoot_Missing(t *testing.T) {
	_, err := ProfilesInRoot(t.TempDir())
	// errors.Is(err, fs.ErrNotExist)
}

// classify_test.go — bảng test, mỗi dòng dựng một thư mục trong t.TempDir()
// {"ImapMail/"} → DirProfile; {"Mail/"} → DirProfile;
// {"profiles.ini"} → DirProfilesRoot; {"ImapMail/", "Mail/", "profiles.ini"} → DirProfile;
// {} → DirNotProfile; dir là một file → DirNotProfile;
// dir không tồn tại → errors.Is(err, fs.ErrNotExist)
```

- [ ] **Step 3: Chạy test, xác nhận thất bại**

Run: `go test -C collector ./internal/profile/`
Expected: FAIL (`undefined: ParseProfilesINI`…)

- [ ] **Step 4: Viết code**

Bộ đọc INI trong `ini.go`: bỏ BOM, chấp nhận `\r\n`, bỏ dòng trống và dòng bắt đầu bằng `;` hoặc `#`, tên khóa giữ nguyên hoa thường, giá trị `TrimSpace`. So sánh đường dẫn khi áp `Default=` của mục Install bằng `strings.EqualFold(filepath.Clean(a), filepath.Clean(b))`.

- [ ] **Step 5: Chạy test, xác nhận đạt**

Run: `go test -C collector ./internal/profile/` → PASS. `go vet -C collector ./...` và `gofmt -l collector` không in gì.

- [ ] **Step 6: Commit**

```bash
git add .gitignore collector/go.mod collector/internal/profile
git commit -m "feat(collector): đọc profiles.ini và nhận diện thư mục profile"
```

---

### Task 2: Dò tài khoản và cây folder, danh sách mặc định bỏ tick

**Files:**
- Create: `collector/internal/profile/tree.go`
- Create: `collector/internal/profile/defaults.go`
- Test: `collector/internal/profile/tree_test.go`, `collector/internal/profile/defaults_test.go`

**Interfaces:**
- Produces:
  - `type AccountKind string` với `KindIMAPCache AccountKind = "imap_cache"`, `KindLocal AccountKind = "local"`
  - `type Account struct { Name string; Kind AccountKind; Unsupported string /* "maildir" hoặc "" */; Folders []*Folder }`
  - `type Folder struct { Path string /* tương đối so với profile, phân cách "/" */; Name string; HasMbox bool; Size int64; ModTime time.Time; Children []*Folder }`
  - `func ScanAccounts(profileDir string) ([]Account, error)`: tài khoản IMAP (thư mục con của `ImapMail/`) trước, rồi tài khoản local (thư mục con của `Mail/`). Trong mỗi nhóm sắp theo tên. Thiếu `ImapMail/` hoặc `Mail/` thì bỏ qua nhóm đó, không báo lỗi.
  - `func DefaultExcluded(name string) bool`: so khớp danh sách ở Global Constraints.
  - `func ExcludedByDefault(relPath string) bool`: bỏ 2 đoạn đầu (`ImapMail|Mail` và tên tài khoản), bỏ hậu tố `.sbd` ở từng đoạn còn lại, trả `true` nếu có đoạn nào `DefaultExcluded`.

Quy tắc duyệt một thư mục (thư mục tài khoản hoặc `*.sbd`), lấy từ mục 7.4 của spec:
- Bỏ entry có `Type()&(fs.ModeSymlink|fs.ModeIrregular) != 0` (symlink, junction).
- File là mbox khi: không có đuôi nằm trong danh sách loại trừ, tên khác `nstmp`, và (không có đuôi **hoặc** có file `<tên>.msf` cạnh nó).
- Thư mục `<tên>.sbd`: duyệt đệ quy. Nếu `<tên>` là mbox thì gắn kết quả làm `Children`. Nếu không mà có con thì tạo nút `HasMbox=false`. Nếu rỗng thì bỏ.
- Thư mục khác (không đuôi `.sbd`) thì bỏ qua.
- `entry.Info()` lỗi (file vừa bị Thunderbird xóa) thì bỏ entry đó, ghi `slog.Warn` kèm đường dẫn, tiếp tục.
- Tài khoản có thư mục con chứa cả `cur/` và `new/` → `Unsupported = "maildir"`, `Folders = nil`.
- Sắp xếp: tên `inbox` (`EqualFold`) đứng đầu, còn lại theo `strings.ToLower(Name)`.

- [ ] **Step 1: Viết test thất bại**

```go
// tree_test.go — helper mk(t, root, spec...): "a/b/" tạo thư mục, "a/b=xyz" tạo file chứa "xyz", "a/b" tạo file rỗng (tự tạo thư mục cha)
func TestScanAccounts(t *testing.T) {
	p := t.TempDir()
	mk(t, p,
		"ImapMail/imap.example.com/INBOX=0123456789", // 10 byte
		"ImapMail/imap.example.com/INBOX.msf",
		"ImapMail/imap.example.com/INBOX.sbd/ProjectA",
		"ImapMail/imap.example.com/INBOX.sbd/ProjectA.msf",
		"ImapMail/imap.example.com/INBOX.sbd/v1.2",
		"ImapMail/imap.example.com/INBOX.sbd/v1.2.msf",
		"ImapMail/imap.example.com/INBOX.sbd/notes.txt", // có đuôi, không .msf → bỏ
		"ImapMail/imap.example.com/Sent",                // không .msf vẫn nhận
		"ImapMail/imap.example.com/Trash",
		"ImapMail/imap.example.com/msgFilterRules.dat",
		"ImapMail/imap.example.com/nstmp",
		"ImapMail/imap.example.com/[Gmail].sbd/All Mail",
		"ImapMail/imap.example.com/Empty.sbd/",
		"ImapMail/imap.example-1.com/INBOX",
		"ImapMail/maildir.example.com/INBOX/cur/",
		"ImapMail/maildir.example.com/INBOX/new/",
		"Mail/Local Folders/Inbox",
		"Mail/Local Folders/Inbox.sbd/",
		"Mail/Local Folders/案件A",
		"Mail/Local Folders/Dự án #1 & co",
		"Mail/Local Folders/Trash",
		"Mail/Local Folders/feeds.json",
	)
	accts, err := ScanAccounts(p)
	// err == nil
	// tên tài khoản theo thứ tự: imap.example-1.com, imap.example.com, maildir.example.com, Local Folders
	// Kind: 3 cái đầu KindIMAPCache, cái cuối KindLocal
	// imap.example.com top-level: ["INBOX", "[Gmail]", "Sent", "Trash"]
	// INBOX: Path "ImapMail/imap.example.com/INBOX", Size 10, HasMbox true, Children ["ProjectA", "v1.2"]
	// ProjectA.Path == "ImapMail/imap.example.com/INBOX.sbd/ProjectA"
	// [Gmail]: HasMbox false, Children ["All Mail"]
	// maildir.example.com: Unsupported "maildir", len(Folders) == 0
	// Local Folders: ["Inbox", "Dự án #1 & co", "Trash", "案件A"]; Inbox.Children rỗng
}

func TestScanAccounts_NoMailDirs(t *testing.T) {
	// profile chỉ có "ImapMail/" rỗng → len(accts) == 0, err == nil
}

// defaults_test.go
func TestDefaultExcluded(t *testing.T) {
	// true: "Trash", "trash", "Junk E-mail", "ゴミ箱", "Thùng rác", "thùng rác", "Unsent Messages"
	// false: "INBOX", "Sent", "Trash2", "ProjectA"
}

func TestExcludedByDefault(t *testing.T) {
	// true:  "ImapMail/a/Trash", "ImapMail/a/Trash.sbd/Old", "ImapMail/a/INBOX.sbd/Junk", "Mail/Local Folders/Drafts"
	// false: "ImapMail/a/INBOX.sbd/ProjectA", "ImapMail/Trash/INBOX" (tên tài khoản không tính)
}
```

- [ ] **Step 2: Chạy test, xác nhận thất bại**

Run: `go test -C collector ./internal/profile/`
Expected: FAIL (`undefined: ScanAccounts`…)

- [ ] **Step 3: Viết code `ScanAccounts`, `DefaultExcluded`, `ExcludedByDefault` theo quy tắc ở trên**

- [ ] **Step 4: Chạy test, xác nhận đạt**

Run: `go test -C collector ./internal/profile/` → PASS. Chạy thêm `go vet` và `gofmt -l`.

- [ ] **Step 5: Commit**

```bash
git add collector/internal/profile
git commit -m "feat(collector): dò tài khoản, cây folder và danh sách mặc định bỏ tick"
```

---

### Task 3: File cấu hình và quy tắc trạng thái chọn

**Files:**
- Create: `collector/internal/config/config.go`
- Create: `collector/internal/config/resolve.go`
- Test: `collector/internal/config/config_test.go`, `collector/internal/config/resolve_test.go`

**Interfaces:**
- Consumes: `profile.Account`, `profile.Folder`, `profile.AccountKind`, `profile.ExcludedByDefault` (Task 2)
- Produces:
  - `const CurrentVersion = 1`
  - `type FolderChoice struct { Path string \`json:"path"\`; Account string \`json:"account"\`; Kind profile.AccountKind \`json:"kind"\`; Selected bool \`json:"selected"\` }`
  - `type Config struct { ConfigVersion int \`json:"config_version"\`; ProfilePath string \`json:"profile_path"\`; AutoIncludeNewFolders bool \`json:"auto_include_new_folders"\`; Folders []FolderChoice \`json:"folders"\`; SavedAt time.Time \`json:"saved_at"\` }`
  - `func DefaultPath(userConfigDir string) string` → `filepath.Join(userConfigDir, "PMMailAssistant", "collector", "config.json")`
  - `func Load(path string) (cfg *Config, found bool, err error)`: thiếu file → `nil, false, nil`. JSON hỏng → `nil, true, err`.
  - `func Save(path string, c *Config) error`: `MkdirAll` thư mục cha, ghi JSON thụt lề vào `os.CreateTemp(dir, "config-*.tmp")`, `Sync`, `Close`, rồi `os.Rename` đè lên `path`. Lỗi thì xóa file tạm.
  - `type FolderState struct { Selected bool; IsNew bool }`
  - `func SameProfile(a, b string) bool`: `strings.EqualFold(filepath.Clean(a), filepath.Clean(b))`
  - `func Resolve(cfg *Config, profileDir string, accounts []profile.Account) map[string]FolderState`: khóa là `Folder.Path`, chỉ gồm folder `HasMbox`.
  - `func Build(profileDir string, autoInclude bool, accounts []profile.Account, selected map[string]bool, now time.Time) (*Config, error)`
  - `func (c *Config) ShouldScan(relPath string) bool`

Quy tắc `Resolve`:
- `cfg == nil` hoặc `!SameProfile(cfg.ProfilePath, profileDir)`: `Selected = !profile.ExcludedByDefault(path)`, `IsNew = false`.
- Ngược lại: path có trong `cfg.Folders` → `Selected` theo đã lưu, `IsNew = false`. Path chưa có → `IsNew = true`, `Selected = cfg.AutoIncludeNewFolders && !profile.ExcludedByDefault(path)`.

Quy tắc `Build`: ghi **mọi** folder `HasMbox` (theo thứ tự duyệt cây) thành `FolderChoice` với `Account = Account.Name`, `Kind = Account.Kind`, `Selected = selected[path]`. Path trong `selected` không phải folder `HasMbox` nào → lỗi `fmt.Errorf("folder không tồn tại: %s", path)`. Gán `ConfigVersion = CurrentVersion`, `SavedAt = now`.

Quy tắc `ShouldScan`: path có trong `Folders` → `Selected`. Path chưa có → `AutoIncludeNewFolders && !profile.ExcludedByDefault(path)`.

- [ ] **Step 1: Viết test thất bại**

```go
// config_test.go
func TestSaveLoadRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sub", "config.json") // thư mục "sub" chưa tồn tại
	in := &Config{ConfigVersion: 1, ProfilePath: `C:\p`, AutoIncludeNewFolders: true,
		Folders: []FolderChoice{{Path: "ImapMail/a/INBOX", Account: "a", Kind: profile.KindIMAPCache, Selected: true}},
		SavedAt: time.Date(2026, 10, 16, 10, 0, 0, 0, time.FixedZone("ICT", 7*3600))}
	// Save(path, in) == nil; out, found, err := Load(path)
	// found == true, err == nil; out bằng in (SavedAt so bằng .Equal)
	// thư mục cha chỉ chứa đúng "config.json" (không còn file *.tmp)
}

func TestLoadMissing(t *testing.T) {
	// cfg, found, err := Load(<file không tồn tại>) → nil, false, nil
}

func TestLoadCorrupt(t *testing.T) {
	// ghi "{not json" → Load trả found == true, err != nil
	// sau đó Save(path, cfg hợp lệ) ghi đè thành công và Load đọc lại được
}

// resolve_test.go — helper accts() dựng tay:
//   Account "a" (imap_cache): INBOX (HasMbox, con ProjectA, ProjectB), Trash (HasMbox, con Old),
//                             Junk (HasMbox), [Gmail] (HasMbox=false, con All Mail)
func TestResolve_NoConfig(t *testing.T) {
	// INBOX, ProjectA, ProjectB, "All Mail" → Selected true; Trash, Trash.sbd/Old, Junk → Selected false
	// mọi IsNew == false; khóa "ImapMail/a/[Gmail]" không có trong map
}
func TestResolve_OtherProfile(t *testing.T) {
	// cfg.ProfilePath khác → kết quả giống TestResolve_NoConfig
}
func TestResolve_WithConfig(t *testing.T) {
	// cfg (cùng profile, khác hoa thường ở ký tự ổ đĩa) lưu: INBOX false, ProjectA true, Trash false, Old false, All Mail true
	// → INBOX {false,false}, ProjectA {true,false}, ProjectB {false,true}, Junk {false,true}
	// cùng cfg nhưng AutoIncludeNewFolders = true → ProjectB {true,true}, Junk {false,true}
}
func TestBuild(t *testing.T) {
	// selected {INBOX, ProjectB} → Folders gồm đủ 7 folder HasMbox, không có "[Gmail]",
	//   INBOX/ProjectB Selected true, còn lại false; Kind == KindIMAPCache; Account == "a"; ConfigVersion == 1
	// selected {"ImapMail/a/Nope"} → err != nil
}
func TestShouldScan(t *testing.T) {
	// đã lưu selected → true; đã lưu bỏ tick → false
	// chưa có + auto false → false; chưa có + auto true → true; chưa có "ImapMail/a/Spam" + auto true → false
}
```

- [ ] **Step 2: Chạy test, xác nhận thất bại**

Run: `go test -C collector ./internal/config/` → FAIL

- [ ] **Step 3: Viết code theo các quy tắc ở trên**

- [ ] **Step 4: Chạy test, xác nhận đạt**

Run: `go test -C collector ./...` → PASS. Chạy thêm `go vet` và `gofmt -l`.

- [ ] **Step 5: Commit**

```bash
git add collector/internal/config
git commit -m "feat(collector): file cấu hình và quy tắc chọn folder"
```

---

### Task 4: HTTP server cục bộ có bảo vệ token, Host, Origin

**Files:**
- Create: `collector/internal/settingsui/server.go`
- Create: `collector/internal/settingsui/web/index.html` (tạm, chỉ có `<title>Cài đặt collector</title>`; Task 7 viết thật)
- Test: `collector/internal/settingsui/server_test.go`

**Interfaces:**
- Produces:
  - `type Options struct { Addr string /* "127.0.0.1:<port>" của listener */; ConfigPath string; AppData string /* thư mục chứa Thunderbird\profiles.ini */; IdleTimeout time.Duration }`
  - `func New(opts Options) (*Server, error)`: sinh token gồm 32 byte `crypto/rand`, mã hóa hex.
  - `func (s *Server) Token() string`
  - `func (s *Server) Handler() http.Handler`
  - `func (s *Server) Run(ctx context.Context, ln net.Listener) error`: phục vụ cho tới khi `ctx` hết hạn, có `/api/shutdown`, hoặc quá `IdleTimeout` không có request hợp lệ (kiểm tra mỗi `IdleTimeout/4`). Sau đó gọi `http.Server.Shutdown` với hạn 5 giây và trả `nil`. Lỗi `Serve` khác `http.ErrServerClosed` thì trả lỗi đó.
  - Hằng `cookieName = "pmmail_key"`
  - `Handler()` tạo `http.ServeMux` (pattern kiểu Go 1.22, ví dụ `"POST /api/shutdown"`), đăng ký static và `/api/shutdown`, rồi bọc bằng middleware bảo vệ. Task 5 thêm `func (s *Server) apiRoutes(mux *http.ServeMux)` trong `api.go` và gọi nó trong `Handler()`.

Middleware bảo vệ, áp cho mọi request, theo thứ tự:
1. `r.Host` phải là `opts.Addr` hoặc `"localhost:<port của Addr>"`. Sai → 403.
2. Hợp lệ khi cookie `pmmail_key` khớp token, **hoặc** request là `GET /` có `?key=` khớp token (khi đó đặt cookie `Path=/; HttpOnly; SameSite=Strict`). So khớp bằng `subtle.ConstantTimeCompare`. Không hợp lệ → 403.
3. `POST`: header `Origin` phải là `"http://"+Addr` hoặc `"http://localhost:<port>"`. Thiếu hoặc sai → 403.
4. Request hợp lệ thì cập nhật thời điểm hoạt động cuối (`atomic.Int64`).

Phản hồi 403 là JSON `{"error":"Không có quyền truy cập"}`. `POST /api/shutdown` trả `{"ok":true}` rồi đóng kênh dừng qua `sync.Once`. Static phục vụ từ `//go:embed web`: `GET /` trả `index.html`, các file khác qua `http.FileServerFS`.

- [ ] **Step 1: Viết test thất bại**

```go
// helper newTestServer(t) → *Server với Addr "127.0.0.1:5555", ConfigPath/AppData trong t.TempDir(), IdleTimeout time.Minute
// helper req(method, target, host, cookie, origin) → *http.Request
func TestRootWithKeySetsCookie(t *testing.T) {
	// GET "/?key="+tok, Host 127.0.0.1:5555 → 200, body chứa "Cài đặt collector"
	// Set-Cookie: tên pmmail_key, giá trị tok, HttpOnly, SameSite=Strict
}
func TestRootWithoutKey(t *testing.T)            { /* GET "/" → 403; GET "/?key=sai" → 403 */ }
func TestAPIWithoutCookie(t *testing.T)          { /* POST /api/shutdown có Origin đúng, không cookie → 403 */ }
func TestWrongHost(t *testing.T)                 { /* cookie đúng, Host "evil.example:5555" → 403; Host "localhost:5555" + "/?key=tok" → 200 */ }
func TestPostOrigin(t *testing.T) {
	// cookie đúng: Origin "http://evil.example" → 403; không Origin → 403;
	// Origin "http://127.0.0.1:5555" → 200 và body {"ok":true}
}
func TestRunStopsOnShutdown(t *testing.T) {
	// ln := net.Listen("tcp", "127.0.0.1:0"); s := New(Options{Addr: ln.Addr().String(), ...})
	// go Run(ctx, ln); POST /api/shutdown qua http.Client (cookie + Origin đúng) → Run trả nil trong vòng 2s
}
func TestRunStopsWhenIdle(t *testing.T) {
	// IdleTimeout 200ms, không request nào → Run trả nil trong vòng 2s
}
func TestRunStopsOnContextCancel(t *testing.T) { /* cancel ctx → Run trả nil trong vòng 2s */ }
```

- [ ] **Step 2: Chạy test, xác nhận thất bại**

Run: `go test -C collector ./internal/settingsui/` → FAIL

- [ ] **Step 3: Viết code `server.go` theo mô tả ở trên**

- [ ] **Step 4: Chạy test, xác nhận đạt**

Run: `go test -C collector ./...` → PASS (thêm `-race` nếu máy có hỗ trợ). Chạy thêm `go vet` và `gofmt -l`.

- [ ] **Step 5: Commit**

```bash
git add collector/internal/settingsui
git commit -m "feat(collector): server cục bộ cho trang setting, bảo vệ token/Host/Origin"
```

---

### Task 5: API JSON

**Files:**
- Create: `collector/internal/settingsui/api.go`
- Create: `collector/internal/settingsui/browse_windows.go`, `collector/internal/settingsui/browse_other.go` (`//go:build !windows`)
- Modify: `collector/internal/settingsui/server.go` (`Handler()` gọi `s.apiRoutes(mux)`)
- Test: `collector/internal/settingsui/api_test.go`

**Interfaces:**
- Consumes: `profile.ProfilesInRoot`, `profile.Classify`, `profile.ScanAccounts`, `profile.ExcludedByDefault` (Task 1, 2); `config.Load`, `config.Save`, `config.Resolve`, `config.Build`, `config.SameProfile` (Task 3); `Server`, `Handler()` (Task 4)
- Produces (hợp đồng với `web/app.js` ở Task 7):

| Route | Đầu vào | Đầu ra 200 |
|---|---|---|
| `GET /api/profiles` | — | `{"detected":[{"name","path","is_default"}],"saved_profile_path":""}` |
| `GET /api/browse?path=` | `path` rỗng = danh sách gốc | `{"path","parent","dirs":[{"name","path"}]}` |
| `GET /api/folders?profile=` | đường dẫn tuyệt đối | profile: `{"kind":"profile","profile_path","auto_include_new_folders","config_warning"?,"accounts":[{"name","kind","unsupported","folders":[F]}]}` · thư mục gốc: `{"kind":"profiles_root","profiles":[...]}` |
| `POST /api/config` | `{"profile_path","auto_include_new_folders","selected":["<path>",…]}` | `{"ok":true,"saved_at":"<RFC3339>"}` |

`F` = `{"path","name","has_mbox","size","mod_time","selected","is_new","default_excluded","children":[F]}`. `selected`/`is_new` lấy từ `config.Resolve`, `default_excluded` lấy từ `profile.ExcludedByDefault`. Mảng rỗng trả `[]`, không trả `null`.

Lỗi trả `{"error":"…"}` với mã 400 và các thông điệp cố định:
- `"Cần đường dẫn đầy đủ"`: sau khi làm sạch, path vẫn không tuyệt đối.
- `"Không tìm thấy thư mục"`: `fs.ErrNotExist`.
- `"Không phải profile Thunderbird"`: `DirNotProfile`, hoặc POST với thư mục không phải `DirProfile`.
- `"Folder không tồn tại: <path>"`: lỗi từ `config.Build`.

Lỗi ghi file trả 500 `{"error":"Không lưu được cấu hình"}`, kèm `slog.Error`.

Chi tiết:
- `cleanUserPath(s string) string`: `TrimSpace`, cắt `"` ở hai đầu, `TrimSpace`, `filepath.Clean`. Dùng cho `profile` và `profile_path`.
- `/api/profiles`: `ProfilesInRoot(filepath.Join(AppData, "Thunderbird"))`. `ErrNotExist` thì trả danh sách rỗng. `saved_profile_path` lấy từ `config.Load`, lỗi thì để rỗng.
- `/api/folders`: `config.Load` lỗi thì `cfg = nil` và `config_warning = "File cấu hình bị hỏng, đang dùng lựa chọn mặc định. Bấm Lưu sẽ ghi đè."`. `auto_include_new_folders` lấy từ `cfg` khi `SameProfile`, ngược lại là `false`.
- `/api/browse`: path rỗng thì trả danh sách gốc (Windows: các ổ `A:\`…`Z:\` mà `os.Stat` thành công, `name` = `"C:"`; nơi khác: `/`), `parent = ""`. Path không tuyệt đối → 400. Chỉ liệt kê thư mục, sắp theo tên không phân biệt hoa thường. `parent = filepath.Dir(path)`, bằng chính `path` thì trả `""`.
- `POST /api/config`: giới hạn body 1 MB (`http.MaxBytesReader`). Sau khi lưu ghi `slog.Info("config saved", "path", cfgPath, "folders", n, "selected", m)`.

- [ ] **Step 1: Viết test thất bại**

```go
// helper doReq(t, s, method, target, body) gắn Host/cookie/Origin hợp lệ, trả *httptest.ResponseRecorder
// helper mkProfile(t, dir) dựng: ImapMail/imap.example.com/{INBOX, INBOX.sbd/ProjectA, Trash}, Mail/Local Folders/Inbox
func TestAPIProfiles(t *testing.T) {
	// AppData/Thunderbird/profiles.ini có 1 profile "work" (IsRelative=1, Path=Profiles/w, Default=1)
	// config đã lưu với profile_path X → detected[0].name == "work", detected[0].is_default == true, saved_profile_path == X
	// AppData không có Thunderbird → 200, detected == []
}
func TestAPIFolders_Profile(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "Hồ sơ #1 & 日本"); mkProfile(t, dir)
	// GET "/api/folders?profile="+url.QueryEscape(dir) → 200, kind "profile", profile_path == dir
	// accounts[0].name "imap.example.com", kind "imap_cache"; INBOX selected true, children[0].name "ProjectA"
	// Trash selected false, default_excluded true; mọi is_new false; accounts[1].name "Local Folders"
}
func TestAPIFolders_QuotedPath(t *testing.T) {
	// profile = `  "` + dir + string(filepath.Separator) + `"  ` → 200, profile_path == dir
}
func TestAPIFolders_ProfilesRoot(t *testing.T) { /* thư mục chỉ có profiles.ini 1 profile → kind "profiles_root", len(profiles) == 1 */ }
func TestAPIFolders_Errors(t *testing.T) {
	// thư mục rỗng → 400 "Không phải profile Thunderbird"
	// "relative/path" → 400 "Cần đường dẫn đầy đủ"
	// thư mục không tồn tại → 400 "Không tìm thấy thư mục"
}
func TestAPIFolders_CorruptConfig(t *testing.T) {
	// ConfigPath chứa "{bad" → 200, config_warning khác rỗng, INBOX selected true (mặc định)
}
func TestAPIConfigSave(t *testing.T) {
	// POST {"profile_path":dir,"auto_include_new_folders":true,"selected":["ImapMail/imap.example.com/INBOX"]} → 200, ok true
	// config.Load: AutoIncludeNewFolders true; INBOX Selected true; Trash Selected false; có đủ 4 folder
	// GET /api/folders lại → INBOX selected true, ProjectA selected false, is_new false
	// ghi đè được file "{bad" (kết hợp với Review Focus 5)
}
func TestAPIConfigSave_Rejects(t *testing.T) {
	// selected ["ImapMail/imap.example.com/Nope"] → 400 "Folder không tồn tại: ImapMail/imap.example.com/Nope", không tạo file config
	// profile_path là thư mục rỗng → 400 "Không phải profile Thunderbird"
}
func TestAPIBrowse(t *testing.T) {
	// path = temp chứa thư mục "b", "A" và file "f.txt" → dirs names ["A","b"], parent == filepath.Dir(temp)
	// path "" → 200, len(dirs) >= 1, parent ""
	// path "relative" → 400
}
```

- [ ] **Step 2: Chạy test, xác nhận thất bại**

Run: `go test -C collector ./internal/settingsui/` → FAIL

- [ ] **Step 3: Viết code `api.go` và `browse_*.go` theo bảng và chi tiết ở trên**

- [ ] **Step 4: Chạy test, xác nhận đạt**

Run: `go test -C collector ./...` → PASS. Chạy thêm `go vet` và `gofmt -l`.

- [ ] **Step 5: Commit**

```bash
git add collector/internal/settingsui
git commit -m "feat(collector): API cho trang setting (profile, folder, lưu cấu hình, duyệt thư mục)"
```

---

### Task 6: Lệnh `collector settings`

**Files:**
- Create: `collector/cmd/collector/main.go`
- Create: `collector/cmd/collector/args.go`
- Create: `collector/cmd/collector/browser_windows.go`, `collector/cmd/collector/browser_other.go`
- Test: `collector/cmd/collector/args_test.go`

**Interfaces:**
- Consumes: `config.DefaultPath`, `settingsui.New`, `settingsui.Options`, `(*Server).Run`, `(*Server).Token`
- Produces:
  - `type cliOptions struct { Command string /* "settings" | "status" */; ConfigPath string }`
  - `func parseArgs(args []string, defaultConfig string, exists func(string) bool) (cliOptions, error)`
  - `func openBrowser(url string) error`: Windows dùng `exec.Command("rundll32", "url.dll,FileProtocolHandler", url).Start()`, nơi khác dùng `xdg-open`.

Luồng `main`: `os.UserConfigDir()`, `parseArgs`. Lỗi tham số → in usage ra stderr, thoát mã 2. `status` → in `"Đã có cấu hình tại <path>. Chạy \"collector settings\" để thay đổi."`, thoát 0. `settings` → `net.Listen("tcp", "127.0.0.1:0")`, `settingsui.New` với `IdleTimeout: 30*time.Minute` và `AppData` = `UserConfigDir`, in `"Mở trang cài đặt: <url>"`, `openBrowser` (lỗi thì chỉ cảnh báo), `signal.NotifyContext(ctx, os.Interrupt)`, `Run`. Lỗi khi chạy → thoát 1.

- [ ] **Step 1: Viết test thất bại**

```go
func TestParseArgs(t *testing.T) {
	// [] , config chưa có        → {Command: "settings", ConfigPath: def}
	// [] , config đã có          → {Command: "status",   ConfigPath: def}
	// ["settings"]               → {"settings", def}
	// ["settings","-config","x.json"] → {"settings", "x.json"}
	// ["scan"]                   → err có chữ "lệnh không hợp lệ"
	// ["settings","-bogus"]      → err != nil
}
```

- [ ] **Step 2: Chạy test, xác nhận thất bại**

Run: `go test -C collector ./cmd/collector/` → FAIL

- [ ] **Step 3: Viết `args.go`, `main.go`, `browser_*.go`**

- [ ] **Step 4: Chạy test và build**

Run: `go test -C collector ./...` → PASS. `$env:CGO_ENABLED="0"; go build -C collector -o collector.exe ./cmd/collector` → tạo `collector/collector.exe` (đã có trong `.gitignore`).

- [ ] **Step 5: Commit**

```bash
git add collector/cmd
git commit -m "feat(collector): lệnh settings mở trang cài đặt cục bộ"
```

---

### Task 7: Giao diện bố cục A, tài liệu, rà soát bảo mật

**Files:**
- Modify: `collector/internal/settingsui/web/index.html`
- Create: `collector/internal/settingsui/web/app.js`, `collector/internal/settingsui/web/style.css`
- Modify: `collector/internal/settingsui/server_test.go` (thêm `TestStaticAssets`)
- Modify: `collector/CLAUDE.md` (mục "Cấu trúc", "Lệnh"), `CLAUDE.md` (mục "Trạng thái hiện tại")

**Interfaces:**
- Consumes: hợp đồng API ở Task 5. Giao diện tham khảo bản phác đã duyệt (`.superpowers/brainstorm/*/content/settings-layout.html`, phương án A).

Yêu cầu của giao diện (mục 6, 7.6, 7.9 của spec):
- [ ] Đầu trang: tiêu đề, liên kết "Đóng" ở góc phải. Bấm "Đóng" → `POST /api/shutdown` → thay nội dung trang bằng "Đã đóng. Bạn có thể đóng tab này."
- [ ] Ô "Profile Thunderbird", nút "Mở" (hoặc nhấn Enter), nút "Chọn thư mục…". Dòng gợi ý "Tự tìm thấy N profile: …", mỗi tên bấm được, profile mặc định có chữ "(mặc định)".
- [ ] Khi tải trang: điền ô bằng `saved_profile_path`, nếu trống thì lấy profile `is_default`, rồi tải cây folder.
- [ ] "Chọn thư mục…" mở hộp duyệt ngay trong trang: danh sách thư mục từ `/api/browse`, nút "Lên" về thư mục cha (khi `parent` rỗng thì về danh sách ổ đĩa), nút "Chọn thư mục này".
- [ ] `kind = "profiles_root"` → hiện danh sách profile để chọn. Lỗi → hiện thông điệp `error` màu đỏ dưới ô đường dẫn. Có `config_warning` → hiện cảnh báo màu vàng.
- [ ] Mỗi tài khoản là một nhóm: checkbox ba trạng thái (dùng `indeterminate`), tên tài khoản, "N folder · X GB". Tài khoản có `unsupported` thì hiện "Chưa hỗ trợ kiểu lưu maildir".
- [ ] Dòng folder: thụt lề theo cấp, checkbox (chỉ khi `has_mbox`), tên, nhãn "mặc định bỏ qua" (`default_excluded`), nhãn "mới" (`is_new`), dung lượng, ngày sửa.
- [ ] Bấm checkbox folder → đặt cùng trạng thái cho folder đó và mọi folder con `has_mbox`.
- [ ] Chân trang: "Đã chọn X/Y folder · Z GB", cập nhật mỗi lần tick. Ô "Tự thêm folder mới xuất hiện sau này". Nút "Lưu" → `POST /api/config` → hiện "Đã lưu lúc HH:mm".
- [ ] Đổi profile khi còn thay đổi chưa lưu → `confirm("Có thay đổi chưa lưu. Bỏ qua và mở profile khác?")`.
- [ ] Định dạng: dung lượng dùng `toLocaleString('vi-VN', {maximumFractionDigits: 1})` với GB/MB/KB/B (`24,7 GB`). Ngày: "hôm nay", "hôm qua", hoặc `dd/MM/yyyy`.
- [ ] Màu dùng biến CSS ở `:root`, có `@media (prefers-color-scheme: dark)`. Không tải gì từ bên ngoài (không CDN, không font web).
- [ ] **Dữ liệu từ server chỉ đưa vào DOM bằng `textContent`/`createElement`, không dùng `innerHTML`**, vì tên folder có thể chứa ký tự HTML.

- [ ] **Step 1: Viết test thất bại**

```go
func TestStaticAssets(t *testing.T) {
	// GET "/?key=tok" → body chứa "<title>Cài đặt collector</title>", "app.js", "style.css"
	// GET "/app.js" có cookie → 200, Content-Type chứa "javascript"
	// GET "/style.css" có cookie → 200, Content-Type chứa "text/css"
}
```

- [ ] **Step 2: Chạy test, xác nhận thất bại**

Run: `go test -C collector ./internal/settingsui/ -run TestStaticAssets` → FAIL (chưa có `app.js`)

- [ ] **Step 3: Viết `index.html`, `app.js`, `style.css` theo danh sách yêu cầu ở trên**

- [ ] **Step 4: Chạy test, xác nhận đạt**

Run: `go test -C collector ./...` → PASS. Chạy thêm `go vet`, `gofmt -l` và build lại `collector.exe`.

- [ ] **Step 5: Chạy thử với profile tự sinh**

Đặt `$env:APPDATA` thành thư mục tạm trong scratchpad, rồi chạy `collector\collector.exe settings -config <scratchpad>\config.json`. Mở URL được in ra (dùng skill `run` nếu có trình duyệt điều khiển được). Nhập đường dẫn tuyệt đối của `data\thunderbird-profile` và kiểm tra:
- Nhóm Server `imap.vn-outsource.example.com` có `INBOX` (con `ProjectA`, `ProjectB`), `Sent`, `Drafts`, `Trash`. `Drafts` và `Trash` bỏ tick sẵn, có nhãn "mặc định bỏ qua". Ở đây `Drafts` và `Trash` có dung lượng 0 B.
- Nhóm `Local Folders` có `Inbox` và `Trash`, không có nút con từ `Inbox.sbd` rỗng.
- Bỏ tick `Sent`, bấm Lưu, rồi đọc `config.json` trong scratchpad (file này chỉ chứa đường dẫn folder): `ImapMail/imap.vn-outsource.example.com/Sent` có `"selected": false`. Tải lại trang thì `Sent` vẫn bỏ tick.
- Chọn thư mục `data\` (cha của profile) → hiện "Không phải profile Thunderbird". Thư mục `data\` không có `profiles.ini`.
- Bấm "Đóng" → tiến trình kết thúc.

- [ ] **Step 6: Cập nhật tài liệu**

`collector/CLAUDE.md`: mục "Cấu trúc" thêm `internal/profile/`, `internal/config/`, `internal/settingsui/`. Mục "Lệnh" ghi lệnh thật (`go test -C collector ./...`, build với `CGO_ENABLED=0`, `collector.exe settings [-config <path>]`), ghi chú `golangci-lint` chưa cài. `CLAUDE.md` gốc, mục "Trạng thái hiện tại": collector đã có màn hình setting, bước tiếp theo là đọc mbox.

- [ ] **Step 7: Rà soát bảo mật**

Chạy subagent `security-reviewer` (theo `CLAUDE.md` của dự án) trên toàn bộ `collector/`. Trọng tâm: không mở mbox/`.msf`, không ghi vào profile, server chỉ ở `127.0.0.1`, token/Host/Origin, log không chứa nội dung mail, không dùng `innerHTML` với dữ liệu. Sửa hết các phát hiện mức nghiêm trọng trước khi commit.

- [ ] **Step 8: Commit**

```bash
git add collector CLAUDE.md
git commit -m "feat(collector): giao diện trang setting (bố cục A) và cập nhật tài liệu"
```
