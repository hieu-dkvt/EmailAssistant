package upload

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"pmmail/collector/internal/parse"
)

func newClient(url string) (*Client, *[]time.Duration) {
	var slept []time.Duration
	c := New(url, "pmm_token")
	c.Sleep = func(_ context.Context, d time.Duration) error { slept = append(slept, d); return nil }
	return c, &slept
}

func sampleBatch() Batch {
	return Batch{
		SchemaVersion: 1,
		ParserVersion: parse.ParserVersion,
		Source:        Source{FolderPath: "ImapMail/h/INBOX", Account: "h", Kind: "imap_cache", FileSize: 10, LastOffset: 5},
		Messages:      []parse.Mail{{MessageID: "a@x", To: []parse.Address{}, Cc: []parse.Address{}, References: []string{}, Attachments: []parse.Attachment{}, MboxOffset: 0}},
	}
}

// Mã lỗi riêng của backend (vd. folder không có trên server) phân biệt với 404 của proxy.
func TestHTTPErrorCarriesBackendCode(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		if r.URL.Path == "/api/v1/sources/delete-mail" {
			_, _ = w.Write([]byte(`{"detail":"Không có folder này trên server","code":"source_not_found"}`))
		}
	}))
	defer srv.Close()
	c, _ := newClient(srv.URL)
	_, err := c.DeleteFolderMail(context.Background(), "ImapMail/h/Old")
	var he *HTTPError
	if !errors.As(err, &he) || he.Status != 404 || he.Code != CodeSourceNotFound {
		t.Errorf("err = %#v", err)
	}
	_, err = c.Me(context.Background())
	if !errors.As(err, &he) || he.Code != "" {
		t.Errorf("plain 404: %#v", err)
	}
}

// Redirect (vd. proxy cấu hình sai) không được làm token và nội dung mail đi tới nơi khác.
func TestRedirectNotFollowed(t *testing.T) {
	var hits atomic.Int32
	other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	defer other.Close()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, other.URL+r.URL.Path, http.StatusTemporaryRedirect)
	}))
	defer srv.Close()
	c, _ := newClient(srv.URL)
	_, err := c.Ingest(context.Background(), sampleBatch())
	var he *HTTPError
	if !errors.As(err, &he) || he.Status != http.StatusTemporaryRedirect {
		t.Errorf("err = %v", err)
	}
	if _, err := c.Me(context.Background()); err == nil {
		t.Error("Me followed redirect")
	}
	if hits.Load() != 0 {
		t.Errorf("redirect target received %d requests", hits.Load())
	}
}

func TestIngestSendsGzipJSONWithToken(t *testing.T) {
	var got map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/ingest" || r.Method != http.MethodPost {
			t.Errorf("request %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer pmm_token" || r.Header.Get("Content-Encoding") != "gzip" ||
			r.Header.Get("Content-Type") != "application/json" {
			t.Errorf("headers = %v", r.Header)
		}
		zr, err := gzip.NewReader(r.Body)
		if err != nil {
			t.Fatal(err)
		}
		if err := json.NewDecoder(zr).Decode(&got); err != nil {
			t.Fatal(err)
		}
		_, _ = io.WriteString(w, `{"source_id":7,"received":1,"inserted":1,"duplicates":0,"mismatched":0}`)
	}))
	defer srv.Close()

	c, _ := newClient(srv.URL + "/")
	res, err := c.Ingest(context.Background(), sampleBatch())
	if err != nil {
		t.Fatalf("Ingest: %v", err)
	}
	if res != (IngestResult{SourceID: 7, Received: 1, Inserted: 1}) {
		t.Errorf("result = %+v", res)
	}
	src := got["source"].(map[string]any)
	if got["schema_version"] != float64(1) || src["folder_path"] != "ImapMail/h/INBOX" || src["kind"] != "imap_cache" {
		t.Errorf("payload = %v", got)
	}
	msgs := got["messages"].([]any)
	if len(msgs) != 1 || msgs[0].(map[string]any)["message_id"] != "a@x" {
		t.Errorf("messages = %v", msgs)
	}
}

func TestIngestRetriesThenSucceeds(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) <= 2 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		_, _ = io.WriteString(w, `{"source_id":1,"received":1,"inserted":0,"duplicates":1,"mismatched":0}`)
	}))
	defer srv.Close()
	c, slept := newClient(srv.URL)
	res, err := c.Ingest(context.Background(), sampleBatch())
	if err != nil || res.Duplicates != 1 || calls.Load() != 3 {
		t.Fatalf("res=%+v err=%v calls=%d", res, err, calls.Load())
	}
	if len(*slept) != 2 || (*slept)[0] != time.Second || (*slept)[1] != 4*time.Second {
		t.Errorf("backoff = %v", *slept)
	}
}

func TestIngestGivesUpAfterRetries(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusBadGateway)
	}))
	defer srv.Close()
	c, _ := newClient(srv.URL)
	_, err := c.Ingest(context.Background(), sampleBatch())
	var he *HTTPError
	if !errors.As(err, &he) || he.Status != http.StatusBadGateway || calls.Load() != 4 {
		t.Errorf("err=%v calls=%d", err, calls.Load())
	}
}

func TestUnauthorizedNotRetried(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer srv.Close()
	c, _ := newClient(srv.URL)
	if _, err := c.Ingest(context.Background(), sampleBatch()); !errors.Is(err, ErrUnauthorized) || calls.Load() != 1 {
		t.Errorf("err=%v calls=%d", err, calls.Load())
	}
}

func TestValidationErrorNotRetried(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusUnprocessableEntity)
		_, _ = io.WriteString(w, `{"detail":[{"loc":["body","messages",0],"msg":"bad","type":"x"}]}`)
	}))
	defer srv.Close()
	c, _ := newClient(srv.URL)
	_, err := c.Ingest(context.Background(), sampleBatch())
	var he *HTTPError
	if !errors.As(err, &he) || he.Status != 422 || calls.Load() != 1 {
		t.Errorf("err=%v calls=%d", err, calls.Load())
	}
}

func TestNetworkErrorRetried(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	url := srv.URL
	srv.Close() // không còn ai nghe
	c, slept := newClient(url)
	if _, err := c.Ingest(context.Background(), sampleBatch()); err == nil || len(*slept) != 3 {
		t.Errorf("err=%v slept=%v", err, *slept)
	}
}

func TestMeSourcesDelete(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method + " " + r.URL.Path {
		case "GET /api/v1/me":
			_, _ = io.WriteString(w, `{"id":3,"email":"pm@example.com","display_name":"PM"}`)
		case "GET /api/v1/sources":
			_, _ = io.WriteString(w, `[{"id":1,"folder_path":"ImapMail/h/INBOX","account":"h","kind":"imap_cache","message_count":5,"last_scan_at":null}]`)
		case "POST /api/v1/sources/delete-mail":
			var body map[string]string
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body["folder_path"] != "ImapMail/h/INBOX" {
				w.WriteHeader(http.StatusNotFound)
				return
			}
			_, _ = io.WriteString(w, `{"deleted_messages":4,"kept_messages":1}`)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()
	c, _ := newClient(srv.URL)
	ctx := context.Background()
	me, err := c.Me(ctx)
	if err != nil || me.Email != "pm@example.com" {
		t.Errorf("me = %+v %v", me, err)
	}
	src, err := c.Sources(ctx)
	if err != nil || len(src) != 1 || src[0].MessageCount != 5 {
		t.Errorf("sources = %+v %v", src, err)
	}
	del, err := c.DeleteFolderMail(ctx, "ImapMail/h/INBOX")
	if err != nil || del.DeletedMessages != 4 || del.KeptMessages != 1 {
		t.Errorf("delete = %+v %v", del, err)
	}
	_, err = c.DeleteFolderMail(ctx, "Nope")
	var he *HTTPError
	if !errors.As(err, &he) || he.Status != 404 {
		t.Errorf("delete missing err = %v", err)
	}
}
