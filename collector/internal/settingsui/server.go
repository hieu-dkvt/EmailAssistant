// Package settingsui phục vụ trang setting cục bộ của collector: chọn profile
// Thunderbird và các folder đồng bộ. Server chỉ nghe trên 127.0.0.1 và chỉ
// chấp nhận request mang token của phiên (qua ?key= lần đầu, sau đó qua cookie).
package settingsui

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"net"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"pmmail/collector/internal/service"
)

//go:embed web
var webFS embed.FS

const cookieName = "pmmail_key"

type Options struct {
	Addr        string // "127.0.0.1:<port>" của listener
	ConfigPath  string
	AppData     string // thư mục chứa Thunderbird\profiles.ini
	IdleTimeout time.Duration
	Service     *service.Service // kết nối server, đồng bộ, xóa mail khỏi server
}

type Server struct {
	opts           Options
	token          string
	allowedHosts   map[string]bool
	allowedOrigins map[string]bool
	handler        http.Handler
	lastActive     atomic.Int64
	stop           chan struct{}
	stopOnce       sync.Once
	sync           *syncManager
	baseCtx        context.Context // hủy khi server dừng: đồng bộ nền dừng theo
	baseCancel     context.CancelFunc
}

func New(opts Options) (*Server, error) {
	_, port, err := net.SplitHostPort(opts.Addr)
	if err != nil {
		return nil, fmt.Errorf("invalid addr %q: %w", opts.Addr, err)
	}
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return nil, err
	}
	s := &Server{
		opts:           opts,
		token:          hex.EncodeToString(b),
		allowedHosts:   map[string]bool{opts.Addr: true, "localhost:" + port: true},
		allowedOrigins: map[string]bool{"http://" + opts.Addr: true, "http://localhost:" + port: true},
		stop:           make(chan struct{}),
		sync:           newSyncManager(),
	}
	s.baseCtx, s.baseCancel = context.WithCancel(context.Background())
	s.handler = s.buildHandler()
	s.touch()
	return s, nil
}

func (s *Server) Token() string { return s.token }

func (s *Server) Handler() http.Handler { return s.handler }

func (s *Server) buildHandler() http.Handler {
	mux := http.NewServeMux()
	static, err := fs.Sub(webFS, "web")
	if err != nil {
		panic(err) // web/ được nhúng lúc build, không thể thiếu
	}
	mux.Handle("GET /", http.FileServerFS(static))
	mux.HandleFunc("POST /api/shutdown", s.handleShutdown)
	s.apiRoutes(mux)
	s.syncRoutes(mux)
	return s.guard(mux)
}

// guard chặn mọi request không đến từ chính người dùng trên máy này:
// Host lạ (DNS rebinding), thiếu token, hoặc POST từ origin khác.
func (s *Server) guard(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Trang chỉ dùng file của chính nó; tên folder có ký tự HTML cũng không chạy được script.
		w.Header().Set("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if !s.allowedHosts[r.Host] {
			forbid(w)
			return
		}
		authed := false
		if c, err := r.Cookie(cookieName); err == nil && s.validToken(c.Value) {
			authed = true
		} else if r.Method == http.MethodGet && r.URL.Path == "/" && s.validToken(r.URL.Query().Get("key")) {
			http.SetCookie(w, &http.Cookie{
				Name: cookieName, Value: s.token, Path: "/",
				HttpOnly: true, SameSite: http.SameSiteStrictMode,
			})
			authed = true
		}
		if !authed {
			forbid(w)
			return
		}
		if r.Method == http.MethodPost && !s.allowedOrigins[r.Header.Get("Origin")] {
			forbid(w)
			return
		}
		s.touch()
		next.ServeHTTP(w, r)
	})
}

func (s *Server) validToken(v string) bool {
	return subtle.ConstantTimeCompare([]byte(v), []byte(s.token)) == 1
}

func (s *Server) touch() { s.lastActive.Store(time.Now().UnixNano()) }

func (s *Server) handleShutdown(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
	s.stopOnce.Do(func() { close(s.stop) })
}

// Run phục vụ trên ln cho tới khi ctx kết thúc, người dùng bấm Đóng, hoặc quá IdleTimeout
// không có request hợp lệ (không tính lúc đang đồng bộ). Đồng bộ đang chạy bị dừng theo
// (state đã lưu tới lô thành công cuối).
func (s *Server) Run(ctx context.Context, ln net.Listener) error {
	defer func() {
		s.baseCancel()
		s.sync.wait(15 * time.Second)
	}()
	srv := &http.Server{Handler: s.handler, ReadHeaderTimeout: 10 * time.Second}
	errc := make(chan error, 1)
	go func() { errc <- srv.Serve(ln) }()
	s.touch()

	interval := s.opts.IdleTimeout / 4
	if interval <= 0 {
		interval = time.Minute
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case err := <-errc:
			if errors.Is(err, http.ErrServerClosed) {
				return nil
			}
			return err
		case <-ctx.Done():
			return shutdown(srv, "interrupted")
		case <-s.stop:
			return shutdown(srv, "closed by user")
		case <-ticker.C:
			if s.sync.running() {
				s.touch()
				continue
			}
			idle := time.Since(time.Unix(0, s.lastActive.Load()))
			if s.opts.IdleTimeout > 0 && idle > s.opts.IdleTimeout {
				return shutdown(srv, "idle timeout")
			}
		}
	}
}

func shutdown(srv *http.Server, reason string) error {
	slog.Info("settings server stopping", "reason", reason)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return srv.Shutdown(ctx)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Warn("write json response", "err", err)
	}
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func forbid(w http.ResponseWriter) {
	writeError(w, http.StatusForbidden, "Không có quyền truy cập")
}
