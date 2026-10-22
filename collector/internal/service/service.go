// Package service ghép các phần của collector cho lệnh `collector sync` và trang setting:
// kết nối server (URL + token), kiểm tra kết nối, đồng bộ, xóa mail của folder khỏi server.
//
// Spec: docs/superpowers/specs/2026-10-17-sync-ui-design.md, 2026-10-17-delete-folder-mail-design.md.
package service

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"pmmail/collector/internal/config"
	"pmmail/collector/internal/state"
	"pmmail/collector/internal/syncer"
	"pmmail/collector/internal/tokenstore"
	"pmmail/collector/internal/upload"
)

var (
	ErrNoConfig       = errors.New("chưa có cấu hình: hãy chọn profile và folder rồi bấm Lưu")
	ErrNoServer       = errors.New("chưa có kết nối server: hãy nhập URL server")
	ErrNoToken        = errors.New("chưa có token: hãy nhập token do quản trị cấp")
	ErrBadURL         = errors.New("URL server không hợp lệ (cần dạng http://127.0.0.1:8000 hoặc https://...)")
	ErrInsecureURL    = errors.New("server không phải máy này phải dùng https://")
	ErrFolderSelected = errors.New("folder đang được chọn đồng bộ: bỏ tick và bấm Lưu trước khi xóa mail khỏi server")
	ErrBadFolder      = errors.New("đường dẫn folder không hợp lệ")
	// Token chỉ được gửi tới server đã nhập cùng nó (chống lộ token khi URL bị đổi).
	ErrTokenOtherServer = errors.New("token đã lưu dùng cho server khác: đổi server thì phải nhập token của server mới")
)

// Client là các lệnh gọi backend mà service dùng (upload.Client; thay được trong test).
type Client interface {
	syncer.Ingester
	Me(ctx context.Context) (upload.Me, error)
	Sources(ctx context.Context) ([]upload.SourceInfo, error)
	DeleteFolderMail(ctx context.Context, folderPath string) (upload.DeleteResult, error)
}

type Paths struct {
	Config string
	State  string
	Token  string
	Lock   string
}

// DefaultPaths: config.json theo đường dẫn đã chọn; state, token, khóa trong %LocalAppData%.
func DefaultPaths(configPath, localAppData string) Paths {
	return Paths{
		Config: configPath,
		State:  state.DefaultPath(localAppData),
		Token:  tokenstore.DefaultPath(localAppData),
		Lock:   syncer.DefaultLockPath(localAppData),
	}
}

type Service struct {
	Paths     Paths
	NewClient func(baseURL, token string) Client
}

func New(paths Paths) *Service {
	return &Service{
		Paths:     paths,
		NewClient: func(baseURL, token string) Client { return upload.New(baseURL, token) },
	}
}

// ValidateServerURL chỉ nhận http(s); http chỉ cho máy này (token đi dạng rõ).
func ValidateServerURL(raw string) (string, error) {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" {
		return "", ErrBadURL
	}
	if u.Scheme == "http" && !isLoopback(u.Hostname()) {
		return "", ErrInsecureURL
	}
	u.Path = strings.TrimRight(u.Path, "/")
	u.RawQuery, u.Fragment, u.User = "", "", nil
	return u.String(), nil
}

// origin của URL đã qua ValidateServerURL: scheme://host:port viết thường.
func origin(serverURL string) string {
	u, err := url.Parse(serverURL)
	if err != nil {
		return ""
	}
	return strings.ToLower(u.Scheme + "://" + u.Host)
}

func isLoopback(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// SaveServer lưu URL vào config.json (giữ nguyên profile và folder) và token (mã hóa, gắn với
// origin của URL). token rỗng: giữ token đã lưu, chỉ khi vẫn là server đó.
func (s *Service) SaveServer(rawURL, token string) error {
	u, err := ValidateServerURL(rawURL)
	if err != nil {
		return err
	}
	cfg, found, err := config.Load(s.Paths.Config)
	if err != nil {
		return fmt.Errorf("file cấu hình bị hỏng: %w", err)
	}
	if !found {
		cfg = &config.Config{ConfigVersion: config.CurrentVersion, Folders: []config.FolderChoice{}}
	}
	if token = strings.TrimSpace(token); token != "" {
		if err := tokenstore.Save(s.Paths.Token, tokenstore.Entry{Origin: origin(u), Token: token}); err != nil {
			return fmt.Errorf("không lưu được token: %w", err)
		}
	} else if old, err := tokenstore.Load(s.Paths.Token); err == nil && old.Token != "" && old.Origin != origin(u) {
		return ErrTokenOtherServer
	}
	cfg.ServerURL = u
	return config.Save(s.Paths.Config, cfg)
}

// clientFor tạo client tới serverURL với token đã lưu. URL được kiểm tra lại (config.json có
// thể bị sửa tay) và phải cùng origin với lúc nhập token; nếu không thì token không được gửi
// đi. Client dùng chính URL đã kiểm tra, không dùng chuỗi thô.
func (s *Service) clientFor(serverURL string) (Client, error) {
	u, err := ValidateServerURL(serverURL)
	if err != nil {
		return nil, err
	}
	e, err := tokenstore.Load(s.Paths.Token)
	if err != nil {
		return nil, fmt.Errorf("không đọc được token: %w", err)
	}
	if e.Token == "" {
		return nil, ErrNoToken
	}
	if e.Origin != origin(u) {
		return nil, ErrTokenOtherServer
	}
	return s.NewClient(u, e.Token), nil
}

type Status struct {
	ServerURL   string `json:"server_url"`
	HasToken    bool   `json:"has_token"`
	Connected   bool   `json:"connected"`
	Email       string `json:"email,omitempty"`
	DisplayName string `json:"display_name,omitempty"`
	Error       string `json:"error,omitempty"`
}

// ServerStatus kiểm tra kết nối bằng GET /api/v1/me. Không bao giờ trả token.
func (s *Service) ServerStatus(ctx context.Context) Status {
	var st Status
	if cfg, found, err := config.Load(s.Paths.Config); err == nil && found {
		st.ServerURL = cfg.ServerURL
	}
	e, err := tokenstore.Load(s.Paths.Token)
	if err != nil {
		st.Error = "Không đọc được token đã lưu: hãy nhập lại token"
		return st
	}
	st.HasToken = e.Token != ""
	if st.ServerURL == "" || !st.HasToken {
		return st
	}
	c, err := s.clientFor(st.ServerURL)
	if err != nil {
		st.Error = Describe(err)
		return st
	}
	me, err := c.Me(ctx)
	if err != nil {
		st.Error = Describe(err)
		return st
	}
	st.Connected, st.Email, st.DisplayName = true, me.Email, me.DisplayName
	return st
}

// Describe chuyển lỗi gọi server thành câu tiếng Việt (không chứa token hay nội dung mail).
func Describe(err error) string {
	var he *upload.HTTPError
	switch {
	case errors.Is(err, upload.ErrUnauthorized):
		return "Token không hợp lệ hoặc đã bị thu hồi"
	case errors.As(err, &he) && he.Status == http.StatusUpgradeRequired:
		return "Collector quá cũ so với server: hãy cập nhật collector rồi đồng bộ lại"
	case errors.As(err, &he):
		return fmt.Sprintf("Server trả lỗi HTTP %d", he.Status)
	case errors.Is(err, context.Canceled):
		return "Đã dừng"
	}
	return err.Error()
}

// connection trả cấu hình đã lưu và client tới server.
func (s *Service) connection() (*config.Config, Client, error) {
	cfg, found, err := config.Load(s.Paths.Config)
	if err != nil {
		return nil, nil, fmt.Errorf("file cấu hình bị hỏng: %w", err)
	}
	if !found || cfg.ProfilePath == "" {
		return nil, nil, ErrNoConfig
	}
	if cfg.ServerURL == "" {
		return nil, nil, ErrNoServer
	}
	c, err := s.clientFor(cfg.ServerURL)
	if err != nil {
		return nil, nil, err
	}
	return cfg, c, nil
}

// Ready kiểm tra đủ điều kiện đồng bộ (cấu hình, server, token) mà không gọi server.
func (s *Service) Ready() error {
	_, _, err := s.connection()
	return err
}

func (s *Service) ServerSources(ctx context.Context) ([]upload.SourceInfo, error) {
	_, c, err := s.connection()
	if err != nil {
		return nil, err
	}
	return c.Sources(ctx)
}

// DeleteFolder xóa mail đã đồng bộ của folder khỏi server. Chỉ cho folder đang không được
// chọn, hoặc folder không còn trong Thunderbird (đổi tên, xóa); không chạy song song với đồng
// bộ. Xong thì quên vị trí đã đọc của folder. Server không còn folder đó → coi như đã xóa.
func (s *Service) DeleteFolder(ctx context.Context, folderPath string) (upload.DeleteResult, error) {
	if !validFolderPath(folderPath) {
		return upload.DeleteResult{}, ErrBadFolder
	}
	cfg, c, err := s.connection()
	if err != nil {
		return upload.DeleteResult{}, err
	}
	if cfg.ShouldScan(folderPath) && mboxExists(cfg.ProfilePath, folderPath) {
		return upload.DeleteResult{}, ErrFolderSelected
	}
	release, err := syncer.AcquireLock(s.Paths.Lock)
	if err != nil {
		return upload.DeleteResult{}, err
	}
	defer release()
	res, err := c.DeleteFolderMail(ctx, folderPath)
	var he *upload.HTTPError
	if errors.As(err, &he) && he.Status == http.StatusNotFound && he.Code == upload.CodeSourceNotFound {
		res, err = upload.DeleteResult{}, nil // backend xác nhận không có folder này: đã xóa từ trước
	}
	if err != nil {
		return upload.DeleteResult{}, err
	}
	st, _ := state.Load(s.Paths.State, cfg.ServerURL, cfg.ProfilePath)
	st.ResetFolder(folderPath)
	if err := st.Save(s.Paths.State); err != nil {
		return res, fmt.Errorf("đã xóa trên server nhưng không lưu được state: %w", err)
	}
	slog.Info("deleted folder mail on server", "folder", folderPath,
		"deleted", res.DeletedMessages, "kept", res.KeptMessages)
	return res, nil
}

// validFolderPath: đường dẫn tương đối trong profile, dạng "ImapMail/host/INBOX".
func validFolderPath(p string) bool {
	if p == "" || strings.HasPrefix(p, "/") || strings.Contains(p, `\`) || strings.Contains(p, ":") {
		return false
	}
	for part := range strings.SplitSeq(p, "/") {
		if part == "" || part == "." || part == ".." {
			return false
		}
	}
	return true
}

func mboxExists(profileDir, folderPath string) bool {
	fi, err := os.Stat(filepath.Join(profileDir, filepath.FromSlash(folderPath)))
	return err == nil && !fi.IsDir()
}

// RunSync chạy một lượt đồng bộ (khóa để không chạy trùng).
func (s *Service) RunSync(ctx context.Context, progress func(syncer.Progress)) (syncer.Summary, error) {
	cfg, c, err := s.connection()
	if err != nil {
		return syncer.Summary{}, err
	}
	release, err := syncer.AcquireLock(s.Paths.Lock)
	if err != nil {
		return syncer.Summary{}, err
	}
	defer release()
	st, reset := state.Load(s.Paths.State, cfg.ServerURL, cfg.ProfilePath)
	if reset {
		slog.Info("sync state reset (server, profile or state file changed)")
	}
	return syncer.Run(ctx, syncer.Options{
		ProfileDir: cfg.ProfilePath,
		Config:     cfg,
		State:      st,
		SaveState:  func(x *state.State) error { return x.Save(s.Paths.State) },
		Client:     c,
		Progress:   progress,
	})
}
