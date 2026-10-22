// Package upload gọi API của backend: gửi lô mail (gzip, retry), kiểm tra kết nối, danh sách
// folder trên server, xóa mail của folder.
//
// Spec: docs/superpowers/specs/2026-10-17-incremental-sync-design.md mục 5. Không log nội dung
// mail hay token.
package upload

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"pmmail/collector/internal/parse"
)

// ErrUnauthorized: token sai hoặc đã bị thu hồi (401/403). Không thử lại.
var ErrUnauthorized = errors.New("token không hợp lệ hoặc đã bị thu hồi")

// HTTPError: server trả mã lỗi khác 401/403.
// CodeSourceNotFound: backend không có folder này (delete-mail). Phân biệt với 404 của proxy
// hay sai đường dẫn API, vốn không có nghĩa là dữ liệu đã bị xóa.
const CodeSourceNotFound = "source_not_found"

// HTTPError: server trả mã ngoài 2xx. Code: trường "code" trong body JSON của backend (nếu có);
// không giữ phần nào khác của body.
type HTTPError struct {
	Status int
	Code   string
}

func (e *HTTPError) Error() string { return fmt.Sprintf("server trả lỗi HTTP %d", e.Status) }

type networkError struct{ err error }

func (e *networkError) Error() string { return "không kết nối được server: " + e.err.Error() }
func (e *networkError) Unwrap() error { return e.err }

type Source struct {
	FolderPath string     `json:"folder_path"`
	Account    string     `json:"account"`
	Kind       string     `json:"kind"`
	FileSize   int64      `json:"file_size"`
	LastOffset int64      `json:"last_offset"`
	LastMtime  *time.Time `json:"last_mtime,omitempty"`
}

type Batch struct {
	SchemaVersion int          `json:"schema_version"`
	ParserVersion string       `json:"parser_version"`
	Source        Source       `json:"source"`
	Messages      []parse.Mail `json:"messages"`
}

type IngestResult struct {
	SourceID   int64 `json:"source_id"`
	Received   int   `json:"received"`
	Inserted   int   `json:"inserted"`
	Duplicates int   `json:"duplicates"`
	Mismatched int   `json:"mismatched"`
}

type Me struct {
	ID          int64  `json:"id"`
	Email       string `json:"email"`
	DisplayName string `json:"display_name"`
}

type SourceInfo struct {
	ID           int64      `json:"id"`
	FolderPath   string     `json:"folder_path"`
	Account      string     `json:"account"`
	Kind         string     `json:"kind"`
	MessageCount int        `json:"message_count"`
	LastScanAt   *time.Time `json:"last_scan_at"`
}

type DeleteResult struct {
	DeletedMessages int `json:"deleted_messages"`
	KeptMessages    int `json:"kept_messages"`
}

type Client struct {
	BaseURL string
	Token   string
	HTTP    *http.Client
	Backoff []time.Duration                                  // chờ trước mỗi lần thử lại
	Sleep   func(ctx context.Context, d time.Duration) error // thay được trong test
}

func New(baseURL, token string) *Client {
	return &Client{
		BaseURL: strings.TrimRight(baseURL, "/"),
		Token:   token,
		HTTP: &http.Client{
			Timeout: 2 * time.Minute,
			// Không theo redirect: Go giữ header Authorization khi chuyển sang cổng khác cùng host
			// và 307/308 gửi lại cả body (nội dung mail). 3xx trả về như lỗi HTTP.
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
		Backoff: []time.Duration{time.Second, 4 * time.Second, 10 * time.Second},
		Sleep:   sleep,
	}
}

func sleep(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

// Ingest gửi một lô (gzip). Lỗi mạng, 5xx, 429: thử lại theo Backoff.
func (c *Client) Ingest(ctx context.Context, b Batch) (IngestResult, error) {
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	if err := json.NewEncoder(zw).Encode(b); err != nil {
		return IngestResult{}, err
	}
	if err := zw.Close(); err != nil {
		return IngestResult{}, err
	}
	var res IngestResult
	err := c.do(ctx, http.MethodPost, "/api/v1/ingest", buf.Bytes(), true, true, &res)
	return res, err
}

func (c *Client) Me(ctx context.Context) (Me, error) {
	var me Me
	err := c.do(ctx, http.MethodGet, "/api/v1/me", nil, false, false, &me)
	return me, err
}

func (c *Client) Sources(ctx context.Context) ([]SourceInfo, error) {
	var out []SourceInfo
	err := c.do(ctx, http.MethodGet, "/api/v1/sources", nil, false, false, &out)
	return out, err
}

func (c *Client) DeleteFolderMail(ctx context.Context, folderPath string) (DeleteResult, error) {
	body, err := json.Marshal(map[string]string{"folder_path": folderPath})
	if err != nil {
		return DeleteResult{}, err
	}
	var out DeleteResult
	err = c.do(ctx, http.MethodPost, "/api/v1/sources/delete-mail", body, false, false, &out)
	return out, err
}

func (c *Client) do(ctx context.Context, method, path string, body []byte, gz, retry bool, out any) error {
	attempts := 1
	if retry {
		attempts += len(c.Backoff)
	}
	for i := 0; ; i++ {
		err := c.once(ctx, method, path, body, gz, out)
		if err == nil || !retryable(err) || i == attempts-1 {
			return err
		}
		if serr := c.Sleep(ctx, c.Backoff[i]); serr != nil {
			return serr
		}
	}
}

func (c *Client) once(ctx context.Context, method, path string, body []byte, gz bool, out any) error {
	var rd io.Reader
	if body != nil {
		rd = bytes.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.BaseURL+path, rd)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+c.Token)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if gz {
		req.Header.Set("Content-Encoding", "gzip")
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return &networkError{err}
	}
	defer resp.Body.Close()
	switch {
	case resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden:
		return ErrUnauthorized
	case resp.StatusCode < 200 || resp.StatusCode > 299:
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
		var e struct {
			Code string `json:"code"`
		}
		_ = json.Unmarshal(body, &e)
		return &HTTPError{Status: resp.StatusCode, Code: e.Code}
	}
	if out == nil {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

func retryable(err error) bool {
	var ne *networkError
	if errors.As(err, &ne) {
		return true
	}
	var he *HTTPError
	return errors.As(err, &he) && (he.Status >= 500 || he.Status == http.StatusTooManyRequests)
}
