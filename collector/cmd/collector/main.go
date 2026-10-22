// Lệnh collector: agent chạy trên máy người dùng để trích xuất mail Thunderbird.
//
//	collector settings  trang cài đặt cục bộ: chọn profile, folder, kết nối server, đồng bộ
//	collector sync      đồng bộ một lượt từ dòng lệnh (dùng cho Task Scheduler)
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	"pmmail/collector/internal/config"
	"pmmail/collector/internal/service"
	"pmmail/collector/internal/settingsui"
	"pmmail/collector/internal/syncer"
)

const usage = `Cách dùng:
  collector settings [-config <file>] [-no-browser]   Mở trang cài đặt (profile, folder, server, đồng bộ)
  collector sync [-config <file>]                     Đồng bộ một lượt
  collector                                           Mở trang cài đặt
`

func main() {
	os.Exit(run(os.Args[1:]))
}

func run(args []string) int {
	userConfigDir, err := os.UserConfigDir() // %AppData%
	if err != nil {
		fmt.Fprintln(os.Stderr, "Không xác định được thư mục cấu hình:", err)
		return 1
	}
	localAppData, err := os.UserCacheDir() // %LocalAppData%
	if err != nil {
		fmt.Fprintln(os.Stderr, "Không xác định được thư mục dữ liệu cục bộ:", err)
		return 1
	}
	opts, err := parseArgs(args, config.DefaultPath(userConfigDir))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		fmt.Fprint(os.Stderr, usage)
		return 2
	}
	svc := service.New(service.DefaultPaths(opts.ConfigPath, localAppData))
	if opts.Command == "sync" {
		return runSync(svc)
	}
	if err := runSettings(svc, opts.ConfigPath, userConfigDir, !opts.NoBrowser); err != nil {
		slog.Error("settings server failed", "err", err)
		return 1
	}
	return 0
}

// Mã thoát của lệnh sync (Task Scheduler hiện là "Last Run Result").
const exitFolderErrors = 3 // xong lượt nhưng có folder lỗi

// stopSignals: Ctrl+C, và đóng cửa sổ / đăng xuất / tắt máy (Go chuyển thành SIGTERM trên
// Windows). Dừng gọn: state đã lưu sau mỗi lô, lần sau tiếp tục.
var stopSignals = []os.Signal{os.Interrupt, syscall.SIGTERM}

func runSync(svc *service.Service) int {
	ctx, stop := signal.NotifyContext(context.Background(), stopSignals...)
	defer stop()
	lastFolder := 0
	sum, err := svc.RunSync(ctx, func(p syncer.Progress) {
		if p.FolderIndex != lastFolder {
			lastFolder = p.FolderIndex
			fmt.Printf("[%d/%d] %s\n", p.FolderIndex, p.FolderCount, p.FolderPath)
		}
	})
	for _, fe := range sum.FolderErrors {
		fmt.Printf("Lỗi folder %s: %s\n", fe.Path, fe.Message)
	}
	if err != nil {
		if errors.Is(err, service.ErrNoConfig) || errors.Is(err, service.ErrNoServer) ||
			errors.Is(err, service.ErrNoToken) || errors.Is(err, syncer.ErrAlreadyRunning) {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
		fmt.Fprintf(os.Stderr, "Đồng bộ dừng do lỗi: %s (đã gửi %d mail mới).\n",
			service.Describe(err), sum.Inserted)
		return 1
	}
	fmt.Println(syncer.FormatSummary(sum))
	if sum.FoldersFailed > 0 {
		return exitFolderErrors
	}
	return 0
}

func runSettings(svc *service.Service, configPath, appData string, launchBrowser bool) error {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return err
	}
	s, err := settingsui.New(settingsui.Options{
		Addr:        ln.Addr().String(),
		ConfigPath:  configPath,
		AppData:     appData,
		IdleTimeout: 30 * time.Minute,
		Service:     svc,
	})
	if err != nil {
		ln.Close()
		return err
	}
	url := "http://" + ln.Addr().String() + "/?key=" + s.Token()
	fmt.Println("Mở trang cài đặt:", url)
	fmt.Println(`Bấm "Đóng" trên trang hoặc nhấn Ctrl+C để kết thúc.`)
	if launchBrowser {
		if err := openBrowser(url); err != nil {
			slog.Warn("cannot open browser", "err", err)
		}
	}
	ctx, stop := signal.NotifyContext(context.Background(), stopSignals...)
	defer stop()
	return s.Run(ctx, ln)
}
