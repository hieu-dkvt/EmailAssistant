# Dịch mail tự động, glossary và folder dự án: kế hoạch triển khai

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mail khách đủ 3 tiêu chí trong Inbox được worker dịch sang tiếng Việt theo glossary, rồi chuyển sang folder dự án trên web; người dùng đọc bản dịch cạnh bản gốc.

**Architecture:** Migration 0018 thêm `glossary_terms`, `message_translations`, `message_auto_runs` và 3 cột của `projects`. `app/ai/translate.py` là luồng dịch dùng chung cho nút bấm và job. `app/auto_translate.py` chọn mail, gọi dịch, ghi bản dịch và trạng thái trong một transaction. Job chạy trên queue `translate` riêng bằng engine `app_api`. Folder hiệu lực tính từ `message_auto_runs`, không đổi `message_ownership`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 async + psycopg 3, Alembic, Procrastinate 3.10, React 19, TanStack Query 5, shadcn/ui, Vitest.

**Spec:** [docs/superpowers/specs/2026-10-31-mail-translation-design.md](../specs/2026-10-31-mail-translation-design.md) (spec 26). Spec giữ nguyên văn system prompt, quy tắc và bảng dữ liệu; kế hoạch không chép lại.

## Global Constraints

- Chỉ dữ liệu giả `*.example.*`. Không mở web, không gọi API đọc mail trên DB dev `mailassist`; chạy thử bằng DB test.
- Mọi text gửi AI qua `Masker`; che lỗi thì không gửi. Không log text, bảng nhãn, giá trị glossary, tên dự án, domain khách. Log chỉ có `user`, `message_id`, số lượng, mã lỗi, `ms`. Lỗi task bọc bằng `sanitized`.
- Mọi đọc, ghi dữ liệu mail của job và API chạy trong `user_session`, role `app_api`. Chỉ `pending_translate_users` chạy role `app_worker`, luôn lọc rõ và không đọc nội dung mail.
- Không ghi vào mbox, profile, mail server.
- Giới hạn: `MAX_SUBJECT_CHARS = 300`, `MAX_BODY_CHARS = 12_000`, `MAX_CONTEXT_CHARS = 1_500`, `MAX_GLOSSARY_IN_PROMPT = 60`, `MAX_TERMS_PER_SCOPE = 5_000`, `MAX_NOTES = 5`, `MAX_DOMAINS = 20`, `AI_SINCE_DAYS = 14`, `MAX_MAIL_ATTEMPTS = 3`, chờ `attempts × 10` phút. Glossary: `term` 2–200, `translation` tối đa 200, `note` tối đa 500. Nhập tối đa 1 MiB và 5.000 dòng.
- Không regex trên dữ liệu người dùng. Không `*`, `+` không giới hạn trong regex chạy trên mail.
- Web: không viết tay kiểu API (`npm run gen:api`), không thêm màu hay font, bản dịch render dạng text thuần, không lưu dữ liệu vào storage trình duyệt.
- Ruff, pyright, ESLint không báo lỗi. Đổi code thì cập nhật tài liệu cùng lượt.
- Nhánh `feature/mail-translation`, tách từ `feature/project-settings`, không làm trên `master`. Mỗi commit chỉ `git add` đúng file của task, không `git add -A`. Commit message kết thúc bằng `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Không đưa `backend/Mail automation spec.md`, `data/project-test-profile/` vào commit của task.

Lệnh dùng chung, cần DB đang chạy (`docker compose up -d`):

```
uv run --directory backend pytest <file> -v
uv run --directory backend ruff check . ; uv run --directory backend ruff format . ; uv run --directory backend pyright
npm --prefix web run test ; npm --prefix web run lint ; npm --prefix web run build
```

## Review Focus

1. Mail khách chỉ có file đính kèm, subject và body rỗng: job không lặp mãi, ghi `done` với `error_code = nothing_to_translate`, vẫn chuyển folder. Test ở Task 5.
2. Mail hoặc dự án bị xóa trong lúc chờ AI: job không lỗi FK, không ghi gì, chuyển sang mail khác. Test ở Task 5.
3. AI chưa cấu hình hoặc gateway sập: job không tự đẩy tiếp thành vòng lặp, không tăng `attempts`, chờ lần quét sau. Test ở Task 5.
4. Người dùng bấm Dịch đúng lúc worker dịch cùng mail: upsert không lỗi, mail vẫn được chuyển folder đúng một lần. Test ở Task 5.
5. Model trả `body` rỗng khi mail có nội dung: coi là `ai_bad_output`, không lưu bản dịch trống. Test ở Task 4.

---

### Task 1: Migration 0018, model và hạ tầng test

**Files:**
- Create: `backend/migrations/versions/0018_translation.py`
- Modify: `backend/app/db/models.py`, `backend/tests/conftest.py` (`truncate_mail_tables`), `backend/tests/dbutil.py`
- Test: `backend/tests/test_rls.py`, `backend/tests/test_translation_db.py`

**Interfaces:**
- Produces: bảng và cột theo spec mục 3, 8, 9, 10. Model SQLAlchemy `GlossaryTerm`, `MessageTranslation`, `MessageAutoRun`. Helper test `add_glossary(conn, user_id, term, translation, *, project_id=None, note=None) -> int`, `add_auto_run(conn, user_id, message_id, status="done", **cols) -> None`, `add_translation(conn, user_id, message_id, **cols) -> None`.

- [ ] **Step 1: Viết test thất bại**

`test_rls.py`: thêm ba bảng vào danh sách bảng có RLS. `test_translation_db.py`:
- `test_b_cannot_read_write_a_rows`: với cả ba bảng, `as_user(B)` đọc ra 0 dòng, `UPDATE`, `DELETE` ảnh hưởng 0 dòng, `INSERT` với `user_id = A` bị `WITH CHECK` chặn.
- `test_glossary_unique_treats_null_project_as_equal`: hai dòng `project_id NULL` cùng `term_norm` → lỗi unique; cùng `term_norm` ở dự án khác → được.
- `test_fk_blocks_other_users_project_and_folder`: `glossary_terms.project_id`, `projects.processed_source_id`, `message_auto_runs.target_source_id` trỏ tới dữ liệu của B đều bị FK kép chặn.
- `test_delete_message_cascades`: xóa mail xóa `message_translations`, `message_auto_runs`.
- `test_delete_project_cascades_glossary`, `test_delete_source_nulls_targets`: xóa folder đích → `target_source_id`, `processed_source_id` NULL, dòng vẫn còn.
- `test_api_grants`: `app_api` không `UPDATE` được `user_id`, `message_id`; `app_worker` chỉ `SELECT` ba bảng mới.

- [ ] **Step 2: Chạy, xác nhận thất bại**

Run: `uv run --directory backend pytest tests/test_translation_db.py tests/test_rls.py -v`
Expected: FAIL (bảng chưa có).

- [ ] **Step 3: Viết migration theo mẫu 0017**

`revision = "0018"`, `down_revision = "0017"`. SQL thuần qua `op.execute`, RLS `USING/WITH CHECK (user_id = app_user_id())`.
- `glossary_terms`: `CONSTRAINT uq_glossary_terms_scope UNIQUE NULLS NOT DISTINCT (user_id, project_id, term_norm)`, FK kép `(project_id, user_id)` tới `projects (id, user_id)` `ON DELETE CASCADE`.
- `message_translations`, `message_auto_runs`: PK `(user_id, message_id)`, FK kép `(message_id, user_id)` tới `messages (id, user_id)` `ON DELETE CASCADE`.
- `message_auto_runs`: FK `(from_source_id, user_id)`, `(target_source_id, user_id)` tới `mail_sources (id, user_id)` `ON DELETE SET NULL (from_source_id)`, `SET NULL (target_source_id)`. `CHECK status IN ('retry','done','failed')`. Index `ix_message_auto_runs_user_id_target_source_id`.
- `ALTER TABLE projects ADD COLUMN customer_domains text[] NOT NULL DEFAULT '{}', ADD COLUMN processed_source_id bigint, ADD COLUMN ai_since timestamptz`, FK kép tới `mail_sources` `ON DELETE SET NULL (processed_source_id)`.
- Quyền `app_api`: `GRANT SELECT, DELETE` ba bảng, `INSERT`/`UPDATE` theo cột (không `id`; `UPDATE` không có `user_id`, `message_id`), thêm 3 cột mới vào `INSERT`, `UPDATE` của `projects`. `app_worker`: `GRANT SELECT` ba bảng.
- `downgrade` xóa theo thứ tự ngược.

Thêm model vào `models.py`, `Project` thêm 3 cột, ba bảng vào `truncate_mail_tables`.

- [ ] **Step 4: Chạy test đạt**

Run: như Step 2. Expected: PASS. `uv run --directory backend alembic check` không báo khác biệt.

- [ ] **Step 5: Commit** `feat: bảng glossary, bản dịch, trạng thái tự động (spec 26)`

### Task 2: Cấu hình dịch tự động của dự án (backend)

**Files:**
- Create: `backend/app/mail/domains.py`, `backend/tests/test_domains.py`
- Modify: `backend/app/web_schemas.py`, `backend/app/projects.py`, `backend/app/api/project_routes.py`
- Test: `backend/tests/test_projects_api.py`

**Interfaces:**
- Produces: `normalize_domain(value: str) -> str` (ném `ValueError`), `domain_matches(addr: str | None, domains: Sequence[str]) -> bool`, `is_inbox_path(path: str) -> bool` trong `app/mail/domains.py`. `ProjectInput` thêm `customer_domains: list[str] = []`, `processed_source_id: int | None = None`. `Project` thêm cả hai và `ai_since: datetime | None`.

- [ ] **Step 1: Viết test thất bại**

`test_domains.py`:
```python
assert normalize_domain("@Sample-A.Example.JP ") == "sample-a.example.jp"
assert normalize_domain("＠sample.example.jp") == "sample.example.jp"
for bad in ("", "nodot", "a b.jp", "x" * 254 + ".jp"):
    with pytest.raises(ValueError): normalize_domain(bad)
assert domain_matches("t@mail.sample-a.example.jp", ["sample-a.example.jp"])
assert not domain_matches("t@xsample-a.example.jp", ["sample-a.example.jp"])
assert not domain_matches(None, ["sample-a.example.jp"])
assert is_inbox_path("ImapMail/imap.example.com/INBOX")
assert is_inbox_path("Mail/Local Folders/Inbox")
assert not is_inbox_path("ImapMail/imap.example.com/INBOX.sbd/ProjectA")
```
`test_projects_api.py`: `test_first_domain_sets_ai_since_once` (lần đầu có domain thì `ai_since` = khoảng `now - 14 ngày`; sửa domain, xóa rồi thêm lại không đổi), `test_no_domain_leaves_ai_since_null`, `test_domain_error_points_to_field` (`loc` chứa `customer_domains`, quá 20 giá trị bị từ chối), `test_processed_folder_rejects_inbox` (422 `project_folder_invalid`), `test_processed_folder_of_other_user_rejected` (422 cùng mã, không lộ tồn tại).

- [ ] **Step 2: Chạy, xác nhận thất bại**

Run: `uv run --directory backend pytest tests/test_domains.py tests/test_projects_api.py -v`
Expected: FAIL.

- [ ] **Step 3: Cài đặt**

`domain_matches` lấy phần sau `@` cuối, `casefold`, khớp khi bằng `d` hoặc kết thúc bằng `"." + d`. `is_inbox_path`: `path.rsplit("/", 1)[-1].casefold() == "inbox"`.

`projects.py`: thêm `customer_domains` vào `LIST_COLUMNS`, thêm hai cột vào `DATA_COLUMNS`, `ai_since` vào `ALL_COLUMNS` (chỉ đọc). `INSERT`/`UPDATE` đặt `ai_since = COALESCE(ai_since, CASE WHEN cardinality(CAST(:customer_domains AS text[])) > 0 THEN now() - make_interval(days => :ai_since_days) END)` (INSERT bỏ `COALESCE`). Trước khi ghi, trong cùng session đọc `folder_path` của `processed_source_id` theo `user_id`: không có hoặc `is_inbox_path` → `ProjectError(422, "project_folder_invalid", "Không dùng được folder này")`.

- [ ] **Step 4: Chạy test đạt**, gồm toàn bộ `test_projects_*.py`. Expected: PASS.

- [ ] **Step 5: Commit** `feat: domain khách và folder xử lý ở dự án (spec 26)`

### Task 3: Glossary backend

**Files:**
- Create: `backend/app/ai/glossary.py`, `backend/app/api/glossary_routes.py`, `backend/seed/glossary-it-ja-vi.csv`
- Modify: `backend/app/web_schemas.py`, `backend/app/main.py`
- Test: `backend/tests/test_glossary.py`, `backend/tests/test_glossary_api.py`

**Interfaces:**
- Produces:
  - `normalize_term(value: str) -> str`: NFKC, casefold, bỏ khoảng trắng.
  - `@dataclass(frozen=True) GlossaryEntry(term: str, term_norm: str, translation: str, note: str | None, project_id: int | None)`.
  - `@dataclass(frozen=True) GlossaryMatch(term: str, term_norm: str, translations: tuple[str, ...], note: str | None, conflict: bool)`.
  - `match_glossary(text: str, entries: Sequence[GlossaryEntry], limit: int = 60) -> list[GlossaryMatch]`: hàm thuần.
  - `async load_entries(session: AsyncSession, user_id: int, message_id: int) -> list[GlossaryEntry]`.
- Schema web: `GlossaryItem`, `GlossaryInput`, `GlossaryPage(items, total)`, `GlossaryImportInput`, `GlossaryImportResult(added, duplicate, invalid, first_error)`.

- [ ] **Step 1: Viết test thất bại**

`test_glossary.py`:
```python
def test_longest_match_no_overlap():
    es = [e("テスト", "kiểm thử"), e("結合テスト", "kiểm thử tích hợp")]
    assert [m.term for m in match_glossary("結合テストを実施", es)] == ["結合テスト"]

def test_short_term_kept_when_also_alone():
    es = [e("テスト", "kiểm thử"), e("結合テスト", "kiểm thử tích hợp")]
    got = {m.term for m in match_glossary("結合テストとテスト", es)}
    assert got == {"結合テスト", "テスト"}

def test_latin_word_boundary():
    assert match_glossary("RAPID build", [e("API", "API")]) == []
    assert len(match_glossary("REST API です", [e("API", "API")])) == 1

def test_nfkc_and_case():
    assert len(match_glossary("ＡＰＩ仕様", [e("api", "API")])) == 1

def test_project_beats_common():
    es = [e("検収", "nghiệm thu"), e("検収", "chấp nhận", project_id=1)]
    assert match_glossary("検収", es)[0].translations == ("chấp nhận",)

def test_two_projects_conflict():
    es = [e("検収", "A", project_id=1), e("検収", "B", project_id=2)]
    m = match_glossary("検収", es)[0]
    assert m.conflict and set(m.translations) == {"A", "B"}

def test_label_inside_text_never_matches():
    assert match_glossary("[ORG_1]様", [e("ORG", "x")]) == []

def test_limit_prefers_longer_terms(): ...  # 70 mục khớp → trả 60 mục dài nhất
```
`test_glossary_api.py`: CRUD; `test_duplicate_409`; `test_import_preview_writes_nothing`; `test_import_skip_and_overwrite`; `test_import_lines_format`; `test_import_bad_row_reported` (`invalid`, `first_error` chỉ có số dòng, không chép giá trị); `test_import_limits`; `test_export_escapes_formula_roundtrip` (giá trị `=SUM(A1)` xuất `'=SUM(A1)`, nhập lại ra `=SUM(A1)`); `test_export_has_bom`; `test_project_of_other_user_404`; `test_requires_session_and_csrf`.

- [ ] **Step 2: Chạy, xác nhận thất bại**

Run: `uv run --directory backend pytest tests/test_glossary.py tests/test_glossary_api.py -v`
Expected: FAIL.

- [ ] **Step 3: Cài đặt `match_glossary`**

Chuẩn hóa từng ký tự và giữ tuyến tính, tìm mọi lần xuất hiện bằng `str.find`, bỏ vùng nằm trong nhãn che (`LABEL_RE`) trước khi tìm. Sắp lần xuất hiện theo độ dài giảm dần, nhận lần không chồng với lần đã nhận; mục có ít nhất một lần được nhận thì giữ. Gộp theo `term_norm`: có mục dự án thì bỏ mục chung; nhiều bản dịch khác nhau thì `conflict=True`. Cắt còn `limit` mục dài nhất. Thuật ngữ toàn chữ Latin/số: ký tự liền trước và sau không thuộc lớp `A-Za-z0-9À-ỹĐđ`.

- [ ] **Step 4: Cài đặt `load_entries` và API theo spec mục 9**

`load_entries` lấy mục `project_id IS NULL` và mục của các dự án trong `message_projects` của mail. Route trong `glossary_routes.py` theo mẫu `project_routes.py` (`read_body`, `json_body`, `ApiError`). Export dùng `csv` chuẩn, `Response(media_type="text/csv; charset=utf-8")`. Thêm router vào `main.py`. Tạo file seed 100–200 thuật ngữ IT/outsource công khai, cột `term,translation,note`, UTF-8.

- [ ] **Step 5: Chạy test đạt.** Thêm `test_seed_csv_imports_cleanly` nạp file seed qua API preview với `invalid == 0`. Expected: PASS.

- [ ] **Step 6: Commit** `feat: glossary chung và theo dự án (spec 26)`

### Task 4: Luồng dịch và nút dịch tay

**Files:**
- Create: `backend/app/ai/translate.py`
- Modify: `backend/app/ai/models.py`, `backend/app/ai/prompts.py`, `backend/app/ai/ask.py`, `backend/app/api/ai_routes.py`, `backend/app/main.py`, `backend/app/web_schemas.py`, `backend/tests/mlfake.py` nếu cần
- Test: `backend/tests/test_translate.py`, `backend/tests/test_translate_api.py`, `backend/tests/test_ai_api.py`

**Interfaces:**
- Consumes: `match_glossary`, `load_entries`, `GlossaryMatch` từ Task 3.
- Produces trong `app/ai/translate.py`:
  - `class TranslationOut(BaseModel)`: `subject: str`, `body: str`, `notes: list[str]` (đặt trong `prompts.py`).
  - `class TranslateError(Exception)`: `status_code`, `code`, `detail`; mã `message_not_found` (404), `mail_not_ready` (409), `nothing_to_translate` (400).
  - `@dataclass(frozen=True) TranslationResult(subject, body, notes: list[str], warnings: list[str], source_hash: bytes, model: str, prompt_version: int, glossary_used: list[dict[str, str]])`.
  - `async produce_translation(engine: AsyncEngine, ml: MlClient, llm: LlmClient, user_id: int, message_id: int, *, custom_provider: bool) -> TranslationResult`: không mở transaction trong lúc chờ AI.
  - `async save_translation(session: AsyncSession, user_id: int, message_id: int, r: TranslationResult) -> None`: upsert.
  - `check_translation(masked_inputs: Sequence[str], masked_outputs: Sequence[str], unmasked_body: str, matches: Sequence[GlossaryMatch], *, truncated: bool) -> list[str]`: hàm thuần.
  - `is_stale(row, source_hash: bytes, current_terms: Mapping[str, str]) -> bool`: hàm thuần.
- Produces trong `app/ai/ask.py`: `async build_masker(session: AsyncSession, ml: MlClient, user_id: int, rows: Sequence[Any]) -> Masker`, thay đoạn dựng từ điển đang có.
- Schema web: `MessageTranslationOut`, `TranslateInput(force: bool = False)`, `ThreadTranslationItem`, `AutoState`, `ThreadTranslations`.
- `TASK_TRANSLATE = "translate"`.

- [ ] **Step 1: Viết test thất bại**

`test_translate.py` (dùng `FakeMl`, `FakeLlm`):
- `test_sends_only_masked_text`: tên, email, số tiền của mail và ngữ cảnh không có trong `llm.sent_text()`; nhãn có.
- `test_result_unmasked_in_all_three_fields`: `subject`, `body`, `notes` đều thay ngược.
- `test_glossary_in_prompt_masked`: `note` có tên công ty đã biết thì gửi đi đã che; thuật ngữ bị che trong mail không xuất hiện trong `<glossary>`.
- `test_conflict_sends_all_translations`: prompt có cả hai bản dịch và "chọn theo ngữ cảnh", không có tên dự án.
- `test_ner_down_no_call`: `ml.ner_down = True` → `MlUnavailable`, `llm.requests == []`.
- `test_body_truncated_at_paragraph`: body 13.000 ký tự, phần gửi ≤ 12.000 và kết thúc ở ranh giới đoạn, có `truncated`.
- `test_context_is_previous_mail_only`: 1.500 ký tự đầu của mail liền trước theo `sent_at`.
- `test_closing_tag_in_mail_neutralized`: mail chứa `</mail>` không còn nguyên chuỗi trong phần nội dung gửi đi.
- `test_empty_result_is_bad_output`: `TranslationOut(subject="x", body="", notes=[])` với mail có body → `LlmError` mã `ai_bad_output`.
- `test_check_labels_missing_unknown`, `test_check_glossary_missing`, `test_check_conflict_accepts_either`: từng mã cảnh báo.
- `test_is_stale_by_hash_version_term`: ba điều kiện.

`test_translate_api.py`: `test_saved_translation_no_ai_call`; `test_force_calls_again`; `test_other_users_mail_404_no_ai_call`; `test_not_cleaned_409`; `test_empty_400`; `test_in_progress_409`; `test_ai_error_saves_nothing`; `test_thread_translations_lists_messages`; `test_logs_have_no_mail_text` (`caplog`).

`test_ai_api.py`: `test_ask_ignores_missing_translate_model`; `test_translate_requires_its_own_model` (503 `ai_model_missing`).

- [ ] **Step 2: Chạy, xác nhận thất bại**

Run: `uv run --directory backend pytest tests/test_translate.py tests/test_translate_api.py tests/test_ai_api.py -v`
Expected: FAIL.

- [ ] **Step 3: Thêm tác vụ và prompt**

`TASKS` thêm `Task("translate", "Dịch mail", "Dịch mail của khách sang tiếng Việt, giữ thuật ngữ theo glossary.", "sonnet", "low", 16_000)`. `prompts.py`: `TRANSLATE_SYSTEM` chép đúng spec mục 7, `TRANSLATE_PROMPT_VERSION = 1`, `TranslationOut`, `translate_message(context, glossary, subject, body) -> str` ghép bốn thẻ.

- [ ] **Step 4: Tách `build_masker` trong `ask.py`, chạy lại `test_ai_api.py` để giữ nguyên hành vi hỏi đáp**

- [ ] **Step 5: Cài đặt `translate.py` theo spec mục 6, 11**

Thứ tự: đọc mail và mail liền trước → dựng Masker → che → `match_glossary` trên text đã che → che `term`, `translation`, `note` → `call_task` → body rỗng thì `LlmError("ai_bad_output", 502, …)` → `check_translation` → `unmask`. `source_hash` là SHA-256 của `subject + "\0" + body` trước khi che. `ai_routes.py`: thêm `POST /messages/{id}/translate`, `GET /threads/{id}/translations`; `ask_guard` riêng `app.state.translate_guard = AskGuard()`; `/ask` kiểm tra `("question_analysis", "answer")`, dịch kiểm tra `("translate",)`.

- [ ] **Step 6: Chạy test đạt.** Expected: PASS.

- [ ] **Step 7: Commit** `feat: luồng dịch mail và nút dịch tay (spec 26)`

### Task 5: Job dịch tự động

**Files:**
- Create: `backend/app/auto_translate.py`
- Modify: `backend/app/tasks/__init__.py`, `backend/app/worker.py`
- Test: `backend/tests/test_auto_translate.py`, `backend/tests/test_translate_worker.py`

**Interfaces:**
- Consumes: `produce_translation`, `save_translation`, `TranslateError`, `TranslationResult`, `domain_matches`, `LlmResolver`.
- Produces:
  - `ELIGIBLE_SQL: str`: điều kiện mục 4 của spec, có tham số `:u`.
  - `async pending_translate_users(engine: AsyncEngine) -> list[int]`: engine role worker.
  - `async next_message(session: AsyncSession, user_id: int) -> tuple[int, int] | None`: `(message_id, from_source_id)`, mail cũ nhất.
  - `async choose_target(session: AsyncSession, user_id: int, message_id: int) -> tuple[int | None, str | None]`: `(target_source_id, move_note)`.
  - `@dataclass(frozen=True) RunStats(handled: bool, more: bool)`.
  - `async translate_next(api_engine: AsyncEngine, ml: MlClient, resolver: LlmResolver, user_id: int) -> RunStats`.
  - `PER_MAIL_ERRORS = {"ai_bad_output", "ai_truncated", "ai_refused"}`. Mọi `LlmError` khác và `MlUnavailable` là lỗi tạm.
- Task Procrastinate: `translate_sweep` (mỗi phút), `translate_user`, queue `translate`, hàm `defer_translate(app, user_id) -> int`, `queueing_lock=f"translate:{user_id}"`.

- [ ] **Step 1: Viết test thất bại**

`test_auto_translate.py`, dữ liệu dựng bằng `dbutil`, `api` là engine `app_api`, `worker` là engine `app_worker`:

Tiêu chí: `test_eligible_full_match`; và mỗi trường hợp sau không được chọn: `test_skip_not_in_project`, `test_skip_domain_mismatch`, `test_skip_subfolder_of_inbox`, `test_skip_before_ai_since`, `test_skip_sent_at_null`, `test_skip_ownership_deleted`, `test_skip_not_cleaned`, `test_skip_done_and_failed`, `test_skip_retry_until_due`; `test_domain_of_other_project_not_counted` (mail thuộc dự án X, domain chỉ khai ở dự án Y); `test_subdomain_matches_python_and_sql` (SQL và `domain_matches` cho cùng kết quả trên bộ giá trị mẫu); `test_inbox_of_any_account`.

Luồng:
- `test_translates_saves_and_moves`: có bản dịch, `status = done`, `target_source_id` = folder dự án, `from_source_id` = Inbox.
- `test_no_folder_note`, `test_two_folders_ambiguous_stays`, `test_two_projects_same_folder_moves`.
- `test_existing_translation_no_ai_call`.
- `test_only_one_mail_per_call_and_more_flag`.
- `test_transient_errors_stop_without_row`: lần lượt `LlmError("ai_unavailable", 503, "")`, `ml.ner_down`, chưa cấu hình AI, thiếu model → `RunStats(handled=False, more=False)`, không có dòng run.
- `test_per_mail_error_retries_then_failed`: `ai_bad_output` ba lần, `attempts` 1→3, `next_attempt_at` tăng theo `attempts × 10` phút, lần ba `failed`, mail ở lại.
- `test_empty_mail_done_and_moved` (Review Focus 1).
- `test_message_deleted_during_ai_call` (Review Focus 2): xóa mail trong `FakeLlm` trước khi trả về; không lỗi, không có dòng run.
- `test_criteria_rechecked_before_write`: dự án bị xóa trong lúc gọi AI thì không chuyển folder.
- `test_race_with_manual_translate` (Review Focus 4): bản dịch được tạo trong lúc gọi AI; không lỗi, một dòng run `done`, một lần chuyển.
- `test_uses_rls_dictionary`: mail của B không xuất hiện trong từ điển che của A: tên chỉ có trong mail B không bị che trong prompt của A dù xuất hiện trong mail A.

`test_translate_worker.py`: `test_sweep_defers_user_on_translate_queue`; `test_no_self_defer_after_transient_error` (Review Focus 3); `test_self_defer_when_more`; `test_two_worker_loops_process_their_own_queue`: chạy `run_worker_async` cho từng queue bằng `wait=False` trên DB test, job `default` xong trong khi job `translate` đang chặn trên `asyncio.Event`.

- [ ] **Step 2: Chạy, xác nhận thất bại**

Run: `uv run --directory backend pytest tests/test_auto_translate.py tests/test_translate_worker.py -v`
Expected: FAIL.

- [ ] **Step 3: Cài đặt `auto_translate.py`**

Điều kiện domain trong SQL:

```sql
EXISTS (SELECT 1 FROM unnest(p.customer_domains) d
        WHERE lower(split_part(m.from_addr, '@', -1)) = d
           OR right(lower(split_part(m.from_addr, '@', -1)), length(d) + 1) = '.' || d)
```

Folder Inbox trong SQL: `lower(regexp_replace(s.folder_path, '^.*/', '')) = 'inbox'`.

`translate_next`:
1. `resolver.for_user` trả `None`, hoặc thiếu model `translate` khi `custom` → `RunStats(False, False)`.
2. `next_message`. Không có → `RunStats(False, False)`.
3. Đọc bản dịch sẵn có; không có thì `produce_translation`, ngoài transaction.
4. `TranslateError` mã `nothing_to_translate` → đi tiếp bước 6, không có bản dịch, `error_code` là mã đó. `message_not_found` → `RunStats(True, True)`.
5. `LlmError` thuộc `PER_MAIL_ERRORS` → ghi `retry` hoặc `failed`, `RunStats(True, True)`. Lỗi tạm → `RunStats(False, False)`.
6. Trong một `user_session`: kiểm tra lại mail còn đủ tiêu chí bằng `ELIGIBLE_SQL` lọc thêm `message_id`, rồi `save_translation`, `choose_target`, upsert `message_auto_runs` `status = done`. Bắt `IntegrityError` sqlstate 23503 → bỏ qua.

- [ ] **Step 4: Thêm task và vòng worker**

`tasks/__init__.py`: hằng `TRANSLATE_USER`, `TRANSLATE_SWEEP`, hai task, `defer_translate` qua `app.configure_task(TRANSLATE_USER, queue="translate", queueing_lock=...)`. `translate_user` chỉ tự đẩy tiếp khi `stats.more`.

`worker.py`:
```python
await asyncio.gather(
    app.run_worker_async(queues=["default"], concurrency=1, **common),
    app.run_worker_async(queues=["translate"], concurrency=1, name="translate",
                         install_signal_handlers=False, **common),
)
```
`additional_context` thêm `api_engine=create_api_engine()`, `resolver=LlmResolver(env_llm())`. Dispose `api_engine` trong `finally`. Queue của các task hiện có để mặc định `default`.

- [ ] **Step 5: Chạy test đạt**, gồm test worker hiện có. Expected: PASS.

- [ ] **Step 6: Commit** `feat: job dịch tự động trên queue riêng (spec 26)`

### Task 6: Folder hiệu lực và API chuyển mail

**Files:**
- Modify: `backend/app/search/keyword.py`, `backend/app/sources.py`, `backend/app/api/web_routes.py`, `backend/app/api/ai_routes.py`, `backend/app/web_schemas.py`
- Test: `backend/tests/test_folder_moves.py`, `backend/tests/test_api_sources.py`

**Interfaces:**
- Produces: `list_folders(session: AsyncSession) -> list[SourceInfo]` trong `sources.py`, số mail theo folder hiệu lực, chỉ cho `GET /folders`. `list_sources` của collector không đổi. `folders_of` trả folder hiệu lực. API `POST /messages/{id}/move-back`, `POST /messages/{id}/move` body `{project_id}`, `POST /messages/{id}/auto-retry`.

- [ ] **Step 1: Viết test thất bại**

Dữ liệu chung: mail trong Inbox, run `done` có `target_source_id = P`.
- `test_filter_inbox_hides_moved`, `test_filter_project_folder_shows_moved`: `filter_conditions` đúng qua `list_threads`, search từ khóa (`app/search/keyword.py`), `hybrid_search` và `ask`.
- `test_mail_in_second_real_folder_stays_there`: mail có ownership thêm ở folder khác vẫn hiện ở folder đó.
- `test_folders_of_effective`; `test_get_folders_counts_effective`; `test_collector_sources_counts_physical`.
- `test_resync_keeps_move`: ingest lại mail, kết quả lọc không đổi.
- `test_move_back_returns_to_inbox_and_stays`: gọi `move-back`, mail về Inbox, `next_message` không chọn lại.
- `test_move_requires_project_of_mail_with_folder`: dự án không chứa mail hoặc không có folder → 422 `move_invalid`; `ambiguous` thành công thì xóa `move_note`.
- `test_auto_retry_only_failed`: dòng `failed` bị xóa; dòng khác → 409 `auto_retry_invalid`.
- `test_delete_target_folder_returns_mail`; `test_delete_inbox_deletes_moved_mail` (theo ownership vật lý).
- `test_other_user_404` cho ba API.

- [ ] **Step 2: Chạy, xác nhận thất bại**

Run: `uv run --directory backend pytest tests/test_folder_moves.py tests/test_api_sources.py -v`
Expected: FAIL.

- [ ] **Step 3: Sửa `filter_conditions`**

```python
"(EXISTS (SELECT 1 FROM message_ownership o WHERE o.message_id = {a}.id "
" AND o.user_id = {a}.user_id AND o.source_id = :folder_id AND NOT o.is_deleted "
" AND NOT EXISTS (SELECT 1 FROM message_auto_runs r WHERE r.message_id = {a}.id "
"  AND r.user_id = {a}.user_id AND r.from_source_id = :folder_id "
"  AND r.target_source_id IS NOT NULL)) "
"OR EXISTS (SELECT 1 FROM message_auto_runs r WHERE r.message_id = {a}.id "
" AND r.user_id = {a}.user_id AND r.target_source_id = :folder_id))"
```

`FOLDERS_OF` áp dụng cùng quy tắc. `/folders` dùng `list_folders`, `routes.py` của collector giữ `list_sources`.

- [ ] **Step 4: Thêm ba API** theo spec mục 8, đặt cạnh route dịch trong `ai_routes.py`, có `X-PMM-Request`.

- [ ] **Step 5: Kiểm tra hiệu năng**

Trên DB test với 50.000 mail tổng hợp, chạy `EXPLAIN (ANALYZE, BUFFERS)` danh sách thread theo folder: dưới 1 giây, không quét tuần tự `message_auto_runs` khi lọc. Ghi số đo vào spec, không có nội dung mail.

- [ ] **Step 6: Chạy toàn bộ test search, thread, hỏi đáp.**

Run: `uv run --directory backend pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit** `feat: folder hiệu lực và API chuyển mail (spec 26)`

### Task 7: Web cấu hình dự án và glossary

**Files:**
- Create: `web/src/components/GlossaryTable.tsx`, `web/src/pages/GlossaryPage.tsx`, `web/src/pages/GlossaryPage.test.tsx`
- Modify: `web/openapi.json` (sinh), `web/src/api/client.ts`, `web/src/api/hooks.ts`, `web/src/pages/ProjectsPage.tsx`, `web/src/pages/ProjectsPage.test.tsx`, router của app, menu Cài đặt

**Interfaces:**
- Produces: `useGlossary(projectId: number | null, q: string, offset: number)`, `useSaveGlossaryTerm()`, `useDeleteGlossaryTerm()`, `useImportGlossary()`; `<GlossaryTable projectId={number | null} />`.

- [ ] **Step 1: Sinh client** `npm --prefix web run gen:api`

- [ ] **Step 2: Viết test thất bại**

`ProjectsPage.test.tsx`: `lưu domain đang gõ dở` (gõ domain rồi bấm Lưu vẫn gửi đi, như các `TagInput` khác); `lỗi domain hiện đúng ô`; `chọn folder gửi processed_source_id`; `danh sách folder không có Inbox`; `hiện ngày mốc chỉ đọc`; `hộp thoại xóa nói rõ glossary bị xóa theo`.

`GlossaryPage.test.tsx`: `tìm kiếm`; `sửa trực tiếp một ô`; `trùng thuật ngữ hiện thông báo`; `nhập: xem trước rồi mới ghi`; `dán nhiều dòng`; `xuất CSV`; `phân trang`.

- [ ] **Step 3: Chạy, xác nhận thất bại**

Run: `npm --prefix web run test`
Expected: FAIL.

- [ ] **Step 4: Cài đặt**

`ProjectsPage`: thêm `customer_domains` vào `TAG_KEYS`; khối "Dịch tự động" gồm `TagInput` domain, `Select` folder từ `useFolders` đã lọc Inbox và nhóm theo `account`, dòng "Dịch mail từ ngày …". Tab Glossary chỉ hiện với dự án đã lưu. Route `/settings/glossary`, thêm vào menu Cài đặt.

- [ ] **Step 5: Chạy test, lint, build đạt.** Expected: PASS.

- [ ] **Step 6: Commit** `feat: web cấu hình dịch tự động và glossary (spec 26)`

### Task 8: Web hiển thị bản dịch

**Files:**
- Create: `web/src/components/MessageTranslation.tsx`, `web/src/components/MessageTranslation.test.tsx`, `web/src/components/AddGlossaryDialog.tsx`
- Modify: `web/src/components/MessageCard.tsx`, `web/src/pages/ThreadPage.tsx`, `web/src/api/hooks.ts`

**Interfaces:**
- Consumes: `useSaveGlossaryTerm` từ Task 7.
- Produces: `useThreadTranslations(threadId: number)`, `useTranslateMessage()`, `useMoveBack()`, `useMoveMessage()`, `useAutoRetry()`; `MessageCard` thêm prop `translation: ThreadTranslationItem | undefined`.

- [ ] **Step 1: Viết test thất bại**

`MessageTranslation.test.tsx`: `hiện hai cột khi có bản dịch`; `nội dung HTML hiện nguyên dạng chữ` (bản dịch chứa `<b>x</b>` không thành thẻ); `ghi chú sắc thái`; `từng cảnh báo có câu tiếng Việt`; `bản dịch cũ có nút Dịch lại`; `mail chưa có bản dịch có nút Dịch`; `đang dịch khóa nút`; `lỗi hiện thông báo`; `đã chuyển hiện folder đích và Chuyển lại Inbox`; `ambiguous cho chọn dự án`; `failed hiện Thử lại`; `bôi đen trong thẻ mở hộp thoại thêm glossary với đoạn đã chọn`.

- [ ] **Step 2: Chạy, xác nhận thất bại**

Run: `npm --prefix web run test`
Expected: FAIL.

- [ ] **Step 3: Cài đặt theo spec mục 12**

`ThreadPage` gọi `useThreadTranslations` một lần cho cả thread và truyền từng phần tử xuống `MessageCard`. Hai cột dùng `grid md:grid-cols-2`, bản dịch trong `<pre className="font-sans …whitespace-pre-wrap">`. Mutation thành công thì invalidate `threadTranslations`, `folders`, `threads`.

- [ ] **Step 4: Chạy test, lint, build đạt.** Expected: PASS.

- [ ] **Step 5: Commit** `feat: web hiển thị bản dịch và chuyển mail (spec 26)`

### Task 9: Chạy thử đầu cuối, rà soát, tài liệu

**Files:**
- Modify: `docs/superpowers/specs/2026-10-31-mail-translation-design.md`, `docs/superpowers/specs/2026-10-31-project-settings-design.md`, `docs/superpowers/specs/2026-10-17-delete-folder-mail-design.md`, `docs/superpowers/specs/README.md`, `docs/CHANGELOG.md`, `CLAUDE.md`, `backend/CLAUDE.md`, `web/CLAUDE.md`

- [ ] **Step 1: Chạy kiểm tra toàn bộ**

Run: pytest, ruff, pyright, `npm run test`, `npm run lint`, `npm run build`.
Expected: tất cả đạt; ghi số test vào CHANGELOG.

- [ ] **Step 2: Chạy thử trên DB test với `data/project-test-profile`**

Dùng model giả qua `FakeLlm` hoặc gateway do người dùng cấp, không dùng DB dev. Khai báo domain và folder ở dự án, chạy worker, xác nhận qua web trên DB test: bản dịch xuất hiện, mail rời Inbox trên web, sang folder dự án, "Chuyển lại Inbox" hoạt động, đồng bộ lại không đổi kết quả.

- [ ] **Step 3: Chạy security-reviewer**, sửa điểm phát hiện, ghi vào mục "Sửa sau review" của spec.

- [ ] **Step 4: Cập nhật tài liệu**

Spec 26: dòng đầu đổi trạng thái, ghi số đo Task 6. Spec 25: thêm trường mới. Spec xóa mail folder: tác động của folder hiệu lực. `backend/CLAUDE.md`: file mới, biến `PMM_SECRET_KEY` cho worker, `api_engine`, bẫy RLS, queue `translate`. `web/CLAUDE.md`: trang và hook mới. `CLAUDE.md` gốc: "Trạng thái hiện tại", bước 6 của "Chạy thử toàn bộ" (worker cần `PMM_SECRET_KEY`). Grep giá trị cũ theo `documentation.md`.

- [ ] **Step 5: Commit** `docs: cập nhật tài liệu dịch mail tự động (spec 26)`
