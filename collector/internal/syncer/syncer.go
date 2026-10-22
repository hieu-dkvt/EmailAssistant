// Package syncer đồng bộ tăng dần các folder đã chọn lên Ingest API.
//
// Spec: docs/superpowers/specs/2026-10-17-incremental-sync-design.md. Chỉ đọc phần mbox mới
// kể từ lần trước, gửi theo lô, lưu state sau mỗi lô thành công (dừng giữa chừng thì lần sau
// tiếp tục). Log chỉ có đường dẫn folder và số lượng.
package syncer

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"time"

	"pmmail/collector/internal/config"
	"pmmail/collector/internal/mbox"
	"pmmail/collector/internal/parse"
	"pmmail/collector/internal/profile"
	"pmmail/collector/internal/state"
	"pmmail/collector/internal/upload"
)

const (
	DefaultQuietPeriod      = 30 * time.Second // mail cuối file chỉ đọc khi file yên lặng chừng này
	DefaultMaxBatchMessages = 200
	DefaultMaxBatchBytes    = 8 << 20
)

// Ingester là phần của upload.Client mà syncer dùng (thay được trong test).
type Ingester interface {
	Ingest(ctx context.Context, b upload.Batch) (upload.IngestResult, error)
}

type Options struct {
	ProfileDir       string
	Config           *config.Config
	State            *state.State
	SaveState        func(*state.State) error
	Client           Ingester
	Now              func() time.Time
	QuietPeriod      time.Duration
	MaxBatchMessages int
	MaxBatchBytes    int
	Progress         func(Progress)
	Parse            func(raw []byte, truncated bool) parse.Result // mặc định parse.Parse
}

type Progress struct {
	FolderIndex int    `json:"folder_index"` // bắt đầu từ 1
	FolderCount int    `json:"folder_count"`
	FolderPath  string `json:"folder_path"`
	BytesDone   int64  `json:"bytes_done"`
	BytesTotal  int64  `json:"bytes_total"`
	Sent        int    `json:"sent"`
	Inserted    int    `json:"inserted"`
	Duplicates  int    `json:"duplicates"`
}

type FolderError struct {
	Path    string `json:"path"`
	Message string `json:"message"`
}

type Summary struct {
	FoldersScanned int           `json:"folders_scanned"`
	FoldersSkipped int           `json:"folders_skipped"` // không đổi kể từ lần trước
	FoldersFailed  int           `json:"folders_failed"`
	MailsRead      int           `json:"mails_read"`
	Inserted       int           `json:"inserted"`   // mail mới trên server
	Duplicates     int           `json:"duplicates"` // đã có trên server
	Expunged       int           `json:"expunged"`   // đã xóa trong Thunderbird, bỏ qua
	Degraded       int           `json:"degraded"`   // chỉ đọc được header
	Rejected       int           `json:"rejected"`   // server từ chối riêng mail này, bỏ qua
	Unreadable     int           `json:"unreadable"` // collector không đọc được, bỏ qua
	Duration       time.Duration `json:"duration_ns"`
	FolderErrors   []FolderError `json:"folder_errors"`
}

// FormatSummary là nội dung thông báo "Đồng bộ hoàn thành" (CLI và trang setting).
func FormatSummary(s Summary) string {
	d := s.Duration.Round(time.Second)
	msg := fmt.Sprintf(
		"Đồng bộ hoàn thành: %d mail mới, %d mail đã có trên server; đã đọc %d folder, "+
			"%d folder không đổi; thời gian %02d:%02d.",
		s.Inserted, s.Duplicates, s.FoldersScanned, s.FoldersSkipped,
		int(d.Minutes()), int(d.Seconds())%60)
	if s.FoldersFailed > 0 {
		msg += fmt.Sprintf(" Có %d folder lỗi.", s.FoldersFailed)
	}
	if skipped := s.Rejected + s.Unreadable; skipped > 0 {
		msg += fmt.Sprintf(" Có %d mail bị bỏ qua (server từ chối hoặc không đọc được).", skipped)
	}
	if s.Degraded > 0 {
		msg += fmt.Sprintf(" Có %d mail chỉ đọc được header.", s.Degraded)
	}
	return msg
}

type target struct {
	path    string // tương đối so với profile
	account profile.Account
	size    int64
	mtime   time.Time
	start   int64
	tail    string // state.Folder.Tail của vị trí start
}

// Run đồng bộ một lượt. Lỗi trả về (token sai, mất kết nối server, bị hủy) làm dừng cả lượt;
// lỗi riêng của một folder (dữ liệu bị từ chối, không đọc được file) chỉ ghi vào Summary.
func Run(ctx context.Context, o Options) (Summary, error) {
	o.defaults()
	started := time.Now()
	accounts, err := profile.ScanAccounts(o.ProfileDir)
	if err != nil {
		return Summary{}, err
	}
	targets, skipped := o.plan(accounts)
	r := &run{o: o, sum: Summary{FoldersSkipped: skipped, FolderErrors: []FolderError{}}}
	for _, t := range targets {
		r.total += t.size - t.start
	}
	for i, t := range targets {
		if err := ctx.Err(); err != nil {
			r.sum.Duration = time.Since(started)
			return r.sum, err
		}
		err := r.folder(ctx, i+1, len(targets), t)
		switch {
		case err == nil:
			r.sum.FoldersScanned++
		case fatal(err):
			r.sum.Duration = time.Since(started)
			return r.sum, err
		default:
			slog.Warn("sync folder failed", "folder", t.path, "err", err)
			r.sum.FoldersFailed++
			r.sum.FolderErrors = append(r.sum.FolderErrors, FolderError{Path: t.path, Message: err.Error()})
		}
	}
	r.sum.Duration = time.Since(started)
	return r.sum, nil
}

func (o *Options) defaults() {
	if o.Now == nil {
		o.Now = time.Now
	}
	if o.QuietPeriod == 0 {
		o.QuietPeriod = DefaultQuietPeriod
	}
	if o.MaxBatchMessages == 0 {
		o.MaxBatchMessages = DefaultMaxBatchMessages
	}
	if o.MaxBatchBytes == 0 {
		o.MaxBatchBytes = DefaultMaxBatchBytes
	}
	if o.SaveState == nil {
		o.SaveState = func(*state.State) error { return nil }
	}
	if o.Progress == nil {
		o.Progress = func(Progress) {}
	}
	if o.Parse == nil {
		o.Parse = parse.Parse
	}
}

// safeParse: lỗi lập trình trong parser (panic) chỉ làm hỏng một mail, không dừng cả lượt.
// Thử lại chỉ với header trước khi bỏ qua. Không log giá trị panic (có thể chứa nội dung mail).
func (o *Options) safeParse(raw []byte, truncated bool) (parse.Result, bool) {
	try := func(headersOnly bool) (res parse.Result, ok bool) {
		defer func() {
			if recover() != nil {
				ok = false
			}
		}()
		return o.Parse(raw, headersOnly), true
	}
	if res, ok := try(truncated); ok {
		return res, true
	}
	if truncated {
		return parse.Result{}, false
	}
	res, ok := try(true)
	res.Degraded = true
	return res, ok
}

// plan chọn folder cần đọc và vị trí bắt đầu (spec mục 4).
func (o *Options) plan(accounts []profile.Account) (targets []target, skipped int) {
	var walk func(a profile.Account, fs []*profile.Folder)
	walk = func(a profile.Account, fs []*profile.Folder) {
		for _, f := range fs {
			if f.HasMbox && o.Config.ShouldScan(f.Path) {
				fi, err := os.Stat(filepath.Join(o.ProfileDir, filepath.FromSlash(f.Path)))
				if err == nil {
					t := target{path: f.Path, account: a, size: fi.Size(), mtime: fi.ModTime()}
					st, known := o.State.Folders[f.Path]
					switch {
					case known && st.Size == t.size && st.MTime.Equal(t.mtime):
						skipped++
						walk(a, f.Children)
						continue
					case known && t.size >= st.Offset:
						t.start, t.tail = st.Offset, st.Tail // kiểm tra lại khi mở file
					}
					targets = append(targets, t) // chưa có state hoặc file nhỏ đi (compact): đọc từ 0
				}
			}
			walk(a, f.Children)
		}
	}
	for _, a := range accounts {
		walk(a, a.Folders)
	}
	return targets, skipped
}

type run struct {
	o     Options
	sum   Summary
	total int64
	done  int64
}

func (r *run) folder(ctx context.Context, index, count int, t target) error {
	o := r.o
	started := time.Now()
	f, err := mbox.Open(filepath.Join(o.ProfileDir, filepath.FromSlash(t.path)))
	if err != nil {
		return err
	}
	defer f.Close()
	// Kích thước lấy từ chính handle đang mở và chỉ đọc tới đó: phần Thunderbird ghi thêm trong
	// lúc đọc để lần sau (spec mục 4).
	fi, err := f.Stat()
	if err != nil {
		return err
	}
	planned := t.size - t.start
	t.size, t.mtime = fi.Size(), fi.ModTime()
	if t.start > 0 && !resumable(f, t.start, t.size, t.tail) {
		slog.Info("sync folder changed before saved offset, reading from start",
			"folder", t.path, "offset", t.start)
		t.start = 0
	}
	r.total += (t.size - t.start) - planned
	if _, err := f.Seek(t.start, io.SeekStart); err != nil {
		return err
	}
	rd := mbox.NewReader(io.LimitReader(f, t.size-t.start), t.start)
	rd.AllowLastAtEOF = o.Now().Sub(t.mtime) >= o.QuietPeriod

	progress := Progress{FolderIndex: index, FolderCount: count, FolderPath: t.path, BytesTotal: r.total}
	report := func() {
		progress.BytesDone = r.done
		progress.Sent, progress.Inserted, progress.Duplicates = r.sum.MailsRead, r.sum.Inserted, r.sum.Duplicates
		o.Progress(progress)
	}
	report()

	var batch []parse.Mail
	batchBytes := 0
	processed := t.start // mọi mail trước vị trí này đã gửi hoặc bỏ qua (expunged)
	save := func(complete bool) error {
		tail, err := tailHash(f, processed)
		if err != nil {
			return err
		}
		fs := state.Folder{Offset: processed, Tail: tail, LastSyncAt: o.Now()}
		if complete && processed == t.size { // đọc hết file: lần sau bỏ qua nếu file không đổi
			fs.Size, fs.MTime = t.size, t.mtime
		}
		o.State.Folders[t.path] = fs
		return o.SaveState(o.State)
	}
	send := func(end int64) error {
		src := upload.Source{
			FolderPath: t.path,
			Account:    t.account.Name,
			Kind:       string(t.account.Kind),
			FileSize:   t.size,
			LastOffset: end,
			LastMtime:  &t.mtime,
		}
		var res upload.IngestResult
		rejected, lastReject, err := r.post(ctx, src, batch, &res)
		if err == nil && rejected == len(batch) {
			err = lastReject // server từ chối mọi mail của lô: lỗi folder, không bỏ qua gì
		}
		if err != nil {
			return err
		}
		r.sum.Inserted += res.Inserted
		r.sum.Duplicates += res.Duplicates
		r.sum.Rejected += rejected
		processed = end
		batch, batchBytes = nil, 0
		if err := save(false); err != nil {
			return err
		}
		report()
		return nil
	}

	end := t.start
	skip := func() { // mail không gửi: nếu lô đang trống thì vị trí đã xử lý tiến qua nó luôn
		if len(batch) == 0 {
			processed = end
		}
	}
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		msg, err := rd.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return err
		}
		r.done += msg.End - end
		end = msg.End
		res, ok := o.safeParse(msg.Raw, msg.Truncated)
		if ok && res.Expunged {
			r.sum.Expunged++
			skip()
			continue
		}
		m := res.Mail
		m.MboxOffset = msg.Offset
		size, err := jsonSize(m)
		if !ok || err != nil {
			r.sum.Unreadable++
			slog.Warn("mail unreadable, skipped", "folder", t.path, "offset", msg.Offset)
			skip()
			continue
		}
		if res.Degraded {
			r.sum.Degraded++
		}
		r.sum.MailsRead++
		batch = append(batch, m)
		batchBytes += size
		if len(batch) >= o.MaxBatchMessages || batchBytes >= o.MaxBatchBytes {
			if err := send(end); err != nil {
				return err
			}
		}
	}
	if len(batch) > 0 {
		if err := send(end); err != nil {
			return err
		}
	}
	processed = end
	if err := save(true); err != nil {
		return err
	}
	report()
	slog.Info("sync folder done", "folder", t.path, "from", t.start, "to", processed,
		"ms", time.Since(started).Milliseconds())
	return nil
}

// jsonSize: kích thước mail khi gửi. Mail không mã hóa JSON được (vd. ngày ngoài năm 0–9999)
// thì làm hỏng cả lô, nên bị loại riêng ở đây.
func jsonSize(m parse.Mail) (int, error) {
	b, err := json.Marshal(m)
	return len(b), err
}

// post gửi lô. Server từ chối dữ liệu (400/413/422) thì chia đôi để tìm mail lỗi; mail bị từ
// chối khi gửi riêng thì bỏ qua (trả về số lượng và lỗi từ chối cuối cùng). Lỗi khác trả ngay.
func (r *run) post(ctx context.Context, src upload.Source, msgs []parse.Mail, total *upload.IngestResult) (int, error, error) {
	res, err := r.o.Client.Ingest(ctx, upload.Batch{
		SchemaVersion: 1,
		ParserVersion: parse.ParserVersion,
		Source:        src,
		Messages:      msgs,
	})
	switch {
	case err == nil:
		total.Inserted += res.Inserted
		total.Duplicates += res.Duplicates
		return 0, nil, nil
	case !rejectedByServer(err):
		return 0, nil, err
	case len(msgs) == 1:
		slog.Warn("mail rejected by server, skipped", "folder", src.FolderPath,
			"offset", msgs[0].MboxOffset, "err", err)
		return 1, err, nil
	}
	mid := len(msgs) / 2
	a, lastA, err := r.post(ctx, src, msgs[:mid], total)
	if err != nil {
		return a, lastA, err
	}
	b, lastB, err := r.post(ctx, src, msgs[mid:], total)
	if lastB == nil {
		lastB = lastA
	}
	return a + b, lastB, err
}

func rejectedByServer(err error) bool {
	var he *upload.HTTPError
	return errors.As(err, &he) && (he.Status == 400 || he.Status == 413 || he.Status == 422)
}

const tailLen = 4096

// tailHash: SHA-256 của tối đa tailLen byte ngay trước offset.
func tailHash(f io.ReaderAt, offset int64) (string, error) {
	buf := make([]byte, min(offset, tailLen))
	if n, err := f.ReadAt(buf, offset-int64(len(buf))); err != nil && !(errors.Is(err, io.EOF) && n == len(buf)) {
		return "", err
	}
	sum := sha256.Sum256(buf)
	return hex.EncodeToString(sum[:]), nil
}

// resumable: vị trí đã lưu còn dùng được khi đoạn ngay trước nó không đổi và tại đó là đầu một
// mail (dòng "From ") hoặc cuối file. Compact làm dịch nội dung nên đoạn này khác đi.
func resumable(f io.ReaderAt, offset, size int64, want string) bool {
	if want == "" || offset > size {
		return false
	}
	if got, err := tailHash(f, offset); err != nil || got != want {
		return false
	}
	if offset == size {
		return true
	}
	head := make([]byte, 16)
	n, _ := f.ReadAt(head, offset)
	// Mail mới nối vào cuối file có thể bắt đầu bằng dòng trống trước dòng "From ".
	return strings.HasPrefix(strings.TrimLeft(string(head[:n]), "\r\n"), "From ")
}

// fatal: lỗi làm dừng cả lượt (không chỉ một folder).
func fatal(err error) bool {
	if errors.Is(err, upload.ErrUnauthorized) || errors.Is(err, context.Canceled) ||
		errors.Is(err, context.DeadlineExceeded) {
		return true
	}
	var he *upload.HTTPError
	if errors.As(err, &he) {
		return he.Status >= 500 || he.Status == 429 // đã thử lại mà server vẫn lỗi
	}
	var pe *os.PathError
	return !errors.As(err, &pe) // lỗi mạng sau khi thử lại; lỗi đọc file chỉ hỏng folder đó
}
