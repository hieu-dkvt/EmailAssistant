//go:build windows

package settingsui

import "os"

// listRoots trả các ổ đĩa đang truy cập được (C:\, D:\…).
func listRoots() []browseEntry {
	var out []browseEntry
	for c := 'A'; c <= 'Z'; c++ {
		p := string(c) + `:\`
		if _, err := os.Stat(p); err == nil {
			out = append(out, browseEntry{Name: string(c) + ":", Path: p})
		}
	}
	return out
}
