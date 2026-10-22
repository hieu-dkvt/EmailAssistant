// Package profile dò profile Thunderbird và cây folder mail bên trong.
// Gói này chỉ đọc metadata (stat, liệt kê thư mục, profiles.ini);
// không bao giờ mở file mbox hay .msf và không ghi gì vào profile.
package profile

import (
	"io"
	"os"
	"path/filepath"
	"strings"
)

// Profile là một mục [ProfileN] trong profiles.ini.
type Profile struct {
	Name      string
	Path      string // đường dẫn tuyệt đối
	IsDefault bool
}

// ParseProfilesINI đọc profiles.ini; đường dẫn tương đối được tính từ baseDir.
// Mục [Install*] có Default=... (Thunderbird 68+) được ưu tiên hơn Default=1.
func ParseProfilesINI(r io.Reader, baseDir string) ([]Profile, error) {
	secs, err := parseINI(r)
	if err != nil {
		return nil, err
	}
	var ps []Profile
	installDefault := ""
	for _, s := range secs {
		switch {
		case strings.HasPrefix(s.name, "Profile"):
			p := s.keys["Path"]
			if p == "" {
				continue
			}
			ps = append(ps, Profile{
				Name:      s.keys["Name"],
				Path:      resolvePath(baseDir, p, s.keys["IsRelative"] == "1"),
				IsDefault: s.keys["Default"] == "1",
			})
		case strings.HasPrefix(s.name, "Install"):
			if d := s.keys["Default"]; d != "" && installDefault == "" {
				installDefault = d
			}
		}
	}
	if installDefault != "" {
		applyInstallDefault(ps, baseDir, installDefault)
	}
	return ps, nil
}

// ProfilesInRoot đọc root/profiles.ini và root/installs.ini (nếu có).
func ProfilesInRoot(root string) ([]Profile, error) {
	f, err := os.Open(filepath.Join(root, "profiles.ini"))
	if err != nil {
		return nil, err
	}
	defer f.Close()
	ps, err := ParseProfilesINI(f, root)
	if err != nil {
		return nil, err
	}

	inst, err := os.Open(filepath.Join(root, "installs.ini"))
	if err != nil {
		return ps, nil
	}
	defer inst.Close()
	secs, err := parseINI(inst)
	if err != nil {
		return ps, nil
	}
	for _, s := range secs {
		if d := s.keys["Default"]; d != "" {
			applyInstallDefault(ps, root, d)
			break
		}
	}
	return ps, nil
}

func resolvePath(baseDir, p string, relative bool) string {
	p = filepath.FromSlash(p)
	if relative {
		return filepath.Join(baseDir, p)
	}
	return filepath.Clean(p)
}

func applyInstallDefault(ps []Profile, baseDir, def string) {
	target := resolvePath(baseDir, def, !filepath.IsAbs(filepath.FromSlash(def)))
	for i := range ps {
		ps[i].IsDefault = samePath(ps[i].Path, target)
	}
}

func samePath(a, b string) bool {
	return strings.EqualFold(filepath.Clean(a), filepath.Clean(b))
}
