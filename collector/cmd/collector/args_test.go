package main

import (
	"strings"
	"testing"
)

func TestParseArgs(t *testing.T) {
	const def = "default.json"
	tests := []struct {
		name string
		args []string
		want cliOptions
	}{
		{"no args", nil, cliOptions{Command: "settings", ConfigPath: def}},
		{"settings", []string{"settings"}, cliOptions{Command: "settings", ConfigPath: def}},
		{"settings -config", []string{"settings", "-config", "x.json"}, cliOptions{Command: "settings", ConfigPath: "x.json"}},
		{"settings -no-browser", []string{"settings", "-no-browser"}, cliOptions{Command: "settings", ConfigPath: def, NoBrowser: true}},
		{"sync", []string{"sync"}, cliOptions{Command: "sync", ConfigPath: def}},
		{"sync -config", []string{"sync", "-config", "y.json"}, cliOptions{Command: "sync", ConfigPath: "y.json"}},
	}
	for _, tt := range tests {
		got, err := parseArgs(tt.args, def)
		if err != nil || got != tt.want {
			t.Errorf("%s: got %+v, %v; want %+v", tt.name, got, err, tt.want)
		}
	}
}

func TestParseArgs_Errors(t *testing.T) {
	if _, err := parseArgs([]string{"scan"}, "d"); err == nil || !strings.Contains(err.Error(), "lệnh không hợp lệ") {
		t.Errorf("scan: err = %v", err)
	}
	if _, err := parseArgs([]string{"settings", "-bogus"}, "d"); err == nil {
		t.Error("settings -bogus: want error")
	}
	if _, err := parseArgs([]string{"settings", "extra"}, "d"); err == nil {
		t.Error("settings extra: want error")
	}
	if _, err := parseArgs([]string{"sync", "-no-browser"}, "d"); err == nil {
		t.Error("sync -no-browser: want error")
	}
}
