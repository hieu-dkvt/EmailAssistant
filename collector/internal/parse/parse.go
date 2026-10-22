// Package parse giải mã một mail (MIME, charset kể cả tiếng Nhật, HTML → text) và lấy metadata
// theo đúng schema của Ingest API.
//
// Spec: docs/superpowers/specs/2026-10-17-mail-extraction-design.md mục 4. Không bao giờ log
// nội dung mail; không gửi nội dung file đính kèm (chỉ tên, kích thước, loại).
package parse

import (
	"bytes"
	"fmt"
	"io"
	"mime"
	"net/mail"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/inbucket/html2text"
	"github.com/jhillyerd/enmime/v2"
	"github.com/jhillyerd/enmime/v2/mediatype"
	"golang.org/x/net/html/charset"
	"golang.org/x/text/encoding"
	"golang.org/x/text/encoding/japanese"
)

const (
	// 0.3.0: part text có tên file không còn vào body_text; ngày bất thường bỏ hẳn.
	// 0.4.0: nhận diện file đính kèm từ header thô (RFC 2231 charset Nhật, Content-Disposition
	// sai/lạ), không lấy text trong message/rfc822, cắt khối uuencode.
	ParserVersion = "collector/0.4.0"
	MaxBodyRunes  = 1_000_000

	// Backend (Python datetime, PostgreSQL) chỉ nhận ngày trong khoảng này; ngoài khoảng → không có ngày.
	minYear, maxYear = 1970, 2100

	// Giới hạn của Ingest API (backend/app/schemas.py): vượt thì cả lô bị từ chối.
	maxIDLen          = 998
	maxSubjectRunes   = 10_000
	maxAddrLen        = 1000
	maxAddresses      = 1000
	maxReferences     = 500
	maxAttachments    = 200
	maxFilenameLen    = 1000
	maxContentTypeLen = 255
	maxThreadIndexLen = 1000
	maxTZOffsetMin    = 840
)

// Cờ trong header X-Mozilla-Status của Thunderbird.
const (
	mozRead     = 0x0001
	mozReplied  = 0x0002
	mozMarked   = 0x0004
	mozExpunged = 0x0008
)

type Address struct {
	Name string `json:"name,omitempty"`
	Addr string `json:"addr"`
}

type Attachment struct {
	Filename    string `json:"filename,omitempty"`
	Size        int    `json:"size"`
	ContentType string `json:"content_type,omitempty"`
}

type Flags struct {
	Read    bool `json:"read"`
	Replied bool `json:"replied"`
	Flagged bool `json:"flagged"`
}

// Mail là một mail theo schema IngestMessage của backend (khóa JSON trùng tên).
type Mail struct {
	MessageID       string       `json:"message_id,omitempty"`
	Subject         string       `json:"subject,omitempty"`
	From            *Address     `json:"from,omitempty"`
	To              []Address    `json:"to"`
	Cc              []Address    `json:"cc"`
	Date            *time.Time   `json:"date,omitempty"`
	DateTZOffsetMin *int         `json:"date_tz_offset_min,omitempty"`
	InReplyTo       string       `json:"in_reply_to,omitempty"`
	References      []string     `json:"references"`
	ThreadIndex     string       `json:"thread_index,omitempty"`
	BodyText        string       `json:"body_text"`
	BodyFromHTML    bool         `json:"body_from_html"`
	Attachments     []Attachment `json:"attachments"`
	MboxOffset      int64        `json:"mbox_offset"`
	Flags           Flags        `json:"flags"`
}

// Result của Parse. Expunged: mail đã xóa (chưa compact), bỏ qua. Degraded: chỉ đọc được header.
type Result struct {
	Mail     Mail
	Expunged bool
	Degraded bool
}

var (
	envParser   = enmime.NewParser(enmime.SetCustomParseMediaType(parseMediaType))
	wordDecoder = &mime.WordDecoder{CharsetReader: charsetReader}
)

// charsetAliases: nhãn charset hay gặp ở mail Nhật mà enmime/x/text không biết. Đổi về bộ mã
// gần nhất (CP932 ⊃ Shift_JIS; bản WHATWG của x/text đã có mở rộng NEC/IBM).
var charsetAliases = map[string]string{
	"ms932":               "shift_jis",
	"x-ms-cp932":          "shift_jis",
	"windows-932":         "shift_jis",
	"cp943":               "shift_jis",
	"cp943c":              "shift_jis",
	"ibm-943":             "shift_jis",
	"ibm943":              "shift_jis",
	"sjis-open":           "shift_jis",
	"sjis-win":            "shift_jis",
	"x-sjis-open":         "shift_jis",
	"iso-2022-jp-ms":      "iso-2022-jp",
	"iso-2022-jp-1":       "iso-2022-jp",
	"iso-2022-jp-2":       "iso-2022-jp",
	"iso-2022-jp-3":       "iso-2022-jp",
	"iso-2022-jp-2004":    "iso-2022-jp",
	"csiso2022jp2":        "iso-2022-jp",
	"x-windows-iso2022jp": "iso-2022-jp",
	"euc-jp-ms":           "euc-jp",
	"eucjp-ms":            "euc-jp",
	"eucjp-win":           "euc-jp",
	"eucjp-open":          "euc-jp",
	"cp51932":             "euc-jp",
}

func normalizeCharset(label string) string {
	l := strings.ToLower(strings.TrimSpace(label))
	if a, ok := charsetAliases[l]; ok {
		return a
	}
	return label
}

func parseMediaType(ctype string) (string, map[string]string, []string, error) {
	mtype, params, invalid, err := mediatype.Parse(ctype)
	if cs, ok := params["charset"]; ok {
		params["charset"] = normalizeCharset(cs)
	}
	return mtype, params, invalid, err
}

// Parse không bao giờ trả lỗi: mail hỏng vẫn cho ra Mail tốt nhất có thể (Degraded).
func Parse(raw []byte, truncated bool) Result {
	if !truncated {
		if env, err := envParser.ReadEnvelope(bytes.NewReader(raw)); err == nil {
			return fit(fromEnvelope(env))
		}
	}
	return fit(headersOnly(raw))
}

// decodeRaw đổi header gửi thẳng byte tiếng Nhật (không mã hóa RFC 2047) sang UTF-8:
// có ESC → ISO-2022-JP; không phải UTF-8 hợp lệ → thử Shift_JIS (CP932), rồi EUC-JP.
func decodeRaw(v string) string {
	if strings.Contains(v, "\x1b$") || strings.Contains(v, "\x1b(") {
		if s, err := japanese.ISO2022JP.NewDecoder().String(v); err == nil {
			return s
		}
	}
	if utf8.ValidString(v) {
		return v
	}
	for _, enc := range []encoding.Encoding{japanese.ShiftJIS, japanese.EUCJP} {
		if s, err := enc.NewDecoder().String(v); err == nil && !strings.ContainsRune(s, utf8.RuneError) {
			return s
		}
	}
	return v
}

func needsRawDecode(v string) bool {
	return strings.ContainsRune(v, '\x1b') || !utf8.ValidString(v)
}

// envHeader: header đã giải mã. Header có byte thô thì tự giải mã, còn lại dùng bộ giải mã
// RFC 2047 của enmime (chịu được nhiều kiểu mã hóa sai của mailer Nhật).
func envHeader(env *enmime.Envelope, k string) string {
	if raw := env.Root.Header.Get(k); needsRawDecode(raw) {
		return decodeHeader(raw)
	}
	return clean(env.GetHeader(k))
}

func decodeHeader(raw string) string {
	v := decodeRaw(raw)
	if d, err := wordDecoder.DecodeHeader(v); err == nil {
		v = d
	}
	return clean(v)
}

func newMail() Mail {
	return Mail{To: []Address{}, Cc: []Address{}, References: []string{}, Attachments: []Attachment{}}
}

func fromEnvelope(env *enmime.Envelope) Result {
	m := newMail()
	h := env.Root.Header
	m.MessageID = trimID(env.GetHeader("Message-ID"))
	m.Subject = envHeader(env, "Subject")
	if from := addressList(h.Get("From")); len(from) > 0 {
		m.From = &from[0]
	}
	m.To = addressList(h.Get("To"))
	m.Cc = addressList(h.Get("Cc"))
	setDate(&m, env.GetHeader("Date"))
	m.InReplyTo = trimID(env.GetHeader("In-Reply-To"))
	m.References = references(env.GetHeader("References"))
	m.ThreadIndex = clean(strings.TrimSpace(env.GetHeader("Thread-Index")))
	text, fromHTML := bodyOf(env)
	text, uu := stripUuencode(text)
	m.BodyText, m.BodyFromHTML = body(text), fromHTML
	if env.Root != nil {
		for _, p := range attachmentParts(env.Root) {
			m.Attachments = append(m.Attachments, Attachment{
				Filename:    clean(attachmentName(p)),
				Size:        len(p.Content),
				ContentType: clean(p.ContentType),
			})
		}
	}
	m.Attachments = append(m.Attachments, uu...)
	flags, expunged := mozillaStatus(env.GetHeader("X-Mozilla-Status"))
	m.Flags = flags
	return Result{Mail: m, Expunged: expunged}
}

// declaredAttachment quyết định "part này là file đính kèm" từ header thô, không từ
// FileName/Disposition của enmime: enmime và mime của Go bỏ tham số RFC 2231 có charset khác
// UTF-8 (tên file ISO-2022-JP, Shift_JIS của mailer Nhật) và bỏ Content-Disposition sai cú pháp.
// Là file đính kèm khi có tham số tên file (name, filename, file, kể cả name*, name*0*, giá trị
// rỗng) ở Content-Type hoặc Content-Disposition, hoặc có Content-Disposition mà không phải
// "inline" hợp lệ (attachment, sai cú pháp, kiểu lạ: RFC 2183 mục 2.8).
func declaredAttachment(p *enmime.Part) bool {
	if p.FileName != "" || hasNameParam(p.Header.Get("Content-Type")) {
		return true
	}
	cd := strings.TrimSpace(p.Header.Get("Content-Disposition"))
	if cd == "" {
		return false
	}
	if hasNameParam(cd) {
		return true
	}
	kind, _, err := mime.ParseMediaType(cd)
	return err != nil || kind != "inline"
}

func isMultipart(p *enmime.Part) bool { return strings.HasPrefix(p.ContentType, "multipart/") }

// walkLeaves gọi visit cho từng part không phải multipart, theo thứ tự trong mail. Chỉ đi
// xuống multipart/*: part khác có part con (message/rfc822 có boundary…) là một khối nguyên.
func walkLeaves(p *enmime.Part, visit func(*enmime.Part)) {
	if !isMultipart(p) {
		visit(p)
		return
	}
	for c := p.FirstChild; c != nil; c = c.NextSibling {
		walkLeaves(c, visit)
	}
}

// attachmentParts: các part là file đính kèm (chỉ gửi metadata), theo thứ tự trong mail.
func attachmentParts(root *enmime.Part) []*enmime.Part {
	var out []*enmime.Part
	walkLeaves(root, func(p *enmime.Part) {
		if p.FirstChild != nil || p.ContentType == "message/rfc822" || p.ContentType == "message/global" ||
			declaredAttachment(p) {
			out = append(out, p)
		}
	})
	return out
}

// bodyParts: các part ctype là thân mail: không có part con, không phải file đính kèm, và chỉ
// nằm dưới multipart/* (text trong mail chuyển tiếp không phải thân mail).
func bodyParts(root *enmime.Part, ctype string) []*enmime.Part {
	var out []*enmime.Part
	walkLeaves(root, func(p *enmime.Part) {
		if p.FirstChild == nil && p.ContentType == ctype && !declaredAttachment(p) {
			out = append(out, p)
		}
	})
	return out
}

// bodyOf lấy thân mail. Không dùng env.Text của enmime cho mail nhiều part: nó gộp cả part
// text có tên file vào thân mail, làm nội dung file đính kèm bị gửi lên server.
func bodyOf(env *enmime.Envelope) (string, bool) {
	root := env.Root
	if root == nil {
		return "", false
	}
	if root.FirstChild == nil && !isMultipart(root) {
		// Mail một part: enmime đã giải mã (kể cả charset trong <meta> của HTML).
		switch root.ContentType {
		case "", "text/plain", "text/html":
		default:
			return "", false
		}
		if declaredAttachment(root) {
			return "", false
		}
		for _, e := range env.Errors {
			if e.Name == enmime.ErrorPlainTextFromHTML {
				return env.Text, true
			}
		}
		return env.Text, false
	}
	parts := bodyParts(root, "text/plain")
	if root.ContentType == "multipart/alternative" && len(parts) > 1 {
		parts = parts[:1]
	}
	if len(parts) > 0 {
		texts := make([]string, len(parts))
		for i, p := range parts {
			texts[i] = string(p.Content)
		}
		return strings.Join(texts, "\n--\n"), false
	}
	html := bodyParts(root, "text/html")
	if len(html) == 0 {
		return "", false
	}
	text, err := html2text.FromString(string(html[0].Content))
	if err != nil {
		return "", true
	}
	return text, true
}

// headerParams tách tham số của header dạng `type; k=v; k2="v 2"` (khóa viết thường, giá trị đã
// bỏ ngoặc kép), kể cả tham số mà mime của Go bỏ qua. Dấu ; trong "..." không tách.
func headerParams(h string) map[string]string {
	params := map[string]string{}
	var segs []string
	quoted, start := false, 0
	for i := 0; i < len(h); i++ {
		switch c := h[i]; {
		case c == '\\' && quoted:
			i++
		case c == '"':
			quoted = !quoted
		case c == ';' && !quoted:
			segs = append(segs, h[start:i])
			start = i + 1
		}
	}
	segs = append(segs, h[start:])
	for _, seg := range segs[1:] {
		k, v, ok := strings.Cut(seg, "=")
		if !ok {
			continue
		}
		v = strings.TrimSpace(v)
		if len(v) >= 2 && v[0] == '"' && v[len(v)-1] == '"' {
			v = strings.ReplaceAll(v[1:len(v)-1], `\`, "")
		}
		params[strings.ToLower(strings.TrimSpace(k))] = v
	}
	return params
}

func hasNameParam(h string) bool {
	for k := range headerParams(h) {
		base, _, _ := strings.Cut(k, "*")
		if base == "name" || base == "filename" || base == "file" {
			return true
		}
	}
	return false
}

// attachmentName: tên file để làm metadata. Tự giải mã RFC 2231 (kể cả charset Nhật và dạng
// tách name*0*, name*1*) vì enmime bỏ hoặc giải mã sai các dạng này.
func attachmentName(p *enmime.Part) string {
	headers := []string{p.Header.Get("Content-Disposition"), p.Header.Get("Content-Type")}
	for _, h := range headers {
		params := headerParams(h)
		for _, base := range []string{"filename", "name"} {
			if s := extParam(params, base); s != "" {
				return s
			}
		}
	}
	if p.FileName != "" {
		return decodeRaw(p.FileName)
	}
	for _, h := range headers {
		params := headerParams(h)
		for _, base := range []string{"filename", "name", "file"} {
			if v := params[base]; v != "" {
				return decodeHeader(v)
			}
		}
	}
	return ""
}

// extParam giải mã tham số RFC 2231: base*=charset'lang'%XX… hoặc base*0*=…; base*1*=…
func extParam(params map[string]string, base string) string {
	if v, ok := params[base+"*"]; ok {
		cs, value := splitCharset(v)
		return toUTF8(cs, percentDecode(value))
	}
	var raw strings.Builder
	cs := ""
	for i := 0; ; i++ {
		if v, ok := params[fmt.Sprintf("%s*%d*", base, i)]; ok {
			if i == 0 {
				cs, v = splitCharset(v)
			}
			raw.WriteString(percentDecode(v))
		} else if v, ok := params[fmt.Sprintf("%s*%d", base, i)]; ok {
			raw.WriteString(v)
		} else {
			break
		}
	}
	if raw.Len() == 0 {
		return ""
	}
	return toUTF8(cs, raw.String())
}

func splitCharset(v string) (string, string) {
	if parts := strings.SplitN(v, "'", 3); len(parts) == 3 {
		return parts[0], parts[2]
	}
	return "", v
}

func percentDecode(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '%' && i+2 < len(s) {
			if n, err := strconv.ParseUint(s[i+1:i+3], 16, 8); err == nil {
				b.WriteByte(byte(n))
				i += 2
				continue
			}
		}
		b.WriteByte(s[i])
	}
	return b.String()
}

func toUTF8(cs, raw string) string {
	switch strings.ToLower(cs) {
	case "", "utf-8", "us-ascii":
		return decodeRaw(raw)
	}
	r, err := charsetReader(cs, strings.NewReader(raw))
	if err != nil {
		return decodeRaw(raw)
	}
	out, err := io.ReadAll(r)
	if err != nil {
		return decodeRaw(raw)
	}
	return string(out)
}

// stripUuencode cắt các khối uuencode ("begin 644 tên" rồi các dòng mã hóa, tới "end") khỏi
// thân mail: đó là file đính kèm kiểu cũ (Thunderbird hiển thị như file đính kèm). Chỉ giữ tên
// và kích thước. Khối bị cắt cụt (không có "end") vẫn được cắt tới dòng cuối có dạng uuencode.
func stripUuencode(text string) (string, []Attachment) {
	if !strings.Contains(text, "begin ") {
		return text, nil
	}
	lines := strings.SplitAfter(text, "\n")
	var out strings.Builder
	var atts []Attachment
	for i := 0; i < len(lines); i++ {
		name, ok := uuBegin(lines[i])
		if !ok {
			out.WriteString(lines[i])
			continue
		}
		size := 0
		j := i + 1
		for ; j < len(lines); j++ {
			l := strings.TrimRight(lines[j], "\r\n")
			if l == "end" {
				break
			}
			if !uuLine(l) {
				j--
				break
			}
			size += (int(l[0]) - 32) & 63
		}
		atts = append(atts, Attachment{Filename: name, Size: size, ContentType: "application/octet-stream"})
		i = j
	}
	return out.String(), atts
}

func uuBegin(line string) (string, bool) {
	rest, ok := strings.CutPrefix(strings.TrimRight(line, "\r\n"), "begin ")
	if !ok {
		return "", false
	}
	mode, name, ok := strings.Cut(rest, " ")
	name = strings.TrimSpace(name)
	if !ok || len(mode) < 3 || len(mode) > 4 || strings.Trim(mode, "01234567") != "" || name == "" {
		return "", false
	}
	return name, true
}

// uuLine: dòng dữ liệu uuencode: ký tự đầu là độ dài, mọi ký tự trong khoảng 0x20–0x60.
func uuLine(l string) bool {
	if l == "" {
		return false
	}
	for i := 0; i < len(l); i++ {
		if l[i] < 0x20 || l[i] > 0x60 {
			return false
		}
	}
	n := (int(l[0]) - 32) & 63
	return len(l)-1 >= (n+2)/3*4
}

// headersOnly: mail bị cắt (quá lớn) hoặc enmime không đọc được: chỉ lấy header.
func headersOnly(raw []byte) Result {
	m := newMail()
	msg, err := mail.ReadMessage(bytes.NewReader(raw))
	if err != nil {
		return Result{Mail: m, Degraded: true}
	}
	header := func(k string) string { return decodeHeader(msg.Header.Get(k)) }
	m.MessageID = trimID(header("Message-ID"))
	m.Subject = header("Subject")
	if from := addressList(msg.Header.Get("From")); len(from) > 0 {
		m.From = &from[0]
	}
	m.To = addressList(msg.Header.Get("To"))
	m.Cc = addressList(msg.Header.Get("Cc"))
	setDate(&m, msg.Header.Get("Date"))
	m.InReplyTo = trimID(header("In-Reply-To"))
	m.References = references(header("References"))
	m.ThreadIndex = strings.TrimSpace(header("Thread-Index"))
	flags, expunged := mozillaStatus(msg.Header.Get("X-Mozilla-Status"))
	m.Flags = flags
	return Result{Mail: m, Expunged: expunged, Degraded: true}
}

func charsetReader(label string, input io.Reader) (io.Reader, error) {
	return charset.NewReaderLabel(normalizeCharset(label), input)
}

// addressList phân tích header địa chỉ (giá trị thô). Cả danh sách lỗi thì phân tích từng mục;
// mục vẫn lỗi thì lấy phần <addr> hoặc từ có @ (spec trích xuất mail, mục 4.2).
func addressList(raw string) []Address {
	raw = strings.TrimSpace(decodeRaw(raw))
	if raw == "" {
		return []Address{}
	}
	if list, err := enmime.ParseAddressList(raw); err == nil {
		return addresses(list)
	}
	out := []Address{}
	for _, entry := range splitAddresses(raw) {
		if list, err := enmime.ParseAddressList(entry); err == nil {
			out = append(out, addresses(list)...)
		} else if a, ok := looseAddress(entry); ok {
			out = append(out, a)
		}
	}
	return out
}

// splitAddresses tách theo dấu phẩy và chấm phẩy nằm ngoài "...", <...> và (...).
func splitAddresses(s string) []string {
	var out []string
	var quoted bool
	depthAngle, depthParen, start := 0, 0, 0
	for i := 0; i < len(s); i++ {
		switch c := s[i]; {
		case c == '\\' && quoted:
			i++
		case c == '"':
			quoted = !quoted
		case quoted:
		case c == '<':
			depthAngle++
		case c == '>' && depthAngle > 0:
			depthAngle--
		case c == '(':
			depthParen++
		case c == ')' && depthParen > 0:
			depthParen--
		case (c == ',' || c == ';') && depthAngle == 0 && depthParen == 0:
			out = append(out, s[start:i])
			start = i + 1
		}
	}
	out = append(out, s[start:])
	return out
}

func looseAddress(entry string) (Address, bool) {
	entry = strings.TrimSpace(entry)
	if open, end := strings.LastIndexByte(entry, '<'), strings.LastIndexByte(entry, '>'); open >= 0 && end > open {
		addr := strings.TrimSpace(entry[open+1 : end])
		if !strings.Contains(addr, "@") || strings.ContainsAny(addr, " \t") {
			return Address{}, false
		}
		name := strings.Trim(strings.TrimSpace(entry[:open]), `"'`)
		if d, err := wordDecoder.DecodeHeader(name); err == nil {
			name = d
		}
		return Address{Name: strings.TrimSpace(clean(name)), Addr: strings.ToLower(clean(addr))}, true
	}
	for _, f := range strings.Fields(entry) {
		f = strings.Trim(f, `<>"'();:,`)
		if strings.Contains(f, "@") {
			return Address{Addr: strings.ToLower(clean(f))}, true
		}
	}
	return Address{}, false
}

func addresses(list []*mail.Address) []Address {
	out := []Address{}
	for _, a := range list {
		if a == nil {
			continue
		}
		out = append(out, Address{
			Name: strings.TrimSpace(clean(a.Name)),
			Addr: strings.ToLower(strings.TrimSpace(clean(a.Address))),
		})
	}
	return out
}

func setDate(m *Mail, v string) {
	t, err := mail.ParseDate(strings.TrimSpace(v))
	if err != nil {
		return
	}
	_, offset := t.Zone()
	mins := offset / 60
	if mins < -maxTZOffsetMin || mins > maxTZOffsetMin {
		return // múi giờ ngoài ±14h: header sai, không tin được ngày
	}
	for _, y := range []int{t.Year(), t.UTC().Year()} {
		if y < minYear || y > maxYear {
			return
		}
	}
	m.Date = &t
	m.DateTZOffsetMin = &mins
}

// trimID lấy ID trong cặp <...> đầu tiên (bỏ chú thích phía sau); không có <...> thì lấy cả chuỗi.
func trimID(v string) string {
	s := strings.TrimSpace(clean(v))
	if open := strings.IndexByte(s, '<'); open >= 0 {
		if end := strings.IndexByte(s[open:], '>'); end > 0 {
			return strings.TrimSpace(s[open+1 : open+end])
		}
	}
	s = strings.TrimPrefix(s, "<")
	s = strings.TrimSuffix(s, ">")
	return strings.TrimSpace(s)
}

func references(v string) []string {
	out := []string{}
	for _, f := range strings.Fields(clean(v)) {
		if id := trimID(f); id != "" {
			out = append(out, id)
		}
	}
	return out
}

func mozillaStatus(v string) (Flags, bool) {
	n, err := strconv.ParseUint(strings.TrimSpace(v), 16, 32)
	if err != nil {
		return Flags{}, false
	}
	return Flags{Read: n&mozRead != 0, Replied: n&mozReplied != 0, Flagged: n&mozMarked != 0},
		n&mozExpunged != 0
}

func body(text string) string {
	s := clean(strings.ReplaceAll(text, "\r\n", "\n"))
	return capRunes(strings.TrimRight(s, "\n\r\t "), MaxBodyRunes)
}

// clean bỏ NUL (PostgreSQL không nhận) và byte UTF-8 hỏng.
func clean(s string) string {
	return strings.ToValidUTF8(strings.ReplaceAll(s, "\x00", ""), string(utf8.RuneError))
}

func capRunes(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	i := 0
	for pos := range s {
		if i == n {
			return s[:pos]
		}
		i++
	}
	return s
}

func capBytes(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return capRunes(s, utf8.RuneCountInString(s[:n])-1)
}

// fit cắt các trường về trong giới hạn của Ingest API.
func fit(r Result) Result {
	m := &r.Mail
	if len(m.MessageID) > maxIDLen {
		m.MessageID = capBytes(m.MessageID, maxIDLen)
	}
	if len(m.InReplyTo) > maxIDLen {
		m.InReplyTo = ""
	}
	m.Subject = capRunes(m.Subject, maxSubjectRunes)
	m.ThreadIndex = capBytes(m.ThreadIndex, maxThreadIndexLen)
	refs := m.References[:0]
	for _, ref := range m.References {
		if len(ref) <= maxIDLen {
			refs = append(refs, ref)
		}
	}
	if len(refs) > maxReferences { // giữ các tham chiếu gần nhất (cuối danh sách)
		refs = refs[len(refs)-maxReferences:]
	}
	m.References = refs
	if m.From != nil {
		*m.From = fitAddr(*m.From)
	}
	m.To = fitAddrs(m.To)
	m.Cc = fitAddrs(m.Cc)
	if len(m.Attachments) > maxAttachments {
		m.Attachments = m.Attachments[:maxAttachments]
	}
	for i := range m.Attachments {
		m.Attachments[i].Filename = capBytes(m.Attachments[i].Filename, maxFilenameLen)
		m.Attachments[i].ContentType = capBytes(m.Attachments[i].ContentType, maxContentTypeLen)
	}
	return r
}

func fitAddr(a Address) Address {
	return Address{Name: capBytes(a.Name, maxAddrLen), Addr: capBytes(a.Addr, maxAddrLen)}
}

func fitAddrs(list []Address) []Address {
	if len(list) > maxAddresses {
		list = list[:maxAddresses]
	}
	for i := range list {
		list[i] = fitAddr(list[i])
	}
	return list
}
