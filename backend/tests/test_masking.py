"""Che thông tin trước khi gửi AI (spec 20). Dữ liệu giả *.example.*, không có mail thật."""

import time
import unicodedata

import pytest

from app.ai.masking import Masker, nfkc_map, pattern_spans, resolve
from app.ai.names import Entity, companies_in, entities_from_display, fold_latin
from app.ml_client import NerEntity


def kinds(text: str) -> set[tuple[str, str]]:
    """Như Masker: mẫu chạy trên bản NFKC, vị trí đổi về text gốc."""
    norm, starts, ends = nfkc_map(text)
    return {(text[starts[s.start] : ends[s.end - 1]], s.kind) for s in resolve(pattern_spans(norm))}


# --- mẫu ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("連絡先: tanaka@client-a.example.jp まで", ("tanaka@client-a.example.jp", "EMAIL")),
        (
            "詳細は https://portal.client-a.example.jp/spec?id=5 を参照",
            ("https://portal.client-a.example.jp/spec?id=5", "URL"),
        ),
        ("Xem tại www.vendor-c.example.com nhé", ("www.vendor-c.example.com", "URL")),
        ("サイト client-b.example.co.jp の件", ("client-b.example.co.jp", "URL")),
        ("TEL: 03-0000-0001", ("03-0000-0001", "PHONE")),
        ("Tel: +84-90-000-0001", ("+84-90-000-0001", "PHONE")),
        ("電話 ０３－１２３４－５６７８ まで", ("０３－１２３４－５６７８", "PHONE")),
        ("FAX (03) 1234-5678", ("(03) 1234-5678", "PHONE")),
        (
            "〒100-0001 東京都千代田区千代田1-1\n次の行",
            ("〒100-0001 東京都千代田区千代田1-1", "ADDRESS"),
        ),
        ("住所: 大阪府大阪市北区梅田1-2-3", ("大阪府大阪市北区梅田1-2-3", "ADDRESS")),
        ("Địa chỉ: 12 Đường Số 1, Quận 1", ("12 Đường Số 1, Quận 1", "ADDRESS")),
        ("金額は1,250,000円です", ("1,250,000円", "MONEY")),
        ("概算で350万円になります", ("350万円", "MONEY")),
        ("単価 ¥12,000", ("¥12,000", "MONEY")),
        ("Total USD 5,000", ("USD 5,000", "MONEY")),
        ("Giá 50 triệu đồng", ("50 triệu đồng", "MONEY")),
        ("Phí 500.000đ mỗi tháng", ("500.000đ", "MONEY")),
        ("契約番号: CT-2026-0042 の件", ("CT-2026-0042", "CONTRACT")),
        ("Số hợp đồng: HD/2026/17", ("HD/2026/17", "CONTRACT")),
        ("PO No. 7788-A", ("7788-A", "CONTRACT")),
        ("株式会社サンプルAの田中です", ("株式会社サンプルA", "ORG")),
        ("サンプルB株式会社 営業企画部", ("サンプルB株式会社", "ORG")),
        ("㈱テストC からの連絡", ("㈱テストC", "ORG")),
        ("Brian Carter, Vendor C Inc. sent", ("Vendor C Inc.", "ORG")),
        ("Hợp đồng với Công ty TNHH Mẫu Thử", ("Công ty TNHH Mẫu Thử", "ORG")),
        ("田中様\n\nお世話になっております。", ("田中", "PERSON")),
        ("山本部長にもご確認ください", ("山本", "PERSON")),
        ("Anh Hiếu, em gửi báo cáo", ("Hiếu", "PERSON")),
        ("Hi Brian,\n\nThanks.", ("Brian", "PERSON")),
        ("Trần様\n\n承知しました。", ("Trần", "PERSON")),
        ("Thanks Tanaka-san for the update", ("Tanaka", "PERSON")),
    ],
)
def test_patterns(text: str, expected: tuple[str, str]) -> None:
    assert expected in kinds(text)


@pytest.mark.parametrize(
    "text",
    [
        "リリースは2026-10-19 22:00開始です",
        "日付 2026/10/19 と 2026.10.20",
        "テストケース212件中、7件が不合格",
        "工数: 45人月、追加3人日",
        "バージョン1.2.3をリリース",
        "エラーコードは E1024 です",
        "皆様、各位、お客様、ご担当者様",
        "estimate_A_v1.xlsx を添付",
        "Bình thường thì không sao",
    ],
)
def test_no_false_positive(text: str) -> None:
    found = kinds(text)
    assert not any(k in ("PHONE", "MONEY", "PERSON", "URL") for _, k in found), found


# --- tên hiển thị ---


def surfaces(name: str | None, addr: str | None = None) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for e in entities_from_display(name, addr):
        out.setdefault(e.kind, set()).update(e.surfaces)
    return out


def test_display_japanese_name() -> None:
    assert surfaces("田中 健二", "tanaka@client-a.example.jp") == {
        "EMAIL": {"tanaka@client-a.example.jp"},
        "PERSON": {"田中 健二", "田中健二", "田中", "健二"},
    }


def test_display_vietnamese_name() -> None:
    got = surfaces("Nguyễn Văn An")["PERSON"]
    assert {"Nguyễn Văn An", "Nguyễn", "An", "Nguyen Van An", "Nguyen"} <= got
    assert "Văn" not in got


def test_display_english_name() -> None:
    assert surfaces("Brian Carter")["PERSON"] >= {"Brian Carter", "Brian", "Carter"}


def test_display_company_and_person() -> None:
    got = surfaces("株式会社サンプルA 田中")
    assert got["ORG"] == {"株式会社サンプルA", "サンプルA"}
    assert got["PERSON"] == {"田中"}


def test_display_company_department_person() -> None:
    got = surfaces("サンプルB株式会社 営業部 鈴木 亮")
    assert got["ORG"] == {"サンプルB株式会社", "サンプルB"}
    assert "鈴木" in got["PERSON"] and not any("営業" in s for s in got["PERSON"])


@pytest.mark.parametrize("name", ["noreply", "Support Team", "営業部", "info"])
def test_display_generic(name: str) -> None:
    assert "PERSON" not in surfaces(name)


def test_fold_latin() -> None:
    assert fold_latin("Trần Hiếu Đặng") == "Tran Hieu Dang"


# --- Masker ---

MAIL = (
    "田中様\n\nお世話になっております。株式会社サンプルAの田中健二です。\n"
    "お見積りの金額は1,250,000円、契約番号: CT-2026-0042 です。\n"
    "ご不明点は tanaka@client-a.example.jp または 03-0000-0001 までご連絡ください。\n"
    "〒100-0001 東京都千代田区千代田1-1\n"
    "佐藤さんにもcc済みです。"
)
SECRETS = [
    "田中",
    "健二",
    "サンプルA",
    "1,250,000",
    "CT-2026-0042",
    "tanaka@",
    "client-a.example.jp",
    "03-0000-0001",
    "千代田",
    "佐藤",
]


async def no_ner(texts: list[str]) -> list[list[NerEntity]]:
    return [[] for _ in texts]


async def test_mask_full_mail_hides_every_secret() -> None:
    m = Masker(no_ner)
    m.add_entities(entities_from_display("田中 健二", "tanaka@client-a.example.jp"))
    (masked,) = await m.mask([MAIL])
    for secret in SECRETS:
        assert secret not in masked, secret
    assert "[PERSON_1]様" in masked
    assert "お見積り" in masked  # nội dung nghiệp vụ giữ nguyên
    assert set(m.counts) == {"PERSON", "ORG", "MONEY", "CONTRACT", "EMAIL", "PHONE", "ADDRESS"}


async def test_same_entity_same_label_across_texts_and_variants() -> None:
    m = Masker(no_ner)
    m.add_entities(entities_from_display("田中 健二", None))
    q, src = await m.mask(["田中さんは何と言っていましたか", "差出人: 田中 健二\n田中健二です。"])
    assert q == "[PERSON_1]さんは何と言っていましたか"
    assert src == "差出人: [PERSON_1]\n[PERSON_1]です。"
    assert m.unmask("[PERSON_1]さんは承認しました") == "田中 健二さんは承認しました"


async def test_ner_entities_propagate_to_other_texts() -> None:
    async def ner(texts: list[str]) -> list[list[NerEntity]]:
        out: list[list[NerEntity]] = []
        for t in texts:
            i = t.find("高橋美咲")
            out.append([NerEntity(i, i + 4, "PERSON")] if i >= 0 and "担当" in t else [])
        return out

    m = Masker(ner)
    a, b = await m.mask(["高橋美咲の件です", "担当は高橋美咲です"])
    assert "高橋" not in a and "高橋" not in b
    assert a.startswith("[PERSON_1]") and "[PERSON_1]" in b


async def test_ner_failure_raises() -> None:
    async def broken(texts: list[str]) -> list[list[NerEntity]]:
        raise RuntimeError("ml-service down")

    with pytest.raises(RuntimeError):
        await Masker(broken).mask(["田中様"])


async def test_latin_names_case_sensitive_word_boundary() -> None:
    m = Masker(no_ner)
    m.add_entities(entities_from_display("Lê Thị Bình", None))
    (out,) = await m.mask(["Bình gửi kết quả, bình thường không có lỗi. Binh OK."])
    assert out == "[PERSON_1] gửi kết quả, bình thường không có lỗi. [PERSON_1] OK."


async def test_existing_label_text_is_escaped() -> None:
    m = Masker(no_ner)
    m.add_entities([Entity("PERSON", ("田中",))])
    (out,) = await m.mask(["[PERSON_1] と田中様"])
    assert out == "［PERSON_1] と[PERSON_1]様"
    assert m.unmask(out) == "［PERSON_1] と田中様"


async def test_honorific_capture_trimmed_to_known_name() -> None:
    m = Masker(no_ner)
    m.add_entities(entities_from_display("田中 健二", None))
    (out,) = await m.mask(["システム開発部田中様"])
    assert out == "システム開発部[PERSON_1]様"


@pytest.mark.parametrize(
    ("full", "core"),
    [
        ("株式会社サンプルA", "サンプルA"),
        ("サンプルD工業株式会社", "サンプルD工業"),
        ("㈱テストC", "テストC"),
        ("Vendor C Inc.", "Vendor C"),
        ("Công ty TNHH Mẫu Thử", "Mẫu Thử"),
    ],
)
async def test_company_core_name_masked_everywhere(full: str, core: str) -> None:
    m = Masker(no_ner)
    a, b = await m.mask([f"【{core}】お見積り", f"{full}からの連絡"])
    assert core not in a + b
    assert a.startswith("【[ORG_1]】") and "[ORG_1]" in b


def test_companies_in_signature() -> None:
    sig = "本文です。\n\n--\n株式会社サンプルA\nシステム開発部 田中 健二\nTEL: 03-0000-0001"
    assert companies_in([sig]) == [Entity("ORG", ("株式会社サンプルA", "サンプルA"))]


async def test_unknown_label_kept() -> None:
    m = Masker(no_ner)
    await m.mask(["田中様"])
    assert m.unmask("[PERSON_1] và [ORG_9]") == "田中 và [ORG_9]"


async def test_vietnamese_and_english_mail() -> None:
    m = Masker(no_ner)
    for name, addr in [
        ("Trần Hiếu", "pm@vn-outsource.example.com"),
        ("Nguyễn Văn An", "an.nv@vn-outsource.example.com"),
        ("Brian Carter", "brian@vendor-c.example.com"),
    ]:
        m.add_entities(entities_from_display(name, addr))
    vi, en = await m.mask(
        [
            "Anh Hiếu,\n\nAn đã sửa lỗi. Liên hệ an.nv@vn-outsource.example.com.",
            "Hi Hieu,\n\nBrian here. Quote: USD 12,000 for 15 seats.\n"
            "--\nBrian Carter\nVendor C Inc.",
        ]
    )
    for secret in ("Hiếu", "Hieu", "An đã", "an.nv", "Brian", "Carter", "12,000", "Vendor C"):
        assert secret not in vi + en, secret
    assert "15 seats" in en


# --- sau security review 2026-10-21 (spec 20 mục 9) ---


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("Chào Anh Tuấn, em gửi báo cáo.", "Tuấn"),
        ("Kính gửi Chị Lan,", "Lan"),
        ("Thanks Anh Minh for the update", "Minh"),
        ("Dear Mr. Tanaka,", "Tanaka"),
        ("Hello Dr. Suzuki", "Suzuki"),
        ("Thank you Tanaka-sama", "Tanaka"),
        ("a Tuấn, xem giúp em", "Tuấn"),
        ("c Lan ơi, file đây", "Lan"),
    ],
)
async def test_greeting_and_title_names(text: str, name: str) -> None:
    (out,) = await Masker(no_ner).mask([text])
    assert name not in out, out
    assert "[PERSON_1]" in out


async def test_title_not_masked_as_name() -> None:
    (out,) = await Masker(no_ner).mask(["Dear Mr. Tanaka,"])
    assert out == "Dear Mr. [PERSON_1],"


async def test_dictionary_case_and_width_variants() -> None:
    m = Masker(no_ner)
    m.add_entities(entities_from_display("Taro YAMADA", None))
    m.add_entities(entities_from_display("Trần Văn Hiếu", None))
    m.add_entities(entities_from_display("サンプル太郎", None))
    m.add_entities([Entity("ORG", ("ABC Systems",))])
    (out,) = await m.mask(
        [
            "Yamada will join. YAMADA too. Ｙａｍａｄａ also. HIẾU, Hieu. ｻﾝﾌﾟﾙ太郎. "
            "【ＡＢＣ Ｓｙｓｔｅｍｓ】"
        ]
    )
    for secret in ("Yamada", "YAMADA", "Ｙａｍａｄａ", "HIẾU", "Hieu", "ｻﾝﾌﾟﾙ", "ＡＢＣ"):
        assert secret not in out, (secret, out)
    assert "will join" in out  # không che từ thường


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("SĐT: 0912345678", "0912345678"),
        ("Mobile 09012345678", "09012345678"),
        ("tel +84912345678", "+84912345678"),
        ("TEL 03(1234)5678", "03(1234)5678"),
        ("電話 03−1234−5678 まで", "1234"),
        ("連絡先 tanaka＠sample-a.example.co.jp", "tanaka"),
        ("Email: nguyễn.an@vn.example.com", "nguyễn.an"),
    ],
)
async def test_phone_and_email_forms(text: str, secret: str) -> None:
    (out,) = await Masker(no_ner).mask([text])
    assert secret not in out, out


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("予算は500万程度です", "500万"),
        ("見積金額 1,200,000（税別）", "1,200,000"),
        ("Giá 500 nghìn mỗi tháng", "500 nghìn"),
        ("Tổng 2 trăm triệu", "2 trăm triệu"),
        ("Budget 15k USD", "15k"),
        ("契約書No.ABC-123 の件", "ABC-123"),
        ("Hợp đồng HD-2026-0012 đã ký", "HD-2026-0012"),
        ("Mã HĐ: X99-01", "X99-01"),
        ("Tầng 5, 123 Nguyễn Trãi, Thanh Xuân, Hà Nội", "Nguyễn Trãi"),
        ("本社は港区芝公園4-2-8です", "芝公園"),
        ("Xem sample-a.example.de/project/tanaka nhé", "tanaka"),
    ],
)
async def test_money_contract_address_domain_forms(text: str, secret: str) -> None:
    (out,) = await Masker(no_ner).mask([text])
    assert secret not in out, out


@pytest.mark.parametrize(
    "text",
    [
        "帳票仕様書をお送りします。同様に、模様替えの件も。書式様式を確認",
        "1万件のデータ、3人日、45人月",
        "PRJ-1234の見積について",
    ],
)
async def test_common_words_not_masked(text: str) -> None:
    (out,) = await Masker(no_ner).mask([text])
    assert out == text


async def test_ner_spans_cleaned() -> None:
    async def ner(texts: list[str]) -> list[list[NerEntity]]:
        out: list[list[NerEntity]] = []
        for t in texts:
            ents = []
            for word in ("田中様", "在庫管理システム"):
                i = t.find(word)
                if i >= 0:
                    ents.append(NerEntity(i, i + len(word), "PERSON"))
            out.append(ents)
        return out

    m = Masker(ner)
    m.add_entities(entities_from_display("田中 健二", None))
    (out,) = await m.mask(["田中様から在庫管理システムの見積依頼。田中健二です。"])
    # Kính ngữ không dính vào tên (cùng nhãn với tên trong từ điển); tên hệ thống không bị che.
    assert out == "[PERSON_1]様から在庫管理システムの見積依頼。[PERSON_1]です。"


@pytest.mark.parametrize(
    "text",
    [
        "1" * 10_000,
        "A-" * 10_000,
        "a." * 10_000,
        "a" * 20_000,
        "Anh " * 5_000,
        "株式会社" * 3_000,
        ("x" * 60 + "@") * 300,
        "0" * 20_000,
        "万" * 10_000,
    ],
    ids=["digits", "A-", "a.", "a", "Anh", "corp", "at", "zeros", "man"],
)
async def test_patterns_linear_on_crafted_text(text: str) -> None:
    # Subject tới 10.000 ký tự, chữ ký tới 20.000: regex phải tuyến tính (security review C3).
    started = time.perf_counter()
    await Masker(no_ner).mask([text])
    assert time.perf_counter() - started < 3.0


async def test_latin_name_next_to_japanese() -> None:
    m = Masker(no_ner)
    m.add_entities(entities_from_display("Trần Hiếu", None))
    (out,) = await m.mask(["お世話になっております。Trầnです。Hiếuさん"])
    assert "Trần" not in out and "Hiếu" not in out, out


async def test_ner_header_words_dropped() -> None:
    async def ner(texts: list[str]) -> list[list[NerEntity]]:
        return [[NerEntity(0, 4, "ORG")] if t.startswith("From") else [] for t in texts]

    (out,) = await Masker(ner).mask(["From: 見積の件"])
    assert out == "From: 見積の件"


# --- security review vòng 2 (2026-10-21) ---


@pytest.mark.parametrize(
    "text",
    [
        "Call me at 090-1234-5678.",
        "SĐT: 0912345678.",
        "Liên hệ 0912 345 678.",
        "+81-90-1234-5678.",
        "Hotline: 1900 1234 5678.",
        "Tel.03-1234-5678",
        "TEL.0312345678",
        "(+84) 912 345 678",
    ],
)
async def test_phone_at_sentence_end_and_after_tel(text: str) -> None:
    (out,) = await Masker(no_ner).mask([text])
    assert "[PHONE_1]" in out and "345" not in out and "+84" not in out, out


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("田中健一様、お世話になります", True),
        ("健一様", True),
        ("本多様、お世話になります", True),
        ("多様なニーズ", False),
        ("一様に対応", False),
        ("鈴木エリカ様より", True),
    ],
)
async def test_sama_filter_keeps_real_names(text: str, masked: bool) -> None:
    (out,) = await Masker(no_ner).mask([text])
    assert ("[PERSON_1]" in out) is masked, out


@pytest.mark.parametrize(
    "name", ["Nguyen Thi Phuong Thao", "Christopher Montgomery-Smith", "鈴木エリカ"]
)
async def test_ner_long_and_mixed_names_kept(name: str) -> None:
    async def ner(texts: list[str]) -> list[list[NerEntity]]:
        return [
            [NerEntity(t.find(name), t.find(name) + len(name), "PERSON")] if name in t else []
            for t in texts
        ]

    (out,) = await Masker(ner).mask([f"Please ask {name}様 about it."])
    assert name not in out and "[PERSON_1]" in out, out


async def test_nfd_vietnamese() -> None:
    m = Masker(no_ner)
    m.add_entities(entities_from_display("Trần Hiếu", None))
    a, b = await m.mask(
        [
            unicodedata.normalize("NFD", "Kính gửi Chị Lan, em gửi Hiếu nhé."),
            unicodedata.normalize("NFD", "Hợp đồng HD-2026-0012, giá 50 triệu đồng"),
        ]
    )
    for secret in ("Lan", "Hiếu", "Hiê", "HD-2026", "50 triệu"):
        assert secret not in unicodedata.normalize("NFC", a + b), (secret, a, b)


async def test_short_numbers_not_propagated() -> None:
    a, b = await Masker(no_ner).mask(["予算 2026年度、Order No. 12", "納期は2026年10月12日です"])
    assert "2026年度" in a
    assert b == "納期は2026年10月12日です"


async def test_tech_names_and_more_addresses() -> None:
    (out,) = await Masker(no_ner).mask(
        ["ASP.NET と VB.NET で開発。TANAKA-san より。本社は港区芝公園4丁目2番8号です。"]
    )
    assert "ASP.NET" in out and "VB.NET" in out
    assert "TANAKA" not in out and "芝公園" not in out, out
