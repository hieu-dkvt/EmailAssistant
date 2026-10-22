package syncer

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
	"pmmail/collector/internal/parse"
	"pmmail/collector/internal/state"
	"pmmail/collector/internal/upload"
)

const (
	inboxPath   = "ImapMail/imap.vn-outsource.example.com/INBOX"
	projectPath = "ImapMail/imap.vn-outsource.example.com/INBOX.sbd/ProjectA"
)

// fakeServer ghi lại các lô; failAt: lô thứ n (bắt đầu từ 1) trả lỗi err; reject: lỗi theo nội dung lô.
type fakeServer struct {
	batches []upload.Batch
	failAt  int
	err     error
	reject  func(upload.Batch) error
	calls   int
}

func (f *fakeServer) Ingest(_ context.Context, b upload.Batch) (upload.IngestResult, error) {
	f.calls++
	if f.failAt > 0 && len(f.batches)+1 == f.failAt {
		f.failAt = 0
		return upload.IngestResult{}, f.err
	}
	if f.reject != nil {
		if err := f.reject(b); err != nil {
			return upload.IngestResult{}, err
		}
	}
	f.batches = append(f.batches, b)
	return upload.IngestResult{Received: len(b.Messages), Inserted: len(b.Messages)}, nil
}

func (f *fakeServer) mails() int {
	n := 0
	for _, b := range f.batches {
		n += len(b.Messages)
	}
	return n
}

func (f *fakeServer) mailsIn(folder string) int {
	n := 0
	for _, b := range f.batches {
		if b.Source.FolderPath == folder {
			n += len(b.Messages)
		}
	}
	return n
}

// copyProfile chép profile tự sinh (data/) sang thư mục tạm để test sửa được file.
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
	return dst
}

// totalMails: số mail trong profile tự sinh, lấy từ đáp án của generator (data/expected_threads.json)
// cộng một bản chép trùng (A-estimate 5) sang Local Folders/Inbox. Không viết cứng: số mail đổi theo
// tham số của generator.
func totalMails(t *testing.T) int {
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

type harness struct {
	t       *testing.T
	dir     string
	cfg     *config.Config
	st      *state.State
	server  *fakeServer
	saves   int
	now     time.Time
	options func(*Options)
}

func newHarness(t *testing.T) *harness {
	dir := copyProfile(t)
	return &harness{
		t:      t,
		dir:    dir,
		cfg:    &config.Config{ProfilePath: dir, AutoIncludeNewFolders: true},
		st:     state.New("http://server", dir),
		server: &fakeServer{},
		now:    time.Now().Add(time.Hour),
	}
}

func (h *harness) run() (Summary, error) {
	h.t.Helper()
	h.server.batches = nil
	opts := Options{
		ProfileDir: h.dir,
		Config:     h.cfg,
		State:      h.st,
		SaveState:  func(*state.State) error { h.saves++; return nil },
		Client:     h.server,
		Now:        func() time.Time { return h.now },
	}
	if h.options != nil {
		h.options(&opts)
	}
	return Run(context.Background(), opts)
}

func (h *harness) file(rel string) string { return filepath.Join(h.dir, filepath.FromSlash(rel)) }

func TestFirstRunSendsEverySelectedMailThenNothing(t *testing.T) {
	h := newHarness(t)
	total := totalMails(t)
	sum, err := h.run()
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if h.server.mails() != total || sum.MailsRead != total || sum.Inserted != total {
		t.Fatalf("sent %d, summary %+v", h.server.mails(), sum)
	}
	fi, _ := os.Stat(h.file(inboxPath))
	if got := h.st.Folders[inboxPath]; got.Offset != fi.Size() || got.Size != fi.Size() {
		t.Errorf("state INBOX = %+v, size %d", got, fi.Size())
	}
	if _, ok := h.st.Folders["ImapMail/imap.vn-outsource.example.com/Trash"]; ok {
		t.Error("Trash (mặc định bỏ tick) không được đọc")
	}
	for _, b := range h.server.batches {
		if b.SchemaVersion != 1 || b.ParserVersion == "" || b.Source.Kind == "" || b.Source.Account == "" {
			t.Errorf("batch header = %+v", b.Source)
		}
	}

	sum, err = h.run()
	if err != nil || h.server.mails() != 0 || sum.FoldersScanned != 0 || sum.FoldersSkipped == 0 {
		t.Errorf("second run: sent %d, summary %+v, err %v", h.server.mails(), sum, err)
	}
}

func TestOnlyNewMailIsSent(t *testing.T) {
	h := newHarness(t)
	if _, err := h.run(); err != nil {
		t.Fatal(err)
	}
	f, err := os.OpenFile(h.file(inboxPath), os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = f.WriteString("\nFrom - Sat Oct 17 10:00:00 2026\nMessage-ID: <new@x.example>\nSubject: new\n\nbody\n\n")
	f.Close()
	h.now = time.Now().Add(time.Hour)

	sum, err := h.run()
	if err != nil || h.server.mails() != 1 || h.server.mailsIn(inboxPath) != 1 || sum.FoldersScanned != 1 {
		t.Fatalf("sent %d (inbox %d), summary %+v, err %v", h.server.mails(), h.server.mailsIn(inboxPath), sum, err)
	}
	if h.server.batches[0].Messages[0].MessageID != "new@x.example" {
		t.Errorf("sent %+v", h.server.batches[0].Messages[0])
	}
}

func TestShrunkFileIsReadAgainFromStart(t *testing.T) {
	h := newHarness(t)
	if _, err := h.run(); err != nil {
		t.Fatal(err)
	}
	// Giả lập compact: file còn đúng một mail (nhỏ hơn offset đã lưu).
	one := "From - x\nMessage-ID: <compacted@x.example>\nSubject: c\n\nbody\n\n"
	if err := os.WriteFile(h.file(projectPath), []byte(one), 0o644); err != nil {
		t.Fatal(err)
	}
	h.now = time.Now().Add(time.Hour)
	if _, err := h.run(); err != nil || h.server.mailsIn(projectPath) != 1 {
		t.Fatalf("project mails = %d, err %v", h.server.mailsIn(projectPath), err)
	}
}

func TestLastMailHeldBackWhileFileIsFresh(t *testing.T) {
	h := newHarness(t)
	fi, _ := os.Stat(h.file(inboxPath))
	h.now = fi.ModTime().Add(time.Second) // file vừa được sửa
	if _, err := h.run(); err != nil {
		t.Fatal(err)
	}
	held := h.st.Folders[inboxPath]
	if held.Offset >= fi.Size() || held.Size != 0 {
		t.Fatalf("INBOX state = %+v (size %d): mail cuối phải bị để lại và Size chưa ghi", held, fi.Size())
	}
	first := h.server.mails()

	h.now = time.Now().Add(time.Hour)
	if _, err := h.run(); err != nil {
		t.Fatal(err)
	}
	if total := totalMails(t); first+h.server.mails() != total || h.st.Folders[inboxPath].Offset != fi.Size() {
		t.Errorf("first %d + second %d != %d", first, h.server.mails(), total)
	}
}

func TestBatchesSplitAndStateSavedPerBatch(t *testing.T) {
	h := newHarness(t)
	h.options = func(o *Options) { o.MaxBatchMessages = 2 }
	if _, err := h.run(); err != nil {
		t.Fatal(err)
	}
	var last int64 = -1
	for _, b := range h.server.batches {
		if len(b.Messages) > 2 {
			t.Fatalf("batch of %d", len(b.Messages))
		}
		if b.Source.FolderPath == projectPath {
			if b.Source.LastOffset <= last {
				t.Errorf("last_offset not increasing: %d after %d", b.Source.LastOffset, last)
			}
			last = b.Source.LastOffset
		}
	}
	if h.saves < len(h.server.batches) {
		t.Errorf("saves %d < batches %d", h.saves, len(h.server.batches))
	}
}

func TestUnauthorizedStopsRun(t *testing.T) {
	h := newHarness(t)
	h.server.failAt, h.server.err = 1, upload.ErrUnauthorized
	_, err := h.run()
	if !errors.Is(err, upload.ErrUnauthorized) || len(h.st.Folders) != 0 {
		t.Errorf("err = %v, state = %+v", err, h.st.Folders)
	}
}

func TestFolderErrorKeepsOffsetAndContinues(t *testing.T) {
	h := newHarness(t)
	h.options = func(o *Options) { o.MaxBatchMessages = 2 }
	h.server.failAt, h.server.err = 2, &upload.HTTPError{Status: 404}
	sum, err := h.run()
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if sum.FoldersFailed != 1 || len(sum.FolderErrors) != 1 || !strings.Contains(sum.FolderErrors[0].Message, "404") {
		t.Fatalf("summary = %+v", sum)
	}
	failed := sum.FolderErrors[0].Path
	firstBatchEnd := h.server.batches[0].Source.LastOffset
	if h.st.Folders[failed].Offset != firstBatchEnd || h.st.Folders[failed].Size != 0 {
		t.Errorf("failed folder state = %+v, want offset %d", h.st.Folders[failed], firstBatchEnd)
	}
	if h.server.mails() != totalMails(t)-countMails(t, h.file(failed))+2 {
		t.Errorf("other folders should continue: sent %d", h.server.mails())
	}
}

func TestUnselectedFolderNotRead(t *testing.T) {
	h := newHarness(t)
	h.cfg = &config.Config{
		ProfilePath:           h.dir,
		AutoIncludeNewFolders: false,
		Folders:               []config.FolderChoice{{Path: inboxPath, Selected: true}},
	}
	if _, err := h.run(); err != nil {
		t.Fatal(err)
	}
	for _, b := range h.server.batches {
		if b.Source.FolderPath != inboxPath {
			t.Errorf("read unselected folder %s", b.Source.FolderPath)
		}
	}
}

func TestExpungedSkipped(t *testing.T) {
	h := newHarness(t)
	mbox := "From - a\nX-Mozilla-Status: 0009\nMessage-ID: <gone@x>\n\nb\n\n" +
		"From - b\nX-Mozilla-Status: 0001\nMessage-ID: <kept@x>\n\nb\n\n"
	if err := os.WriteFile(h.file("Mail/Local Folders/Inbox"), []byte(mbox), 0o644); err != nil {
		t.Fatal(err)
	}
	sum, err := h.run()
	if err != nil || sum.Expunged != 1 || h.server.mailsIn("Mail/Local Folders/Inbox") != 1 {
		t.Errorf("summary %+v err %v", sum, err)
	}
}

func sentIDs(s *fakeServer, folder string) map[string]bool {
	ids := map[string]bool{}
	for _, b := range s.batches {
		if b.Source.FolderPath == folder {
			for _, m := range b.Messages {
				ids[m.MessageID] = true
			}
		}
	}
	return ids
}

func appendMails(t *testing.T, path string, ids ...string) {
	t.Helper()
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	for _, id := range ids {
		_, _ = f.WriteString("\nFrom - Sat Oct 17 10:00:00 2026\nMessage-ID: <" + id + ">\nSubject: " + id +
			"\n\n" + strings.Repeat("padding line\n", 40) + "\n")
	}
}

// Compact (bỏ mail đầu) rồi có mail mới làm file lớn hơn vị trí đã lưu: vị trí cũ không còn
// đúng, phải đọc lại từ đầu, nếu không mail mới bị bỏ sót vĩnh viễn.
func TestCompactionThenGrowthRereads(t *testing.T) {
	h := newHarness(t)
	if _, err := h.run(); err != nil {
		t.Fatal(err)
	}
	path := h.file(projectPath)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	oldSize := int64(len(data))
	s := string(data)
	second := strings.Index(s[1:], "\nFrom ") + 1 // dòng trống trước mail thứ hai
	if err := os.WriteFile(path, []byte(s[second+1:]), 0o644); err != nil {
		t.Fatal(err)
	}
	appendMails(t, path, "grow-1@x.example", "grow-2@x.example", "grow-3@x.example")
	if fi, _ := os.Stat(path); fi.Size() < oldSize {
		t.Fatalf("test setup: new size %d < old %d", fi.Size(), oldSize)
	}
	h.now = time.Now().Add(time.Hour)
	if _, err := h.run(); err != nil {
		t.Fatal(err)
	}
	got := sentIDs(h.server, projectPath)
	for _, id := range []string{"grow-1@x.example", "grow-2@x.example", "grow-3@x.example"} {
		if !got[id] {
			t.Errorf("new mail %s not sent (sent %v)", id, got)
		}
	}
}

// File lớn thêm trong lúc đang đọc: chỉ đọc tới kích thước lúc mở, không gửi mail ghi dở.
func TestReadStopsAtSizeSeenAtOpen(t *testing.T) {
	h := newHarness(t)
	path := h.file(inboxPath)
	fi, _ := os.Stat(path)
	appended := false
	h.options = func(o *Options) {
		o.Progress = func(p Progress) {
			if p.FolderPath == inboxPath && !appended {
				appended = true
				f, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
				_, _ = f.WriteString("\nFrom - x\nMessage-ID: <partial@x.example>\nSubject: partial\n\nha")
				f.Close()
			}
		}
	}
	if _, err := h.run(); err != nil {
		t.Fatal(err)
	}
	if !appended || sentIDs(h.server, inboxPath)["partial@x.example"] {
		t.Fatalf("appended %v, partial mail sent: %v", appended, sentIDs(h.server, inboxPath))
	}
	if got := h.st.Folders[inboxPath]; got.Offset != fi.Size() {
		t.Errorf("offset %d, want size at open %d", got.Offset, fi.Size())
	}
}

func rejectID(id string) func(upload.Batch) error {
	return func(b upload.Batch) error {
		for _, m := range b.Messages {
			if m.MessageID == id {
				return &upload.HTTPError{Status: 422}
			}
		}
		return nil
	}
}

// Một mail bị server từ chối không được làm kẹt folder mãi mãi.
func TestRejectedMailIsolated(t *testing.T) {
	h := newHarness(t)
	appendMails(t, h.file(inboxPath), "poison@x.example", "after-poison@x.example")
	h.server.reject = rejectID("poison@x.example")
	sum, err := h.run()
	if err != nil {
		t.Fatal(err)
	}
	if sum.Rejected != 1 || sum.FoldersFailed != 0 || sum.Inserted != totalMails(t)+1 {
		t.Fatalf("summary %+v", sum)
	}
	if ids := sentIDs(h.server, inboxPath); !ids["after-poison@x.example"] || ids["poison@x.example"] {
		t.Errorf("sent %v", ids)
	}
	fi, _ := os.Stat(h.file(inboxPath))
	if h.st.Folders[inboxPath].Offset != fi.Size() {
		t.Errorf("offset %d, want %d", h.st.Folders[inboxPath].Offset, fi.Size())
	}
}

// Server từ chối mọi mail (vd. khác phiên bản): lỗi folder, không bỏ qua mail nào.
func TestServerRejectingEverythingIsFolderError(t *testing.T) {
	h := newHarness(t)
	h.cfg.Folders = []config.FolderChoice{{Path: inboxPath, Selected: true}}
	h.cfg.AutoIncludeNewFolders = false
	h.server.reject = func(upload.Batch) error { return &upload.HTTPError{Status: 422} }
	sum, err := h.run()
	if err != nil {
		t.Fatal(err)
	}
	if sum.FoldersFailed != 1 || sum.Rejected != 0 || h.st.Folders[inboxPath].Offset != 0 {
		t.Errorf("summary %+v, state %+v", sum, h.st.Folders[inboxPath])
	}
}

func TestParserPanicSkipsMail(t *testing.T) {
	h := newHarness(t)
	appendMails(t, h.file(inboxPath), "boom@x.example")
	h.options = func(o *Options) {
		o.Parse = func(raw []byte, truncated bool) parse.Result {
			if strings.Contains(string(raw), "boom@x.example") {
				panic("parser bug")
			}
			return parse.Parse(raw, truncated)
		}
	}
	sum, err := h.run()
	if err != nil || sum.Unreadable != 1 || sum.Inserted != totalMails(t) {
		t.Errorf("summary %+v err %v", sum, err)
	}
}

func TestCancelStopsInsideFolder(t *testing.T) {
	h := newHarness(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	opts := Options{
		ProfileDir: h.dir, Config: h.cfg, State: h.st, Client: h.server,
		Now:      func() time.Time { return h.now },
		Progress: func(Progress) { cancel() },
	}
	_, err := Run(ctx, opts)
	if !errors.Is(err, context.Canceled) || h.server.calls != 0 {
		t.Errorf("err %v, ingest calls %d", err, h.server.calls)
	}
}

func TestFormatSummary(t *testing.T) {
	s := Summary{FoldersScanned: 3, FoldersSkipped: 2, MailsRead: 12, Inserted: 10, Duplicates: 2, Duration: 75 * time.Second}
	got := FormatSummary(s)
	for _, want := range []string{"Đồng bộ hoàn thành", "10 mail mới", "2 mail đã có", "3 folder", "2 folder không đổi", "01:15"} {
		if !strings.Contains(got, want) {
			t.Errorf("%q missing %q", got, want)
		}
	}
	s.FoldersFailed = 1
	if !strings.Contains(FormatSummary(s), "1 folder lỗi") {
		t.Errorf("failed folders not reported: %q", FormatSummary(s))
	}
	s.Rejected, s.Unreadable, s.Degraded = 2, 1, 4
	got = FormatSummary(s)
	for _, want := range []string{"3 mail bị bỏ qua", "4 mail chỉ đọc được header"} {
		if !strings.Contains(got, want) {
			t.Errorf("%q missing %q", got, want)
		}
	}
}

func countMails(t *testing.T, path string) int {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	// Dòng phân cách: "From " ở đầu file hoặc ngay sau dòng trống (generator ghi "From MAILER-DAEMON",
	// xuống dòng CRLF khi chạy trên Windows).
	s := strings.ReplaceAll(string(b), "\r\n", "\n")
	n := strings.Count(s, "\n\nFrom ")
	if strings.HasPrefix(s, "From ") {
		n++
	}
	return n
}
