"""Sinh profile Thunderbird GIẢ (toàn bộ nội dung tự bịa) để test collector/backend.

Chạy:  python data/generate_sample_profile.py
Kết quả: data/thunderbird-profile/ (mbox + .msf + .sbd như Thunderbird thật)
         data/expected_threads.json (đáp án gom thread để làm golden test)

Tất cả địa chỉ dùng tên miền dành riêng *.example.*; không có dữ liệu thật.
"""
import json
import mailbox
import random
import shutil
from datetime import datetime, timedelta, timezone
from email.charset import Charset, BASE64, QP
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import format_datetime, formataddr
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "thunderbird-profile"
IMAP = PROFILE / "ImapMail" / "imap.vn-outsource.example.com"
LOCAL = PROFILE / "Mail" / "Local Folders"
rng = random.Random(42)

JST = timezone(timedelta(hours=9))
ICT = timezone(timedelta(hours=7))
UTC = timezone.utc

# key: (tên hiển thị, email, charset, timezone, chữ ký)
PEOPLE = {
    "me": ("Trần Hiếu", "pm@vn-outsource.example.com", "utf-8", ICT,
           "--\nTrần Hiếu\nProject Manager | VN Outsource Co., Ltd.\nTel: +84-90-000-0001"),
    "an": ("Nguyễn Văn An", "an.nv@vn-outsource.example.com", "utf-8", ICT,
           "--\nNguyễn Văn An\nDev Lead | VN Outsource Co., Ltd."),
    "binh": ("Lê Thị Bình", "binh.lt@vn-outsource.example.com", "utf-8", ICT,
             "--\nLê Thị Bình\nQA Engineer | VN Outsource Co., Ltd."),
    "tanaka": ("田中 健二", "tanaka@client-a.example.jp", "iso-2022-jp", JST,
               "--\n株式会社サンプルA\nシステム開発部 田中 健二\nTEL: 03-0000-0001"),
    "sato": ("佐藤 由紀", "sato@client-a.example.jp", "shift_jis", JST,
             "--\n株式会社サンプルA\n品質管理課 佐藤 由紀\nTEL: 03-0000-0002"),
    "suzuki": ("鈴木 亮", "suzuki@client-b.example.co.jp", "utf-8", JST,
               "--\nサンプルB株式会社\n営業企画部 鈴木 亮\nTEL: 06-0000-0003"),
    "brian": ("Brian Carter", "brian@vendor-c.example.com", "utf-8", UTC,
              "--\nBrian Carter\nAccount Manager, Vendor C Inc."),
}

# Mỗi thread: id, thư mục của mail đến, ngày bắt đầu, danh sách mail.
# Mail: (key, parent_key|None, sender, body, extras)  extras: dict tùy chọn
#   cc=[...], attach=(tên, bytes), nolink=True (mất In-Reply-To/References kiểu Outlook), html=True
THREADS = [
    dict(id="A-estimate", folder="ProjectA", start=(2026, 9, 1), subject="【サンプルA】在庫管理システム改修のお見積りについて",
         to=["tanaka"], msgs=[
        ("1", None, "tanaka", "ご担当者様\n\nお世話になっております。田中です。\n在庫管理システムの改修について、概算のお見積りをお願いできますでしょうか。\n対象は入出庫画面と帳票出力です。希望納期は11月末です。", {"cc": ["sato"]}),
        ("2", "1", "me", "田中様\n\nお世話になっております。Trầnです。\n要件を確認いたしました。詳細見積りのため、帳票の仕様書を頂けますでしょうか。", {}),
        ("3", "2", "tanaka", "Trần様\n\n帳票の仕様書は明日お送りします。\n帳票は全部で12種類です。", {}),
        ("4", "3", "me", "田中様\n\n承知しました。仕様書の受領後、2営業日以内にお見積りを提出します。", {}),
        ("5", "4", "me", "田中様\n\nお見積りをお送りします。\n工数: 45人月、金額は別紙の通りです。ご確認をお願いいたします。", {"attach": ("estimate_A_v1.xlsx", b"PK\x03\x04 dummy xlsx for tests")}),
        ("6", "5", "tanaka", "Trần様\n\nお見積りありがとうございます。社内で確認し、来週回答します。", {}),
    ]),
    dict(id="A-bug", folder="ProjectA", start=(2026, 9, 8), subject="【障害報告】入庫登録画面でエラーが発生します",
         to=["tanaka"], msgs=[
        ("1", None, "sato", "お世話になっております。佐藤です。\n本番環境で入庫登録時に「E1024」エラーが発生しています。\n発生時刻は本日14:30頃です。至急ご確認をお願いします。", {"cc": ["tanaka"]}),
        ("2", "1", "me", "佐藤様\n\n申し訳ございません。直ちに調査を開始します。\nログを確認し、1時間以内に一次報告をします。", {"cc": ["an"]}),
        ("3", "2", "an", "Hiếu さん\n\nNguyên nhân sơ bộ: deadlock khi 2 batch cùng cập nhật bảng tồn kho. Em đang viết bản vá.", {"internal": True, "to": ["me"]}),
        ("4", "2", "me", "佐藤様\n\n一次報告です。原因はDBの排他制御と思われます。本日中に修正版を適用します。", {}),
        ("5", "4", "sato", "Trần様\n\n承知しました。適用後、再度テストを行います。", {}),
        ("6", "5", "me", "佐藤様\n\n修正版を適用しました。ご確認をお願いします。", {}),
        ("7", "6", "sato", "Trần様\n\n確認しました。エラーは再現しません。ありがとうございました。", {"nolink": True}),
    ]),
    dict(id="A-spec", folder="ProjectA", start=(2026, 9, 15), subject="帳票レイアウト仕様変更のご相談",
         to=["me"], msgs=[
        ("1", None, "tanaka", "Trần様\n\n帳票No.5のレイアウトを変更したく、ご相談です。\n添付の修正案をご確認ください。", {"attach": ("report5_layout.pdf", b"%PDF-1.4 dummy pdf for tests")}),
        ("2", "1", "me", "田中様\n\n修正案を確認しました。追加工数は約3人日です。ご承認いただけますか。", {}),
        ("3", "2", "tanaka", "Trần様\n\n3人日で問題ありません。進めてください。", {}),
        ("4", "3", "me", "田中様\n\n承知しました。着手します。完了は来週金曜の予定です。", {}),
        ("5", "4", "tanaka", "Trần様\n\nよろしくお願いします。", {"cc": ["sato"]}),
    ]),
    dict(id="B-kickoff", folder="ProjectB", start=(2026, 9, 10), subject="Project B キックオフ日程の調整",
         to=["me"], msgs=[
        ("1", None, "suzuki", "Trần様\n\nProject Bのキックオフ会議の日程調整をお願いします。\n候補: 9/24(木) 15時、9/25(金) 10時。", {}),
        ("2", "1", "me", "鈴木様\n\n9/25(金) 10時でお願いします。Teamsの招待をお送りします。", {}),
        ("3", "2", "suzuki", "Trần様\n\n承知しました。当日は弊社から3名参加します。", {}),
        ("4", "3", "me", "鈴木様\n\n当方からはPM、開発リード、QAの3名が参加します。", {}),
    ]),
    dict(id="B-invoice", folder="ProjectB", start=(2026, 10, 1), subject="9月分請求書の送付について",
         to=["suzuki"], msgs=[
        ("1", None, "me", "鈴木様\n\n9月分の請求書を添付します。ご確認をお願いします。", {"attach": ("invoice_2026_09.pdf", b"%PDF-1.4 dummy invoice")}),
        ("2", "1", "suzuki", "Trần様\n\n請求書を受領しました。金額に誤りがあるようです。単価をご確認ください。", {}),
        ("3", "2", "me", "鈴木様\n\n失礼しました。単価を修正した請求書を再送します。", {"attach": ("invoice_2026_09_rev1.pdf", b"%PDF-1.4 dummy invoice rev1")}),
    ]),
    dict(id="VN-sprint", folder="INBOX", start=(2026, 9, 3), subject="Sprint 12 planning",
         to=["me"], msgs=[
        ("1", None, "an", "Anh Hiếu,\n\nEm đề xuất scope Sprint 12: sửa lỗi tồn kho, làm màn hình xuất báo cáo số 5.\nTổng khoảng 38 story point.", {}),
        ("2", "1", "me", "An,\n\nOK. Báo cáo số 5 khách vừa đổi layout, em chờ spec mới nhé. Ưu tiên lỗi tồn kho trước.", {}),
        ("3", "2", "an", "Anh,\n\nEm nhận rồi. Em sẽ đưa báo cáo số 5 xuống cuối sprint.", {}),
        ("4", "3", "binh", "Anh Hiếu, anh An,\n\nBên QA cần build trước ngày 18 để kịp regression test.", {"cc": ["me"]}),
        ("5", "4", "me", "Bình, An,\n\nChốt: build ngày 17. Mọi người cố gắng nhé.", {}),
    ]),
    dict(id="VN-qa", folder="INBOX", start=(2026, 9, 20), subject="Báo cáo kết quả test regression Project A",
         to=["me"], msgs=[
        ("1", None, "binh", "Anh Hiếu,\n\nKết quả regression: 212 case, pass 205, fail 7.\nChi tiết 7 case lỗi em liệt kê bên dưới: #31, #58, #77, #90, #121, #150, #188.", {}),
        ("2", "1", "me", "Bình,\n\nEm gửi anh An xử lý giúp anh. Case nào ảnh hưởng release thì báo ngay.", {"cc": ["an"]}),
        ("3", "2", "an", "Anh,\n\nEm xem rồi: 5 case là lỗi dữ liệu test, 2 case (#77, #150) là lỗi thật. Em sửa trong hôm nay.", {}),
        ("4", "3", "binh", "Anh An,\n\nEm retest #77 và #150 sau khi anh deploy nhé.", {}),
    ]),
    dict(id="C-license", folder="Inbox", local=True, start=(2026, 9, 5), subject="Renewal of API license for 2027",
         to=["me"], msgs=[
        ("1", None, "brian", "Hi Hieu,\n\nYour API license expires on 2026-12-31. Please find our renewal quote for 2027.\nLet me know if you'd like to adjust the seat count.", {}),
        ("2", "1", "me", "Hi Brian,\n\nThanks. We plan to reduce seats from 20 to 15. Could you update the quote?", {}),
        ("3", "2", "brian", "Hi Hieu,\n\nSure, updated quote attached. 15 seats at the same unit price.", {"attach": ("quote_2027.pdf", b"%PDF-1.4 dummy quote")}),
        ("4", "3", "me", "Hi Brian,\n\nThanks, we will confirm by end of October.", {}),
    ]),
    dict(id="A-minutes", folder="ProjectA", start=(2026, 9, 29), subject="議事録: 定例会議 9/29",
         to=["me"], msgs=[
        ("1", None, "sato", "<html><body><p>各位</p><p>本日の定例会議の議事録です。</p><ul><li>入庫エラー: 修正済み、経過観察</li><li>帳票No.5: 来週金曜に納品</li><li>次回: 10/6 10時</li></ul><p>佐藤</p></body></html>", {"html": True}),
        ("2", "1", "me", "佐藤様\n\n議事録ありがとうございます。内容に相違ありません。", {}),
        ("3", "2", "sato", "Trần様\n\n確認ありがとうございます。", {}),
    ]),
]

# Mail lẻ (không thuộc thread): (id, folder, local?, sender, subject, body, ngày, extras)
STANDALONE = [
    ("S1", "INBOX", False, "suzuki", "年末年始の休業日のお知らせ", "関係各位\n\n弊社は12/29から1/3まで休業とさせていただきます。", (2026, 10, 2), {}),
    ("S2", "INBOX", False, "an", "[Auto] Out of office: Nguyễn Văn An", "Em đang nghỉ phép đến hết 10/9, vui lòng liên hệ anh Hiếu nếu gấp.", (2026, 10, 5), {"auto": True}),
    ("S3", "INBOX", False, "brian", "Vendor C product newsletter - October", "Hello,\n\nThis month: new SDK release, webinar on Oct 22.", (2026, 10, 3), {"newsletter": True}),
    ("S4", "INBOX", False, "me", "Thông báo lịch nghỉ lễ Quốc khánh", "Cả nhà,\n\nCông ty nghỉ lễ 2/9. Các deadline trong tuần đó dời lại.", (2026, 8, 28), {"to": ["an", "binh"], "sent": True}),
    ("S5", "ProjectA", False, "sato", "【ご依頼】テスト結果報告書のフォーマットについて", "Trần様\n\nテスト結果報告書は添付のフォーマットでご提出をお願いします。", (2026, 10, 7), {"attach": ("test_report_format.xlsx", b"PK\x03\x04 dummy format")}),
    ("S6", "INBOX", False, "binh", "Mời tham gia buổi chia sẻ về automation test", "Anh Hiếu,\n\nThứ 6 tuần này 16h có buổi chia sẻ về automation test. Mời anh tham gia.", (2026, 10, 8), {}),
    ("S7", "ProjectB", False, "suzuki", "Re: 見積書のご送付(Project B)", "Trần様\n\n前回の件ですが、Project Bの見積書を再度お送りいただけますか。", (2026, 10, 9), {"nolink": True}),
    ("S8", "INBOX", False, "tanaka", "来週の訪問について", "Trần様\n\n来週火曜に御社を訪問したいのですが、ご都合はいかがでしょうか。", (2026, 10, 12), {}),
    ("S9", "Inbox", True, "brian", "Webinar invitation: API best practices", "Hi,\n\nJoin us on Oct 22 for a webinar on API best practices.", (2026, 10, 10), {}),
]

# Mail trùng: cùng Message-ID xuất hiện ở 2 thư mục (kiểm tra chống trùng / message_ownership).
DUP_OF = [("A-estimate", "5", "Inbox", True)]  # (thread, key, folder, local)


def body_for_reply(text_of, parent, sender_key):
    pname, pmail, _, ptz, _ = PEOPLE[parent["sender"]]
    d = parent["date"].astimezone(PEOPLE[sender_key][3])
    if PEOPLE[parent["sender"]][2] in ("iso-2022-jp", "shift_jis") or PEOPLE[sender_key][2] != "utf-8":
        head = f"{d.year}年{d.month}月{d.day}日 {d:%H:%M} {pname} <{pmail}>:"
    elif PEOPLE[sender_key][1].endswith("vendor-c.example.com") or parent["sender"] == "brian" or sender_key == "brian":
        head = f"On {d:%a, %b %d, %Y at %H:%M}, {pname} <{pmail}> wrote:"
    else:
        head = f"Vào {d:%H:%M} ngày {d:%d/%m/%Y}, {pname} <{pmail}> đã viết:"
    quoted = "\n".join("> " + l if l else ">" for l in text_of[parent["id"]].splitlines())
    return head, quoted


def make_charset(name):
    if name == "iso-2022-jp":
        return Charset("iso-2022-jp")
    if name == "shift_jis":
        c = Charset("shift_jis")
        c.output_charset = "shift_jis"  # mặc định Python đổi thành iso-2022-jp
        c.body_encoding = BASE64
        return c
    c = Charset("utf-8")
    c.body_encoding = BASE64 if rng.random() < 0.5 else QP
    return c


def addr_list(keys):
    return ", ".join(addr(k) for k in keys)


def addr(k):
    name, mail, cs, _, _ = PEOPLE[k]
    return formataddr((str(Header(name, "utf-8")) if not name.isascii() else name, mail))


def build(m):
    """m: dict gồm id, sender, to, cc, subject, date, body, refs, attach, html, ..."""
    cs = PEOPLE[m["sender"]][2]
    if m.get("html"):
        payload = MIMEText(m["body"], "html", "utf-8")
    else:
        text = m["body"] + "\n\n" + PEOPLE[m["sender"]][4]
        if cs in ("iso-2022-jp", "shift_jis"):  # người Nhật gõ tên người Việt bằng katakana
            text = text.replace("Trần Hiếu", "チャン・ヒエウ").replace("Trần", "チャン")
        payload = MIMEText(text, "plain", _charset=None)
        payload.replace_header("Content-Type", "text/plain")
        try:
            text.encode(cs)
        except UnicodeEncodeError:  # có ký tự Việt (Trần...) mà charset JP không mã hóa được
            cs = "utf-8"
        # Bỏ CTE do MIMEText đặt sẵn: nếu còn, set_payload không mã hóa lại body
        # (header ghi base64 nhưng body là byte thô → mail hỏng).
        del payload["Content-Transfer-Encoding"]
        payload.set_payload(text, make_charset(cs))
    if m.get("attach"):
        root = MIMEMultipart("mixed")
        root.attach(payload)
        name, data = m["attach"]
        part = MIMEApplication(data, Name=name)
        part.add_header("Content-Disposition", "attachment", filename=(("utf-8", "", name)))
        root.attach(part)
    elif m.get("newsletter"):
        root = MIMEMultipart("alternative")
        root.attach(payload)
        root.attach(MIMEText("<html><body><h1>Newsletter</h1><p>" + m["body"].replace("\n", "<br>") + "</p></body></html>", "html", "utf-8"))
    else:
        root = payload
    root["Message-ID"] = m["id"]
    root["Date"] = format_datetime(m["date"])
    root["From"] = addr(m["sender"])
    root["To"] = addr_list(m["to"])
    if m.get("cc"):
        root["Cc"] = addr_list(m["cc"])
    try:
        m["subject"].encode(cs)
        root["Subject"] = Header(m["subject"], cs)
    except UnicodeEncodeError:
        root["Subject"] = Header(m["subject"], "utf-8")
    if m.get("refs") and not m.get("nolink"):
        root["In-Reply-To"] = m["refs"][-1]
        root["References"] = " ".join(m["refs"])
    if m.get("nolink") and m.get("thread_index"):
        root["Thread-Index"] = m["thread_index"]
    if m.get("auto"):
        root["Auto-Submitted"] = "auto-replied"
    root["X-Mozilla-Status"] = "0001" if m["folder_kind"] == "recv" else "0000"
    root["X-Mozilla-Status2"] = "00000000"
    return root


def mbox_from_line(msg):
    return msg


# ---------------------------------------------------------------------------
# Bulk: 70 thread, 1000 mail (mỗi thread 10–20 mail).
# Có reply (In-Reply-To/References), forward (Fwd:, References tới mail gốc),
# mail tiếng Nhật kèm bản dịch tiếng Việt, mail nội bộ viết tiếng Việt.
# ---------------------------------------------------------------------------
BULK_TOTAL = 1000
BULK_THREADS = 70
BULK_PROJECTS = ["Project-C", "Project-D", "Project-E", "Project-F", "Project-G"]
BULK_COMPANIES = [
    ("株式会社サンプルA", "client-a.example.jp"),
    ("サンプルB株式会社", "client-b.example.co.jp"),
    ("株式会社テストC", "client-c.example.jp"),
    ("サンプルD工業株式会社", "client-d.example.jp"),
]
BULK_JP_NAMES = [
    ("田中", "健二", "tanaka"), ("佐藤", "由紀", "sato"), ("鈴木", "亮", "suzuki"),
    ("高橋", "美咲", "takahashi"), ("伊藤", "翔太", "ito"), ("渡辺", "麻衣", "watanabe"),
    ("山本", "大輔", "yamamoto"), ("中村", "彩", "nakamura"), ("小林", "直樹", "kobayashi"),
    ("加藤", "玲奈", "kato"), ("吉田", "亮太", "yoshida"), ("山田", "美優", "yamada"),
]
BULK_VN = [("dung", "Phạm Quốc Dũng"), ("ha", "Hoàng Thu Hà"), ("khoa", "Đặng Minh Khoa"), ("lan", "Vũ Ngọc Lan")]
SCREENS = [("入庫登録", "nhập kho"), ("出庫実績", "thực xuất kho"), ("在庫照会", "tra cứu tồn kho"),
           ("帳票出力", "xuất báo cáo"), ("受注管理", "quản lý đơn hàng")]

# topic: subj [(jp, vi)], s [(jp, vi)] — {screen} {month} {days} {days2} {num} {fail} {code} {hour} {min} {ver}
TOPICS = {
    "quote": {
        "subj": [("在庫管理機能の追加開発お見積り依頼", "Yêu cầu báo giá phát triển thêm chức năng quản lý tồn kho"),
                 ("帳票出力機能の改修について", "Về việc sửa chức năng xuất báo cáo"),
                 ("新規API連携の概算見積もり", "Báo giá sơ bộ tích hợp API mới")],
        "s": [("対象は{screen}画面と関連する帳票です。", "Đối tượng là màn hình {screen} và các báo cáo liên quan."),
              ("希望納期は{month}月末を想定しております。", "Chúng tôi dự kiến thời hạn mong muốn là cuối tháng {month}."),
              ("工数の内訳（設計・実装・テスト）もご提示いただけますと助かります。", "Nếu được, xin anh/chị cho biết chi tiết công số (thiết kế, lập trình, kiểm thử)."),
              ("概算で構いませんので、{days}営業日以内にご回答いただけますか。", "Chỉ cần báo giá sơ bộ cũng được, anh/chị có thể trả lời trong {days} ngày làm việc không?")]},
    "incident": {
        "subj": [("【障害報告】{screen}でエラーが発生", "[Báo sự cố] Lỗi xảy ra tại {screen}"),
                 ("【至急】本番環境の処理遅延", "[Khẩn] Xử lý chậm trên môi trường production"),
                 ("【障害】夜間バッチの失敗について", "[Sự cố] Batch ban đêm bị lỗi")],
        "s": [("本番環境で{screen}の処理がタイムアウトしています。", "Trên môi trường production, xử lý tại {screen} bị timeout."),
              ("エラーコードは E{code} です。", "Mã lỗi là E{code}."),
              ("発生時刻は本日{hour}時{min}分頃です。", "Thời điểm xảy ra khoảng {hour} giờ {min} phút hôm nay."),
              ("至急ご確認をお願いします。", "Xin anh/chị vui lòng kiểm tra gấp.")]},
    "spec": {
        "subj": [("帳票レイアウトの仕様変更について", "Về thay đổi đặc tả bố cục báo cáo"),
                 ("{screen}画面の項目追加のご相談", "Trao đổi về việc thêm trường trên màn hình {screen}")],
        "s": [("変更内容は添付の修正案のとおりです。", "Nội dung thay đổi như bản chỉnh sửa đính kèm."),
              ("追加工数は約{days}人日と見込んでおります。", "Dự kiến công số tăng thêm khoảng {days} man-day."),
              ("ご承認いただけますでしょうか。", "Anh/chị có thể phê duyệt giúp không ạ?")]},
    "meeting": {
        "subj": [("定例会議の日程調整", "Điều chỉnh lịch họp định kỳ"),
                 ("キックオフ会議のご案内", "Thông báo họp kick-off")],
        "s": [("候補日は{month}月{days}日（木）15時、または{month}月{days2}日（金）10時です。", "Các ngày ứng viên: 15h thứ Năm ngày {days}/{month} hoặc 10h thứ Sáu ngày {days2}/{month}."),
              ("会議はTeamsで実施する予定です。", "Cuộc họp dự kiến tổ chức trên Teams."),
              ("ご都合の良い日時をご返信ください。", "Xin anh/chị phản hồi thời gian thuận tiện.")]},
    "invoice": {
        "subj": [("{month}月分の請求書送付について", "Về việc gửi hóa đơn tháng {month}"),
                 ("請求金額の確認のお願い", "Nhờ xác nhận số tiền hóa đơn")],
        "s": [("請求書を添付いたします。", "Chúng tôi gửi kèm hóa đơn."),
              ("金額に誤りがある可能性がございます。ご確認ください。", "Có thể số tiền có sai sót, xin anh/chị kiểm tra giúp."),
              ("入金期日は{month}月末です。", "Hạn thanh toán là cuối tháng {month}.")]},
    "accept": {
        "subj": [("検収書類のご送付", "Gửi hồ sơ nghiệm thu"),
                 ("納品物の受領確認", "Xác nhận nhận bàn giao")],
        "s": [("納品物一式をお送りします。", "Chúng tôi gửi bộ sản phẩm bàn giao."),
              ("検収は{days}営業日以内にお願いします。", "Mong anh/chị nghiệm thu trong {days} ngày làm việc."),
              ("問題がなければ検収書に押印をお願いします。", "Nếu không có vấn đề gì, xin anh/chị đóng dấu biên bản nghiệm thu.")]},
    "test": {
        "subj": [("結合テスト結果の報告", "Báo cáo kết quả kiểm thử tích hợp"),
                 ("回帰テストの実施結果", "Kết quả kiểm thử hồi quy")],
        "s": [("テストケース{num}件中、{fail}件が不合格でした。", "Trong {num} test case, {fail} case không đạt."),
              ("不合格の内容は添付のExcelをご確認ください。", "Chi tiết các case lỗi xem trong file Excel đính kèm."),
              ("次回リリースまでに修正を完了させます。", "Chúng tôi sẽ hoàn thành sửa lỗi trước bản release tiếp theo.")]},
    "contract": {
        "subj": [("API利用契約の更新について", "Về gia hạn hợp đồng sử dụng API"),
                 ("ライセンス更新のお見積り", "Báo giá gia hạn license")],
        "s": [("現行契約は{month}月末に満了します。", "Hợp đồng hiện tại hết hạn cuối tháng {month}."),
              ("ライセンス数を{num}件に変更したいと考えております。", "Chúng tôi muốn đổi số lượng license thành {num}."),
              ("更新後の見積書をご送付ください。", "Xin gửi báo giá sau khi gia hạn.")]},
    "progress": {
        "subj": [("週次進捗報告", "Báo cáo tiến độ hàng tuần"),
                 ("進捗遅延に関するご報告", "Báo cáo về việc chậm tiến độ")],
        "s": [("今週の完了率は{num}%です。", "Tỷ lệ hoàn thành tuần này là {num}%."),
              ("遅延の原因は仕様確認に時間を要したことです。", "Nguyên nhân chậm là mất nhiều thời gian xác nhận đặc tả."),
              ("来週中に巻き返しを図ります。", "Tuần sau chúng tôi sẽ cố gắng bắt kịp.")]},
    "tech": {
        "subj": [("API仕様の確認事項", "Các điểm cần xác nhận về đặc tả API"),
                 ("データ連携の方式について", "Về phương thức liên kết dữ liệu")],
        "s": [("レスポンスの項目名が仕様書と異なるようです。", "Tên trường trong response có vẻ khác với đặc tả."),
              ("認証トークンの有効期限を教えてください。", "Xin cho biết thời hạn hiệu lực của token xác thực."),
              ("文字コードはUTF-8で統一してよいでしょうか。", "Có thể thống nhất mã hóa ký tự là UTF-8 không?")]},
    "release": {
        "subj": [("リリース予定のご連絡", "Thông báo lịch release"),
                 ("バージョン{ver}のリリースについて", "Về release phiên bản {ver}")],
        "s": [("リリースは{month}月{days}日の予定です。", "Dự kiến release vào ngày {days} tháng {month}."),
              ("作業時間は夜間の22時から2時を予定しております。", "Thời gian thi công dự kiến từ 22h đến 2h sáng."),
              ("事前に影響範囲をご確認ください。", "Xin anh/chị vui lòng xác nhận phạm vi ảnh hưởng trước.")]},
}
TOPIC_ORDER = list(TOPICS)
OPEN_JP = ["お世話になっております。", "いつもお世話になっております。", "ご連絡ありがとうございます。"]
OPEN_VI = ["Cảm ơn anh/chị đã hỗ trợ.", "Cảm ơn anh/chị luôn hỗ trợ chúng tôi.", "Cảm ơn anh/chị đã liên hệ."]
REPLY_JP = ["承知いたしました。", "ご確認ありがとうございます。", "かしこまりました。確認して改めてご連絡いたします。"]
REPLY_VI = ["Đã rõ.", "Cảm ơn anh/chị đã xác nhận.", "Chúng tôi đã nhận được, sẽ kiểm tra và phản hồi lại."]
CLOSE_JP = ["よろしくお願いいたします。", "ご確認のほど、よろしくお願いいたします。"]
CLOSE_VI = ["Rất mong nhận được phản hồi của anh/chị.", "Mong anh/chị xem giúp."]
INTERNAL_VI = ["Em sẽ xử lý trong hôm nay.", "Anh cho em xin ý kiến nhé.", "Em đã cập nhật tiến độ trên sheet, anh xem giúp."]
FWD_NOTE_VI = ["Anh An xem giúp mail của khách này nhé.", "Em xem và trả lời khách giúp anh.", "Chuyển để team xử lý, cần phản hồi trong hôm nay."]


def _vals(rnd):
    vals = dict(month=rnd.randint(10, 12), days=rnd.randint(1, 28), days2=rnd.randint(1, 28),
                num=rnd.randint(20, 300), fail=rnd.randint(1, 12), code=rnd.randint(1000, 9999),
                hour=rnd.randint(8, 20), min=rnd.randint(0, 59),
                ver=f"{rnd.randint(1, 3)}.{rnd.randint(0, 9)}.{rnd.randint(0, 9)}")
    return rnd.randrange(len(SCREENS)), vals


def _fill(text, scr, vals, lang):  # lang: 0 = JP, 1 = VI
    return text.format(screen=SCREENS[scr][lang], **vals)


def _jp_vi_pair(topic, scr, vals, rnd, greet_jp, greet_vi, open_jp, open_vi):
    picks = rnd.sample(TOPICS[topic]["s"], 3)
    jp = "\n".join(_fill(p[0], scr, vals, 0) for p in picks)
    vi = "\n".join(_fill(p[1], scr, vals, 1) for p in picks)
    jp_text = f"{greet_jp}\n\n{open_jp}\n\n{jp}\n\n{rnd.choice(CLOSE_JP)}"
    vi_text = f"{greet_vi}\n\n{open_vi}\n\n{vi}\n\n{rnd.choice(CLOSE_VI)}"
    return jp_text, vi_text


def _quote(parent):
    d = parent["date"]
    name, mail = PEOPLE[parent["sender"]][0], PEOPLE[parent["sender"]][1]
    if parent["jp"]:
        head = f"{d.year}年{d.month}月{d.day}日 {d:%H:%M} {name} <{mail}>:"
        lines = parent["jp"].splitlines()[:4]
    else:
        head = f"Vào {d:%H:%M} ngày {d:%d/%m/%Y}, {name} <{mail}> đã viết:"
        lines = parent["vi"].splitlines()[:4]
    return "\n\n" + head + "\n" + "\n".join("> " + l if l else ">" for l in lines)


def add_bulk(put, path_for, expected):
    rnd = random.Random(20261017)
    for k, (sur, given, rom) in enumerate(BULK_JP_NAMES):
        comp_name, domain = BULK_COMPANIES[k % len(BULK_COMPANIES)]
        name = f"{sur} {given}"
        dept = ["システム開発部", "品質管理課", "営業企画部", "情報システム室"][k % 4]
        title = ["部長", "課長", "主任", "担当"][k % 4]
        cs = ["iso-2022-jp", "shift_jis", "utf-8"][k % 3]
        PEOPLE[f"jp{k:02d}"] = (name, f"{rom}{k:02d}@{domain}", cs, JST,
                                f"--\n{comp_name}\n{dept} {name} {title}\nTEL: 03-0000-{k:04d}")
    for key, name in BULK_VN:
        PEOPLE[key] = (name, f"{key}@vn-outsource.example.com", "utf-8", ICT,
                       f"--\n{name}\nVN Outsource Co., Ltd.")
    vn_keys = ["an", "binh"] + [key for key, _ in BULK_VN]
    jp_keys = [f"jp{k:02d}" for k in range(len(BULK_JP_NAMES))]

    lengths = [10] * BULK_THREADS
    extra = BULK_TOTAL - sum(lengths)
    while extra > 0:
        t = rnd.randrange(BULK_THREADS)
        if lengths[t] < 20:
            lengths[t] += 1
            extra -= 1

    for t in range(BULK_THREADS):
        tid = f"BULK-{t + 1:03d}"
        comp_keys = [k for i, k in enumerate(jp_keys) if i % len(BULK_COMPANIES) == t % len(BULK_COMPANIES)]
        main_client = comp_keys[t % len(comp_keys)]
        topic = TOPIC_ORDER[t % len(TOPIC_ORDER)]
        proj = BULK_PROJECTS[t % len(BULK_PROJECTS)]
        scr, vals = _vals(rnd)
        subj_jp_t, subj_vi_t = rnd.choice(TOPICS[topic]["subj"])
        subj_jp = _fill(subj_jp_t, scr, vals, 0)
        subj_vi = _fill(subj_vi_t, scr, vals, 1)
        cur = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=rnd.randint(0, 270), hours=rnd.randint(0, 9))
        expected[tid] = []
        msgs = []
        last_client = None
        for i in range(lengths[t]):
            kind = "client" if i == 0 else {1: "pm", 2: "client", 3: "vn", 0: "fwd"}[i % 4]
            prev = msgs[-1] if msgs else None
            cc = None
            jp = vi = ""
            if kind == "client":
                sender = main_client if i == 0 else rnd.choice(comp_keys)
                to = ["me"]
                if i == 0:
                    rest = [k for k in comp_keys if k != sender]
                    cc = rest[:1] or None
                jp, vi = _jp_vi_pair(topic, scr, vals, rnd, "Trần様", "Kính gửi anh Trần Hiếu,",
                                     rnd.choice(OPEN_JP), rnd.choice(OPEN_VI))
                parent = prev
                subject = subj_jp if i == 0 else "Re: " + subj_jp
            elif kind == "pm":
                parent = last_client
                sender = "me"
                to = [parent["sender"]]
                pname = PEOPLE[parent["sender"]][0]
                jp, vi = _jp_vi_pair(topic, scr, vals, rnd, f"{pname} 様", f"Kính gửi {pname},",
                                     rnd.choice(REPLY_JP), rnd.choice(REPLY_VI))
                subject = "Re: " + subj_jp
            elif kind == "vn":
                sender = rnd.choice(vn_keys)
                to = ["me"]
                parent = prev
                vi_sents = [_fill(p[1], scr, vals, 1) for p in rnd.sample(TOPICS[topic]["s"], 2)]
                vi = "Anh Hiếu,\n\n" + "\n".join(vi_sents) + "\n\n" + rnd.choice(INTERNAL_VI)
                subject = "Re: " + subj_vi
            else:  # fwd: PM chuyển mail khách cho team
                sender = "me"
                to = [rnd.choice(vn_keys)]
                parent = last_client
                fwd_hdr = (f"---------- Forwarded message ----------\n"
                           f"From: {PEOPLE[parent['sender']][0]} <{PEOPLE[parent['sender']][1]}>\n"
                           f"Date: {parent['date']:%Y-%m-%d %H:%M}\n"
                           f"Subject: {parent['subject']}\n\n")
                vi = rnd.choice(FWD_NOTE_VI) + "\n\n" + fwd_hdr + parent["text"]
                subject = "Fwd: " + parent["subject"]

            text = f"{jp}\n\n--- Bản dịch tiếng Việt ---\n{vi}" if jp else vi
            body = text + (_quote(parent) if kind in ("client", "pm") and parent else "")
            refs = (parent["refs"] + [parent["id"]]) if parent else []
            sender_tz = PEOPLE[sender][3]
            cur = cur + timedelta(hours=rnd.randint(1, 12))
            domain = PEOPLE[sender][1].split("@")[1]
            msg = dict(id=f"<{tid.lower()}.{i:02d}.{rnd.randint(10000, 99999)}@{domain}>", sender=sender,
                       to=to, cc=cc, subject=subject, date=cur.astimezone(sender_tz), body=body,
                       refs=refs, jp=jp, vi=vi, text=text, parent_kind=kind)
            if kind == "client" and i == 0 and rnd.random() < 0.2:
                msg["attach"] = (f"{tid}_quote.xlsx", b"PK\x03\x04 dummy spreadsheet for tests")
            msg["folder_kind"] = "sent" if sender == "me" else "recv"
            msgs.append(msg)
            if kind == "client":
                last_client = msg
            expected[tid].append(msg["id"])
            # Mail local: ghi vào Mail/Local Folders (không qua IMAP)
            put(LOCAL / ("Sent" if sender == "me" else proj), build(msg))


def main():
    if PROFILE.exists():
        # Thunderbird (hoặc collector) đang mở profile thì Windows không cho xóa: báo rõ thay vì xóa dở.
        for f in PROFILE.rglob("*"):
            if f.is_file():
                try:
                    with open(f, "ab"):
                        pass
                except PermissionError:
                    raise SystemExit(f"Không ghi được {f}: đóng Thunderbird/collector đang mở profile rồi chạy lại.")
        shutil.rmtree(PROFILE)
    for d in (IMAP / "INBOX.sbd", LOCAL / "Inbox.sbd"):
        d.mkdir(parents=True, exist_ok=True)

    boxes = {}  # đường dẫn mbox -> list message
    expected = {}
    text_of = {}
    by_id = {}

    def path_for(folder, local, sent):
        if local:
            return LOCAL / folder
        if sent:
            return IMAP / "Sent"
        if folder == "INBOX":
            return IMAP / "INBOX"
        return IMAP / "INBOX.sbd" / folder

    def put(path, msg):
        boxes.setdefault(path, []).append(msg)

    for t in THREADS:
        base = datetime(*t["start"], 9, 0, tzinfo=UTC)
        cur = base
        info = {}
        expected[t["id"]] = []
        for key, parent, sender, body, ex in t["msgs"]:
            cur = cur + timedelta(hours=rng.randint(2, 30), minutes=rng.randint(0, 59))
            mid = f"<{t['id'].lower()}.{key}.{rng.randint(10000, 99999)}@{PEOPLE[sender][1].split('@')[1]}>"
            p = info.get(parent) if parent else None
            subject = t["subject"] if not p else ("Re: " + t["subject"] if not p["subject"].startswith("Re:") else p["subject"])
            tz = PEOPLE[sender][3]
            if p:
                head, quoted = body_for_reply(text_of, p, sender)
                full = f"{body}\n\n{head}\n{quoted}" if not ex.get("html") else body
                recip = [p["sender"]] if p["sender"] != sender else p["to"]
                recip = ex.get("to") or recip
                # Giữ cc của thread đầu nếu có
            else:
                full = body
                recip = ex.get("to") or t["to"]
            if sender == "me" and not p:
                recip = t["to"]
            m = dict(id=mid, sender=sender, to=recip, cc=ex.get("cc"), subject=subject,
                     date=cur.astimezone(tz), body=full, html=ex.get("html"), attach=ex.get("attach"),
                     refs=(p["refs"] + [p["id"]]) if p else [], nolink=ex.get("nolink"),
                     thread_index="AQHa" + t["id"].encode().hex()[:16] if ex.get("nolink") else None)
            sent = sender == "me"
            m["folder_kind"] = "sent" if sent else "recv"
            m["thread_key"] = t["id"]
            text_of[mid] = full if not ex.get("html") else body
            info[key] = m
            by_id[(t["id"], key)] = m
            msg = build(m)
            put(path_for(t["folder"], t.get("local", False), sent), msg)
            expected[t["id"]].append(mid)

    for sid, folder, local, sender, subject, body, d, ex in STANDALONE:
        mid = f"<{sid.lower()}.{rng.randint(10000, 99999)}@{PEOPLE[sender][1].split('@')[1]}>"
        date = datetime(*d, rng.randint(1, 8), rng.randint(0, 59), tzinfo=UTC).astimezone(PEOPLE[sender][3])
        sent = ex.get("sent", False)
        m = dict(id=mid, sender=sender, to=ex.get("to") or ["me"], subject=subject, date=date, body=body,
                 attach=ex.get("attach"), nolink=ex.get("nolink"), auto=ex.get("auto"),
                 newsletter=ex.get("newsletter"), refs=[], folder_kind="sent" if sent else "recv",
                 thread_index="AQHb" + sid.encode().hex() if ex.get("nolink") else None)
        put(path_for(folder, local, sent), build(m))
        expected[sid] = [mid]

    add_bulk(put, path_for, expected)

    # Mail trùng Message-ID ở thư mục khác
    for tid, key, folder, local in DUP_OF:
        m = dict(by_id[(tid, key)])
        m["folder_kind"] = "recv"
        put(path_for(folder, local, False), build(m))

    total = 0
    for path, msgs in boxes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        mb = mailbox.mbox(str(path), create=True)
        for msg in msgs:
            mb.add(msg)
            total += 1
        mb.flush()
        mb.close()
        (path.parent / (path.name + ".msf")).write_text('// <!-- <mdb:mork:z v="1.4"/> -->\n', encoding="utf-8")
    # Thư mục rỗng như thật
    for p in (IMAP / "Drafts", IMAP / "Trash", LOCAL / "Trash"):
        p.touch()
        (p.parent / (p.name + ".msf")).write_text('// <!-- <mdb:mork:z v="1.4"/> -->\n', encoding="utf-8")

    (PROFILE / "profiles.ini").write_text(
        "[General]\nStartWithLastProfile=1\n\n[Profile0]\nName=sample\nIsRelative=1\nPath=.\nDefault=1\n", encoding="utf-8")
    (PROFILE / "prefs.js").write_text(
        '// Sample prefs (synthetic)\n'
        'user_pref("mail.accountmanager.accounts", "account1,account2");\n'
        'user_pref("mail.server.server1.hostname", "imap.vn-outsource.example.com");\n'
        'user_pref("mail.server.server1.type", "imap");\n'
        'user_pref("mail.server.server1.userName", "pm@vn-outsource.example.com");\n'
        'user_pref("mail.server.server2.hostname", "Local Folders");\n'
        'user_pref("mail.server.server2.type", "none");\n', encoding="utf-8")

    (ROOT / "expected_threads.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"mbox files: {len(boxes)}, messages written: {total}, threads/groups: {len(expected)}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--threads", type=int, default=BULK_THREADS, help="số thread bulk (mỗi thread 10–20 mail)")
    ap.add_argument("--total", type=int, default=BULK_TOTAL, help="tổng số mail bulk, trong khoảng threads*10 .. threads*20")
    args = ap.parse_args()
    if not args.threads * 10 <= args.total <= args.threads * 20:
        ap.error("total phải nằm trong khoảng threads*10 .. threads*20")
    BULK_THREADS, BULK_TOTAL = args.threads, args.total
    main()
