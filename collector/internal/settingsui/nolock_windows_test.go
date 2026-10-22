//go:build windows

package settingsui

import (
	"net/url"
	"path/filepath"
	"syscall"
	"testing"
)

// lockExclusive mở file với share mode 0: mọi lần mở khác (kể cả chỉ đọc) đều lỗi
// sharing violation. Dùng để chứng minh code không mở file mbox.
func lockExclusive(t *testing.T, path string) {
	t.Helper()
	p, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		t.Fatal(err)
	}
	h, err := syscall.CreateFile(p, syscall.GENERIC_READ, 0, nil, syscall.OPEN_EXISTING, syscall.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		t.Fatalf("lock %s: %v", path, err)
	}
	t.Cleanup(func() { syscall.CloseHandle(h) })
}

func TestAPIBrowse_DoesNotOpenFile(t *testing.T) {
	dir := t.TempDir()
	mkProfile(t, dir)
	mbox := filepath.Join(dir, "ImapMail", "imap.example.com", "INBOX")
	lockExclusive(t, mbox)
	s := newTestServer(t)

	w := doReq(s, "GET", "/api/browse?path="+url.QueryEscape(mbox), "")
	wantError(t, w, 400, "Không phải thư mục")
}

func TestAPIFolders_DoesNotOpenMbox(t *testing.T) {
	dir := t.TempDir()
	mkProfile(t, dir)
	lockExclusive(t, filepath.Join(dir, "ImapMail", "imap.example.com", "INBOX"))
	lockExclusive(t, filepath.Join(dir, "ImapMail", "imap.example.com", "INBOX.sbd", "ProjectA"))
	s := newTestServer(t)

	r := getFolders(t, s, dir)
	inbox := folderByName(r.Accounts[0].Folders, "INBOX")
	if !inbox.HasMbox || inbox.Size != 1 || len(inbox.Children) != 1 {
		t.Errorf("INBOX = %+v", inbox)
	}
}
