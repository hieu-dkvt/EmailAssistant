package syncer

import (
	"errors"
	"os"
	"path/filepath"
)

// ErrAlreadyRunning: đang có một lượt đồng bộ khác (trang setting hoặc lệnh sync).
var ErrAlreadyRunning = errors.New("đang có một lần đồng bộ khác chạy")

// DefaultLockPath trả <LocalAppData>\PMMailAssistant\collector\sync.lock.
func DefaultLockPath(localAppData string) string {
	return filepath.Join(localAppData, "PMMailAssistant", "collector", "sync.lock")
}

// AcquireLock giữ khóa độc quyền trong suốt lượt đồng bộ; release nhả khóa. Khóa do hệ điều
// hành giữ theo handle đang mở (lockFile): tiến trình chết thì khóa tự nhả, lượt đồng bộ dài
// bao lâu cũng không bị coi là khóa cũ.
func AcquireLock(path string) (release func(), err error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	return lockFile(path)
}
