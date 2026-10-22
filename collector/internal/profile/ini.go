package profile

import (
	"bufio"
	"io"
	"strings"
)

// utf8BOM là BOM mà Notepad hay ghi vào đầu file .ini trên Windows.
var utf8BOM = string([]byte{0xEF, 0xBB, 0xBF})

type iniSection struct {
	name string
	keys map[string]string
}

// parseINI đọc file INI tối giản kiểu Thunderbird: [section], key=value,
// bỏ BOM, dòng trống, dòng chú thích (; hoặc #) và dòng nằm ngoài section.
func parseINI(r io.Reader) ([]iniSection, error) {
	var secs []iniSection
	sc := bufio.NewScanner(r)
	first := true
	for sc.Scan() {
		line := sc.Text()
		if first {
			line = strings.TrimPrefix(line, utf8BOM)
			first = false
		}
		line = strings.TrimSpace(line)
		if line == "" || line[0] == ';' || line[0] == '#' {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			secs = append(secs, iniSection{name: line[1 : len(line)-1], keys: map[string]string{}})
			continue
		}
		if len(secs) == 0 {
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		secs[len(secs)-1].keys[strings.TrimSpace(k)] = strings.TrimSpace(v)
	}
	return secs, sc.Err()
}
