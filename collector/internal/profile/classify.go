package profile

import (
	"os"
	"path/filepath"
)

// DirKind cho biết thư mục người dùng chọn là loại gì.
type DirKind int

const (
	DirNotProfile   DirKind = iota
	DirProfile              // có ImapMail\ hoặc Mail\
	DirProfilesRoot         // thư mục gốc Thunderbird, chứa profiles.ini
)

// Classify nhận diện dir. dir không tồn tại thì trả lỗi fs.ErrNotExist.
func Classify(dir string) (DirKind, error) {
	fi, err := os.Stat(dir)
	if err != nil {
		return DirNotProfile, err
	}
	if !fi.IsDir() {
		return DirNotProfile, nil
	}
	if isDir(filepath.Join(dir, "ImapMail")) || isDir(filepath.Join(dir, "Mail")) {
		return DirProfile, nil
	}
	if fi, err := os.Stat(filepath.Join(dir, "profiles.ini")); err == nil && fi.Mode().IsRegular() {
		return DirProfilesRoot, nil
	}
	return DirNotProfile, nil
}

func isDir(p string) bool {
	fi, err := os.Stat(p)
	return err == nil && fi.IsDir()
}
