package service

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"pmmail/collector/internal/config"
	"pmmail/collector/internal/state"
	"pmmail/collector/internal/syncer"
	"pmmail/collector/internal/tokenstore"
	"pmmail/collector/internal/upload"
)

type fakeClient struct {
	url, token string
	meErr      error
	deleteErr  error
	deleted    []string
	ingested   int
}

func (f *fakeClient) Ingest(_ context.Context, b upload.Batch) (upload.IngestResult, error) {
	f.ingested += len(b.Messages)
	return upload.IngestResult{Received: len(b.Messages), Inserted: len(b.Messages)}, nil
}
func (f *fakeClient) Me(context.Context) (upload.Me, error) {
	return upload.Me{ID: 1, Email: "pm@example.com"}, f.meErr
}
func (f *fakeClient) Sources(context.Context) ([]upload.SourceInfo, error) {
	return []upload.SourceInfo{{FolderPath: "ImapMail/h/Old", MessageCount: 3}}, nil
}
func (f *fakeClient) DeleteFolderMail(_ context.Context, p string) (upload.DeleteResult, error) {
	f.deleted = append(f.deleted, p)
	if f.deleteErr != nil {
		return upload.DeleteResult{}, f.deleteErr
	}
	return upload.DeleteResult{DeletedMessages: 3}, nil
}

func newService(t *testing.T) (*Service, *fakeClient) {
	t.Helper()
	dir := t.TempDir()
	fc := &fakeClient{}
	s := &Service{
		Paths: Paths{
			Config: filepath.Join(dir, "config.json"),
			State:  filepath.Join(dir, "local", "state.json"),
			Token:  filepath.Join(dir, "local", "token.dat"),
			Lock:   filepath.Join(dir, "local", "sync.lock"),
		},
		NewClient: func(url, token string) Client {
			fc.url, fc.token = url, token
			return fc
		},
	}
	return s, fc
}

func TestSaveServerValidatesURL(t *testing.T) {
	s, _ := newService(t)
	for _, bad := range []string{"", "ftp://x", "http://server.company.example:8000", "not a url", "http://"} {
		if err := s.SaveServer(bad, "pmm_x"); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
	for _, ok := range []string{"http://127.0.0.1:8000", "http://localhost:8000/", "https://mail.company.example"} {
		if err := s.SaveServer(ok, "pmm_x"); err != nil {
			t.Errorf("%q rejected: %v", ok, err)
		}
	}
}

func TestSaveServerKeepsFoldersAndToken(t *testing.T) {
	s, _ := newService(t)
	cfg := &config.Config{ConfigVersion: 1, ProfilePath: `C:\p`, Folders: []config.FolderChoice{{Path: "ImapMail/h/INBOX", Selected: true}}}
	if err := config.Save(s.Paths.Config, cfg); err != nil {
		t.Fatal(err)
	}
	if err := s.SaveServer("http://127.0.0.1:8000/", "pmm_first"); err != nil {
		t.Fatal(err)
	}
	if err := s.SaveServer("HTTP://127.0.0.1:8000/api", ""); err != nil { // token rỗng, cùng server: giữ token
		t.Fatal(err)
	}
	got, _, err := config.Load(s.Paths.Config)
	if err != nil || got.ServerURL != "http://127.0.0.1:8000/api" || len(got.Folders) != 1 || got.ProfilePath != `C:\p` {
		t.Errorf("config = %+v, %v", got, err)
	}
	if e, _ := tokenstore.Load(s.Paths.Token); e.Token != "pmm_first" || e.Origin != "http://127.0.0.1:8000" {
		t.Errorf("token = %+v", e)
	}
}

// Token chỉ được gửi tới server đã nhập cùng nó: đổi server mà không nhập token mới bị từ chối.
func TestChangingServerRequiresNewToken(t *testing.T) {
	s, _ := newService(t)
	if err := s.SaveServer("http://127.0.0.1:8000", "pmm_first"); err != nil {
		t.Fatal(err)
	}
	for _, other := range []string{"https://other.example", "http://127.0.0.1:9000", "http://localhost:8000"} {
		if err := s.SaveServer(other, ""); !errors.Is(err, ErrTokenOtherServer) {
			t.Errorf("%s without token: err = %v", other, err)
		}
	}
	if cfg, _, _ := config.Load(s.Paths.Config); cfg.ServerURL != "http://127.0.0.1:8000" {
		t.Errorf("server changed to %q", cfg.ServerURL)
	}
	if err := s.SaveServer("https://other.example", "pmm_second"); err != nil {
		t.Fatal(err)
	}
	if e, _ := tokenstore.Load(s.Paths.Token); e != (tokenstore.Entry{Origin: "https://other.example", Token: "pmm_second"}) {
		t.Errorf("token = %+v", e)
	}
}

// config.json sửa tay (hoặc bị sửa) trỏ sang server khác: không gửi token đi.
func TestTamperedServerURLNeverGetsToken(t *testing.T) {
	for _, bad := range []string{"https://evil.example", "http://evil.example"} {
		s, fc := newService(t)
		_ = s.SaveServer("http://127.0.0.1:8000", "pmm_tok")
		cfg, _, _ := config.Load(s.Paths.Config)
		cfg.ProfilePath, cfg.ServerURL = t.TempDir(), bad
		_ = config.Save(s.Paths.Config, cfg)
		if _, err := s.RunSync(context.Background(), nil); err == nil {
			t.Errorf("%s: RunSync accepted", bad)
		}
		if st := s.ServerStatus(context.Background()); st.Connected || st.Error == "" {
			t.Errorf("%s: status = %+v", bad, st)
		}
		if fc.token != "" {
			t.Errorf("%s: token sent to %q", bad, fc.url)
		}
	}
}

func TestServerStatus(t *testing.T) {
	s, fc := newService(t)
	st := s.ServerStatus(context.Background())
	if st.HasToken || st.Connected || st.ServerURL != "" {
		t.Errorf("empty status = %+v", st)
	}
	_ = s.SaveServer("http://127.0.0.1:8000", "pmm_tok")
	st = s.ServerStatus(context.Background())
	if !st.HasToken || !st.Connected || st.Email != "pm@example.com" || fc.token != "pmm_tok" {
		t.Errorf("status = %+v", st)
	}
	fc.meErr = upload.ErrUnauthorized
	st = s.ServerStatus(context.Background())
	if st.Connected || st.Error == "" {
		t.Errorf("unauthorized status = %+v", st)
	}
}

// deleteSetup: profile có INBOX (chọn) và Old (bỏ chọn); state có vị trí của cả hai.
func deleteSetup(t *testing.T, s *Service) string {
	t.Helper()
	profileDir := t.TempDir()
	for _, p := range []string{"ImapMail/h/INBOX", "ImapMail/h/Old"} {
		full := filepath.Join(profileDir, filepath.FromSlash(p))
		_ = os.MkdirAll(filepath.Dir(full), 0o755)
		_ = os.WriteFile(full, []byte("From - x\n\nb\n"), 0o644)
	}
	_ = s.SaveServer("http://127.0.0.1:8000", "pmm_tok")
	cfg, _, _ := config.Load(s.Paths.Config)
	cfg.ProfilePath = profileDir
	cfg.AutoIncludeNewFolders = true
	cfg.Folders = []config.FolderChoice{
		{Path: "ImapMail/h/INBOX", Selected: true},
		{Path: "ImapMail/h/Old", Selected: false},
	}
	_ = config.Save(s.Paths.Config, cfg)
	st := state.New("http://127.0.0.1:8000", profileDir)
	st.Folders["ImapMail/h/Old"] = state.Folder{Offset: 99}
	st.Folders["ImapMail/h/INBOX"] = state.Folder{Offset: 5}
	st.Folders["ImapMail/h/Gone"] = state.Folder{Offset: 7}
	_ = st.Save(s.Paths.State)
	return profileDir
}

func TestDeleteFolderRequiresUnselectedAndResetsState(t *testing.T) {
	s, fc := newService(t)
	profileDir := deleteSetup(t, s)
	if _, err := s.DeleteFolder(context.Background(), "ImapMail/h/INBOX"); !errors.Is(err, ErrFolderSelected) {
		t.Errorf("selected folder: err = %v", err)
	}
	res, err := s.DeleteFolder(context.Background(), "ImapMail/h/Old")
	if err != nil || res.DeletedMessages != 3 || len(fc.deleted) != 1 || fc.deleted[0] != "ImapMail/h/Old" {
		t.Fatalf("delete: %+v %v %v", res, err, fc.deleted)
	}
	after, _ := state.Load(s.Paths.State, "http://127.0.0.1:8000", profileDir)
	if _, ok := after.Folders["ImapMail/h/Old"]; ok || after.Folders["ImapMail/h/INBOX"].Offset != 5 {
		t.Errorf("state after delete = %+v", after.Folders)
	}
}

// Folder đã đổi tên/xóa trong Thunderbird (không còn file mbox) xóa được dù "tự thêm folder mới"
// đang bật; đường dẫn lạ bị từ chối.
func TestDeleteVanishedFolder(t *testing.T) {
	s, fc := newService(t)
	profileDir := deleteSetup(t, s)
	if _, err := s.DeleteFolder(context.Background(), "ImapMail/h/Gone"); err != nil {
		t.Fatalf("vanished folder: %v", err)
	}
	after, _ := state.Load(s.Paths.State, "http://127.0.0.1:8000", profileDir)
	if _, ok := after.Folders["ImapMail/h/Gone"]; ok {
		t.Errorf("state not reset: %+v", after.Folders)
	}
	for _, bad := range []string{"../outside", "C:/x", "/abs", "ImapMail/../../x", ""} {
		if _, err := s.DeleteFolder(context.Background(), bad); err == nil {
			t.Errorf("%q accepted", bad)
		}
	}
	if len(fc.deleted) != 1 {
		t.Errorf("deleted = %v", fc.deleted)
	}
}

// Server không còn folder (đã xóa ở máy khác): coi như đã xóa, quên vị trí đã đọc.
func TestDeleteFolderAlreadyGoneOnServer(t *testing.T) {
	s, fc := newService(t)
	profileDir := deleteSetup(t, s)
	// 404 bất kỳ (sai đường dẫn, proxy) không được coi là "đã xóa": dữ liệu có thể vẫn còn.
	fc.deleteErr = &upload.HTTPError{Status: 404}
	if _, err := s.DeleteFolder(context.Background(), "ImapMail/h/Old"); err == nil {
		t.Fatal("plain 404 treated as deleted")
	}
	if st, _ := state.Load(s.Paths.State, "http://127.0.0.1:8000", profileDir); st.Folders["ImapMail/h/Old"].Offset != 99 {
		t.Errorf("state reset on plain 404: %+v", st.Folders)
	}
	fc.deleteErr = &upload.HTTPError{Status: 404, Code: upload.CodeSourceNotFound}
	res, err := s.DeleteFolder(context.Background(), "ImapMail/h/Old")
	if err != nil || res.DeletedMessages != 0 {
		t.Fatalf("res %+v err %v", res, err)
	}
	after, _ := state.Load(s.Paths.State, "http://127.0.0.1:8000", profileDir)
	if _, ok := after.Folders["ImapMail/h/Old"]; ok {
		t.Errorf("state not reset: %+v", after.Folders)
	}
}

func TestDescribeUpgradeRequired(t *testing.T) {
	if got := Describe(&upload.HTTPError{Status: 426}); !strings.Contains(got, "cập nhật collector") {
		t.Errorf("Describe(426) = %q", got)
	}
}

// Client được tạo bằng đúng URL đã kiểm tra (đã chuẩn hóa), không phải chuỗi thô trong config.
func TestClientUsesValidatedURL(t *testing.T) {
	s, fc := newService(t)
	_ = s.SaveServer("http://127.0.0.1:8000", "pmm_tok")
	cfg, _, _ := config.Load(s.Paths.Config)
	cfg.ServerURL = " HTTP://127.0.0.1:8000/?x=1#f "
	_ = config.Save(s.Paths.Config, cfg)
	s.ServerStatus(context.Background())
	if fc.url != "http://127.0.0.1:8000" {
		t.Errorf("client url = %q", fc.url)
	}
}

func TestRunSyncPreconditions(t *testing.T) {
	s, _ := newService(t)
	ctx := context.Background()
	if _, err := s.RunSync(ctx, nil); !errors.Is(err, ErrNoConfig) {
		t.Errorf("no config: %v", err)
	}
	_ = config.Save(s.Paths.Config, &config.Config{ConfigVersion: 1, ProfilePath: t.TempDir()})
	if _, err := s.RunSync(ctx, nil); !errors.Is(err, ErrNoServer) {
		t.Errorf("no server: %v", err)
	}
	cfg, _, _ := config.Load(s.Paths.Config)
	cfg.ServerURL = "http://127.0.0.1:8000"
	_ = config.Save(s.Paths.Config, cfg)
	if _, err := s.RunSync(ctx, nil); !errors.Is(err, ErrNoToken) {
		t.Errorf("no token: %v", err)
	}
	_ = tokenstore.Save(s.Paths.Token, tokenstore.Entry{Origin: "http://127.0.0.1:8000", Token: "pmm_tok"})
	release, _ := syncer.AcquireLock(s.Paths.Lock)
	if _, err := s.RunSync(ctx, nil); !errors.Is(err, syncer.ErrAlreadyRunning) {
		t.Errorf("locked: %v", err)
	}
	release()
}

func TestRunSyncSavesState(t *testing.T) {
	s, fc := newService(t)
	profileDir := copyProfile(t)
	_ = config.Save(s.Paths.Config, &config.Config{
		ConfigVersion: 1, ProfilePath: profileDir, AutoIncludeNewFolders: true,
		ServerURL: "http://127.0.0.1:8000",
	})
	_ = tokenstore.Save(s.Paths.Token, tokenstore.Entry{Origin: "http://127.0.0.1:8000", Token: "pmm_tok"})
	var progressCalls int
	sum, err := s.RunSync(context.Background(), func(syncer.Progress) { progressCalls++ })
	if total := sampleMailCount(t); err != nil || sum.Inserted != total || fc.ingested != total || progressCalls == 0 {
		t.Fatalf("sum %+v err %v ingested %d progress %d", sum, err, fc.ingested, progressCalls)
	}
	st, reset := state.Load(s.Paths.State, "http://127.0.0.1:8000", profileDir)
	if reset || len(st.Folders) == 0 {
		t.Errorf("state not saved: reset=%v %+v", reset, st.Folders)
	}
	if _, err := os.Stat(s.Paths.Lock); !os.IsNotExist(err) {
		t.Errorf("lock not released: %v", err)
	}
}

// sampleMailCount: số mail trong profile tự sinh, lấy từ đáp án của generator
// (data/expected_threads.json) cộng một bản chép trùng sang Local Folders/Inbox.
func sampleMailCount(t *testing.T) int {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "..", "..", "data", "expected_threads.json"))
	if err != nil {
		t.Fatal(err)
	}
	var groups map[string][]string
	if err := json.Unmarshal(b, &groups); err != nil {
		t.Fatal(err)
	}
	n := 1
	for _, ids := range groups {
		n += len(ids)
	}
	return n
}

func copyProfile(t *testing.T) string {
	t.Helper()
	src := filepath.Join("..", "..", "..", "data", "thunderbird-profile")
	if _, err := os.Stat(src); err != nil {
		t.Skip("không có data/thunderbird-profile")
	}
	dst := t.TempDir()
	err := filepath.WalkDir(src, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, p)
		target := filepath.Join(dst, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		in, err := os.Open(p)
		if err != nil {
			return err
		}
		defer in.Close()
		out, err := os.Create(target)
		if err != nil {
			return err
		}
		defer out.Close()
		_, err = io.Copy(out, in)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	// mtime cũ để mail cuối file được đọc ngay (không bị giữ lại).
	old := time.Now().Add(-time.Hour)
	_ = filepath.WalkDir(dst, func(p string, d fs.DirEntry, _ error) error {
		if !d.IsDir() {
			_ = os.Chtimes(p, old, old)
		}
		return nil
	})
	return dst
}
