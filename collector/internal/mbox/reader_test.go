package mbox

import (
	"bytes"
	"errors"
	"io"
	"strings"
	"testing"
)

// readAll đọc mọi mail từ start; trả về các mail và lỗi kết thúc (io.EOF nếu bình thường).
func readAll(t *testing.T, data string, start int64, allowLast bool, opts ...func(*Reader)) []Message {
	t.Helper()
	r := NewReader(strings.NewReader(data[start:]), start)
	r.AllowLastAtEOF = allowLast
	for _, o := range opts {
		o(r)
	}
	var out []Message
	for {
		m, err := r.Next()
		if errors.Is(err, io.EOF) {
			return out
		}
		if err != nil {
			t.Fatalf("Next: %v", err)
		}
		out = append(out, m)
	}
}

const msg1 = "From - Mon Sep 01 10:00:00 2026\nSubject: one\n\nbody one\n\n"
const msg2 = "From - Mon Sep 01 11:00:00 2026\nSubject: two\n\nbody two\n>From escaped\n\n"

func TestSplitTwoMessages(t *testing.T) {
	data := msg1 + msg2
	got := readAll(t, data, 0, true)
	if len(got) != 2 {
		t.Fatalf("got %d messages, want 2", len(got))
	}
	if got[0].Offset != 0 || got[0].End != int64(len(msg1)) || got[1].Offset != int64(len(msg1)) || got[1].End != int64(len(data)) {
		t.Errorf("offsets = %+v %+v", got[0], got[1])
	}
	if string(got[0].Raw) != "Subject: one\n\nbody one\n" {
		t.Errorf("raw[0] = %q", got[0].Raw)
	}
	if string(got[1].Raw) != "Subject: two\n\nbody two\n>From escaped\n" {
		t.Errorf("raw[1] = %q", got[1].Raw)
	}
}

func TestFromInsideBodyNotAfterBlankLineIsContent(t *testing.T) {
	data := "From - x\nSubject: a\n\nline\nFrom here on we talk\n\n" + msg2
	got := readAll(t, data, 0, true)
	if len(got) != 2 || !strings.Contains(string(got[0].Raw), "From here on we talk") {
		t.Fatalf("got %d messages: %+v", len(got), got)
	}
}

func TestCRLF(t *testing.T) {
	data := strings.ReplaceAll(msg1+msg2, "\n", "\r\n")
	got := readAll(t, data, 0, true)
	if len(got) != 2 {
		t.Fatalf("got %d messages, want 2", len(got))
	}
	if string(got[0].Raw) != "Subject: one\r\n\r\nbody one\r\n" {
		t.Errorf("raw[0] = %q", got[0].Raw)
	}
	if got[1].Offset != int64(len(strings.ReplaceAll(msg1, "\n", "\r\n"))) {
		t.Errorf("offset[1] = %d", got[1].Offset)
	}
}

func TestStartAtOffset(t *testing.T) {
	data := msg1 + msg2
	got := readAll(t, data, int64(len(msg1)), true)
	if len(got) != 1 || got[0].Offset != int64(len(msg1)) || !bytes.HasPrefix(got[0].Raw, []byte("Subject: two")) {
		t.Fatalf("got %+v", got)
	}
}

func TestStartNotAtSeparatorSkipsToNext(t *testing.T) {
	data := msg1 + msg2
	got := readAll(t, data, 5, true)
	if len(got) != 1 || got[0].Offset != int64(len(msg1)) {
		t.Fatalf("got %+v", got)
	}
}

func TestLastMessageAtEOF(t *testing.T) {
	data := msg1 + "From - y\nSubject: writing\n\npartial"
	if got := readAll(t, data, 0, false); len(got) != 1 {
		t.Errorf("AllowLastAtEOF=false: got %d messages, want 1", len(got))
	}
	got := readAll(t, data, 0, true)
	if len(got) != 2 || got[1].End != int64(len(data)) || string(got[1].Raw) != "Subject: writing\n\npartial" {
		t.Errorf("AllowLastAtEOF=true: got %+v", got)
	}
}

func TestEmptyAndGarbage(t *testing.T) {
	if got := readAll(t, "", 0, true); len(got) != 0 {
		t.Errorf("empty: %+v", got)
	}
	if got := readAll(t, "no separator here\n", 0, true); len(got) != 0 {
		t.Errorf("garbage: %+v", got)
	}
}

func TestOversizedMessageTruncated(t *testing.T) {
	big := "From - x\nSubject: big\n\n" + strings.Repeat("0123456789\n", 50) + "\n"
	data := big + msg2
	got := readAll(t, data, 0, true, func(r *Reader) { r.MaxSize = 100; r.KeepWhenTruncated = 40 })
	if len(got) != 2 {
		t.Fatalf("got %d messages", len(got))
	}
	if !got[0].Truncated || len(got[0].Raw) != 40 || got[0].End != int64(len(big)) {
		t.Errorf("truncated = %v len = %d end = %d", got[0].Truncated, len(got[0].Raw), got[0].End)
	}
	if got[1].Truncated || got[1].Offset != int64(len(big)) {
		t.Errorf("second = %+v", got[1])
	}
}

func TestVeryLongLine(t *testing.T) {
	long := strings.Repeat("x", 300_000)
	data := "From - x\nSubject: l\n\n" + long + "\n\n" + msg2
	got := readAll(t, data, 0, true)
	if len(got) != 2 || !strings.Contains(string(got[0].Raw), long) {
		t.Fatalf("got %d messages", len(got))
	}
}

// Một dòng không có xuống dòng dài hơn MaxSize (file hỏng) không được đọc hết vào RAM.
func TestLineLongerThanMaxSizeIsCapped(t *testing.T) {
	long := strings.Repeat("x", 10_000)
	data := "From - x\nSubject: l\n\n" + long + "\n\n" + msg2
	r := NewReader(strings.NewReader(data), 0)
	r.MaxSize, r.KeepWhenTruncated = 100, 40
	for range 3 { // "From - x", "Subject: l", dòng trống
		if _, _, err := r.readLine(); err != nil {
			t.Fatal(err)
		}
	}
	line, start, err := r.readLine()
	if err != nil || len(line) > 100 || start != int64(len("From - x\nSubject: l\n\n")) {
		t.Fatalf("len %d start %d err %v", len(line), start, err)
	}
	if r.pos != start+int64(len(long))+1 {
		t.Errorf("pos %d, want %d", r.pos, start+int64(len(long))+1)
	}
	got := readAll(t, data, 0, true, func(r *Reader) { r.MaxSize = 100; r.KeepWhenTruncated = 40 })
	if len(got) != 2 || !got[0].Truncated || got[1].Offset != int64(len(data)-len(msg2)) {
		t.Errorf("got %+v", got)
	}
}
