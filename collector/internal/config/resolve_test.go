package config

import (
	"errors"
	"reflect"
	"testing"
	"time"

	"pmmail/collector/internal/profile"
)

const (
	pInbox    = "ImapMail/a/INBOX"
	pProjectA = "ImapMail/a/INBOX.sbd/ProjectA"
	pProjectB = "ImapMail/a/INBOX.sbd/ProjectB"
	pTrash    = "ImapMail/a/Trash"
	pOld      = "ImapMail/a/Trash.sbd/Old"
	pJunk     = "ImapMail/a/Junk"
	pGmail    = "ImapMail/a/[Gmail]"
	pAllMail  = "ImapMail/a/[Gmail].sbd/All Mail"
)

func leaf(path, name string) *profile.Folder {
	return &profile.Folder{Path: path, Name: name, HasMbox: true}
}

// accts dựng tay: INBOX (ProjectA, ProjectB), Trash (Old), Junk, [Gmail] không có mbox (All Mail).
func accts() []profile.Account {
	inbox := leaf(pInbox, "INBOX")
	inbox.Children = []*profile.Folder{leaf(pProjectA, "ProjectA"), leaf(pProjectB, "ProjectB")}
	trash := leaf(pTrash, "Trash")
	trash.Children = []*profile.Folder{leaf(pOld, "Old")}
	gmail := &profile.Folder{Path: pGmail, Name: "[Gmail]", Children: []*profile.Folder{leaf(pAllMail, "All Mail")}}
	return []profile.Account{{
		Name: "a", Kind: profile.KindIMAPCache,
		Folders: []*profile.Folder{inbox, gmail, leaf(pJunk, "Junk"), trash},
	}}
}

var defaultStates = map[string]FolderState{
	pInbox: {Selected: true}, pProjectA: {Selected: true}, pProjectB: {Selected: true}, pAllMail: {Selected: true},
	pTrash: {}, pOld: {}, pJunk: {},
}

func TestResolve_NoConfig(t *testing.T) {
	got := Resolve(nil, `C:\p`, accts())
	if !reflect.DeepEqual(got, defaultStates) {
		t.Errorf("got %v\nwant %v", got, defaultStates)
	}
}

func TestResolve_OtherProfile(t *testing.T) {
	cfg := &Config{ProfilePath: `C:\other`, Folders: []FolderChoice{{Path: pInbox, Selected: false}}}
	got := Resolve(cfg, `C:\p`, accts())
	if !reflect.DeepEqual(got, defaultStates) {
		t.Errorf("got %v\nwant %v", got, defaultStates)
	}
}

func savedConfig(auto bool) *Config {
	return &Config{
		ProfilePath:           `c:\p`, // khác hoa thường so với C:\p
		AutoIncludeNewFolders: auto,
		Folders: []FolderChoice{
			{Path: pInbox, Selected: false},
			{Path: pProjectA, Selected: true},
			{Path: pTrash, Selected: false},
			{Path: pOld, Selected: false},
			{Path: pAllMail, Selected: true},
		},
	}
}

func TestResolve_WithConfig(t *testing.T) {
	got := Resolve(savedConfig(false), `C:\p`, accts())
	want := map[string]FolderState{
		pInbox: {}, pProjectA: {Selected: true}, pTrash: {}, pOld: {}, pAllMail: {Selected: true},
		pProjectB: {IsNew: true}, pJunk: {IsNew: true},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("auto=false: got %v\nwant %v", got, want)
	}

	// ProjectB mới nằm dưới INBOX đã bỏ tick: kế thừa bỏ tick dù bật tự thêm.
	got = Resolve(savedConfig(true), `C:\p`, accts())
	if !reflect.DeepEqual(got, want) {
		t.Errorf("auto=true: got %v\nwant %v", got, want)
	}
}

const (
	pPersonal = "ImapMail/a/Personal"
	pBank     = "ImapMail/a/Personal.sbd/Bank"
	pWork     = "ImapMail/a/Work"
	pQ4       = "ImapMail/a/Work.sbd/Q4"
	pNewTop   = "ImapMail/a/Projects2027"
)

// inheritAccts: Personal (Bank), Work (Q4), Projects2027 — Bank, Q4, Projects2027 là folder mới.
func inheritAccts() []profile.Account {
	personal := leaf(pPersonal, "Personal")
	personal.Children = []*profile.Folder{leaf(pBank, "Bank")}
	work := leaf(pWork, "Work")
	work.Children = []*profile.Folder{leaf(pQ4, "Q4")}
	return []profile.Account{{Name: "a", Kind: profile.KindIMAPCache,
		Folders: []*profile.Folder{personal, leaf(pNewTop, "Projects2027"), work}}}
}

func inheritConfig(auto bool) *Config {
	return &Config{ProfilePath: `C:\p`, AutoIncludeNewFolders: auto, Folders: []FolderChoice{
		{Path: "ImapMail/a/personal", Selected: false}, // khác hoa thường so với cây
		{Path: pWork, Selected: true},
	}}
}

func TestResolve_NewFolderInheritsParent(t *testing.T) {
	got := Resolve(inheritConfig(true), `C:\p`, inheritAccts())
	want := map[string]FolderState{
		pPersonal: {},                            // đã lưu (khớp không phân biệt hoa thường)
		pBank:     {IsNew: true},                 // cha bỏ tick → bỏ tick
		pWork:     {Selected: true},              // đã lưu
		pQ4:       {Selected: true, IsNew: true}, // cha được chọn + tự thêm
		pNewTop:   {Selected: true, IsNew: true}, // không có cha đã biết + tự thêm
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %v\nwant %v", got, want)
	}
}

func TestShouldScan_InheritsParent(t *testing.T) {
	tests := []struct {
		auto bool
		path string
		want bool
	}{
		{true, pPersonal, false},
		{true, pBank, false},
		{true, "ImapMail/a/Personal.sbd/Bank.sbd/2026", false}, // cháu
		{true, pQ4, true},
		{true, pNewTop, true},
		{false, pQ4, false},
	}
	for _, tt := range tests {
		if got := inheritConfig(tt.auto).ShouldScan(tt.path); got != tt.want {
			t.Errorf("auto=%v %s: got %v, want %v", tt.auto, tt.path, got, tt.want)
		}
	}
}

func TestResolve_FolderGoneFromTree(t *testing.T) {
	cfg := savedConfig(false)
	cfg.Folders = append(cfg.Folders, FolderChoice{Path: "ImapMail/a/Deleted2025", Selected: true})
	got := Resolve(cfg, `C:\p`, accts())
	if _, ok := got["ImapMail/a/Deleted2025"]; ok || len(got) != 7 {
		t.Errorf("folder no longer in tree must not appear: %v", got)
	}
}

func TestBuild(t *testing.T) {
	now := time.Date(2026, 10, 16, 9, 0, 0, 0, time.UTC)
	// Người dùng chỉ thấy (và quyết định) 4 folder; Junk, Old, All Mail không có trong decisions.
	decisions := map[string]bool{pInbox: true, pProjectA: false, pProjectB: true, pTrash: false}
	cfg, err := Build(`C:\p`, true, accts(), decisions, now)
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	if cfg.ConfigVersion != 1 || cfg.ProfilePath != `C:\p` || !cfg.AutoIncludeNewFolders || !cfg.SavedAt.Equal(now) {
		t.Errorf("header = %+v", cfg)
	}
	want := []FolderChoice{
		{Path: pInbox, Account: "a", Kind: profile.KindIMAPCache, Selected: true},
		{Path: pProjectA, Account: "a", Kind: profile.KindIMAPCache, Selected: false},
		{Path: pProjectB, Account: "a", Kind: profile.KindIMAPCache, Selected: true},
		{Path: pTrash, Account: "a", Kind: profile.KindIMAPCache, Selected: false},
	}
	if !reflect.DeepEqual(cfg.Folders, want) {
		t.Errorf("folders = %+v\nwant %+v", cfg.Folders, want)
	}
	// Folder chưa có quyết định vẫn là "mới" ở lần mở sau.
	if st := Resolve(cfg, `C:\p`, accts()); !st[pJunk].IsNew || !st[pAllMail].IsNew {
		t.Errorf("undecided folders must stay new: %v", st)
	}

	_, err = Build(`C:\p`, false, accts(), map[string]bool{"ImapMail/a/Nope": true}, now)
	var unknown *UnknownFolderError
	if !errors.As(err, &unknown) || unknown.Path != "ImapMail/a/Nope" {
		t.Errorf("unknown selected path: got %v, want *UnknownFolderError{ImapMail/a/Nope}", err)
	}
	if _, err := Build(`C:\p`, false, accts(), map[string]bool{pGmail: true}, now); err == nil {
		t.Error("selected parent-only node: want error")
	}
}

func TestShouldScan(t *testing.T) {
	tests := []struct {
		name string
		auto bool
		path string
		want bool
	}{
		{"saved selected", false, pProjectA, true},
		{"saved unselected", true, pInbox, false},
		{"unknown, auto off", false, "ImapMail/a/NewTop", false},
		{"unknown, auto on", true, "ImapMail/a/NewTop", true},
		{"unknown under unticked parent, auto on", true, pProjectB, false},
		{"unknown excluded, auto on", true, "ImapMail/a/Spam", false},
		{"saved path, other case", true, "imapmail/a/inbox", false},
	}
	for _, tt := range tests {
		if got := savedConfig(tt.auto).ShouldScan(tt.path); got != tt.want {
			t.Errorf("%s: got %v, want %v", tt.name, got, tt.want)
		}
	}
}
