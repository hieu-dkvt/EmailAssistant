package settingsui

import (
	"context"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const (
	testAddr   = "127.0.0.1:5555"
	testOrigin = "http://127.0.0.1:5555"
)

func newTestServerWith(t *testing.T, opts Options) *Server {
	t.Helper()
	dir := t.TempDir()
	if opts.Addr == "" {
		opts.Addr = testAddr
	}
	if opts.ConfigPath == "" {
		opts.ConfigPath = filepath.Join(dir, "config.json")
	}
	if opts.AppData == "" {
		opts.AppData = dir
	}
	if opts.IdleTimeout == 0 {
		opts.IdleTimeout = time.Minute
	}
	s, err := New(opts)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return s
}

func newTestServer(t *testing.T) *Server { return newTestServerWith(t, Options{}) }

// rawReq gọi handler với Host, cookie, Origin tùy chọn (chuỗi rỗng = không gửi).
func rawReq(s *Server, method, target, host, cookie, origin string, body io.Reader) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, target, body)
	r.Host = host
	if cookie != "" {
		r.AddCookie(&http.Cookie{Name: cookieName, Value: cookie})
	}
	if origin != "" {
		r.Header.Set("Origin", origin)
	}
	w := httptest.NewRecorder()
	s.Handler().ServeHTTP(w, r)
	return w
}

func TestRootWithKeySetsCookie(t *testing.T) {
	s := newTestServer(t)
	w := rawReq(s, "GET", "/?key="+s.Token(), testAddr, "", "", nil)
	if w.Code != 200 {
		t.Fatalf("status = %d, want 200", w.Code)
	}
	if !strings.Contains(w.Body.String(), "Cài đặt collector") {
		t.Errorf("body does not contain page title")
	}
	var c *http.Cookie
	for _, ck := range w.Result().Cookies() {
		if ck.Name == cookieName {
			c = ck
		}
	}
	if c == nil || c.Value != s.Token() || !c.HttpOnly || c.SameSite != http.SameSiteStrictMode {
		t.Errorf("cookie = %+v", c)
	}
}

func TestStaticAssets(t *testing.T) {
	s := newTestServer(t)
	w := rawReq(s, "GET", "/?key="+s.Token(), testAddr, "", "", nil)
	for _, want := range []string{"<title>Cài đặt collector</title>", `src="app.js"`, `href="style.css"`} {
		if !strings.Contains(w.Body.String(), want) {
			t.Errorf("index.html missing %q", want)
		}
	}
	if csp := w.Header().Get("Content-Security-Policy"); !strings.Contains(csp, "default-src 'self'") || !strings.Contains(csp, "frame-ancestors 'none'") {
		t.Errorf("Content-Security-Policy = %q", csp)
	}
	for path, ctype := range map[string]string{"/app.js": "javascript", "/style.css": "text/css"} {
		w := rawReq(s, "GET", path, testAddr, s.Token(), "", nil)
		if w.Code != 200 || !strings.Contains(w.Header().Get("Content-Type"), ctype) {
			t.Errorf("%s: status %d, Content-Type %q", path, w.Code, w.Header().Get("Content-Type"))
		}
	}
}

func TestRootWithoutKey(t *testing.T) {
	s := newTestServer(t)
	for _, target := range []string{"/", "/?key=wrong"} {
		w := rawReq(s, "GET", target, testAddr, "", "", nil)
		if w.Code != 403 {
			t.Errorf("%s: status = %d, want 403", target, w.Code)
		}
		if !strings.Contains(w.Body.String(), "Không có quyền truy cập") {
			t.Errorf("%s: body = %s", target, w.Body.String())
		}
	}
}

func TestAPIWithoutCookie(t *testing.T) {
	s := newTestServer(t)
	if w := rawReq(s, "POST", "/api/shutdown", testAddr, "", testOrigin, nil); w.Code != 403 {
		t.Errorf("status = %d, want 403", w.Code)
	}
	if w := rawReq(s, "POST", "/api/shutdown?key="+s.Token(), testAddr, "", testOrigin, nil); w.Code != 403 {
		t.Errorf("key in query on API: status = %d, want 403", w.Code)
	}
}

func TestWrongHost(t *testing.T) {
	s := newTestServer(t)
	if w := rawReq(s, "GET", "/", "evil.example:5555", s.Token(), "", nil); w.Code != 403 {
		t.Errorf("evil host: status = %d, want 403", w.Code)
	}
	if w := rawReq(s, "GET", "/?key="+s.Token(), "localhost:5555", "", "", nil); w.Code != 200 {
		t.Errorf("localhost: status = %d, want 200", w.Code)
	}
	if w := rawReq(s, "GET", "/?key="+s.Token(), "localhost:6666", "", "", nil); w.Code != 403 {
		t.Errorf("localhost other port: status = %d, want 403", w.Code)
	}
}

func TestPostOrigin(t *testing.T) {
	s := newTestServer(t)
	if w := rawReq(s, "POST", "/api/shutdown", testAddr, s.Token(), "http://evil.example", nil); w.Code != 403 {
		t.Errorf("evil origin: status = %d, want 403", w.Code)
	}
	if w := rawReq(s, "POST", "/api/shutdown", testAddr, s.Token(), "", nil); w.Code != 403 {
		t.Errorf("no origin: status = %d, want 403", w.Code)
	}
	w := rawReq(s, "POST", "/api/shutdown", testAddr, s.Token(), testOrigin, nil)
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"ok":true`) {
		t.Errorf("good origin: status = %d body = %s", w.Code, w.Body.String())
	}
}

// startRun chạy Run trên một listener thật và trả kênh nhận kết quả.
func startRun(t *testing.T, ctx context.Context, idle time.Duration) (*Server, string, <-chan error) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	s := newTestServerWith(t, Options{Addr: ln.Addr().String(), IdleTimeout: idle})
	done := make(chan error, 1)
	go func() { done <- s.Run(ctx, ln) }()
	return s, ln.Addr().String(), done
}

func waitDone(t *testing.T, done <-chan error) {
	t.Helper()
	select {
	case err := <-done:
		if err != nil {
			t.Errorf("Run returned %v, want nil", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Run did not stop within 2s")
	}
}

func TestRunStopsOnShutdown(t *testing.T) {
	s, addr, done := startRun(t, context.Background(), time.Minute)
	req, _ := http.NewRequest("POST", "http://"+addr+"/api/shutdown", nil)
	req.AddCookie(&http.Cookie{Name: cookieName, Value: s.Token()})
	req.Header.Set("Origin", "http://"+addr)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("shutdown request: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	waitDone(t, done)
}

func TestRunStopsWhenIdle(t *testing.T) {
	_, _, done := startRun(t, context.Background(), 200*time.Millisecond)
	waitDone(t, done)
}

func TestRunStopsOnContextCancel(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	_, _, done := startRun(t, ctx, time.Minute)
	cancel()
	waitDone(t, done)
}
