package main

import (
	"flag"
	"fmt"
	"io"
)

type cliOptions struct {
	Command    string // "settings" | "sync"
	ConfigPath string
	NoBrowser  bool // chỉ in URL, không tự mở trình duyệt
}

// parseArgs: không tham số thì mở trang cài đặt (double-click collector.exe luôn
// quay lại được màn hình setting).
func parseArgs(args []string, defaultConfig string) (cliOptions, error) {
	if len(args) == 0 {
		return cliOptions{Command: "settings", ConfigPath: defaultConfig}, nil
	}
	cmd := args[0]
	if cmd != "settings" && cmd != "sync" {
		return cliOptions{}, fmt.Errorf("lệnh không hợp lệ: %q", cmd)
	}
	fs := flag.NewFlagSet(cmd, flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	cfg := fs.String("config", defaultConfig, "đường dẫn file cấu hình")
	var noBrowser *bool
	if cmd == "settings" {
		noBrowser = fs.Bool("no-browser", false, "không tự mở trình duyệt")
	}
	if err := fs.Parse(args[1:]); err != nil {
		return cliOptions{}, fmt.Errorf("tham số không hợp lệ: %w", err)
	}
	if fs.NArg() > 0 {
		return cliOptions{}, fmt.Errorf("tham số thừa: %q", fs.Arg(0))
	}
	opts := cliOptions{Command: cmd, ConfigPath: *cfg}
	if noBrowser != nil {
		opts.NoBrowser = *noBrowser
	}
	return opts, nil
}
