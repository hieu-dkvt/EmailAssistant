package profile

import (
	"errors"
	"io/fs"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// AccountKind trùng giá trị với mail_sources.kind ở backend.
type AccountKind string

const (
	KindIMAPCache AccountKind = "imap_cache"
	KindLocal     AccountKind = "local"
)

// Account là một thư mục con của ImapMail\ (server IMAP) hoặc Mail\ (Local Folders, POP).
type Account struct {
	Name        string
	Kind        AccountKind
	Unsupported string // "maildir" nếu tài khoản lưu kiểu maildir; khi đó Folders rỗng
	Folders     []*Folder
}

// Folder là một folder mail. HasMbox = false nghĩa là chỉ có <tên>.sbd chứa folder con.
type Folder struct {
	Path     string // tương đối so với profile, phân cách bằng "/"
	Name     string
	HasMbox  bool
	Size     int64
	ModTime  time.Time
	Children []*Folder
}

// Đuôi file chắc chắn không phải mbox.
var nonMboxExt = map[string]bool{
	".msf": true, ".dat": true, ".html": true, ".json": true, ".sqlite": true, ".js": true,
	".ini": true, ".log": true, ".mab": true, ".bak": true, ".tmp": true,
}

// File tạm Thunderbird tạo khi compact folder: nstmp, hoặc nstmp-1, nstmp-2…
// (CreateUnique) khi còn sót nstmp cũ.
var compactTempName = regexp.MustCompile(`(?i)^nstmp(-\d+)?$`)

// ScanAccounts liệt kê tài khoản và cây folder của profile.
// Chỉ dùng ReadDir và thông tin stat; không mở file mbox.
func ScanAccounts(profileDir string) ([]Account, error) {
	var out []Account
	for _, g := range []struct {
		dir  string
		kind AccountKind
	}{{"ImapMail", KindIMAPCache}, {"Mail", KindLocal}} {
		accts, err := scanGroup(profileDir, g.dir, g.kind)
		if err != nil {
			return nil, err
		}
		out = append(out, accts...)
	}
	return out, nil
}

func scanGroup(profileDir, group string, kind AccountKind) ([]Account, error) {
	groupDir := filepath.Join(profileDir, group)
	entries, err := os.ReadDir(groupDir)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var accts []Account
	for _, e := range entries {
		if isLink(e) || !e.IsDir() {
			continue
		}
		a := Account{Name: e.Name(), Kind: kind}
		dir := filepath.Join(groupDir, e.Name())
		if isMaildir(dir) {
			a.Unsupported = "maildir"
		} else {
			a.Folders = scanDir(dir, group+"/"+e.Name())
		}
		accts = append(accts, a)
	}
	return accts, nil
}

// scanDir duyệt một thư mục tài khoản hoặc một thư mục <tên>.sbd.
func scanDir(dir, rel string) []*Folder {
	entries, err := os.ReadDir(dir)
	if err != nil {
		slog.Warn("cannot read folder directory", "path", dir, "err", err)
		return nil
	}
	present := make(map[string]bool, len(entries))
	for _, e := range entries {
		present[e.Name()] = true
	}

	var folders []*Folder
	byName := map[string]*Folder{}
	for _, e := range entries {
		if isLink(e) || e.IsDir() || !isMbox(e.Name(), present) {
			continue
		}
		info, err := e.Info()
		if err != nil { // file vừa bị Thunderbird xóa hoặc đổi tên
			slog.Warn("cannot stat mbox", "path", filepath.Join(dir, e.Name()), "err", err)
			continue
		}
		if !info.Mode().IsRegular() {
			continue
		}
		f := &Folder{Path: rel + "/" + e.Name(), Name: e.Name(), HasMbox: true, Size: info.Size(), ModTime: info.ModTime()}
		byName[f.Name] = f
		folders = append(folders, f)
	}
	for _, e := range entries {
		if isLink(e) || !e.IsDir() || !strings.HasSuffix(e.Name(), ".sbd") {
			continue
		}
		base := strings.TrimSuffix(e.Name(), ".sbd")
		children := scanDir(filepath.Join(dir, e.Name()), rel+"/"+e.Name())
		if f, ok := byName[base]; ok {
			f.Children = children
			continue
		}
		if len(children) == 0 {
			continue
		}
		folders = append(folders, &Folder{Path: rel + "/" + base, Name: base, Children: children})
	}
	sortFolders(folders)
	return folders
}

// isMbox: không có đuôi nằm trong danh sách loại trừ, không phải file tạm nstmp,
// và (không có đuôi hoặc có <tên>.msf cạnh nó — để nhận folder có dấu chấm như "v1.2").
func isMbox(name string, present map[string]bool) bool {
	if compactTempName.MatchString(name) {
		return false
	}
	ext := strings.ToLower(filepath.Ext(name))
	if nonMboxExt[ext] {
		return false
	}
	return ext == "" || present[name+".msf"]
}

// isMaildir: có thư mục con chứa cả cur\ và new\.
func isMaildir(dir string) bool {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return false
	}
	for _, e := range entries {
		if isLink(e) || !e.IsDir() {
			continue
		}
		sub := filepath.Join(dir, e.Name())
		if isDir(filepath.Join(sub, "cur")) && isDir(filepath.Join(sub, "new")) {
			return true
		}
	}
	return false
}

// isLink: symlink hoặc junction/reparse point — không đi theo ra ngoài profile.
func isLink(e fs.DirEntry) bool {
	return e.Type()&(fs.ModeSymlink|fs.ModeIrregular) != 0
}

func sortFolders(fs []*Folder) {
	sort.SliceStable(fs, func(i, j int) bool {
		ii, ji := strings.EqualFold(fs[i].Name, "inbox"), strings.EqualFold(fs[j].Name, "inbox")
		if ii != ji {
			return ii
		}
		return strings.ToLower(fs[i].Name) < strings.ToLower(fs[j].Name)
	})
}
