//go:build !windows

package settingsui

func listRoots() []browseEntry {
	return []browseEntry{{Name: "/", Path: "/"}}
}
