package parse

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"pmmail/collector/internal/mbox"
	"pmmail/collector/internal/profile"
)

// Đọc toàn bộ profile tự sinh (data/, dữ liệu giả *.example.*) và đối chiếu với đáp án của
// generator (data/expected_threads.json).
func TestSyntheticProfile(t *testing.T) {
	dataDir := filepath.Join("..", "..", "..", "data")
	profileDir := filepath.Join(dataDir, "thunderbird-profile")
	if _, err := os.Stat(profileDir); err != nil {
		t.Skip("không có data/thunderbird-profile")
	}
	var expected map[string][]string
	b, err := os.ReadFile(filepath.Join(dataDir, "expected_threads.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, &expected); err != nil {
		t.Fatal(err)
	}
	wantIDs := map[string]bool{}
	wantTotal := 1 // generator chép thêm một mail (A-estimate 5) sang Local Folders/Inbox
	for _, ids := range expected {
		for _, id := range ids {
			wantIDs[strings.Trim(id, "<>")] = true
			wantTotal++
		}
	}

	accounts, err := profile.ScanAccounts(profileDir)
	if err != nil {
		t.Fatal(err)
	}
	var all []Mail
	var walk func(fs []*profile.Folder)
	walk = func(fs []*profile.Folder) {
		for _, f := range fs {
			if f.HasMbox {
				all = append(all, readFolder(t, filepath.Join(profileDir, filepath.FromSlash(f.Path)))...)
			}
			walk(f.Children)
		}
	}
	for _, a := range accounts {
		walk(a.Folders)
	}

	gotIDs := map[string]bool{}
	for _, m := range all {
		gotIDs[m.MessageID] = true
	}
	if len(all) != wantTotal || len(gotIDs) != len(wantIDs) {
		t.Fatalf("mails = %d (want %d), distinct ids = %d (want %d)", len(all), wantTotal, len(gotIDs), len(wantIDs))
	}
	for id := range wantIDs {
		if !gotIDs[id] {
			t.Errorf("missing %s", id)
		}
	}

	find := func(pred func(Mail) bool) *Mail {
		for i := range all {
			if pred(all[i]) {
				return &all[i]
			}
		}
		return nil
	}
	if find(func(m Mail) bool { return strings.Contains(m.Subject, "在庫管理システム改修のお見積り") }) == nil {
		t.Error("ISO-2022-JP subject not decoded")
	}
	if m := find(func(m Mail) bool { return m.From != nil && m.From.Addr == "sato@client-a.example.jp" }); m == nil ||
		!strings.Contains(m.BodyText, "佐藤") {
		t.Error("Shift_JIS body (sato) not decoded")
	}
	if find(func(m Mail) bool { return strings.Contains(m.BodyText, "田中です") }) == nil {
		t.Error("ISO-2022-JP body (tanaka) not decoded")
	}
	empty := 0
	for _, m := range all {
		if m.BodyText == "" {
			empty++
		}
	}
	if empty > 0 {
		t.Errorf("%d mails with empty body", empty)
	}
	if m := find(func(m Mail) bool { return strings.Contains(m.Subject, "議事録") && len(m.References) == 0 }); m == nil ||
		!m.BodyFromHTML || strings.Contains(m.BodyText, "<li>") {
		t.Errorf("HTML-only minutes: %+v", m)
	}
	if find(func(m Mail) bool {
		return len(m.Attachments) == 1 && m.Attachments[0].Filename == "estimate_A_v1.xlsx" && m.Attachments[0].Size > 0
	}) == nil {
		t.Error("attachment metadata missing")
	}
	if find(func(m Mail) bool { return m.Flags.Read }) == nil {
		t.Error("no mail with read flag (X-Mozilla-Status 0001)")
	}
}

func readFolder(t *testing.T, path string) []Mail {
	t.Helper()
	f, err := mbox.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	r := mbox.NewReader(f, 0)
	r.AllowLastAtEOF = true
	var out []Mail
	for {
		msg, err := r.Next()
		if errors.Is(err, io.EOF) {
			return out
		}
		if err != nil {
			t.Fatal(err)
		}
		res := Parse(msg.Raw, msg.Truncated)
		if !res.Expunged {
			out = append(out, res.Mail)
		}
	}
}
