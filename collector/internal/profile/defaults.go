package profile

import "strings"

// defaultExcludedNames là các folder mặc định bỏ tick (rác, nháp, mẫu, hộp thư đi),
// gồm cả tên tiếng Nhật và tiếng Việt mà server IMAP hay dùng.
var defaultExcludedNames = []string{
	"Trash", "Deleted Items", "Deleted Messages", "Junk", "Junk E-mail", "Spam",
	"Drafts", "Templates", "Unsent Messages", "Outbox",
	"ゴミ箱", "削除済みアイテム", "迷惑メール", "下書き", "送信トレイ",
	"Thùng rác", "Thư rác", "Thư nháp",
}

// DefaultExcluded cho biết tên folder có nằm trong danh sách mặc định bỏ tick không.
func DefaultExcluded(name string) bool {
	for _, n := range defaultExcludedNames {
		if strings.EqualFold(name, n) {
			return true
		}
	}
	return false
}

// ExcludedByDefault nhận đường dẫn tương đối của folder (vd. "ImapMail/<host>/Trash.sbd/Old")
// và trả true nếu chính folder đó hoặc một folder cha nằm trong danh sách mặc định bỏ tick.
// Hai đoạn đầu (ImapMail|Mail và tên tài khoản) không được xét.
func ExcludedByDefault(relPath string) bool {
	segs := strings.Split(relPath, "/")
	if len(segs) <= 2 {
		return false
	}
	for _, s := range segs[2:] {
		if DefaultExcluded(strings.TrimSuffix(s, ".sbd")) {
			return true
		}
	}
	return false
}
