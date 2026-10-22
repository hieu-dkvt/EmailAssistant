package settingsui

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"pmmail/collector/internal/service"
	"pmmail/collector/internal/syncer"
	"pmmail/collector/internal/upload"
)

// API kết nối server, đồng bộ và xóa mail khỏi server.
// Spec: docs/superpowers/specs/2026-10-17-sync-ui-design.md mục 4. Token không bao giờ trả về.

const (
	msgNoService = "Chức năng đồng bộ chưa được bật trong lần chạy này"
	msgSyncing   = "Đang đồng bộ: bấm Dừng rồi lưu lại"
)

func (s *Server) syncRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /api/server/status", s.withService(s.handleServerStatus))
	mux.HandleFunc("POST /api/server/settings", s.withService(s.handleServerSettings))
	mux.HandleFunc("GET /api/server/sources", s.withService(s.handleServerSources))
	mux.HandleFunc("POST /api/server/delete-folder", s.withService(s.handleDeleteFolder))
	mux.HandleFunc("POST /api/sync/start", s.withService(s.handleSyncStart))
	mux.HandleFunc("POST /api/sync/stop", s.handleSyncStop)
	mux.HandleFunc("GET /api/sync/status", s.handleSyncStatus)
}

func (s *Server) withService(h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if s.opts.Service == nil {
			writeError(w, http.StatusServiceUnavailable, msgNoService)
			return
		}
		h(w, r)
	}
}

func decodeBody(w http.ResponseWriter, r *http.Request, v any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, 64<<10)
	if err := json.NewDecoder(r.Body).Decode(v); err != nil {
		writeError(w, http.StatusBadRequest, msgBadRequest)
		return false
	}
	return true
}

func (s *Server) handleServerStatus(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, s.opts.Service.ServerStatus(r.Context()))
}

func (s *Server) handleServerSettings(w http.ResponseWriter, r *http.Request) {
	var req struct {
		ServerURL string `json:"server_url"`
		Token     string `json:"token"`
	}
	if !decodeBody(w, r, &req) {
		return
	}
	if s.sync.running() {
		writeError(w, http.StatusConflict, msgSyncing)
		return
	}
	if err := s.opts.Service.SaveServer(req.ServerURL, req.Token); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, s.opts.Service.ServerStatus(r.Context()))
}

func (s *Server) handleServerSources(w http.ResponseWriter, r *http.Request) {
	sources, err := s.opts.Service.ServerSources(r.Context())
	if err != nil {
		writeError(w, http.StatusBadRequest, service.Describe(err))
		return
	}
	if sources == nil {
		sources = []upload.SourceInfo{}
	}
	writeJSON(w, http.StatusOK, sources)
}

func (s *Server) handleDeleteFolder(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Path string `json:"path"`
	}
	if !decodeBody(w, r, &req) {
		return
	}
	res, err := s.opts.Service.DeleteFolder(r.Context(), req.Path)
	switch {
	case errors.Is(err, service.ErrFolderSelected), errors.Is(err, syncer.ErrAlreadyRunning):
		writeError(w, http.StatusConflict, err.Error())
	case err != nil:
		writeError(w, http.StatusBadRequest, service.Describe(err))
	default:
		writeJSON(w, http.StatusOK, res)
	}
}

func (s *Server) handleSyncStart(w http.ResponseWriter, _ *http.Request) {
	if err := s.opts.Service.Ready(); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if !s.sync.start(s.baseCtx, s.opts.Service.RunSync, s.touch) {
		writeError(w, http.StatusConflict, "Đang đồng bộ")
		return
	}
	writeJSON(w, http.StatusOK, s.sync.snapshot())
}

func (s *Server) handleSyncStop(w http.ResponseWriter, _ *http.Request) {
	s.sync.stop()
	writeJSON(w, http.StatusOK, s.sync.snapshot())
}

func (s *Server) handleSyncStatus(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.sync.snapshot())
}

// syncStatus: state là idle | running | done | error | stopped.
type syncStatus struct {
	State    string           `json:"state"`
	Progress *syncer.Progress `json:"progress,omitempty"`
	Summary  *syncer.Summary  `json:"summary,omitempty"`
	Message  string           `json:"message,omitempty"`
}

type runFunc func(ctx context.Context, progress func(syncer.Progress)) (syncer.Summary, error)

// syncManager chạy đồng bộ nền; mỗi lúc chỉ một lượt.
type syncManager struct {
	mu     sync.Mutex
	status syncStatus
	cancel context.CancelFunc
	done   chan struct{}
}

func newSyncManager() *syncManager { return &syncManager{status: syncStatus{State: "idle"}} }

func (m *syncManager) start(parent context.Context, run runFunc, onProgress func()) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.status.State == "running" {
		return false
	}
	ctx, cancel := context.WithCancel(parent)
	done := make(chan struct{})
	m.cancel, m.done = cancel, done
	m.status = syncStatus{State: "running"}
	go func() {
		defer close(done)
		defer cancel()
		sum, err := safeRun(ctx, run, func(p syncer.Progress) {
			m.mu.Lock()
			m.status.Progress = &p
			m.mu.Unlock()
			onProgress()
		})
		m.mu.Lock()
		defer m.mu.Unlock()
		m.status.Summary = &sum
		switch {
		case errors.Is(err, errInternal):
			m.status.State, m.status.Message = "error", "Đồng bộ dừng do lỗi nội bộ của collector. "+
				"Mail đã gửi vẫn được giữ; hãy thử lại, nếu lặp lại thì báo cho quản trị."
		case err == nil:
			m.status.State, m.status.Message = "done", syncer.FormatSummary(sum)
		case errors.Is(err, context.Canceled):
			m.status.State = "stopped"
			m.status.Message = fmt.Sprintf(
				"Đã dừng đồng bộ (đã gửi %d mail mới). Lần sau sẽ tiếp tục từ chỗ dừng.", sum.Inserted)
		default:
			m.status.State = "error"
			m.status.Message = fmt.Sprintf("Đồng bộ dừng do lỗi: %s (đã gửi %d mail mới).",
				service.Describe(err), sum.Inserted)
		}
	}()
	return true
}

var errInternal = errors.New("internal error")

// safeRun: panic trong lượt đồng bộ chỉ làm lượt đó lỗi, không làm sập collector (các defer
// như nhả khóa vẫn chạy). Không log giá trị panic: có thể chứa nội dung mail.
func safeRun(ctx context.Context, run runFunc, progress func(syncer.Progress)) (sum syncer.Summary, err error) {
	defer func() {
		if v := recover(); v != nil {
			slog.Error("sync panic", "type", fmt.Sprintf("%T", v))
			err = errInternal
		}
	}()
	return run(ctx, progress)
}

func (m *syncManager) stop() {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.cancel != nil {
		m.cancel()
	}
}

func (m *syncManager) running() bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.status.State == "running"
}

func (m *syncManager) snapshot() syncStatus {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.status
}

// wait chờ lượt đang chạy kết thúc (sau khi đã hủy) tối đa timeout.
func (m *syncManager) wait(timeout time.Duration) {
	m.mu.Lock()
	done := m.done
	m.mu.Unlock()
	if done == nil {
		return
	}
	select {
	case <-done:
	case <-time.After(timeout):
	}
}
