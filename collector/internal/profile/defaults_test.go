package profile

import "testing"

func TestDefaultExcluded(t *testing.T) {
	for _, n := range []string{"Trash", "trash", "Junk E-mail", "ゴミ箱", "Thùng rác", "thùng rác", "Unsent Messages"} {
		if !DefaultExcluded(n) {
			t.Errorf("DefaultExcluded(%q) = false, want true", n)
		}
	}
	for _, n := range []string{"INBOX", "Sent", "Trash2", "ProjectA"} {
		if DefaultExcluded(n) {
			t.Errorf("DefaultExcluded(%q) = true, want false", n)
		}
	}
}

func TestExcludedByDefault(t *testing.T) {
	for _, p := range []string{
		"ImapMail/a/Trash",
		"ImapMail/a/Trash.sbd/Old",
		"ImapMail/a/INBOX.sbd/Junk",
		"Mail/Local Folders/Drafts",
	} {
		if !ExcludedByDefault(p) {
			t.Errorf("ExcludedByDefault(%q) = false, want true", p)
		}
	}
	for _, p := range []string{
		"ImapMail/a/INBOX.sbd/ProjectA",
		"ImapMail/Trash/INBOX",
	} {
		if ExcludedByDefault(p) {
			t.Errorf("ExcludedByDefault(%q) = true, want false", p)
		}
	}
}
