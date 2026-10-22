// Package mbox đọc file mbox của Thunderbird dạng stream và tách từng mail.
//
// Spec: docs/superpowers/specs/2026-10-17-mail-extraction-design.md mục 3. Mỗi lần chỉ giữ
// một mail trong RAM; không bao giờ ghi hay khóa file (mở bằng Open).
package mbox

import (
	"bufio"
	"bytes"
	"errors"
	"io"
)

const (
	DefaultMaxSize           = 64 << 20 // mail lớn hơn: chỉ giữ phần đầu
	DefaultKeepWhenTruncated = 1 << 20  // đủ cho phần header
)

// Message là một mail trong mbox.
type Message struct {
	Offset    int64  // vị trí dòng "From " của mail
	End       int64  // vị trí ngay sau mail (dòng "From " kế tiếp hoặc cuối file)
	Raw       []byte // header + body, không gồm dòng "From " và dòng trống cuối
	Truncated bool   // mail vượt MaxSize, Raw chỉ có KeepWhenTruncated byte đầu
}

// Reader tách mail. Dòng phân cách: dòng bắt đầu bằng "From " ở đầu dữ liệu hoặc ngay sau
// một dòng trống. Dòng ">From " (Thunderbird escape) là nội dung.
type Reader struct {
	// AllowLastAtEOF: trả cả mail kết thúc ở cuối file (không có dòng phân cách sau nó).
	// Mặc định false vì Thunderbird có thể đang ghi dở mail cuối.
	AllowLastAtEOF    bool
	MaxSize           int
	KeepWhenTruncated int

	br         *bufio.Reader
	pos        int64 // offset trong file của byte kế tiếp sẽ đọc
	prevBlank  bool  // dòng vừa đọc là dòng trống (hoặc đang ở đầu dữ liệu)
	pendingSep int64 // offset dòng "From " của mail kế tiếp đã đọc; -1 nếu chưa
}

// NewReader đọc từ r, coi byte đầu tiên của r là offset start trong file.
func NewReader(r io.Reader, start int64) *Reader {
	return &Reader{
		MaxSize:           DefaultMaxSize,
		KeepWhenTruncated: DefaultKeepWhenTruncated,
		br:                bufio.NewReaderSize(r, 64<<10),
		pos:               start,
		prevBlank:         true,
		pendingSep:        -1,
	}
}

// Next trả mail kế tiếp; io.EOF khi không còn mail hoàn chỉnh.
func (r *Reader) Next() (Message, error) {
	if r.pendingSep < 0 {
		for {
			line, start, err := r.readLine()
			if err != nil {
				return Message{}, err
			}
			if r.prevBlank && isSeparator(line) {
				r.pendingSep, r.prevBlank = start, false
				break
			}
			r.prevBlank = isBlank(line)
		}
	}

	msg := Message{Offset: r.pendingSep}
	r.pendingSep = -1
	var raw []byte
	for {
		line, start, err := r.readLine()
		if errors.Is(err, io.EOF) {
			if !r.AllowLastAtEOF {
				return Message{}, io.EOF
			}
			msg.End = r.pos
			break
		}
		if err != nil {
			return Message{}, err
		}
		if r.prevBlank && isSeparator(line) {
			r.pendingSep, r.prevBlank = start, false
			msg.End = start
			break
		}
		r.prevBlank = isBlank(line)
		raw = r.appendLimited(raw, line, &msg.Truncated)
	}
	if !msg.Truncated {
		raw = trimTrailingBlank(raw)
	}
	msg.Raw = raw
	return msg, nil
}

func (r *Reader) appendLimited(raw, line []byte, truncated *bool) []byte {
	if *truncated {
		return raw
	}
	if len(raw)+len(line) <= r.MaxSize {
		return append(raw, line...)
	}
	*truncated = true
	keep := r.KeepWhenTruncated
	if len(raw) >= keep {
		return raw[:keep]
	}
	return append(raw, line[:min(len(line), keep-len(raw))]...)
}

// readLine trả dòng (gồm ký tự xuống dòng) và offset đầu dòng; io.EOF khi hết dữ liệu.
// Dòng dài hơn MaxSize chỉ giữ MaxSize byte đầu (phần còn lại bỏ qua nhưng vẫn tính offset):
// file hỏng không có xuống dòng không được đọc hết vào RAM.
func (r *Reader) readLine() ([]byte, int64, error) {
	start := r.pos
	limit := max(r.MaxSize, len(fromPrefix))
	var line []byte
	for {
		chunk, err := r.br.ReadSlice('\n')
		r.pos += int64(len(chunk))
		if room := limit - len(line); room > 0 {
			line = append(line, chunk[:min(len(chunk), room)]...)
		}
		if errors.Is(err, bufio.ErrBufferFull) {
			continue
		}
		if err != nil && !(errors.Is(err, io.EOF) && r.pos > start) {
			return nil, start, err
		}
		return line, start, nil
	}
}

var fromPrefix = []byte("From ")

func isSeparator(line []byte) bool { return bytes.HasPrefix(line, fromPrefix) }

func isBlank(line []byte) bool {
	return len(line) == 1 && line[0] == '\n' || len(line) == 2 && line[0] == '\r' && line[1] == '\n'
}

func trimTrailingBlank(raw []byte) []byte {
	switch {
	case bytes.HasSuffix(raw, []byte("\r\n\r\n")):
		return raw[:len(raw)-2]
	case bytes.HasSuffix(raw, []byte("\n\n")):
		return raw[:len(raw)-1]
	}
	return raw
}
