"""Sinh profile Thunderbird GIẢ nhỏ để thử cấu hình dự án (spec 25): 3 dự án "Test 1", "Test 2", "Test 3".

Chạy:  python data/generate_project_test_profile.py
Kết quả: data/project-test-profile/ (Local Folders: Inbox, Sent)
         data/expected_documents.json (mail có tài liệu đính kèm, dự án và phiên bản mong đợi)
         data/expected_projects.json (Message-ID -> tên dự án mong đợi, để đối chiếu)

Bộ lọc nên nhập trên màn hình Dự án (xem docs/superpowers/specs/2026-10-31-project-settings-design.md):
  Test 1: From/To/CC = @test1.example.jp                            Subject = (trống)
  Test 2: From/To/CC = @test2.example.jp                            Subject = 【Test2】
  Test 3: From/To/CC = @test2.example.jp, @test3.example.jp         Subject = 【Test3】

Không đụng data/thunderbird-profile (test khác đang đếm mail ở đó). Địa chỉ dùng *.example.*.
"""
import json
import mailbox
import shutil
from datetime import datetime, timedelta, timezone

import generate_sample_profile as g

ROOT = g.ROOT
PROFILE = ROOT / "project-test-profile"
LOCAL = PROFILE / "Mail" / "Local Folders"
UTC = timezone.utc

P = g.PEOPLE
for key, name, mail, cs, tz in [
    ("t1a", "Test1 Ichiro", "ichiro@test1.example.jp", "utf-8", g.JST),
    ("t1b", "Test1 Hanako", "hanako@test1.example.jp", "utf-8", g.JST),
    ("t2a", "Test2 Jiro", "jiro@test2.example.jp", "utf-8", g.JST),
    ("t3a", "Test3 Saburo", "saburo@test3.example.jp", "utf-8", g.JST),
    ("other", "Other Person", "someone@other.example.com", "utf-8", g.UTC),
]:
    P[key] = (name, mail, cs, tz, f"--\n{name}")

def DOC(name, version, text, pdf=False):
    """Mail đính kèm tài liệu giả. Nội dung file khác nhau theo version để thử so sánh bản cập nhật."""
    head = b"%PDF-1.4\n" if pdf else b"PK\x03\x04 "
    return dict(body=f"お世話になっております。\n{text}\n(Tài liệu {name}, bản {version})",
                attach=(name, head + f"dummy document {name} version {version}".encode()),
                doc=(name, version))


# Mỗi thread: (id, subject, [(người gửi, người nhận, cc[, extras]), ...], dự án mong đợi theo từng mail)
THREADS = [
    ("T1-A", "仕様確認のお願い", [("t1a", ["me"], None), ("me", ["t1a"], None), ("t1b", ["me"], None)], ["Test 1"] * 3),
    ("T1-B", "テスト環境の件", [("t1b", ["me"], ["an"]), ("me", ["t1b"], None)], ["Test 1"] * 2),
    ("T2-A", "【Test2】見積依頼", [("t2a", ["me"], None), ("me", ["t2a"], None), ("t2a", ["me"], None)], ["Test 2"] * 3),
    ("T2-B", "【Test2】リリース日程", [("t2a", ["me"], ["binh"]), ("an", ["me"], None)], ["Test 2"] + []),
    ("T3-A", "【Test3】追加開発の相談", [("t3a", ["me"], None), ("me", ["t3a"], None), ("t3a", ["me"], None)], ["Test 3"] * 3),
    ("T3-B", "【Test3】障害報告", [("t2a", ["me"], None), ("me", ["t2a"], None)], ["Test 3"] * 2),
    # Cả hai dự án: mail từ Test 1 có CC khách Test 3 và subject mang nhãn Test3 -> Test 1 (địa chỉ) và Test 3 (địa chỉ + subject)
    ("MULTI", "【Test3】合同キックオフ", [("t1a", ["me"], ["t3a"]), ("me", ["t1a", "t3a"], None)],
     ["Test 1, Test 3"] * 2),
    # Không dự án nào
    ("NONE-A", "Newsletter tháng 10", [("other", ["me"], None)], [""]),
    ("NONE-B", "【Test1】nội bộ, không có địa chỉ khách", [("an", ["me"], None), ("me", ["an"], None)], ["", ""]),
    ("NONE-C", "出張のご案内(Test2 khách nhưng thiếu nhãn)", [("t2a", ["me"], None)], [""]),
    # Khách gửi tài liệu (đính kèm), có bản cập nhật cùng tên file: để thử luồng lấy tài liệu về local.
    # Phần tử thứ 4 của mail: dict extras (body, attach=(tên file, bytes)).
    ("DOC-1", "設計書送付のご連絡", [
        ("t1a", ["me"], None, DOC("基本設計書.xlsx", 1, "初版をお送りします。")),
        ("me", ["t1a"], None),
        ("t1a", ["me"], None, DOC("基本設計書.xlsx", 2, "レビュー指摘を反映した更新版です。差し替えをお願いします。")),
    ], ["Test 1"] * 3),
    ("DOC-2", "【Test2】仕様書の更新版について", [
        ("t2a", ["me"], None, DOC("画面仕様書.pdf", 1, "仕様書を添付します。", pdf=True)),
        ("t2a", ["me"], ["binh"], DOC("画面仕様書.pdf", 2, "本日付で改訂しました。最新版は添付をご確認ください。", pdf=True)),
        ("me", ["t2a"], None),
    ], ["Test 2"] * 3),
    ("DOC-3", "【Test3】追加開発 要件定義書", [
        ("t3a", ["me"], None, DOC("要件定義書.xlsx", 1, "要件定義書を送付いたします。")),
        ("t3a", ["me"], None, DOC("テスト仕様書.xlsx", 1, "あわせてテスト仕様書も共有します。")),
    ], ["Test 3"] * 2),
    # Có tài liệu nhưng người gửi không thuộc dự án nào -> không được lấy về
    ("DOC-NONE", "資料送付", [("other", ["me"], None, DOC("unknown.xlsx", 1, "資料です。"))], [""]),
]
# T2-B: mail của an (nội bộ, subject 【Test2】 nhưng không có địa chỉ khách) không khớp Test 2
THREADS[3] = ("T2-B", THREADS[3][1], THREADS[3][2], ["Test 2", ""])


def main():
    if PROFILE.exists():
        shutil.rmtree(PROFILE)
    LOCAL.mkdir(parents=True)
    boxes, expected, docs = {}, {}, []
    cur = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    for tid, subject, msgs, projs in THREADS:
        refs, last = [], None
        for i, (sender, to, cc, *rest) in enumerate(msgs):
            ex = rest[0] if rest else {}
            cur += timedelta(hours=3, minutes=7)
            mid = f"<{tid.lower()}.{i}@{P[sender][1].split('@')[1]}>"
            m = dict(id=mid, sender=sender, to=to, cc=cc,
                     subject=subject if i == 0 else "Re: " + subject,
                     date=cur.astimezone(P[sender][3]),
                     body=ex.get("body", f"Nội dung giả {tid} #{i}."), attach=ex.get("attach"), refs=list(refs),
                     folder_kind="sent" if sender == "me" else "recv")
            path = LOCAL / ("Sent" if sender == "me" else "Inbox")
            boxes.setdefault(path, []).append(g.build(m))
            expected[mid] = projs[i]
            if "doc" in ex:
                docs.append(dict(message_id=mid, project=projs[i], filename=ex["doc"][0], version=ex["doc"][1]))
            refs.append(mid)
    total = 0
    for path, msgs in boxes.items():
        mb = mailbox.mbox(str(path), create=True)
        for msg in msgs:
            mb.add(msg)
            total += 1
        mb.flush()
        mb.close()
        (path.parent / (path.name + ".msf")).write_text('// <!-- <mdb:mork:z v="1.4"/> -->\n', encoding="utf-8")
    (PROFILE / "profiles.ini").write_text(
        "[General]\nStartWithLastProfile=1\n\n[Profile0]\nName=project-test\nIsRelative=1\nPath=.\nDefault=1\n",
        encoding="utf-8")
    (PROFILE / "prefs.js").write_text(
        '// Project test prefs (synthetic)\n'
        'user_pref("mail.accountmanager.accounts", "account1");\n'
        'user_pref("mail.server.server1.hostname", "Local Folders");\n'
        'user_pref("mail.server.server1.type", "none");\n', encoding="utf-8")
    (ROOT / "expected_projects.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "expected_documents.json").write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
    by_proj = {}
    for v in expected.values():
        by_proj[v or "(none)"] = by_proj.get(v or "(none)", 0) + 1
    print(f"mbox files: {len(boxes)}, messages: {total}, expected: {by_proj}")


if __name__ == "__main__":
    main()
