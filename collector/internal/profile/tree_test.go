package profile

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// mk dựng cây thư mục: "a/b/" tạo thư mục, "a/b=xyz" tạo file chứa "xyz",
// "a/b" tạo file rỗng. Thư mục cha được tạo tự động.
func mk(t *testing.T, root string, specs ...string) {
	t.Helper()
	for _, s := range specs {
		if strings.HasSuffix(s, "/") {
			if err := os.MkdirAll(filepath.Join(root, filepath.FromSlash(s)), 0o755); err != nil {
				t.Fatal(err)
			}
			continue
		}
		name, content, _ := strings.Cut(s, "=")
		writeFile(t, filepath.Join(root, filepath.FromSlash(name)), content)
	}
}

func names(fs []*Folder) []string {
	out := []string{}
	for _, f := range fs {
		out = append(out, f.Name)
	}
	return out
}

func findFolder(fs []*Folder, name string) *Folder {
	for _, f := range fs {
		if f.Name == name {
			return f
		}
	}
	return nil
}

func TestScanAccounts(t *testing.T) {
	p := t.TempDir()
	mk(t, p,
		"ImapMail/imap.example.com/INBOX=0123456789",
		"ImapMail/imap.example.com/INBOX.msf",
		"ImapMail/imap.example.com/INBOX.sbd/ProjectA",
		"ImapMail/imap.example.com/INBOX.sbd/ProjectA.msf",
		"ImapMail/imap.example.com/INBOX.sbd/v1.2",
		"ImapMail/imap.example.com/INBOX.sbd/v1.2.msf",
		"ImapMail/imap.example.com/INBOX.sbd/notes.txt",
		"ImapMail/imap.example.com/Sent",
		"ImapMail/imap.example.com/Trash",
		"ImapMail/imap.example.com/msgFilterRules.dat",
		"ImapMail/imap.example.com/nstmp",
		"ImapMail/imap.example.com/nstmp-1",  // CreateUnique khi còn nstmp cũ
		"ImapMail/imap.example.com/Nstmp-12", // không phân biệt hoa thường
		"ImapMail/imap.example.com/[Gmail].sbd/All Mail",
		"ImapMail/imap.example.com/Empty.sbd/",
		"ImapMail/imap.example-1.com/INBOX",
		"ImapMail/maildir.example.com/INBOX/cur/",
		"ImapMail/maildir.example.com/INBOX/new/",
		"Mail/Local Folders/Inbox",
		"Mail/Local Folders/Inbox.sbd/",
		"Mail/Local Folders/案件A",
		"Mail/Local Folders/Dự án #1 & co",
		"Mail/Local Folders/Trash",
		"Mail/Local Folders/feeds.json",
	)

	accts, err := ScanAccounts(p)
	if err != nil {
		t.Fatalf("ScanAccounts: %v", err)
	}

	var gotAccts []string
	for _, a := range accts {
		gotAccts = append(gotAccts, a.Name+":"+string(a.Kind))
	}
	wantAccts := []string{
		"imap.example-1.com:imap_cache",
		"imap.example.com:imap_cache",
		"maildir.example.com:imap_cache",
		"Local Folders:local",
	}
	if !reflect.DeepEqual(gotAccts, wantAccts) {
		t.Fatalf("accounts = %v, want %v", gotAccts, wantAccts)
	}

	imap := accts[1]
	if got, want := names(imap.Folders), []string{"INBOX", "[Gmail]", "Sent", "Trash"}; !reflect.DeepEqual(got, want) {
		t.Errorf("imap.example.com folders = %v, want %v", got, want)
	}
	inbox := findFolder(imap.Folders, "INBOX")
	if inbox == nil || inbox.Path != "ImapMail/imap.example.com/INBOX" || inbox.Size != 10 || !inbox.HasMbox || inbox.ModTime.IsZero() {
		t.Errorf("INBOX = %+v", inbox)
	}
	if got, want := names(inbox.Children), []string{"ProjectA", "v1.2"}; !reflect.DeepEqual(got, want) {
		t.Errorf("INBOX children = %v, want %v", got, want)
	}
	if pa := findFolder(inbox.Children, "ProjectA"); pa == nil || pa.Path != "ImapMail/imap.example.com/INBOX.sbd/ProjectA" {
		t.Errorf("ProjectA = %+v", pa)
	}
	gmail := findFolder(imap.Folders, "[Gmail]")
	if gmail == nil || gmail.HasMbox || !reflect.DeepEqual(names(gmail.Children), []string{"All Mail"}) {
		t.Errorf("[Gmail] = %+v", gmail)
	}
	if gmail != nil && gmail.Children[0].Path != "ImapMail/imap.example.com/[Gmail].sbd/All Mail" {
		t.Errorf("All Mail path = %q", gmail.Children[0].Path)
	}

	if md := accts[2]; md.Unsupported != "maildir" || len(md.Folders) != 0 {
		t.Errorf("maildir account = %+v", md)
	}

	local := accts[3]
	if got, want := names(local.Folders), []string{"Inbox", "Dự án #1 & co", "Trash", "案件A"}; !reflect.DeepEqual(got, want) {
		t.Errorf("Local Folders = %v, want %v", got, want)
	}
	if li := findFolder(local.Folders, "Inbox"); li == nil || len(li.Children) != 0 {
		t.Errorf("local Inbox = %+v", li)
	}
}

func TestScanAccounts_NoMailDirs(t *testing.T) {
	p := t.TempDir()
	mk(t, p, "ImapMail/")
	accts, err := ScanAccounts(p)
	if err != nil || len(accts) != 0 {
		t.Errorf("got %v, %v; want empty, nil", accts, err)
	}
}
