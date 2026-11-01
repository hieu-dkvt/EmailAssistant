# Chuyển sang model nội bộ: kế hoạch triển khai

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hỏi đáp và dịch mail chạy qua gateway do người dùng nhập ở Cài đặt AI (mọi URL), không còn che thông tin, không còn đường gọi Anthropic mặc định.

**Architecture:** `LlmResolver` chỉ trả client từ nhà cung cấp người dùng đã lưu. Bỏ ba cấp model: mọi tác vụ dùng tên model người dùng nhập. Xóa `Masker` và NER, luồng hỏi đáp và dịch gửi thẳng text đã giới hạn độ dài.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, Alembic, httpx, SDK `anthropic`, sentence-transformers, React 19, Vitest.

**Spec:** [docs/superpowers/specs/2026-11-01-internal-model-design.md](../specs/2026-11-01-internal-model-design.md) (spec 28). Đọc spec cùng kế hoạch này.

## Global Constraints

- Làm và commit trực tiếp trên `master`. Không tạo nhánh, không worktree.
- **Không push cho tới Step cuối của Task 7.** Giữa các task, API và web tạm lệch nhau; Task 2 và Task 3–4 phải vào cùng một đợt push.
- **Thứ tự Task 2 trước Task 3, 4 là bắt buộc:** bỏ khóa mặc định trước, bỏ che sau, để không commit nào gửi được mail chưa che tới Anthropic.
- Commit message kết thúc bằng `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Không đưa `backend/Mail automation spec.md`, `data/project-test-profile/` vào commit.
- Chỉ dữ liệu giả `*.example.*`, chỉ DB test `mailassist_test`. Không mở web, không gọi API đọc mail trên `mailassist`.
- Không log text mail, câu hỏi, câu trả lời, API key, URL đầy đủ, body phản hồi. Không chép thông điệp của gateway vào log hay response.
- Tìm kiếm và RAG vẫn chạy trong `user_session` (RLS) trước khi gửi AI. AI vẫn không có tool.
- Xóa test che cùng code che, không xóa test RLS, test log, test phân quyền.
- Giữ giới hạn độ dài: subject 300, To/Cc 10 người, tên và địa chỉ 100, `MAX_BODY_CHARS = 12_000`, `MAX_CONTEXT_CHARS = 1_500`.
- Ruff, pyright, ESLint không báo lỗi. Web không viết tay kiểu API.

Lệnh dùng chung (cần `docker compose up -d`):

```
uv run --directory backend pytest <file> -v
uv run --directory backend ruff check . ; uv run --directory backend ruff format . ; uv run --directory backend pyright
uv run --directory ml-service pytest <file> -v
npm --prefix web run test -- <file> ; npm --prefix web run lint ; npm --prefix web run build
```

## Review Focus

- Người dùng cũ có dòng `user_ai_settings` chỉ có `tier`, không có `model`: sau migration, hỏi đáp báo `ai_model_missing`, không lỗi 500. Test ở Task 2.
- Gateway đang tạm không phân giải được DNS: `reject_link_local` không chặn lưu cấu hình hay tạo client, lỗi mạng báo ở lần gọi. Test ở Task 1.
- Mail có sẵn chuỗi giống nhãn như `[PERSON_1]`: gửi đi và trả về nguyên văn, không bị thay hay escape. Test ở Task 3, Task 4.
- Mail đang ở trạng thái `retry` với mã `mask_unstable` từ trước: job xử lý lại bình thường, không kẹt. Test ở Task 4.
- Bản dịch cũ còn cảnh báo `labels_missing`, `labels_unknown`: web vẫn hiện chữ, không lỗi. Test ở Task 6.

## Trước khi bắt đầu

- [ ] `git switch master`, `git pull --ff-only`, `git status --short` chỉ còn hai mục không commit ở Global Constraints.
- [ ] Commit kế hoạch này.

## Tasks

### Task 1: Nhận mọi URL và phát hiện địa chỉ công cộng

**Files:**
- Modify: `backend/app/ai/provider.py` (`validate_base_url`, `ensure_public_host`, `LlmResolver`, docstring đầu file)
- Modify: `backend/app/api/ai_routes.py` (`put_provider`, `_provider_info`), `backend/app/web_schemas.py` (`AiProviderCustom`)
- Test: `backend/tests/test_ai_provider.py`, `backend/tests/test_ai_provider_api.py`

**Interfaces:**
- Produces: `validate_base_url(raw: str) -> str`; `async def reject_link_local(base_url: str) -> None` (thay `ensure_public_host`); `async def is_public_host(base_url: str) -> bool`; `AiProviderCustom.public_host: bool`.

- [ ] **Step 1: Viết test thất bại**

```python
@pytest.mark.parametrize("url", [
    "http://10.0.0.5:20128/v1", "http://192.168.1.2", "http://gateway:8080",
    "https://gateway.internal/v1", "http://127.0.0.1:20128", "https://localhost",
    "https://openrouter.ai/api/v1",
])
def test_accepts_any_address(url): assert validate_base_url(url)

@pytest.mark.parametrize("url", [
    "ftp://x.example.com", "http://u:p@host", "http://host?x=1", "http://host#a",
    "http://2130706433", "http://0x7f.1", "http://169.254.169.254", "http://[fe80::1]",
])
def test_rejects_bad_or_link_local(url):
    with pytest.raises(ValueError): validate_base_url(url)

async def test_reject_link_local_when_host_resolves_there(monkeypatch):
    # resolve_ips giả trả ["10.0.0.1", "169.254.169.254"] → ValueError

async def test_reject_link_local_ignores_dns_failure(monkeypatch):
    # resolve_ips giả ném OSError → không ném

@pytest.mark.parametrize("ips,expected", [
    (["93.184.216.34"], True), (["10.0.0.5", "93.184.216.34"], True),
    (["10.0.0.5"], False), (["127.0.0.1"], False),
])
async def test_is_public_host(monkeypatch, ips, expected): ...

async def test_is_public_host_false_when_unresolved(monkeypatch): ...

async def test_openai_llm_posts_to_lan_url(): 
    # OpenAiLlm(base_url="http://10.0.0.5:20128/v1", ...) với MockTransport
    # request.url == "http://10.0.0.5:20128/v1/chat/completions"
```

Trong `test_ai_provider_api.py`: PUT với `http://10.0.0.5:20128/v1` trả 200; GET trả `custom.public_host` đúng (monkeypatch `resolve_ips`). Test cũ khẳng định từ chối LAN hoặc http thì đổi theo bảng trên.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run --directory backend pytest tests/test_ai_provider.py tests/test_ai_provider_api.py -v`
Expected: FAIL

- [ ] **Step 3: Sửa `provider.py`, route, schema theo spec mục 3**

Bỏ `INTERNAL_SUFFIXES`, `LOOPBACK_NAMES`. Trong `validate_base_url`, IP thật thì chỉ từ chối `is_link_local`; tên miền chỉ từ chối nhãn cuối toàn số hoặc bắt đầu bằng `0x`. Thay hai chỗ gọi `ensure_public_host` bằng `reject_link_local`. Thông điệp lỗi `ai_bad_request` trong `LlmResolver`: "Địa chỉ nhà cung cấp AI không hợp lệ".

- [ ] **Step 4: Chạy test, xác nhận đạt**

Run: `uv run --directory backend pytest tests/test_ai_provider.py tests/test_ai_provider_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: ô URL gateway nhận mọi địa chỉ, báo địa chỉ công cộng (spec 28)"
```

### Task 2: Bỏ khóa mặc định và ba cấp model

**Files:**
- Create: `backend/migrations/versions/0019_drop_ai_tier.py`
- Modify: `backend/app/ai/client.py`, `backend/app/ai/models.py`, `backend/app/ai/settings.py`, `backend/app/ai/provider.py`
- Modify: `backend/app/ai/ask.py`, `backend/app/ai/translate.py`, `backend/app/auto_translate.py` (bỏ `custom_provider`, `tiers`)
- Modify: `backend/app/main.py`, `backend/app/worker.py`, `backend/app/api/ai_routes.py`, `backend/app/web_schemas.py`, `backend/app/db/models.py`
- Test: `backend/tests/dbutil.py`, `backend/tests/test_ai_api.py`, `backend/tests/test_ai_client.py`, `backend/tests/test_ai_provider.py`, `backend/tests/test_ai_provider_api.py`, `backend/tests/test_translate.py`, `backend/tests/test_translate_api.py`, `backend/tests/test_auto_translate.py`

**Interfaces:**
- Produces:

```python
# app/ai/models.py: chỉ còn Effort, Task(task, label, description, effort, max_tokens),
# TASK_TRANSLATE, TASKS

# app/ai/client.py
async def call_task[T: BaseModel](
    engine, llm, user_id: int, task: str, model: str, *,
    system: str, user: str, output_type: type[T], log_to: CallLog,
) -> T: ...
# LlmClient.create(*, model, system, user, schema, max_tokens, effort) (bỏ fallbacks)

# app/ai/provider.py
class LlmResolver:
    def __init__(self, default: LlmClient | None = None) -> None: ...  # default chỉ cho test
    async def for_user(self, engine, user_id: int) -> LlmClient | None: ...

# app/ai/settings.py
async def user_models(session) -> dict[str, str]: ...
async def save_models(session, user_id: int, models: Mapping[str, str | None]) -> None: ...
    # None → DELETE dòng của tác vụ, có tên → upsert

# tests/dbutil.py
def set_ai_models(conn, user_id: int, **models: str) -> None: ...
    # mặc định question_analysis, answer, translate = "test-model"
```

- [ ] **Step 1: Viết test thất bại**

```python
async def test_env_key_ignored_without_provider(no_llm_client, monkeypatch):
    # no_llm_client: như fixture client của test_ai_api.py, app dựng bằng
    # create_app(api, ml=ml, web_dist=None), không truyền llm
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    r = await ask(no_llm_client)
    assert r.status_code == 503 and r.json()["code"] == "ai_not_configured"

async def test_job_stops_without_provider(...):  # translate_next → RunStats(False, False)

async def test_user_with_only_old_tier_gets_model_missing(...):
    # có nhà cung cấp, không có dòng user_ai_settings → 503 ai_model_missing, không 500

async def test_clearing_model_deletes_row(client, owner_engine):
    # PUT /ai/settings {"models": {"answer": null}} → không còn dòng, GET trả model null

async def test_settings_rejects_tiers(client):  # {"tiers": {...}} → 422 (StrictModel)

async def test_usage_has_no_price(client):
    assert "estimated_usd" not in r.json()["rows"][0] and "total_estimated_usd" not in r.json()

def test_migration_drops_tier(owner_engine):
    # chèn trước bằng downgrade 0018: dòng tier-only và dòng có model
    # upgrade head: không còn cột tier, dòng tier-only bị xóa, model NOT NULL
    # app_api DELETE được dòng của mình; A không xóa được dòng của B
```

Thêm `set_ai_models` vào fixture `world` của các file test hỏi đáp và dịch.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run --directory backend pytest tests/test_ai_api.py -v`
Expected: FAIL

- [ ] **Step 3: Viết migration 0019**

`down_revision = "0018"`. `DELETE FROM user_ai_settings WHERE model IS NULL`, `DROP COLUMN tier`, `ALTER COLUMN model SET NOT NULL`, `GRANT DELETE ON user_ai_settings TO app_api`. Downgrade: `ADD COLUMN tier text`, `CHECK` như 0015 (cho phép NULL), `ALTER COLUMN model DROP NOT NULL`, `REVOKE DELETE`, `GRANT INSERT (tier)`, `GRANT UPDATE (tier)`.

- [ ] **Step 4: Sửa code theo spec mục 4**

`_llm_for` trả `LlmClient` và luôn kiểm tên model. `produce_translation` đọc `models` và ném `ai_model_missing` khi thiếu. `/ai/provider/test` bỏ nhánh `not custom`, bỏ `fallbacks=False`. `AiSettings` giữ `configured` (có nhà cung cấp), bỏ `tiers`, `custom_provider`; `masking` để Task 3. `AnthropicLlm.__init__` nhận client bắt buộc, chỉ gọi `client.messages.create`. Worker: `LlmResolver()`. Xóa test của `anthropic_base_url`, `fallbacks`, bảng giá.

- [ ] **Step 5: Chạy test, lint, kiểu**

Run: `uv run --directory backend pytest tests/test_ai_api.py tests/test_ai_client.py tests/test_ai_provider.py tests/test_ai_provider_api.py tests/test_translate.py tests/test_translate_api.py tests/test_translate_worker.py tests/test_auto_translate.py tests/test_rls.py -v ; uv run --directory backend ruff check . ; uv run --directory backend pyright`
Expected: PASS, không lỗi. `Masker` vẫn chạy ở bước này.

- [ ] **Step 6: Commit**

```bash
git add backend/app backend/tests backend/migrations
git commit -m "feat: bỏ khóa Anthropic mặc định và ba cấp model (spec 28)"
```

### Task 3: Bỏ che ở hỏi đáp

**Files:**
- Create: `backend/app/ai/text.py`
- Modify: `backend/app/ai/ask.py`, `backend/app/ai/translate.py` (nhận `build_masker`, `learn_rows`, `_participants`, tạm thời tới Task 4)
- Modify: `backend/app/ai/prompts.py` (`ANALYSIS_SYSTEM`, `ANSWER_SYSTEM`, mô tả `sender`), `backend/app/api/ai_routes.py`, `backend/app/web_schemas.py`
- Test: `backend/tests/test_ask_text.py` (mới), `backend/tests/test_ai_api.py`

**Interfaces:**
- Produces: `cut_text(text: str, limit: int) -> tuple[str, bool]` trong `app/ai/text.py`; `ask(engine, ml, llm, user_id, question, filters, *, today=None)`; `AskResult` không có `mask_counts`; `AiSettings` không có `masking`.

- [ ] **Step 1: Viết test thất bại**

```python
def test_cut_text_not_needed(): assert cut_text("abc", 5) == ("abc", False)
def test_cut_text_at_paragraph(): assert cut_text("aaaaaa\n\nbbbb\ncc", 12) == ("aaaaaa", True)
def test_cut_text_at_line(): assert cut_text("aaaaaa\nbbbb", 8) == ("aaaaaa", True)
def test_cut_text_hard_when_no_break(): assert cut_text("a" * 20, 8) == ("a" * 8, True)
def test_cut_text_ignores_break_in_first_half(): assert cut_text("a\n" + "b" * 20, 10) == ("a\n" + "b" * 8, True)

async def test_sends_original_text(client, ml, llm, world):
    await ask(client)
    assert "田中 健二" in llm.sent_text() and "1,250,000円" in llm.sent_text()
    assert "[PERSON_" not in llm.requests[-1]["user"]

async def test_no_ner_call(client, ml, llm, world):
    await ask(client); assert ml.ner_inputs == []

async def test_prompts_do_not_mention_placeholders():
    assert "placeholder" not in ANALYSIS_SYSTEM + ANSWER_SYSTEM

async def test_label_like_text_kept_verbatim(client, ml, llm, world, owner_engine):
    # mail có "[PERSON_1]", AI trả "[PERSON_1]": request và câu trả lời giữ nguyên chuỗi

async def test_plan_values_used_as_is(client, llm, world): ...  # sender, keywords không qua unmask
```

Xóa khỏi `test_ai_api.py` các test chỉ kiểm việc che ở `/ask`. Giữ `test_ask_log_has_no_question`, test mail của B không vào request của A.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run --directory backend pytest tests/test_ask_text.py tests/test_ai_api.py -v`
Expected: FAIL

- [ ] **Step 3: Viết `text.py`, sửa `ask.py`, prompt, API theo spec mục 5**

`cut_text` theo phần đầu của `cut_masked`: tìm `"\n\n"` rồi `"\n"` trong `[limit // 2, limit]`, `rstrip()`, không có thì cắt tại `limit`. Bỏ `WINDOW_CHARS`, `chunk_window`, `_mask_ranges`. `SOURCES` đọc lại đúng đoạn `[start, end)`. Bỏ cột và phần chỉ phục vụ học từ điển (`full_text`). `AskSent` giữ nguyên. Chuyển `build_masker`, `learn_rows`, `_participants` sang `translate.py`.

- [ ] **Step 4: Chạy test, xác nhận đạt**

Run: `uv run --directory backend pytest tests/test_ask_text.py tests/test_ai_api.py tests/test_translate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: bỏ che thông tin ở hỏi đáp (spec 28)"
```

### Task 4: Bỏ che ở dịch mail, xóa Masker

**Files:**
- Delete: `backend/app/ai/masking.py`, `backend/app/ai/names.py`, `backend/app/ai/people.py`, `backend/tests/test_masking.py`
- Modify: `backend/app/ai/translate.py`, `backend/app/ai/glossary.py`, `backend/app/ai/prompts.py` (`TRANSLATE_SYSTEM`, `TRANSLATE_PROMPT_VERSION = 2`)
- Modify: `backend/app/auto_translate.py`, `backend/app/tasks/__init__.py`, `backend/app/api/ai_routes.py`, `backend/app/ml_client.py`
- Test: `backend/tests/mlfake.py`, `backend/tests/test_translate.py`, `backend/tests/test_translate_api.py`, `backend/tests/test_translate_worker.py`, `backend/tests/test_auto_translate.py`, `backend/tests/test_glossary.py`, `backend/tests/test_ml_client.py`

**Interfaces:**
- Consumes: `cut_text` từ Task 3.
- Produces: `produce_translation(engine, llm, user_id, message_id) -> TranslationResult`; `translate_next(api_engine, resolver, user_id) -> RunStats`; `check_translation(result_text: str, matches, *, truncated: bool) -> list[str]`; `nfkc_map` trong `app/ai/glossary.py`.

- [ ] **Step 1: Viết test thất bại**

```python
async def test_translate_runs_without_ml_service(...):
    # gọi produce_translation không có ml, request có text gốc, không có nhãn

async def test_translate_route_works_when_ml_down(client, ml, llm, world):
    # POST /messages/{id}/translate bằng helper có sẵn của test_translate_api.py
    ml.down = True; assert (await translate(client, world.est)).status_code == 200

async def test_worker_translates_without_ml(...):
    # additional_context không có "ml", có "resolver": translate_user vẫn chạy

def test_glossary_matches_person_name_in_raw_text():
    # thuật ngữ là tên người khớp được, trước đây bị nhãn chặn

def test_check_translation_codes():
    assert check_translation("bản dịch", [match_missing], truncated=True) == ["glossary_missing", "truncated"]

async def test_old_prompt_version_marks_stale_but_not_rerun(...):
    # bản dịch prompt_version=1 hiện cũ; translate_next không gọi AI lại

async def test_retry_row_with_mask_unstable_runs_again(...):
    # message_auto_runs state retry, last_error mask_unstable, đã hết giờ chờ: dịch xong thành done

async def test_label_like_text_in_mail_kept_verbatim(...): ...
```

- [ ] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run --directory backend pytest tests/test_translate.py tests/test_auto_translate.py tests/test_glossary.py -v`
Expected: FAIL

- [ ] **Step 3: Sửa code theo spec mục 5**

`_mask_mail` đổi thành hàm thuần `_prepare_mail(subject, body, context, entries)`: `cut_text` ba phần rồi `match_glossary`. Chuyển `nfkc_map`, `_SEMI_VOICED` sang `glossary.py`. Bỏ `blocked`, `labelled`. Bỏ `mask_unstable` khỏi `PER_MAIL_ERRORS`, bỏ `except MlUnavailable`. `translate_sweep`, `translate_user`: thay guard `_ml(context)` bằng `"resolver" in context.additional_context`, giữ guard cho `embed_*`. Route dịch bỏ `get_ml`. `ml_client.py` bỏ `ner`, `NerEntity`, `NER_LABELS`, `NER_MAX_TEXTS`; `FakeMl` bỏ `ner`, `ner_down`, `ner_names`, `ner_inputs`, và các tham số tương ứng ở fixture `ml`.

- [ ] **Step 4: Xóa `masking.py`, `names.py`, `people.py`, `test_masking.py`**

Run: `git grep -nE "masking|Masker|people_mod|companies_in|LABEL_RE|cut_masked|mask_unstable" -- backend/app backend/tests`
Expected: chỉ còn chuỗi `mask_unstable` trong `test_retry_row_with_mask_unstable_runs_again`.

- [ ] **Step 5: Chạy toàn bộ test backend, lint, kiểu**

Run: `uv run --directory backend pytest ; uv run --directory backend ruff check . ; uv run --directory backend ruff format . ; uv run --directory backend pyright`
Expected: PASS, không lỗi.

- [ ] **Step 6: Commit**

```bash
git add -u backend
git add backend/app backend/tests
git commit -m "feat: bỏ che ở dịch mail, xóa Masker (spec 28)"
```

### Task 5: Xóa NER khỏi ml-service

**Files:**
- Delete: `ml-service/app/ner.py`, `ml-service/tests/test_ner.py`
- Modify: `ml-service/app/main.py`, `ml-service/app/config.py`, `ml-service/app/run.py`, `ml-service/pyproject.toml`, `ml-service/uv.lock`
- Test: `ml-service/tests/test_api.py`, `ml-service/tests/test_run.py`

**Interfaces:**
- Produces: `create_app(load_embedder: Callable[[], Embedder], settings: Settings | None = None) -> FastAPI`; `Health(status, embed_model, dim, max_seq_length)`.

- [ ] **Step 1: Viết test thất bại**

```python
def test_health(client):
    assert client.get("/health").json() == {
        "status": "ok", "embed_model": "fake/model", "dim": 3, "max_seq_length": 16,
    }

def test_ner_removed(client):
    assert client.post("/ner", json={"texts": ["x"]}).status_code == 404

def test_no_spacy_import():
    # chạy python -c "import app.main; import sys; assert 'spacy' not in sys.modules"
```

Xóa test `/ner`, `FakeRecognizer`, `BrokenRecognizer`.

- [ ] **Step 2: Chạy test, xác nhận thất bại**

Run: `uv run --directory ml-service pytest tests/test_api.py -v`
Expected: FAIL

- [ ] **Step 3: Sửa code, gỡ dependency**

Bỏ `spacy`, `ginza`, `ja-ginza`, `en-core-web-sm` và mục `[tool.uv.sources]` tương ứng. Bỏ `MAX_NER_CHARS`, `ML_NER_JA`, `ML_NER_EN`.

Run: `uv lock --directory ml-service ; uv sync --directory ml-service`
Expected: `uv.lock` không còn `spacy`, `ginza`.

- [ ] **Step 4: Chạy test, lint, kiểu**

Run: `uv run --directory ml-service pytest ; uv run --directory ml-service ruff check . ; uv run --directory ml-service pyright`
Expected: PASS, không lỗi.

- [ ] **Step 5: Commit**

```bash
git add -u ml-service
git commit -m "feat: xóa NER khỏi ml-service (spec 28)"
```

### Task 6: Web

**Files:**
- Modify: `web/openapi.json`, `web/src/api/schema.ts` (sinh tự động), `web/src/pages/AiSettingsPage.tsx`, `web/src/pages/AskPage.tsx`, `web/src/components/MessageTranslation.tsx`
- Test: `web/src/pages/AiSettingsPage.test.tsx`, `web/src/pages/AskPage.test.tsx`, `web/src/components/MessageTranslation.test.tsx`

**Interfaces:**
- Consumes: `AiProviderCustom.public_host` (Task 1); `AiSettings` không có `tiers`, `custom_provider`, `masking` (Task 2, 3); `AskResult` không có `mask_counts` (Task 3).

- [ ] **Step 1: Sinh lại kiểu API**

Run: `$env:PYTHONUTF8 = "1"; npm --prefix web run gen:api`
Expected: `schema.ts` không còn `mask_counts`, `tiers`, `estimated_usd`.

- [ ] **Step 2: Viết test thất bại**

```tsx
it("hiện ô tên model cho từng tác vụ, không còn ba cấp và thẻ che thông tin", ...)
it("luôn báo mail không che sẽ gửi tới host", ...)      // "không che" và host
it("cảnh báo khi public_host là true", ...)            // "địa chỉ công cộng"
it("không cảnh báo công cộng khi public_host là false", ...)
it("AskPage không còn chữ đã che", ...)                // "Nội dung đã gửi cho AI" còn, "đã che" không
it("bản dịch cũ có labels_missing vẫn hiện cảnh báo", ...)
```

- [ ] **Step 3: Chạy test, xác nhận thất bại**

Run: `npm --prefix web run test -- AiSettingsPage AskPage MessageTranslation`
Expected: FAIL

- [ ] **Step 4: Sửa giao diện theo spec mục 3, 5**

Chữ đúng nguyên văn: "Mail **không che** sẽ gửi tới `{host}`." và cảnh báo "Đây là địa chỉ công cộng, mail sẽ ra ngoài mạng nội bộ." `TierForm` đổi thành `ModelForm`. Bỏ cột giá ở bảng thống kê. Sửa thông báo `ml_unavailable` trong `MessageTranslation` thành nội dung không nói về che; giữ `labels_missing`, `labels_unknown`.

- [ ] **Step 5: Chạy test, lint, build**

Run: `npm --prefix web run test ; npm --prefix web run lint ; npm --prefix web run build`
Expected: PASS, không lỗi.

- [ ] **Step 6: Commit**

```bash
git add web
git commit -m "feat: cài đặt AI theo model nội bộ trên web (spec 28)"
```

### Task 7: Tài liệu, chạy thử, rà soát, push

**Files:**
- Modify: spec 17, 20, 21, 22, 24, 26, 27, 28, danh mục spec, `docs/CHANGELOG.md`, `CLAUDE.md`, `backend/CLAUDE.md`, `ml-service/CLAUDE.md`, `web/CLAUDE.md`, `email-assistant-plan.md`

- [ ] **Step 1: Đo ml-service sau khi gỡ NER**

Thời gian khởi động, bộ nhớ process. Ghi vào `ml-service/CLAUDE.md`, thay số cũ, bỏ dòng NER.

- [ ] **Step 2: Chạy thử đầu cuối trên DB test**

Script tạm trong scratchpad, không commit: server tương thích OpenAI giả trên `127.0.0.2`, lưu nhà cung cấp qua API, đặt tên model, hỏi đáp và dịch trên profile giả. Kiểm request nhận được có text gốc, không có nhãn, `model` đúng tên đã nhập. Đặt `ANTHROPIC_API_KEY` giả và xóa nhà cung cấp: `/ask` trả 503.

- [ ] **Step 3: Cập nhật tài liệu**

Spec 28: trạng thái xong, thêm kết quả đo. Sửa dòng cập nhật ở đầu spec 17, 20, 21, 22, 24, 26 thành "đã làm ở spec 28, nội dung che chỉ còn ý nghĩa lịch sử". `CLAUDE.md` gốc: bỏ đoạn "Code chưa theo kịp", sửa bảng trạng thái, bước chạy thử 4–5. `backend/CLAUDE.md`: mục "Cấu trúc" bỏ ba file đã xóa, thêm `app/ai/text.py`; mục "Gọi AI" viết lại theo code mới, bỏ mọi quy tắc về `Masker`, `ANTHROPIC_API_KEY`, ba cấp. `web/CLAUDE.md`, `ml-service/CLAUDE.md`: bỏ dòng che, NER. CHANGELOG: mục "Đã làm", số test. Grep `Masker`, `ANTHROPIC_API_KEY`, `Haiku`, `GiNZA`, `/ner` trong mọi `*.md` còn hiệu lực.

- [ ] **Step 4: Chạy toàn bộ kiểm tra**

Run: `uv run --directory backend pytest ; uv run --directory ml-service pytest ; npm --prefix web run test ; npm --prefix web run lint ; npm --prefix web run build`
Expected: PASS, không lỗi. Dán kết quả vào báo cáo.

- [ ] **Step 5: Chạy `security-reviewer` trên các commit của spec 28, sửa phát hiện**

Ghi phần sửa vào mục "Sửa sau review (ngày)" của spec 28.

- [ ] **Step 6: Commit tài liệu**

```bash
git add -u docs CLAUDE.md backend/CLAUDE.md ml-service/CLAUDE.md web/CLAUDE.md email-assistant-plan.md
git commit -m "docs: cập nhật tài liệu sau khi chuyển sang model nội bộ (spec 28)"
```

- [ ] **Step 7: Push `master`**

Run: `git push origin master`
Expected: `origin/master` trùng `master`. Báo người dùng chạy `alembic upgrade head` trên DB dev và `uv sync --directory ml-service`.
