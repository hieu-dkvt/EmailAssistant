package settingsui

import (
	"context"
	"encoding/json"
	"io"
	"io/fs"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"pmmail/collector/internal/config"
	"pmmail/collector/internal/service"
	"pmmail/collector/internal/syncer"
	"pmmail/collector/internal/tokenstore"
	"pmmail/collector/internal/upload"
)

// fakeBackend thay cho backend thật. gate != nil: Ingest chờ tới khi gate đóng (hoặc bị hủy).
type fakeBackend struct {
	mu       sync.Mutex
	gate     chan struct{}
	ingested int
	deleted  []string
}

func (f *fakeBackend) Ingest(ctx context.Context, b upload.Batch) (upload.IngestResult, error) {
	if f.gate != nil {
		select {
		case <-f.gate:
		case <-ctx.Done():
			return upload.IngestResult{}, ctx.Err()
		}
	}
	f.mu.Lock()
	f.ingested += len(b.Messages)
	f.mu.Unlock()
	return upload.IngestResult{Received: len(b.Messages), Inserted: len(b.Messages)}, nil
}
func (f *fakeBackend) Me(context.Context) (upload.Me, error) {
	return upload.Me{ID: 1, Email: "pm@example.com", DisplayName: "PM"}, nil
}
func (f *fakeBackend) Sources(context.Context) ([]upload.SourceInfo, error) {
	return []upload.SourceInfo{{ID: 1, FolderPath: "ImapMail/imap.example.com/Trash", MessageCount: 4}}, nil
}
func (f *fakeBackend) DeleteFolderMail(_ context.Context, p string) (upload.DeleteResult, error) {
	f.mu.Lock()
	f.deleted = append(f.deleted, p)
	f.mu.Unlock()
	return upload.DeleteResult{DeletedMessages: 4}, nil
}

type syncEnv struct {
	s       *Server
	svc     *service.Service
	backend *fakeBackend
	cfgPath string
}

func newSyncEnv(t *testing.T, opts Options) *syncEnv {
	t.Helper()
	dir := t.TempDir()
	fb := &fakeBackend{}
	svc := &service.Service{
		Paths: service.Paths{
			Config: filepath.Join(dir, "config.json"),
			State:  filepath.Join(dir, "local", "state.json"),
			Token:  filepath.Join(dir, "local", "token.dat"),
			Lock:   filepath.Join(dir, "local", "sync.lock"),
		},
		NewClient: func(string, string) service.Client { return fb },
	}
	opts.Service = svc
	opts.ConfigPath = svc.Paths.Config
	return &syncEnv{s: newTestServerWith(t, opts), svc: svc, backend: fb, cfgPath: svc.Paths.Config}
}

// ready: cấu hình trỏ tới bản sao profile tự sinh, có server và token.
func (e *syncEnv) ready(t *testing.T) {
	t.Helper()
	dir := copySyntheticProfile(t)
	cfg := &config.Config{ConfigVersion: 1, ProfilePath: dir, AutoIncludeNewFolders: true, ServerURL: "http://127.0.0.1:8000"}
	if err := config.Save(e.cfgPath, cfg); err != nil {
		t.Fatal(err)
	}
	if err := tokenstore.Save(e.svc.Paths.Token, tokenstore.Entry{Origin: "http://127.0.0.1:8000", Token: "pmm_tok"}); err != nil {
		t.Fatal(err)
	}
}

func decodeJSON(t *testing.T, body string, v any) {
	t.Helper()
	if err := json.Unmarshal([]byte(body), v); err != nil {
		t.Fatalf("decode %q: %v", body, err)
	}
}

type syncStatusResp struct {
	State    string `json:"state"`
	Message  string `json:"message"`
	Progress *struct {
		FolderCount int `json:"folder_count"`
		Sent        int `json:"sent"`
	} `json:"progress"`
	Summary *struct {
		Inserted int `json:"inserted"`
	} `json:"summary"`
}

func waitSync(t *testing.T, s *Server, want string) syncStatusResp {
	t.Helper()
	deadline := time.Now().Add(15 * time.Second)
	for {
		var st syncStatusResp
		decodeJSON(t, doReq(s, "GET", "/api/sync/status", "").Body.String(), &st)
		if st.State == want {
			return st
		}
		if time.Now().After(deadline) {
			t.Fatalf("sync state = %+v, want %s", st, want)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func TestServerSettingsNeverReturnToken(t *testing.T) {
	e := newSyncEnv(t, Options{})
	w := doReq(e.s, "POST", "/api/server/settings", `{"server_url":"http://127.0.0.1:8000/","token":"pmm_SECRET_TOKEN"}`)
	if w.Code != 200 || strings.Contains(w.Body.String(), "SECRET") {
		t.Fatalf("settings: %d %s", w.Code, w.Body.String())
	}
	var st service.Status
	decodeJSON(t, doReq(e.s, "GET", "/api/server/status", "").Body.String(), &st)
	if !st.HasToken || !st.Connected || st.Email != "pm@example.com" || st.ServerURL != "http://127.0.0.1:8000" {
		t.Errorf("status = %+v", st)
	}
	w = doReq(e.s, "POST", "/api/server/settings", `{"server_url":"http://evil.example:8000","token":""}`)
	if w.Code != 400 || !strings.Contains(w.Body.String(), "https") {
		t.Errorf("insecure url: %d %s", w.Code, w.Body.String())
	}
}

func TestSaveFoldersKeepsServerURL(t *testing.T) {
	e := newSyncEnv(t, Options{})
	doReq(e.s, "POST", "/api/server/settings", `{"server_url":"http://127.0.0.1:8000","token":"pmm_t"}`)
	dir := t.TempDir()
	mkProfile(t, dir)
	body, _ := json.Marshal(map[string]any{
		"profile_path": dir, "auto_include_new_folders": false,
		"folders": map[string]bool{"ImapMail/imap.example.com/INBOX": true},
	})
	if w := doReq(e.s, "POST", "/api/config", string(body)); w.Code != 200 {
		t.Fatalf("save: %d %s", w.Code, w.Body.String())
	}
	cfg, _, err := config.Load(e.cfgPath)
	if err != nil || cfg.ServerURL != "http://127.0.0.1:8000" || cfg.ProfilePath != dir {
		t.Errorf("config = %+v, %v", cfg, err)
	}
}

func TestSyncStartRequiresSetup(t *testing.T) {
	e := newSyncEnv(t, Options{})
	w := doReq(e.s, "POST", "/api/sync/start", "{}")
	if w.Code != 400 || !strings.Contains(w.Body.String(), "cấu hình") {
		t.Errorf("start without config: %d %s", w.Code, w.Body.String())
	}
}

func TestSyncRunsToCompletion(t *testing.T) {
	e := newSyncEnv(t, Options{})
	e.ready(t)
	if w := doReq(e.s, "POST", "/api/sync/start", "{}"); w.Code != 200 {
		t.Fatalf("start: %d %s", w.Code, w.Body.String())
	}
	st := waitSync(t, e.s, "done")
	if st.Summary == nil || st.Summary.Inserted != sampleMailCount(t) || !strings.Contains(st.Message, "Đồng bộ hoàn thành") {
		t.Errorf("status = %+v", st)
	}
	if st.Progress == nil || st.Progress.FolderCount == 0 {
		t.Errorf("progress = %+v", st.Progress)
	}
}

func TestSyncConflictAndStop(t *testing.T) {
	e := newSyncEnv(t, Options{})
	e.ready(t)
	e.backend.gate = make(chan struct{})
	defer close(e.backend.gate)
	if w := doReq(e.s, "POST", "/api/sync/start", "{}"); w.Code != 200 {
		t.Fatalf("start: %d", w.Code)
	}
	if w := doReq(e.s, "POST", "/api/sync/start", "{}"); w.Code != 409 {
		t.Errorf("second start: %d", w.Code)
	}
	doReq(e.s, "POST", "/api/sync/stop", "{}")
	st := waitSync(t, e.s, "stopped")
	if !strings.Contains(st.Message, "dừng") {
		t.Errorf("stopped message = %q", st.Message)
	}
}

func TestDeleteFolderEndpoint(t *testing.T) {
	e := newSyncEnv(t, Options{})
	e.ready(t)
	cfg, _, _ := config.Load(e.cfgPath)
	cfg.AutoIncludeNewFolders = false
	cfg.Folders = []config.FolderChoice{
		{Path: "ImapMail/imap.vn-outsource.example.com/INBOX", Selected: true},
		{Path: "ImapMail/imap.vn-outsource.example.com/Trash", Selected: false},
	}
	_ = config.Save(e.cfgPath, cfg)

	w := doReq(e.s, "POST", "/api/server/delete-folder", `{"path":"ImapMail/imap.vn-outsource.example.com/INBOX"}`)
	if w.Code != 409 {
		t.Errorf("selected folder: %d %s", w.Code, w.Body.String())
	}
	w = doReq(e.s, "POST", "/api/server/delete-folder", `{"path":"ImapMail/imap.vn-outsource.example.com/Trash"}`)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"deleted_messages":4`) {
		t.Errorf("delete: %d %s", w.Code, w.Body.String())
	}
	w = doReq(e.s, "GET", "/api/server/sources", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"message_count":4`) {
		t.Errorf("sources: %d %s", w.Code, w.Body.String())
	}
}

// Đổi cấu hình folder hoặc server giữa lượt đồng bộ làm state lệch: từ chối, bảo bấm Dừng.
func TestConfigChangeRefusedWhileSyncing(t *testing.T) {
	e := newSyncEnv(t, Options{})
	e.ready(t)
	e.backend.gate = make(chan struct{})
	defer close(e.backend.gate)
	if w := doReq(e.s, "POST", "/api/sync/start", "{}"); w.Code != 200 {
		t.Fatalf("start: %d", w.Code)
	}
	cfg, _, _ := config.Load(e.cfgPath)
	body, _ := json.Marshal(map[string]any{
		"profile_path": cfg.ProfilePath, "auto_include_new_folders": false, "folders": map[string]bool{},
	})
	if w := doReq(e.s, "POST", "/api/config", string(body)); w.Code != 409 || !strings.Contains(w.Body.String(), "Dừng") {
		t.Errorf("save config while syncing: %d %s", w.Code, w.Body.String())
	}
	w := doReq(e.s, "POST", "/api/server/settings", `{"server_url":"http://127.0.0.1:8000","token":"pmm_new"}`)
	if w.Code != 409 {
		t.Errorf("save server while syncing: %d %s", w.Code, w.Body.String())
	}
	doReq(e.s, "POST", "/api/sync/stop", "{}")
	waitSync(t, e.s, "stopped")
}

// Lỗi lập trình (panic) trong lượt đồng bộ không được làm sập collector.
func TestSyncPanicReportedAsError(t *testing.T) {
	m := newSyncManager()
	m.start(context.Background(), func(context.Context, func(syncer.Progress)) (syncer.Summary, error) {
		panic("SECRET mail content in panic value")
	}, func() {})
	m.wait(5 * time.Second)
	st := m.snapshot()
	if st.State != "error" || strings.Contains(st.Message, "SECRET") {
		t.Errorf("status = %+v", st)
	}
}

// Server cục bộ không tự tắt vì "không hoạt động" khi đang đồng bộ.
func TestIdleTimeoutWaitsForSync(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	e := newSyncEnv(t, Options{Addr: ln.Addr().String(), IdleTimeout: 200 * time.Millisecond})
	e.ready(t)
	e.backend.gate = make(chan struct{})
	done := make(chan error, 1)
	go func() { done <- e.s.Run(context.Background(), ln) }()

	w := rawReq(e.s, "POST", "/api/sync/start", ln.Addr().String(), e.s.Token(), "http://"+ln.Addr().String(), strings.NewReader("{}"))
	if w.Code != 200 {
		t.Fatalf("start: %d %s", w.Code, w.Body.String())
	}
	select {
	case <-done:
		t.Fatal("server stopped while sync was running")
	case <-time.After(time.Second):
	}
	close(e.backend.gate)
	select {
	case err := <-done:
		if err != nil {
			t.Errorf("Run: %v", err)
		}
	case <-time.After(15 * time.Second):
		t.Fatal("server did not stop after sync finished and idle timeout passed")
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

func copySyntheticProfile(t *testing.T) string {
	t.Helper()
	src := filepath.Join("..", "..", "..", "data", "thunderbird-profile")
	if _, err := os.Stat(src); err != nil {
		t.Skip("không có data/thunderbird-profile")
	}
	dst := t.TempDir()
	old := time.Now().Add(-time.Hour)
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
		if _, err := io.Copy(out, in); err != nil {
			out.Close()
			return err
		}
		out.Close()
		return os.Chtimes(target, old, old)
	})
	if err != nil {
		t.Fatal(err)
	}
	return dst
}
