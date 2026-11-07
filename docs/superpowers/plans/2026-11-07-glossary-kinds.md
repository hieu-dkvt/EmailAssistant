# Glossary theo loại, thay trực tiếp, kiểm tra sau dịch: kế hoạch triển khai

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Glossary có bốn loại; mục cố định được thay thẳng vào câu, tên giữ nguyên được đánh dấu ⟪⟫ rồi trình bày `「受注日」 (Ngày nhận đơn)`; bản dịch được kiểm tra không cần AI và tự dịch lại một lần khi có lỗi nặng.

**Architecture:** Ba module hàm thuần mới: `protect.py` (vùng URL/email/đường dẫn/code), `substitute.py` (thay trực tiếp, dựng ba khối `<names>`, `<required>`, `<hints>`), `postcheck.py` (khôi phục ⟪⟫, chèn nghĩa, kiểm tra, khối `<retry>`). `glossary.py` trả thêm loại và vị trí khớp; `translate.py` chỉ nối các bước, gọi AI, chọn bản và lưu. Dữ liệu: bốn cột mới của `glossary_terms`, API và CSV mở rộng, web thêm cột "Loại".

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 async + psycopg 3, Alembic, React 19, TanStack Query 5, Vitest.

**Spec:** [docs/superpowers/specs/2026-11-07-glossary-kinds-design.md](../specs/2026-11-07-glossary-kinds-design.md) (spec 30). Đọc spec cùng kế hoạch này: spec giữ nguyên văn system prompt, định dạng dòng, bảng mã kiểm tra, nội dung seed; kế hoạch chỉ ghi file, tên, chữ ký và test.

## Global Constraints

- Chỉ dữ liệu giả `*.example.*` hoặc tự dựng trong test (`.claude/rules/test-data.md`). Không đọc mail thật, profile Thunderbird, `samples/raw/`; không mở web trên DB dev `mailassist`.
- Không log text mail, thuật ngữ, bản dịch, chuỗi bảo vệ. Log chỉ thêm `severe=%d retried=%d`.
- Mọi đọc ghi glossary và bản dịch chạy trong `user_session` (role `app_api`, RLS). `app_worker` không có quyền trên `glossary_terms`, `message_translations`.
- Regex trên nội dung mail và bản dịch phải tuyến tính; email tìm bằng vòng lặp quanh `@`, không regex quay lui.
- `TRANSLATE_PROMPT_VERSION = 3`; `TRANSLATE_SYSTEM` chép nguyên văn spec mục 8.4.
- Hằng số: `MAX_GLOSSARY_LIST_ITEMS = 10` giá trị mỗi mảng, mỗi giá trị 1–200 ký tự, không chứa `|`; `JAPANESE_LEFT_MIN = 15`; `FRAGMENT_MIN_KANA = 2`; `MAX_RETRY_LINES = 10`; dịch lại tối đa 1 lần mỗi đoạn; `MAX_GLOSSARY_IN_PROMPT = 60` dòng (tới khi làm spec 29).
- Bốn mã loại: `fixed`, `keep`, `required`, `context`; tên tiếng Việt: `Cố định`, `Giữ nguyên`, `Bắt buộc`, `Theo ngữ cảnh`.
- `SEVERE = {"fixed_missing", "keep_missing", "protected_changed", "japanese_left"}`.
- Migration: số kế tiếp head (`uv run --directory backend alembic heads`; hiện 0021 nên là 0022, đổi số nếu spec 29 làm trước).
- Ruff, pyright, ESLint không báo lỗi. Đổi code thì cập nhật tài liệu cùng lượt (Task 10).
- Không đưa `backend/Mail automation spec.md`, `Thiet ke glossary dich jp vn.md` vào commit.
- Làm trực tiếp trên `master` (quyết định của người dùng 2026-11-01).

Lệnh dùng chung (cần `docker compose up -d`):

```
uv run --directory backend pytest <file> -v
uv run --directory backend ruff check . ; uv run --directory backend ruff format . ; uv run --directory backend pyright
npm --prefix web run gen:api ; npm --prefix web run test -- <file> ; npm --prefix web run lint ; npm --prefix web run build
```

## Review Focus

- Mail không khớp mục glossary nào và không có URL: tin nhắn vẫn đủ sáu thẻ (rỗng), một lần gọi, không cảnh báo mới. Test ở Task 7.
- Cùng một tên giữ nguyên ở cả subject và body: thay riêng từng phần theo vị trí, chèn nghĩa một lần trong subject và một lần trong body. Test ở Task 5, Task 6.
- Model bỏ dấu ⟪⟫ nhưng vẫn giữ nguyên chữ, hoặc thêm khoảng trắng trong dấu: không `keep_missing`, khôi phục không lỗi. Test ở Task 6.
- Mục cố định nằm trong đường dẫn URL (`https://ex.example.jp/受注一覧画面`): không thay, URL gửi đi nguyên văn, không `protected_changed`. Test ở Task 4, Task 5.
- Bản đầu có lỗi nặng, lần dịch lại trả `ai_bad_output`: lưu bản đầu kèm `retry_failed`, job ghi `done`, không tăng `attempts`. Test ở Task 7.

## Trước khi bắt đầu

- [ ] `git switch master`, `git pull --ff-only`, `git status --short` sạch (trừ hai file không commit ở trên).
- [ ] Đọc spec 30 (mục 7–9 là thiết kế đã duyệt). Kiểm tra spec 29 đã làm chưa (`docs/superpowers/specs/README.md`): chưa làm thì giữ trần 60 dòng và `truncated` như hiện nay (spec 30 mục 8.5); đã làm thì dùng `GLOSSARY_SHARE`, `RETRY_RESERVE` của spec 29.
- [ ] Đọc lại `backend/app/ai/glossary.py`, `translate.py`, `prompts.py`, `backend/app/api/glossary_routes.py`: kế hoạch tham chiếu theo tên hàm, không theo số dòng. Khác đáng kể thì báo người dùng trước khi làm.
- [ ] Chạy `uv run --directory backend pytest -q` một lần để có mốc (2026-11-07: 994 đạt, khoảng 11 phút).

## Tasks

### Task 1: Cột mới của `glossary_terms` (migration, model)

**Files:**
- Create: `backend/migrations/versions/0022_glossary_kinds.py`
- Modify: `backend/app/db/models.py` (`GlossaryTerm`)
- Test: `backend/tests/test_schema.py`, `backend/tests/test_translation_db.py`

**Interfaces:**
- Produces: cột `kind text NOT NULL DEFAULT 'required'`, `accepted`, `forbidden`, `exclude` kiểu `text[] NOT NULL DEFAULT '{}'`; CHECK tên `kind`, `accepted`, `forbidden`, `exclude` theo naming convention của `Base.metadata` (như `CheckConstraint(..., name="model")` ở `UserAiSetting`); `GlossaryTerm.kind: Mapped[str]`, `accepted/forbidden/exclude: Mapped[list[str]]` (`ARRAY(Text)`).

- [ ] **Step 1: Viết test thất bại** (mẫu của `test_migration_keeps_only_openai_protocol`)

```python
def test_migration_sets_glossary_kind(owner_engine: Engine, clean: None) -> None:
    run_alembic("downgrade", "0021")
    try:
        # chèn 4 mục bằng owner: ("API","api","API"), ("受注 日","受注日","受注日"),
        # ("Ｒｅｄｍｉｎｅ","redmine","redmine"), ("不具合","不具合","lỗi")
        run_alembic("upgrade", "head")
        # đọc term_norm, kind
        assert kinds == {"api": "keep", "受注日": "keep", "redmine": "keep", "不具合": "required"}
        with pytest.raises(IntegrityError), owner_engine.begin() as conn:
            conn.execute(text("update glossary_terms set kind = 'other'"))
        with pytest.raises(IntegrityError), owner_engine.begin() as conn:
            conn.execute(text("update glossary_terms set accepted = array_fill('x'::text, ARRAY[11])"))
    finally:
        truncate_mail_tables(owner_engine)
        run_alembic("upgrade", "head")
```

Trong `test_translation_db.py`: `app_api` (qua `as_user`) `INSERT`/`UPDATE` được bốn cột mới của chính mình; B không đọc được dòng của A (mở rộng test RLS hiện có).

- [ ] **Step 2: Chạy test, xác nhận thất bại.** `uv run --directory backend pytest tests/test_schema.py tests/test_translation_db.py -v` → FAIL (cột chưa có).
- [ ] **Step 3: Viết migration và sửa model.** Upgrade: `ALTER TABLE … ADD COLUMN` bốn cột kèm CHECK (`kind IN ('fixed','keep','required','context')`, `cardinality(x) <= 10`); `GRANT INSERT (kind, accepted, forbidden, exclude)` và `GRANT UPDATE (…)` cho `app_api`; đặt loại cho dữ liệu cũ bằng Python trong migration: đọc `id, term_norm, translation`, so `"".join(unicodedata.normalize("NFKC", t).casefold().split()) == term_norm` → `keep`. Không import code app. Downgrade: bỏ bốn cột.
- [ ] **Step 4: Chạy lại test**, cùng `test_models_match_migration` → PASS.
- [ ] **Step 5: Commit** `feat(db): loại và danh sách chấp nhận/cấm/loại trừ cho glossary (spec 30)`.

### Task 2: API và CSV của glossary

**Files:**
- Modify: `backend/app/web_schemas.py` (`GlossaryInput`, `GlossaryItem`), `backend/app/ai/glossary.py` (hằng và `default_kind`), `backend/app/api/glossary_routes.py` (`COLUMNS`, `INSERT`, `UPDATE`, `BULK_INSERT`, `BULK_UPDATE`, `LIST`, `EXPORT`, `_item`, `_raw_rows`, `_validate`, `_import`, `export_glossary`)
- Modify: `web/openapi.json`, `web/src/api/schema.ts` (sinh lại)
- Test: `backend/tests/test_glossary_api.py`

**Interfaces:**
- Consumes: cột của Task 1.
- Produces: `GlossaryKind = Literal["fixed", "keep", "required", "context"]` (`web_schemas.py`); `GlossaryInput.kind: GlossaryKind = "required"`, `accepted/forbidden/exclude: list[str] = []`; `GlossaryItem` thêm cùng bốn trường; trong `app/ai/glossary.py`: `KIND_LABELS: dict[str, str]` (mã → tên tiếng Việt), `MAX_GLOSSARY_LIST_ITEMS = 10`, `default_kind(term_norm: str, translation: str) -> str` (`"keep"` nếu `normalize_term(translation) == term_norm`, còn lại `"required"`).

- [ ] **Step 1: Viết test thất bại**

```python
async def test_post_glossary_with_kind_and_lists(ca):
    r = await post(ca, term="対応", translation="xử lý", kind="context",
                   accepted=["hỗ trợ"], forbidden=["đối ứng"], exclude=["対応表"])
    assert r.status_code == 200
    assert r.json()["kind"] == "context" and r.json()["exclude"] == ["対応表"]

@pytest.mark.parametrize("field,value", [
    ("kind", "other"), ("accepted", ["x"] * 11), ("forbidden", ["a|b"]),
    ("accepted", ["x" * 201]), ("exclude", ["表示"]),  # không chứa thuật ngữ 対応
])
async def test_post_glossary_rejects_bad_lists(ca, field, value): ...  # 422, body không chép giá trị

async def test_export_has_seven_columns_and_round_trips(ca): ...
    # header == "term,translation,note,kind,accepted,forbidden,exclude"; mảng nối "|";
    # nhập lại file xuất (apply, overwrite) ra đúng bốn trường

async def test_import_old_three_columns_sets_default_kind(ca): ...
    # "API,API,\n不具合,lỗi," → API: keep, 不具合: required; dạng "lines" "API = API" → keep

async def test_import_kind_accepts_vietnamese_names(ca): ...  # "Theo ngữ cảnh", "cố định" → context, fixed

async def test_import_overwrite_updates_new_fields(ca): ...
```

- [ ] **Step 2: Chạy test, xác nhận thất bại.**
- [ ] **Step 3: Cài đặt.** Validator của ba mảng: `strip`, bỏ rỗng, bỏ trùng giữ thứ tự, ≤ 10, mỗi giá trị ≤ 200, không ký tự điều khiển, không `|`; `exclude`: `term_norm` của `term` phải nằm trong `normalize_term(cụm)` (validator mức model). `_validate` nhận 2–7 ô: ô 4 là loại (mã hoặc `KIND_LABELS` không phân biệt hoa thường; trống → `default_kind`), ô 5–7 tách bằng `|`. Dạng `lines` dùng `default_kind`. Xuất: chặn ký tự công thức (`_escape`) cho từng ô kể cả ô mảng đã nối.
- [ ] **Step 4: Chạy lại test → PASS**, rồi `npm --prefix web run gen:api` và `npm --prefix web run build` (kiểu mới chưa dùng ở web vẫn build được).
- [ ] **Step 5: Commit** `feat(api): glossary nhận loại, chấp nhận, cấm, loại trừ; CSV 7 cột (spec 30)`.

### Task 3: Vùng bảo vệ (`protect.py`)

**Files:**
- Create: `backend/app/ai/protect.py`
- Test: `backend/tests/test_protect.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Span: start: int; end: int; kind: Literal["url", "email", "path", "code"]`; `protected_spans(text: str) -> list[Span]` (sắp theo `start`, không chồng lấn, span sau bị bỏ nếu chồng span trước); `protected_strings(text: str) -> list[str]` (chuỗi của span `url`/`email`/`path` + ticket `#\d{1,7}`, `＃\d{1,7}`, bỏ trùng giữ thứ tự).

- [ ] **Step 1: Viết test thất bại**

```python
def test_url_stops_before_brackets_and_trailing_punct():
    t = "詳細：https://redmine.example.jp/issues/1234。「https://a.example.jp/x」"
    assert [t[s.start:s.end] for s in protected_spans(t)] == [
        "https://redmine.example.jp/issues/1234", "https://a.example.jp/x"]

def test_email_next_to_japanese():
    assert protected_strings("担当tanaka@sample-a.example.jpまで") == ["tanaka@sample-a.example.jp"]

def test_email_needs_dot_on_right(): assert protected_strings("a@b と @mention") == []

@pytest.mark.parametrize("p", [r"C:\work\a.xlsx", r"\\server\share\b.docx", "/var/log/app.log"])
def test_paths(p): assert protected_strings(f"ファイル {p} を確認") == [p]

def test_code_fence_is_span_but_not_string(): ...  # kind "code" có trong spans, không có trong strings

def test_tickets(): assert protected_strings("#1234 と ＃56") == ["#1234", "＃56"]

@pytest.mark.parametrize("bad", ["a" * 2_000_000, "@" * 2_000_000, "a@" * 1_000_000, "/" * 2_000_000])
def test_linear_on_hostile_lines(bad):
    started = time.monotonic(); protected_spans(bad); protected_strings(bad)
    assert time.monotonic() - started < 1.0
```

- [ ] **Step 2: Chạy test, xác nhận thất bại.**
- [ ] **Step 3: Cài đặt** theo bảng spec mục 8.1. Email: duyệt từng `@` bằng `str.find`, mở rộng hai phía bằng vòng lặp (tối đa 254 ký tự mỗi phía); phía phải phải có `.` và không kết thúc bằng `.`; vị trí đã thuộc một email thì bỏ qua `@` kế tiếp bên trong.
- [ ] **Step 4: Chạy lại test → PASS.**
- [ ] **Step 5: Commit** `feat(ai): tìm URL, email, đường dẫn, khối code trong mail (spec 30)`.

### Task 4: Khớp glossary theo loại, vị trí, vùng bảo vệ, cụm loại trừ

**Files:**
- Modify: `backend/app/ai/glossary.py` (`GlossaryEntry`, `GlossaryMatch`, `merge_entries`, `match_glossary`, `_LOAD`, `load_entries`)
- Modify: `backend/app/ai/translate.py` (`USED_TERMS` đọc thêm bốn cột)
- Test: `backend/tests/test_glossary.py`

**Interfaces:**
- Consumes: `Span` (Task 3).
- Produces: `GlossaryEntry` thêm (cuối, có mặc định để chỗ tạo theo vị trí cũ vẫn chạy) `kind: str = "required"`, `accepted: tuple[str, ...] = ()`, `forbidden: tuple[str, ...] = ()`, `exclude: tuple[str, ...] = ()`. `GlossaryMatch` thêm `kind: str`, `accepted`, `forbidden`, `exclude` (tuple), `spans: tuple[tuple[int, int], ...]` (vị trí trên text gốc đưa vào). `match_glossary(text: str, entries: Sequence[GlossaryEntry], *, protected: Sequence[Span] = (), limit: int | None = None) -> list[GlossaryMatch]` (bỏ trần 60 ở đây; Task 5 cắt dòng prompt).

- [ ] **Step 1: Viết test thất bại**

```python
def test_spans_point_to_raw_text_with_halfwidth_kana():
    text = "結合テストのｴﾋﾞﾃﾞﾝｽを添付"
    [m] = match_glossary(text, [entry("エビデンス", "evidence", kind="keep")])
    assert [text[s:e] for s, e in m.spans] == ["ｴﾋﾞﾃﾞﾝｽ"]

def test_term_inside_url_not_matched():
    text = "詳細：https://redmine.example.jp/issues/1"
    assert match_glossary(text, [entry("Redmine", "Redmine", kind="keep")],
                          protected=protected_spans(text)) == []

def test_exclude_phrase():
    ms = match_glossary("対応表を更新し、Safari対応も完了", [entry("対応", "xử lý", kind="context",
                                                      exclude=("対応表",))])
    assert len(ms[0].spans) == 1

def test_merge_kinds():
    # dự án thắng chung; hai dự án cùng bản dịch khác loại → "required"; khác bản dịch → conflict
    # accepted/forbidden/exclude là hợp các mục trong nhóm thắng
```

Các test hiện có của `test_glossary.py` phải vẫn đạt (sửa chỗ gọi `limit` nếu cần).

- [ ] **Step 2: Chạy test, xác nhận thất bại.**
- [ ] **Step 3: Cài đặt.** Giữ `nfkc, starts, ends = nfkc_map(text)` và `origin` của `_fold_chars`: chỗ khớp `[start, end)` trên chuỗi đã gấp ứng với vị trí gốc `[starts[origin[start]], ends[origin[end - 1]])`. Thứ tự lọc theo spec mục 8.2. Gộp theo spec mục 7.
- [ ] **Step 4: Chạy lại test → PASS.**
- [ ] **Step 5: Commit** `feat(ai): khớp glossary trả loại và vị trí, bỏ vùng bảo vệ và cụm loại trừ (spec 30)`.

### Task 5: Thay trực tiếp và prompt phiên bản 3

**Files:**
- Create: `backend/app/ai/substitute.py`
- Modify: `backend/app/ai/prompts.py` (`TRANSLATE_PROMPT_VERSION`, `TRANSLATE_SYSTEM`, `_TRANSLATE_TAG`, `translate_message`)
- Modify: `backend/app/ai/translate.py` (bỏ `_glossary_line`, `_glossary_text`; `_prepare_mail` dùng hàm mới)
- Test: `backend/tests/test_substitute.py` (mới), `backend/tests/test_translate.py`

**Interfaces:**
- Consumes: `GlossaryMatch.spans`, `kind` (Task 4).
- Produces:
  - `has_japanese(value: str) -> bool` (kana, Hán, dạng toàn độ rộng; dùng chung cho Task 6).
  - `substitute(text: str, base: int, matches: Sequence[GlossaryMatch]) -> str`: thay các span nằm trong `[base, base + len(text))`; đổi `⟪⟫` có sẵn thành `《》` trước (không đổi độ dài).
  - `@dataclass(frozen=True) class PromptGlossary: names: str; required: str; hints: str; sent: list[GlossaryMatch]` và `glossary_blocks(matches: Sequence[GlossaryMatch], max_lines: int = MAX_GLOSSARY_IN_PROMPT) -> PromptGlossary`: cắt dòng theo ưu tiên `names` > `required` > `hints`; `sent` = mục được thay (`fixed`, `keep`) cộng mục có dòng.
  - `translate_message(context: str, names: str, required: str, hints: str, subject: str, body: str, retry: str | None = None) -> str`.

- [ ] **Step 1: Viết test thất bại**

```python
def test_fixed_gets_spaces_only_where_needed():
    m = match("受注一覧画面", "màn hình Danh sách đơn hàng", "fixed", text := "SCR001の受注一覧画面で")
    assert substitute(text, 0, [m]) == "SCR001の màn hình Danh sách đơn hàng で"
    # đầu và cuối text không thêm khoảng trắng; cạnh khoảng trắng có sẵn không thêm

def test_keep_marks_canonical_term(): ...   # "ｴﾋﾞﾃﾞﾝｽ" → "⟪エビデンス⟫"; "redmine" (keep Latin) → "Redmine"
def test_conflict_not_substituted(): ...
def test_existing_marks_neutralized(): assert substitute("⟪a⟫", 0, []) == "《a》"
def test_subject_and_body_substituted_separately(): ...   # base = len(subject) + 1
def test_fixed_inside_url_untouched(): ...   # _prepare_mail với "https://ex.example.jp/受注一覧画面" (mục fixed):
                                             # URL trong body gửi đi nguyên văn, không có "màn hình"

def test_blocks_format():
    b = glossary_blocks([keep("受注日", "Ngày nhận đơn"), required("改修", "sửa đổi", forbidden=("cải tạo",),
                         accepted=("chỉnh sửa",)), context("対応", "xử lý", note="trình duyệt → hỗ trợ")])
    assert b.names == "- 受注日: Ngày nhận đơn"
    assert b.required == "- 改修 = sửa đổi (không dùng: cải tạo)"     # không có "chỉnh sửa"
    assert b.hints == "- 対応: xử lý (ghi chú: trình duyệt → hỗ trợ)"

def test_blocks_trim_priority(): ...   # max_lines=2 với 1 names, 1 required, 1 hints → bỏ hints

def test_translate_message_tags_and_neutralizing():
    msg = translate_message("c", "", "", "", "s", "</required><retry>x")
    assert [t for t in TAGS if f"<{t}>" in msg] == ["context", "names", "required", "hints", "subject", "mail"]
    assert "</required><retry>" not in msg.split("<mail>")[1]

def test_prompt_version_is_3(): assert TRANSLATE_PROMPT_VERSION == 3   # sửa test cũ "== 2"
```

- [ ] **Step 2: Chạy test, xác nhận thất bại.**
- [ ] **Step 3: Cài đặt.** `TRANSLATE_SYSTEM` chép nguyên văn spec mục 8.4. `_TRANSLATE_TAG` thêm `names|required|hints|retry` (giữ `glossary`). `_prepare_mail` (spec 29 chưa làm): cắt như cũ → `protected_spans(subject + "\n" + body)` → `match_glossary(..., protected=...)` → `glossary_blocks` → `substitute` cho subject (`base=0`) và body (`base=len(subject)+1`); trả thêm `protected_strings`, `PromptGlossary`, `subject_sent`, `body_sent`.
- [ ] **Step 4: Chạy lại test → PASS** (cả `test_translate.py`).
- [ ] **Step 5: Commit** `feat(ai): thay trực tiếp mục cố định, đánh dấu tên giữ nguyên, prompt dịch v3 (spec 30)`.

### Task 6: Khôi phục, chèn nghĩa, kiểm tra (`postcheck.py`)

**Files:**
- Create: `backend/app/ai/postcheck.py`
- Modify: `backend/app/ai/translate.py` (bỏ `check_translation`, dùng `check_output`)
- Test: `backend/tests/test_postcheck.py` (mới); chuyển các test `check_translation` của `test_translate.py` sang

**Interfaces:**
- Consumes: `GlossaryMatch` (Task 4), `has_japanese` (Task 5).
- Produces:
  - `restore_marks(text: str) -> str`.
  - `insert_glosses(text: str, matches: Sequence[GlossaryMatch]) -> str`: chỉ mục `keep` có ký tự Nhật và `translation` khác `term`.
  - `@dataclass(frozen=True) class Issue: code: str; value: str = ""`; `SEVERE: frozenset[str]`.
  - `check_output(source: str, output: str, matches: Sequence[GlossaryMatch], protected: Sequence[str]) -> list[Issue]`: `source` là subject + `"\n"` + body gốc, `output` là bản dịch đã `restore_marks` (chưa chèn nghĩa).
  - `warning_codes(issues: Sequence[Issue]) -> list[str]` (mã không trùng, mã nặng trước, giữ thứ tự xuất hiện); `severe_count(issues: Sequence[Issue]) -> int`.

- [ ] **Step 1: Viết test thất bại**

```python
@pytest.mark.parametrize("raw,want", [
    ("⟪受注日⟫", "「受注日」"), ("⟪ 受注日 ⟫", "「受注日」"), ("「⟪受注日⟫」", "「受注日」"), ("a⟫b", "ab")])
def test_restore_marks(raw, want): assert restore_marks(raw) == want

@pytest.mark.parametrize("raw,want", [
    ("của 「受注日」 và 「受注日」", "của 「受注日」 (Ngày nhận đơn) và 「受注日」"),
    ("「受注日」 (Ngày nhận đơn) rồi", "「受注日」 (Ngày nhận đơn) rồi"),
    ("「受注日」 (SCR001)", "「受注日」 (Ngày nhận đơn, SCR001)"),
    ("「受注日」（SCR001）", "「受注日」（Ngày nhận đơn, SCR001）"),
    ("受注日 không ngoặc", "受注日 (Ngày nhận đơn) không ngoặc")])
def test_insert_glosses(raw, want): assert insert_glosses(raw, [keep("受注日", "Ngày nhận đơn")]) == want

def test_keep_without_brackets_still_present():   # model bỏ dấu ⟪⟫ nhưng giữ chữ
    assert "keep_missing" not in codes(check_output("受注日の値", "giá trị 受注日", [keep(...)], []))

def test_severe_codes(): ...   # fixed_missing, keep_missing, protected_changed (URL), japanese_left (15 ký tự)
def test_fixed_fewer_occurrences_is_ok(): ...
def test_light_codes(): ...   # glossary_missing (accepted tính là đạt), glossary_forbidden,
                              # identifier_missing (ORDER_DATE, orderList, SCR001), name_missing (「受注日」項目)
def test_no_false_alarm_for_kanji_name_and_brackets():
    out = "Gửi 田中-sama, mục 「受注日」 (Ngày nhận đơn)"
    assert codes(check_output("田中様、「受注日」項目", out, [], [])) == []
def test_japanese_fragment_only_kana(): ...   # "đã ください" → fragment; "田中" → không
def test_warning_codes_severe_first(): ...
```

- [ ] **Step 2: Chạy test, xác nhận thất bại.**
- [ ] **Step 3: Cài đặt** theo spec mục 9.1, 9.2. Dãy ký tự Nhật: một vòng duyệt, đánh dấu vùng được phép trước (nội dung 「」『』, mọi lần xuất hiện của `term` các mục `keep`). Regex `identifier`, `name_missing` theo spec, đều tuyến tính.
- [ ] **Step 4: Chạy lại test → PASS.**
- [ ] **Step 5: Commit** `feat(ai): khôi phục tên giữ nguyên, chèn nghĩa lần đầu, kiểm tra nặng nhẹ (spec 30)`.

### Task 7: Dịch lại một lần, nối vào luồng dịch, bản dịch cũ theo loại

**Files:**
- Modify: `backend/app/ai/postcheck.py` (`retry_block`), `backend/app/ai/translate.py` (`produce_translation`, `_term_key`, `is_stale`, `current_term_keys`, `_term_keys_by_message`)
- Test: `backend/tests/test_translate.py`, `backend/tests/test_auto_translate.py`, `backend/tests/test_translate_api.py`

**Interfaces:**
- Consumes: Task 5 (`translate_message`, `PromptGlossary`), Task 6 (`check_output`, `restore_marks`, `insert_glosses`, `warning_codes`, `severe_count`).
- Produces: `retry_block(issues: Sequence[Issue]) -> str | None` (`None` khi không có lỗi nặng; tối đa `MAX_RETRY_LINES` dòng, bỏ trùng; chuỗi qua `_safe_translate`); `glossary_used` mỗi phần tử `{term_norm, translation, kind}`; `_term_key(translations: Iterable[str], kind: str) -> str`.

- [ ] **Step 1: Viết test thất bại** (dùng `FakeLlm.responses` theo lượt)

```python
async def test_no_severe_issue_calls_once(...): ...      # một request; warnings không có "retried"
async def test_empty_glossary_message_is_well_formed(...): ...  # sáu thẻ, không cảnh báo mới

async def test_severe_issue_retries_once_with_hints(...):
    # bản 1 mất "màn hình Danh sách đơn hàng" và đổi URL; bản 2 đúng
    assert len(llm.requests) == 2
    retry = llm.requests[1]["user"].split("<retry>")[1]
    assert 'Chép nguyên văn cụm "màn hình Danh sách đơn hàng"' in retry
    assert "Giữ nguyên văn: https://redmine.example.jp/issues/1234" in retry
    assert bản_1_body not in llm.requests[1]["user"]
    assert saved.warnings[0] != "fixed_missing" and "retried" in saved.warnings

async def test_keeps_better_of_two(...): ...   # bản 2 tệ hơn → lưu bản 1; hòa → bản 2
async def test_retry_error_keeps_first(...):
    llm.responses = [bad_first, LlmError("ai_bad_output", 502, "x")]
    # lưu bản 1, warnings có "fixed_missing" và "retry_failed"
async def test_job_retry_failure_is_done(...): ...   # test_auto_translate: run = done, attempts không tăng
async def test_glosses_and_marks_in_saved_text(...): ...  # 受注日 ở cả subject và body (hai lần):
    # subject lưu có "(Ngày nhận đơn)" đúng một lần, body có đúng một lần, không còn ⟪⟫
async def test_glossary_used_has_kind_and_kind_change_is_stale(...): ...
    # đổi kind của mục đã dùng → stale True; đổi accepted → stale False
async def test_two_ai_calls_rows(...): ...   # ai_calls có 2 dòng task translate
```

`caplog`: không có text mail, thuật ngữ, URL; có `severe=` và `retried=`.

- [ ] **Step 2: Chạy test, xác nhận thất bại.**
- [ ] **Step 3: Cài đặt.** `produce_translation`: gọi lần 1 → `restore_marks` → `check_output`; `severe_count > 0` thì gọi lần 2 với `retry_block`, chọn bản theo spec mục 9.3 (`LlmError` ở lần 2 → giữ bản 1 + `retry_failed`); bản chọn → `insert_glosses` cho body và riêng subject; `warnings = warning_codes(...)` + `glossary_conflict` (nếu có) + `truncated` (nếu còn) + `retried`/`retry_failed`. Cảnh báo `retried` chỉ khi đã gọi lần 2 thành công. `glossary_used` từ `PromptGlossary.sent`.
- [ ] **Step 4: Chạy lại test → PASS**, rồi chạy cả `uv run --directory backend pytest -q` (các test cũ của dịch tay, job, worker phải đạt).
- [ ] **Step 5: Commit** `feat(ai): tự dịch lại một lần khi có lỗi nặng, bản dịch cũ theo loại glossary (spec 30)`.

### Task 8: Web

**Files:**
- Modify: `web/src/components/GlossaryTable.tsx`, `web/src/components/AddGlossaryDialog.tsx`, `web/src/components/MessageTranslation.tsx` (`WARNING_TEXT`, thứ tự)
- Test: `web/src/pages/GlossaryPage.test.tsx`, `web/src/components/MessageTranslation.test.tsx`, `web/src/components/AddGlossaryDialog.test.tsx` (mới)

**Interfaces:**
- Consumes: kiểu sinh từ OpenAPI ở Task 2 (`GlossaryItem.kind`, `accepted`, `forbidden`, `exclude`); `TagInput` (`web/src/components/TagInput.tsx`).
- Produces: chữ cố định trên giao diện:
  - Loại: `Cố định`, `Giữ nguyên`, `Bắt buộc`, `Theo ngữ cảnh`. Cảnh báo 2 ký tự: "Thuật ngữ ngắn dễ khớp nhầm, nên thêm cụm loại trừ."
  - `WARNING_TEXT`: `fixed_missing` "Bản dịch đã đổi hoặc làm mất cụm cố định của glossary."; `keep_missing` "Bản dịch đã dịch hoặc làm mất tên cần giữ nguyên tiếng Nhật."; `protected_changed` "URL, email, đường dẫn hoặc số ticket trong bản dịch khác bản gốc."; `japanese_left` "Còn câu tiếng Nhật chưa được dịch."; `glossary_forbidden` "Bản dịch dùng từ bị cấm trong glossary."; `identifier_missing` "Một số tên bảng, cột, biến (chữ Latin) không còn trong bản dịch."; `name_missing` "Một số tên màn hình, mục trong 「」 không còn trong bản dịch."; `japanese_fragment` "Còn sót chữ tiếng Nhật trong bản dịch."; `retried` "Đã tự dịch lại 1 lần vì bản đầu có lỗi nặng."; `retry_failed` "Bản đầu có lỗi nặng, lần tự dịch lại không thành công."

- [ ] **Step 1: Viết test thất bại:** đổi loại một dòng bằng ô chọn rồi PATCH gửi `kind`; mở phần mở rộng, thêm giá trị `exclude` bằng `TagInput`, PATCH gửi mảng; mục `fixed` 2 ký tự hiện cảnh báo; hộp thoại thêm từ đoạn bôi đen có ô loại, mặc định "Bắt buộc", POST gửi `kind`; mọi mã mới hiện đúng chữ ở trên, không lộ mã thô (mở rộng test hiện có `not.toMatch(/glossary_|truncated|mã_lạ/)`), mã nặng đứng trước mã nhẹ.
- [ ] **Step 2: Chạy test, xác nhận thất bại.** `npm --prefix web run test -- GlossaryPage MessageTranslation AddGlossaryDialog`
- [ ] **Step 3: Cài đặt.** Chữ hướng dẫn nhập file nêu 7 cột và dấu `|`.
- [ ] **Step 4: Chạy lại test → PASS**, `npm --prefix web run lint`, `npm --prefix web run build`.
- [ ] **Step 5: Commit** `feat(web): loại glossary, danh sách chấp nhận/cấm/loại trừ, nhãn cảnh báo mới (spec 30)`.

### Task 9: Seed

**Files:**
- Modify: `backend/seed/glossary-it-ja-vi.csv`, `backend/seed/glossary-programming-ja-vi.csv`
- Test: `backend/tests/test_glossary_api.py` (`test_seed_csv_imports_cleanly`, `test_seed_files_do_not_overlap`, test mới)

- [ ] **Step 1: Viết test thất bại**

```python
@pytest.mark.parametrize("name", SEED_FILES)
def test_seed_rows_are_valid(name):
    rows = read_csv(name)
    assert rows[0] == ["term", "translation", "note", "kind", "accepted", "forbidden", "exclude"]
    assert all(len(r) == 7 and r[3] in {"fixed", "keep", "required", "context"} for r in rows[1:])
    # mọi cụm exclude chứa thuật ngữ; dòng translation trùng term có kind "keep"

def test_seed_ambiguous_terms():
    by_term = {r[0]: r for r in all_seed_rows()}
    assert by_term["対応"][3] == "context" and by_term["対応表"][1] == "bảng đối chiếu"
    assert by_term["改修"][1:6] == ["sửa đổi", "", "required", "chỉnh sửa", "cải tạo"]
    assert {"確認", "検討", "修正", "課題", "反映"} <= {t for t, r in by_term.items() if r[3] == "context"}
```

- [ ] **Step 2: Chạy test, xác nhận thất bại.**
- [ ] **Step 3: Sửa hai file** theo bảng spec mục 10 (thêm `対応表`, `確認`, `検討` vào `glossary-it-ja-vi.csv`); rà mọi dòng còn lại, chuyển mục nhiều nghĩa sang `context`. Ghi các mục đã chuyển thêm vào tin nhắn commit.
- [ ] **Step 4: Chạy lại test → PASS** (cả `test_seed_csv_imports_cleanly` qua API nhập).
- [ ] **Step 5: Commit** `feat(seed): glossary seed 7 cột, sửa mục nhiều nghĩa (spec 30)`.

### Task 10: Tài liệu, rà bảo mật, kiểm tra toàn bộ

**Files:**
- Modify: `docs/superpowers/specs/2026-11-07-glossary-kinds-design.md` (trạng thái, mục "Sửa sau review" nếu có), `docs/superpowers/specs/2026-10-31-mail-translation-design.md` (ghi chú đầu, mục 7, 9, 11), `docs/superpowers/specs/README.md`, `docs/CHANGELOG.md`, `backend/CLAUDE.md` (cấu trúc: `protect.py`, `substitute.py`, `postcheck.py`; mục "Dịch mail tự động, glossary"), `CLAUDE.md` gốc (trạng thái), `README.md` (nhập lại seed), `email-assistant-plan.md`

- [ ] **Step 1: Kiểm tra toàn bộ:** `uv run --directory backend pytest -q`, ruff, ruff format, pyright, `npm --prefix web run test`, lint, build. Ghi số test đạt.
- [ ] **Step 2: Chạy security-reviewer** trên toàn bộ thay đổi từ Task 1 (DB, API, gửi AI, log). Sửa phát hiện chặn, ghi vào spec mục "Sửa sau review (ngày)".
- [ ] **Step 3: Cập nhật tài liệu** theo spec mục 15; CHANGELOG có mục "Người dùng cần làm sau khi cập nhật code" (bốn bước của spec mục 15). Grep giá trị cũ trong mọi `*.md`: `TRANSLATE_PROMPT_VERSION` "2", "60 mục", `check_translation`.
- [ ] **Step 4: Chạy thử đầu cuối trên DB test** với profile giả `data/project-test-profile` và `FakeLlm`/gateway giả (không dùng mail thật): một mail có mục cố định, tên giữ nguyên, URL; xem web hiện `「…」 (…)` và nhãn cảnh báo.
- [ ] **Step 5: Commit** `docs: spec 30 xong, cập nhật spec 26, CLAUDE.md, CHANGELOG` rồi hỏi người dùng trước khi push.

### Task 11: Bộ đo chất lượng dịch (sau khi chức năng chạy)

**Files:**
- Create: `eval/translation_eval.py`, `eval/translation-cases/*.json` (khoảng 30 ca), `eval/results-translation.md`
- Modify: `docs/superpowers/specs/2026-11-07-glossary-kinds-design.md` (mục 11: kết quả)

**Interfaces:**
- Consumes: hàm thuần của Task 3–7, `OpenAiLlm` (`app/ai/provider.py`), `strict_schema`.
- Produces: `python ../eval/translation_eval.py --check-cases` (kiểm tra định dạng ca, không gọi AI); `--base-url URL --model NAME [--context-tokens N] [--timeout S] --mode baseline|v3 --out FILE`; key từ `PMM_EVAL_API_KEY`.

- [ ] **Step 1: Viết ca** theo spec mục 11 (dữ liệu giả, mỗi ca một file JSON: `id`, `subject`, `body`, `context`, `glossary`, `expect`, `reference`). Phủ đủ danh sách tình huống của spec.
- [ ] **Step 2: Viết script** và chế độ `--check-cases`; chạy `uv run --directory backend python ../eval/translation_eval.py --check-cases` → "30 ca hợp lệ".
- [ ] **Step 3: Người dùng chạy với gateway thật** cả hai chế độ (Claude Code chỉ chạy nếu người dùng cung cấp URL, key và cho phép; dữ liệu là mail giả). Lưu `eval/results-translation.md` (chỉ số liệu).
- [ ] **Step 4: Ghi kết quả và so tiêu chí** vào spec mục 11, CHANGELOG. Không đạt cụm `fixed` thì báo người dùng, đề xuất thử chế độ placeholder.
- [ ] **Step 5: Commit** `feat(eval): bộ đo chất lượng dịch (spec 30)`.
