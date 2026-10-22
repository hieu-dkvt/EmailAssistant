package parse

import (
	"encoding/base64"
	"strings"
	"testing"
	"unicode/utf8"
)

// Byte ISO-2022-JP viết tay theo bảng JIS X 0208 (không sinh bằng thư viện đang kiểm tra):
// 見 = 0x382B, 積 = 0x4051; hàng 13 (mở rộng NEC/CP932): ① = 0x2D21, Ⅲ = 0x2D37, ㈱ = 0x2D6A.
const (
	jisMitsumori = "\x1b$B8+@Q\x1b(B"
	jisNEC       = "\x1b$B-!-7-j\x1b(B"
	// Shift_JIS (CP932): 見 = 8CA9, 積 = 90CF, ㈱ = 878A (mở rộng NEC hàng 13).
	sjisMitsumoriKabu = "\x8c\xa9\x90\xcf\x87\x8a"
)

func mustParse(t *testing.T, raw string) Mail {
	t.Helper()
	r := Parse([]byte(strings.ReplaceAll(raw, "\n", "\r\n")), false)
	if r.Expunged {
		t.Fatalf("unexpected expunged")
	}
	return r.Mail
}

func TestJapaneseISO2022JP(t *testing.T) {
	subject := "=?ISO-2022-JP?B?" + base64.StdEncoding.EncodeToString([]byte(jisMitsumori)) + "?="
	m := mustParse(t, "Message-ID: < A1@client.example.jp >\n"+
		"From: =?ISO-2022-JP?B?"+base64.StdEncoding.EncodeToString([]byte(jisMitsumori))+"?= <Tanaka@Client.Example.jp>\n"+
		"To: pm@vn.example.com, \"B\" <b@vn.example.com>\n"+
		"Subject: "+subject+"\n"+
		"Date: Mon, 1 Sep 2026 10:00:00 +0900\n"+
		"MIME-Version: 1.0\n"+
		"Content-Type: text/plain; charset=ISO-2022-JP\n"+
		"Content-Transfer-Encoding: 7bit\n\n"+
		jisMitsumori+jisNEC+"\n")
	if m.MessageID != "A1@client.example.jp" {
		t.Errorf("message_id = %q", m.MessageID)
	}
	if m.Subject != "見積" {
		t.Errorf("subject = %q", m.Subject)
	}
	if m.From == nil || m.From.Name != "見積" || m.From.Addr != "tanaka@client.example.jp" {
		t.Errorf("from = %+v", m.From)
	}
	if len(m.To) != 2 || m.To[1] != (Address{Name: "B", Addr: "b@vn.example.com"}) {
		t.Errorf("to = %+v", m.To)
	}
	if m.BodyText != "見積①Ⅲ㈱" {
		t.Errorf("body = %q", m.BodyText)
	}
	if m.Date == nil || m.DateTZOffsetMin == nil || *m.DateTZOffsetMin != 540 ||
		m.Date.UTC().Format("2006-01-02T15:04") != "2026-09-01T01:00" {
		t.Errorf("date = %v tz = %v", m.Date, m.DateTZOffsetMin)
	}
	if m.BodyFromHTML {
		t.Errorf("body_from_html should be false")
	}
}

func TestShiftJISBodyWithCP932Extension(t *testing.T) {
	m := mustParse(t, "Subject: sjis\nMIME-Version: 1.0\n"+
		"Content-Type: text/plain; charset=Shift_JIS\nContent-Transfer-Encoding: 8bit\n\n"+
		sjisMitsumoriKabu+"\n")
	if m.BodyText != "見積㈱" {
		t.Errorf("body = %q", m.BodyText)
	}
}

func TestUTF8QuotedPrintableAndThreadHeaders(t *testing.T) {
	m := mustParse(t, "Message-ID: <r2@vn.example.com>\n"+
		"In-Reply-To: <r1@vn.example.com>\n"+
		"References: <r0@vn.example.com>\n <r1@vn.example.com>\n"+
		"Thread-Index: AQHaABCD\n"+
		"Subject: =?UTF-8?Q?B=C3=A1o_gi=C3=A1?=\n"+
		"Date: not a date\n"+
		"MIME-Version: 1.0\nContent-Type: text/plain; charset=UTF-8\n"+
		"Content-Transfer-Encoding: quoted-printable\n\n"+
		"Ti=E1=BA=BFng Vi=E1=BB=87t\n")
	if m.Subject != "Báo giá" || m.BodyText != "Tiếng Việt" {
		t.Errorf("subject = %q body = %q", m.Subject, m.BodyText)
	}
	if m.InReplyTo != "r1@vn.example.com" || len(m.References) != 2 || m.References[1] != "r1@vn.example.com" {
		t.Errorf("in_reply_to = %q references = %v", m.InReplyTo, m.References)
	}
	if m.ThreadIndex != "AQHaABCD" {
		t.Errorf("thread_index = %q", m.ThreadIndex)
	}
	if m.Date != nil || m.DateTZOffsetMin != nil {
		t.Errorf("invalid date should be nil, got %v", m.Date)
	}
}

func TestHTMLOnly(t *testing.T) {
	m := mustParse(t, "Subject: html\nMIME-Version: 1.0\nContent-Type: text/html; charset=UTF-8\n\n"+
		"<html><body><p>議事録</p><ul><li>入庫エラー</li></ul></body></html>\n")
	if !m.BodyFromHTML || !strings.Contains(m.BodyText, "議事録") || strings.Contains(m.BodyText, "<p>") {
		t.Errorf("body_from_html = %v body = %q", m.BodyFromHTML, m.BodyText)
	}
}

func TestAttachmentMetadataOnly(t *testing.T) {
	m := mustParse(t, "Subject: att\nMIME-Version: 1.0\n"+
		"Content-Type: multipart/mixed; boundary=\"XX\"\n\n"+
		"--XX\nContent-Type: text/plain; charset=UTF-8\n\nnội dung\n"+
		"--XX\nContent-Type: application/vnd.ms-excel\n"+
		"Content-Disposition: attachment; filename*=UTF-8''%E8%A6%8B%E7%A9%8D.xlsx\n"+
		"Content-Transfer-Encoding: base64\n\n"+
		base64.StdEncoding.EncodeToString([]byte("SECRET-ATTACHMENT-BYTES"))+"\n"+
		"--XX--\n")
	if len(m.Attachments) != 1 {
		t.Fatalf("attachments = %+v", m.Attachments)
	}
	a := m.Attachments[0]
	if a.Filename != "見積.xlsx" || a.Size != len("SECRET-ATTACHMENT-BYTES") || a.ContentType != "application/vnd.ms-excel" {
		t.Errorf("attachment = %+v", a)
	}
	if m.BodyText != "nội dung" {
		t.Errorf("body = %q", m.BodyText)
	}
}

// Part text có tên file là file đính kèm: nội dung không được vào body_text (NDA: không lưu
// nội dung file đính kèm), chỉ có metadata.
func TestTextAttachmentsNotInBody(t *testing.T) {
	m := mustParse(t, "Subject: att\nMIME-Version: 1.0\n"+
		"Content-Type: multipart/mixed; boundary=\"XX\"\n\n"+
		"--XX\nContent-Type: text/plain; charset=UTF-8\n\nthân mail\n"+
		"--XX\nContent-Type: text/plain; charset=UTF-8\nContent-Disposition: inline; filename=\"notes.txt\"\n\n"+
		"SECRET-INLINE-NOTES\n"+
		"--XX\nContent-Type: text/plain; name=\"log.txt\"\n\nSECRET-LOG\n"+
		"--XX\nContent-Type: text/html; name=\"report.html\"\n\n<p>SECRET-HTML</p>\n"+
		"--XX\nContent-Type: text/csv; charset=UTF-8\nContent-Disposition: attachment\n\nSECRET,CSV\n"+
		"--XX\nContent-Type: message/rfc822\n\nSubject: fwd\n\nSECRET-FORWARDED\n"+
		"--XX--\n")
	if m.BodyText != "thân mail" {
		t.Errorf("body = %q", m.BodyText)
	}
	var names []string
	for _, a := range m.Attachments {
		names = append(names, a.Filename+"|"+a.ContentType)
	}
	want := "notes.txt|text/plain log.txt|text/plain report.html|text/html |text/csv |message/rfc822"
	if strings.Join(names, " ") != want {
		t.Errorf("attachments = %q, want %q", strings.Join(names, " "), want)
	}
}

// enmime/mime của Go bỏ tham số RFC 2231 có charset khác UTF-8, và bỏ Content-Disposition sai
// cú pháp: quyết định "là file đính kèm" phải dựa trên header thô (security review lần 2).
func TestAttachmentDetectedFromRawHeaders(t *testing.T) {
	jisName := "%1B%24B8%2B%40Q%1B%28B.txt" // 見積.txt viết bằng ISO-2022-JP, mã hóa %
	cases := []struct {
		name, part, wantFile string
	}{
		{"rfc2231 iso-2022-jp name* in content-type",
			"Content-Type: text/plain; charset=ISO-2022-JP; name*=iso-2022-jp''" + jisName + "\n", "見積.txt"},
		{"rfc2231 shift_jis filename* inline",
			"Content-Type: text/plain\nContent-Disposition: inline; filename*=shift_jis''%8C%A9%90%CF.txt\n", "見積.txt"},
		{"rfc2231 continuation",
			"Content-Type: text/plain\nContent-Disposition: inline; filename*0*=iso-2022-jp''%1B%24B8%2B; filename*1*=%40Q%1B%28B.txt\n", "見積.txt"},
		{"html with rfc2231 name",
			"Content-Type: text/html; name*=iso-2022-jp''" + jisName + "\n", "見積.txt"},
		{"malformed disposition", "Content-Type: text/plain\nContent-Disposition: attachment filename=\"g.txt\"\n", ""},
		{"misspelled disposition", "Content-Type: text/plain\nContent-Disposition: attachement\n", ""},
		{"unknown disposition", "Content-Type: text/plain\nContent-Disposition: x-attached\n", ""},
		{"empty filename", "Content-Type: text/plain\nContent-Disposition: inline; filename=\"\"\n", ""},
	}
	for _, c := range cases {
		m := mustParse(t, "Subject: a\nMIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=\"XX\"\n\n"+
			"--XX\nContent-Type: text/plain; charset=UTF-8\n\nthân mail\n"+
			"--XX\n"+c.part+"\nSECRET-ATTACHMENT\n--XX--\n")
		if m.BodyText != "thân mail" {
			t.Errorf("%s: body = %q", c.name, m.BodyText)
		}
		if len(m.Attachments) != 1 || m.Attachments[0].Filename != c.wantFile {
			t.Errorf("%s: attachments = %+v", c.name, m.Attachments)
		}
	}
	// Mail một part có tên file kiểu RFC 2231.
	m := mustParse(t, "Subject: one\nMIME-Version: 1.0\nContent-Type: text/plain; charset=ISO-2022-JP; name*=iso-2022-jp''"+
		jisName+"\n\nSECRET-ATTACHMENT\n")
	if m.BodyText != "" || len(m.Attachments) != 1 || m.Attachments[0].Filename != "見積.txt" {
		t.Errorf("single part: body = %q attachments = %+v", m.BodyText, m.Attachments)
	}
	// Thân mail đánh dấu "inline" không tham số (Apple Mail…) vẫn là thân mail.
	m = mustParse(t, "Subject: a\nMIME-Version: 1.0\nContent-Type: text/plain; charset=UTF-8\nContent-Disposition: inline\n\nthân mail\n")
	if m.BodyText != "thân mail" || len(m.Attachments) != 0 {
		t.Errorf("inline body: body = %q attachments = %+v", m.BodyText, m.Attachments)
	}
}

// message/rfc822 có tham số boundary (sai chuẩn) bị enmime mở như multipart: nội dung mail
// chuyển tiếp không được vào thân mail.
func TestMessageRFC822WithBoundaryIsAttachment(t *testing.T) {
	m := mustParse(t, "Subject: fwd\nMIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=\"XX\"\n\n"+
		"--XX\nContent-Type: text/plain; charset=UTF-8\n\nthân mail\n"+
		"--XX\nContent-Type: message/rfc822; boundary=\"YY\"\n\n"+
		"--YY\nContent-Type: text/plain\n\nSECRET-FORWARDED\n--YY--\n"+
		"--XX--\n")
	if m.BodyText != "thân mail" || len(m.Attachments) != 1 || m.Attachments[0].ContentType != "message/rfc822" {
		t.Errorf("body = %q attachments = %+v", m.BodyText, m.Attachments)
	}
}

// Đính kèm uuencode trong text/plain (Thunderbird hiển thị như file đính kèm).
func TestUuencodedAttachmentStripped(t *testing.T) {
	m := mustParse(t, "Subject: uu\nContent-Type: text/plain\n\nxem file\n\n"+
		"begin 644 secret.txt\n"+
		"24T5#4D54+4%45$%#2$U%3E0*\n"+ // "SECRET-ATTACHMENT\n" (18 byte), sinh bằng binascii.b2a_uu
		"`\n"+
		"end\n\ncảm ơn\n")
	if strings.Contains(m.BodyText, "4T5#") || !strings.Contains(m.BodyText, "xem file") || !strings.Contains(m.BodyText, "cảm ơn") {
		t.Errorf("body = %q", m.BodyText)
	}
	if len(m.Attachments) != 1 || m.Attachments[0].Filename != "secret.txt" || m.Attachments[0].Size != 18 {
		t.Errorf("attachments = %+v", m.Attachments)
	}
}

func TestNamedHTMLIsNotBody(t *testing.T) {
	m := mustParse(t, "Subject: att\nMIME-Version: 1.0\n"+
		"Content-Type: multipart/mixed; boundary=\"XX\"\n\n"+
		"--XX\nContent-Type: text/html; charset=UTF-8\nContent-Disposition: inline; filename=\"r.html\"\n\n"+
		"<p>SECRET-HTML</p>\n"+
		"--XX--\n")
	if m.BodyText != "" || m.BodyFromHTML || len(m.Attachments) != 1 {
		t.Errorf("body = %q from_html = %v attachments = %+v", m.BodyText, m.BodyFromHTML, m.Attachments)
	}
	m = mustParse(t, "Subject: alt\nMIME-Version: 1.0\n"+
		"Content-Type: multipart/mixed; boundary=\"XX\"\n\n"+
		"--XX\nContent-Type: text/html; charset=UTF-8\n\n<p>議事録</p>\n"+
		"--XX\nContent-Type: text/html; name=\"r.html\"\n\n<p>SECRET-HTML</p>\n"+
		"--XX--\n")
	if m.BodyText != "議事録" || !m.BodyFromHTML {
		t.Errorf("body = %q from_html = %v", m.BodyText, m.BodyFromHTML)
	}
}

func TestSinglePartNamedTextIsAttachment(t *testing.T) {
	m := mustParse(t, "Subject: one\nMIME-Version: 1.0\n"+
		"Content-Type: text/plain; charset=UTF-8; name=\"log.txt\"\n\nSECRET-LOG\n")
	if m.BodyText != "" || len(m.Attachments) != 1 || m.Attachments[0].Filename != "log.txt" {
		t.Errorf("body = %q attachments = %+v", m.BodyText, m.Attachments)
	}
}

// Ngày mà backend không biểu diễn được (Python datetime) làm cả lô bị từ chối: bỏ hẳn.
func TestImplausibleDateDropped(t *testing.T) {
	for _, d := range []string{
		"Mon, 1 Jan 0001 00:00:00 +1400",
		"Sat, 1 Jan 0000 00:00:00 +0000",
		"Fri, 31 Dec 9999 23:59:59 -1200",
		"Mon, 1 Sep 2026 10:00:00 +2400",
		"Mon, 1 Sep 2026 10:00:00 -1500",
	} {
		m := mustParse(t, "Subject: d\nDate: "+d+"\nContent-Type: text/plain\n\nb\n")
		if m.Date != nil || m.DateTZOffsetMin != nil {
			t.Errorf("%s: date = %v tz = %v", d, m.Date, m.DateTZOffsetMin)
		}
	}
	m := mustParse(t, "Subject: d\nDate: Mon, 1 Sep 2026 10:00:00 -1200\nContent-Type: text/plain\n\nb\n")
	if m.Date == nil || *m.DateTZOffsetMin != -720 {
		t.Errorf("valid -1200 date dropped: %v", m.Date)
	}
}

// Một số mailer Nhật gửi header bằng byte JIS/Shift_JIS thô (không mã hóa RFC 2047).
func TestRawJapaneseHeaders(t *testing.T) {
	m := mustParse(t, "From: "+jisMitsumori+" <Tanaka@X.example>\n"+
		"To: "+sjisMitsumoriKabu+" <b@x.example>, c@x.example\n"+
		"Subject: "+jisMitsumori+jisNEC+"\n"+
		"Content-Type: text/plain; charset=ISO-2022-JP\n\nb\n")
	if m.Subject != "見積①Ⅲ㈱" {
		t.Errorf("subject = %q", m.Subject)
	}
	if m.From == nil || *m.From != (Address{Name: "見積", Addr: "tanaka@x.example"}) {
		t.Errorf("from = %+v", m.From)
	}
	if len(m.To) != 2 || m.To[0] != (Address{Name: "見積㈱", Addr: "b@x.example"}) || m.To[1].Addr != "c@x.example" {
		t.Errorf("to = %+v", m.To)
	}
}

// Một địa chỉ hỏng không được làm mất cả danh sách.
func TestMalformedAddressKeepsOthers(t *testing.T) {
	m := mustParse(t, "From: Tanaka [Sales] <t@x.example>\n"+
		"To: a@x.example, Bad Name <broken@@x.example>, \"C\" <c@x.example>\n"+
		"Cc: undisclosed-recipients:;\n"+
		"Content-Type: text/plain\n\nb\n")
	if m.From == nil || m.From.Addr != "t@x.example" || !strings.Contains(m.From.Name, "Tanaka") {
		t.Errorf("from = %+v", m.From)
	}
	var to []string
	for _, a := range m.To {
		to = append(to, a.Addr)
	}
	if got := strings.Join(to, " "); !strings.Contains(got, "a@x.example") || !strings.Contains(got, "c@x.example") {
		t.Errorf("to = %+v", m.To)
	}
	if len(m.Cc) != 0 {
		t.Errorf("cc = %+v", m.Cc)
	}
}

func TestCharsetAliases(t *testing.T) {
	for _, label := range []string{"MS932", "x-ms-cp932", "Windows-932"} {
		m := mustParse(t, "Subject: a\nMIME-Version: 1.0\n"+
			"Content-Type: text/plain; charset="+label+"\nContent-Transfer-Encoding: 8bit\n\n"+
			sjisMitsumoriKabu+"\n")
		if m.BodyText != "見積㈱" {
			t.Errorf("%s: body = %q", label, m.BodyText)
		}
	}
	m := mustParse(t, "Subject: a\nMIME-Version: 1.0\n"+
		"Content-Type: text/plain; charset=ISO-2022-JP-MS\n\n"+jisMitsumori+jisNEC+"\n")
	if m.BodyText != "見積①Ⅲ㈱" {
		t.Errorf("iso-2022-jp-ms: body = %q", m.BodyText)
	}
}

func TestMessageIDWithComment(t *testing.T) {
	m := mustParse(t, "Message-ID: <a1@x.example> (sent by gateway)\n"+
		"In-Reply-To: <r0@x.example> (Tanaka's message of Mon)\n"+
		"Content-Type: text/plain\n\nb\n")
	if m.MessageID != "a1@x.example" || m.InReplyTo != "r0@x.example" {
		t.Errorf("message_id = %q in_reply_to = %q", m.MessageID, m.InReplyTo)
	}
}

func TestMozillaStatusFlags(t *testing.T) {
	cases := map[string]Flags{
		"0001": {Read: true},
		"0003": {Read: true, Replied: true},
		"0005": {Read: true, Flagged: true},
		"0000": {},
	}
	for status, want := range cases {
		r := Parse([]byte("X-Mozilla-Status: "+status+"\r\nSubject: s\r\n\r\nb\r\n"), false)
		if r.Mail.Flags != want || r.Expunged {
			t.Errorf("%s: flags = %+v expunged = %v", status, r.Mail.Flags, r.Expunged)
		}
	}
	if r := Parse([]byte("X-Mozilla-Status: 0009\r\nSubject: s\r\n\r\nb\r\n"), false); !r.Expunged {
		t.Errorf("0009 should be expunged")
	}
}

func TestNULStrippedAndBodyCapped(t *testing.T) {
	m := mustParse(t, "Subject: a\x00b\nContent-Type: text/plain; charset=UTF-8\n\nx\x00y\n")
	if m.Subject != "ab" || m.BodyText != "xy" {
		t.Errorf("subject = %q body = %q", m.Subject, m.BodyText)
	}
	long := strings.Repeat("あ", MaxBodyRunes+10)
	m = mustParse(t, "Subject: l\nContent-Type: text/plain; charset=UTF-8\n\n"+long+"\n")
	if utf8.RuneCountInString(m.BodyText) != MaxBodyRunes || !utf8.ValidString(m.BodyText) {
		t.Errorf("body runes = %d", utf8.RuneCountInString(m.BodyText))
	}
}

func TestTruncatedUsesHeadersOnly(t *testing.T) {
	raw := "Message-ID: <big@x>\r\nSubject: =?UTF-8?Q?L=E1=BB=9Bn?=\r\nFrom: a@x.example\r\n" +
		"Content-Type: multipart/mixed; boundary=\"B\"\r\n\r\n--B\r\nContent-Type: text/plain\r\n\r\nbody cut he"
	r := Parse([]byte(raw), true)
	if !r.Degraded || r.Mail.MessageID != "big@x" || r.Mail.Subject != "Lớn" || r.Mail.BodyText != "" {
		t.Errorf("result = %+v", r)
	}
	if r.Mail.From == nil || r.Mail.From.Addr != "a@x.example" {
		t.Errorf("from = %+v", r.Mail.From)
	}
}

// Một trường vượt giới hạn của Ingest API làm cả lô bị từ chối (422): collector cắt trước khi gửi.
func TestFitsIngestLimits(t *testing.T) {
	var refs strings.Builder
	for i := range 600 {
		refs.WriteString(" <r" + strings.Repeat("x", i%3) + "@x>")
	}
	m := mustParse(t, "Message-ID: <"+strings.Repeat("m", 2000)+"@x>\n"+
		"References:"+refs.String()+"\n"+
		"Subject: "+strings.Repeat("s", 20000)+"\n"+
		"Date: Mon, 1 Sep 2026 10:00:00 +2500\n"+
		"Content-Type: text/plain\n\nb\n")
	if len(m.MessageID) > 998 || len(m.References) > 500 || utf8.RuneCountInString(m.Subject) > 10000 {
		t.Errorf("message_id len %d, references %d, subject runes %d",
			len(m.MessageID), len(m.References), utf8.RuneCountInString(m.Subject))
	}
	if m.DateTZOffsetMin != nil && (*m.DateTZOffsetMin > 840 || *m.DateTZOffsetMin < -840) {
		t.Errorf("tz offset %d out of range", *m.DateTZOffsetMin)
	}
}

func TestGarbageDoesNotPanic(t *testing.T) {
	r := Parse([]byte("\x00\x01\x02 not a mail at all"), false)
	if r.Mail.To == nil || r.Mail.References == nil || r.Mail.Attachments == nil {
		t.Errorf("slices must be non-nil for JSON []: %+v", r.Mail)
	}
}
