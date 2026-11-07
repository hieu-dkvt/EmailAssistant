# Cấu hình dự án và gán mail theo bộ lọc: kế hoạch triển khai

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Người dùng khai báo dự án trên web (bộ lọc From, To, CC, Subject cùng các trường lưu sẵn), worker tự gán mail vào dự án, và danh sách thread hiện tên dự án.

**Architecture:** Bảng `projects`, `message_projects`, `user_project_state` (RLS theo người dùng). `app/projects.py` chứa hàm khớp thuần, CRUD và luồng gán. Mail mới được gán ngay trong transaction của `rethread_user`; sửa bộ lọc tăng `rules_version` và job `assign_user_projects` quét lại theo lô. Web thêm trang `/settings/projects` và Badge trên danh sách thread.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 async + psycopg 3, Alembic, Procrastinate 3.10, React 19, TanStack Query 5, shadcn/ui, Vitest.

**Spec:** [docs/superpowers/specs/2026-10-31-project-settings-design.md](../specs/2026-10-31-project-settings-design.md) (spec 25). Đọc spec cùng kế hoạch này; spec giữ nguyên văn các quy tắc và thông báo.

## Global Constraints

- Chỉ dữ liệu giả `*.example.*`. Không mở web, không gọi API đọc mail, không đọc `projects` trên DB dev `mailassist`; chạy thử bằng DB test.
- Không log giá trị bộ lọc, tên dự án, nội dung mail. Log chỉ có `user`, số lượng, thời gian. Lỗi DB chỉ log `sqlstate` và tên ràng buộc.
- Worker chạy `app_worker` (BYPASSRLS): mọi truy vấn có `user_id = :u`. API chạy trong `user_session`.
- Giới hạn: 100 dự án mỗi người; mỗi ô danh sách 20 giá trị, mỗi giá trị 1–255 ký tự; `name` 1–200; URL tối đa 2.000; đường dẫn local tối đa 1.000; `backlog_project_key`, `redmine_project` tối đa 200.
- Lô quét `ASSIGN_BATCH = 2_000`, job tối đa `ASSIGN_JOB_SECONDS = 10.0`.
- Không regex trên dữ liệu người dùng. So khớp bằng `in` sau NFKC + `casefold`.
- Không gọi URL, không đọc đường dẫn local, không gọi AI, không lưu API key.
- Web: không viết tay kiểu API, không thêm màu hay font, không lưu dữ liệu vào storage trình duyệt.
- Ruff, pyright, ESLint không báo lỗi. Đổi code thì cập nhật tài liệu cùng lượt.
- Không làm trên `master`: dùng nhánh `feature/project-settings`. Không đưa `backend/Mail automation spec.md` vào commit.

Lệnh dùng chung, cần DB đang chạy (`docker compose up -d`):

```
uv run --directory backend pytest <file> -v
uv run --directory backend ruff check . ; uv run --directory backend ruff format . ; uv run --directory backend pyright
```

## Review Focus

1. `to_addrs`, `cc_addrs` có phần tử sai dạng (không phải dict, thiếu `addr`, `addr` không phải chuỗi): bỏ qua phần tử đó, không làm hỏng lô. Test ở Task 2.
2. Dự án bị xóa trong lúc worker đang gán: job không lỗi FK, chỉ bỏ dự án đó. Test ở Task 4.
3. Hai request cùng lưu một tên dự án: request thua nhận 409 `project_name_taken`, không phải 500. Test ở Task 3.
4. Người dùng gõ giá trị vào ô rồi bấm thẳng "Lưu dự án" mà chưa nhấn Enter: giá trị đó vẫn được lưu. Test ở Task 6.
5. URL có `?project=<id>` của dự án đã xóa hoặc không tồn tại: trang hiện danh sách và thông báo chọn dự án, không vỡ. Test ở Task 6.

---

### Task 1: Migration, model và hạ tầng test

**Files:**
- Create: `backend/migrations/versions/0017_projects.py`, `backend/tests/test_projects_db.py`
- Modify: `backend/app/db/models.py`, `backend/tests/conftest.py`, `backend/tests/dbutil.py`, `backend/tests/test_rls.py`

**Interfaces:**
- Produces: bảng và cột đúng spec mục 4; model `Project`, `MessageProject`, `UserProjectState`; helper test `add_project(conn, user_id, name, **cols) -> int`; `add_mail(..., cc: tuple[str, ...] = ())`.

- [ ] **Step 1: Tạo nhánh**

```bash
git switch -c feature/project-settings
```

- [ ] **Step 2: Thêm helper test và viết test thất bại**

Thêm vào `MAIL_TABLES` trong `conftest.py`: `projects`, `message_projects`, `user_project_state`. Thêm `"projects": True, "message_projects": True, "user_project_state": True` vào bảng RLS ở `test_rls.py:54`. `add_mail` thêm tham số `cc` ghi vào `cc_addrs`.

`tests/test_projects_db.py`:

```python
def test_project_is_private(owner_engine, api_engine, seeded):  # B không thấy dự án của A
def test_api_role_cannot_write_message_projects(api_engine, seeded):  # InsufficientPrivilege
def test_api_role_cannot_set_project_id_or_change_owner(api_engine, seeded):  # InsufficientPrivilege
def test_message_projects_rejects_other_users_project(owner_engine, seeded):  # IntegrityError, FK kép
def test_deleting_message_removes_link(owner_engine, seeded):  # count = 0
def test_deleting_project_removes_link_keeps_message(owner_engine, seeded):
def test_project_name_unique_per_user(owner_engine, seeded):  # A và B dùng chung tên được, A trùng thì lỗi
```

- [ ] **Step 3: Chạy test, mong đợi FAIL** vì chưa có bảng.

Run: `uv run --directory backend pytest tests/test_projects_db.py -v`

- [ ] **Step 4: Viết migration `0017` (revises `0016`) và model**

SQL viết tay theo `0016_ai_provider.py`, gồm RLS, policy `*_owner`, GRANT đúng spec mục 4, `downgrade` xóa ba bảng. Model theo kiểu trong `models.py` (đặt tên ràng buộc theo `NAMING_CONVENTION`) để `test_schema.py` so khớp được.

- [ ] **Step 5: Chạy test**

Run: `uv run --directory backend pytest tests/test_projects_db.py tests/test_schema.py tests/test_rls.py -v`
Expected: PASS

- [ ] **Step 6: Commit** `git add` các file trên cùng tài liệu spec đã duyệt, message `feat: thêm bảng dự án và liên kết mail (spec 25)`.

---

### Task 2: Hàm khớp bộ lọc

**Files:**
- Create: `backend/app/projects.py`, `backend/tests/test_projects.py`

**Interfaces:**
- Produces:
  - `normalize(value: str) -> str`: NFKC rồi `casefold`.
  - `addresses(value: Any) -> tuple[str, ...]`: từ JSONB địa chỉ, bỏ phần tử sai dạng.
  - `@dataclass(frozen=True) MailFields(from_addr: str | None, to: tuple[str, ...], cc: tuple[str, ...], subject: str | None)`
  - `@dataclass(frozen=True) ProjectRule(id: int, from_: tuple[str, ...], to: tuple[str, ...], cc: tuple[str, ...], subject: tuple[str, ...])`, giá trị đã `normalize`.
  - `match_projects(mail: MailFields, rules: Sequence[ProjectRule]) -> set[int]`

- [ ] **Step 1: Viết test thất bại** (bảng ca, dùng `pytest.mark.parametrize`)

```python
CUSTOMER = ("@sample-a.example.jp",)
TAG = ("【サンプルA】",)
RULE = ProjectRule(1, CUSTOMER, CUSTOMER, CUSTOMER, TAG)

@pytest.mark.parametrize("mail, expected", [
    (mail(frm="tanaka@sample-a.example.jp", subject="【サンプルA】仕様確認"), {1}),
    (mail(frm="pm@vn.example.com", to=("tanaka@sample-a.example.jp",), subject="Re: 【サンプルA】仕様確認"), {1}),
    (mail(frm="pm@vn.example.com", cc=("tanaka@sample-a.example.jp",), subject="【サンプルA】共有"), {1}),
    (mail(frm="tanaka@sample-a.example.jp", subject="【サンプルB】見積"), set()),
    (mail(frm="x@other.example.jp", subject="【サンプルA】"), set()),
    (mail(frm="tanaka@sample-a.example.jp", subject=None), set()),
    (mail(frm=None, to=("tanaka@sample-a.example.jp",), subject="【サンプルA】"), {1}),
])
def test_rule_table(mail, expected): assert match_projects(mail, [RULE]) == expected

def test_all_filters_empty_matches_nothing(): ...       # ProjectRule(1, (), (), (), ()) → set()
def test_subject_only(): ...                            # chỉ subject thì mọi địa chỉ đều được
def test_address_only_ignores_subject(): ...
def test_multiple_projects(): ...                       # hai rule cùng khớp → {1, 2}
def test_full_width_and_case(): ...                     # normalize("ＳＡＭＰＬＥ-1") khớp "[sample-1]"
def test_addresses_skips_bad_items(): ...               # [{"addr": "a@x.example"}, "lạ", {"name": "n"}, {"addr": 5}, None] → ("a@x.example",)
def test_addresses_handles_none(): ...                  # addresses(None) → ()
```

- [ ] **Step 2: Chạy test, mong đợi FAIL** vì chưa có module.

Run: `uv run --directory backend pytest tests/test_projects.py -v`

- [ ] **Step 3: Viết các hàm trên** theo công thức ở spec mục 3. `MailFields` nhận giá trị thô; `match_projects` tự `normalize` giá trị mail một lần cho mỗi mail.

- [ ] **Step 4: Chạy test**, mong đợi PASS.

- [ ] **Step 5: Commit** `feat: thêm hàm khớp bộ lọc dự án`.

---

### Task 3: API dự án

**Files:**
- Create: `backend/app/api/project_routes.py`, `backend/tests/test_projects_api.py`
- Modify: `backend/app/web_schemas.py`, `backend/app/projects.py`, `backend/app/main.py:108-111`

**Interfaces:**
- Consumes: bảng Task 1.
- Produces:
  - Schema `ProjectInput(StrictModel)`, `Project`, `ProjectList(items: list[Project], assigning: bool)`, `ProjectRef(id: int, name: str)`.
  - `class ProjectError(Exception)` có `status_code`, `code`, `detail`, theo mẫu `OverrideError`.
  - `async list_projects(s, user_id) -> ProjectList`, `async create_project(s, user_id, req: ProjectInput) -> Project`, `async update_project(s, user_id, project_id, req) -> Project`, `async delete_project(s, user_id, project_id) -> None`.

- [ ] **Step 1: Viết test thất bại** (dùng `web_client`, `add_web_user`, `login`, `CSRF`; engine async role api như `test_web_api.py`)

```python
FILTERS = {"filter_from": ["@sample-a.example.jp"], "filter_subject": ["【サンプルA】"]}

async def test_create_list_update_delete(...)            # 201, GET thấy, PUT đổi, DELETE 204
async def test_create_without_filter_keeps_version(...)  # rules_version không tăng
async def test_create_with_filter_bumps_version(...)     # rules_version == 1, assigning True
async def test_update_filter_bumps_version(...)
async def test_update_other_field_keeps_version(...)     # đổi redmine_url: version giữ nguyên
async def test_delete_keeps_version(...)
async def test_name_taken_409(...)                       # code == "project_name_taken"
async def test_name_taken_on_concurrent_save(...)        # giả lập: IntegrityError từ DB → 409, không phải 500
async def test_project_limit_409(...)                    # 100 dự án rồi tạo thêm: code == "project_limit"
async def test_other_user_gets_404(...)                  # PUT, DELETE dự án của A bằng phiên B
async def test_write_requires_csrf_header(...)           # 403
async def test_requires_session(...)                     # 401
async def test_collector_token_rejected(...)             # Bearer token: 401
async def test_id_out_of_range_422(...)                  # /projects/9999999999999999999
@pytest.mark.parametrize("bad", [
    {"name": "  "}, {"name": "a\x01"}, {"filter_from": ["x"] * 21}, {"filter_from": ["a" * 256]},
    {"redmine_url": "https://user:pass@redmine.example"}, {"docs_repo_url": "ftp://x.example"},
    {"docs_repo_url": "ssh://git:secret@git.example/a.git"}, {"unknown": 1},
])
async def test_invalid_422_without_echo(bad, ...)        # 422, giá trị đã gửi không có trong response
async def test_values_trimmed_and_deduplicated(...)      # [" a@x.example ", "A@X.EXAMPLE"] → ["a@x.example"]
async def test_git_ssh_url_accepted(...)                 # "git@git.example:team/spec.git" lưu được
async def test_filter_values_not_logged(caplog, ...)     # không thấy giá trị bộ lọc trong caplog
```

- [ ] **Step 2: Chạy test, mong đợi FAIL**.

Run: `uv run --directory backend pytest tests/test_projects_api.py -v`

- [ ] **Step 3: Viết schema** trong `web_schemas.py`: validator dùng lại cho các ô danh sách và URL theo spec mục 6. Thông báo lỗi validator là chuỗi cố định tiếng Việt, không chứa giá trị. So khớp lược URL bằng `urllib.parse.urlsplit`, dạng `git@host:path` nhận riêng.

- [ ] **Step 4: Viết CRUD trong `projects.py`**

Mọi câu lọc `user_id = :u` tường minh. `BUMP_RULES` là `INSERT … ON CONFLICT (user_id) DO UPDATE SET rules_version = user_project_state.rules_version + 1, updated_at = now()`. `update_project` đọc 4 ô lọc cũ trong cùng transaction để so sánh. Bắt `IntegrityError` có `sqlstate = '23505'` trong khi ghi rồi đổi thành `ProjectError(409, "project_name_taken", …)`.

- [ ] **Step 5: Viết `project_routes.py`** theo mẫu `ai_routes.py` (`read_body`, `model_validate_json`, `json_body(ProjectInput)`, `Path(gt=0, le=MAX_BIGINT)`), đổi `ProjectError` thành `ApiError`, rồi `include_router` trong `main.py`.

- [ ] **Step 6: Chạy test** `tests/test_projects_api.py tests/test_app_hardening.py`, mong đợi PASS.

- [ ] **Step 7: Commit** `feat: thêm API cấu hình dự án`.

---

### Task 4: Gán mail bằng worker

**Files:**
- Create: `backend/app/db/locks.py`
- Modify: `backend/app/projects.py`, `backend/app/processing.py`, `backend/app/tasks/__init__.py`, `backend/app/cli.py`, `backend/app/review.py`
- Test: `backend/tests/test_projects_db.py`, `backend/tests/test_tasks.py`, `backend/tests/test_review.py`

**Interfaces:**
- Consumes: `match_projects`, `MailFields`, `ProjectRule`, `addresses`.
- Produces:
  - `app/db/locks.py`: `USER_MAIL_LOCK` (câu `LOCK` hiện có ở `processing.py:33`, chuyển sang đây để tránh import vòng; `processing.py` import lại).
  - `async load_rules(s, user_id) -> list[ProjectRule]`
  - `async assign_messages(s, user_id, message_ids: Sequence[int], rules: Sequence[ProjectRule]) -> int`
  - `@dataclass AssignStats(messages: int = 0, more: bool = False)`
  - `async assign_batch(engine, user_id, batch: int = ASSIGN_BATCH) -> AssignStats`
  - `async assign_user(engine, user_id, *, budget_seconds: float = ASSIGN_JOB_SECONDS, batch: int = ASSIGN_BATCH) -> AssignStats`
  - `async pending_rule_users(engine) -> list[int]`
  - `async reset_scan(engine, user_id) -> bool`: `False` khi người dùng chưa có dòng state.
  - Task `mail:assign_user_projects`, periodic `project_rules_sweep`; `defer_assign(app, user_id) -> int`.

- [ ] **Step 1: Viết test thất bại**

`test_projects_db.py` (chạy `process_user`, `assign_user` bằng fixture `worker`):

```python
async def test_new_mail_assigned_by_process_user(...)    # khớp thì có dòng, thread_id đã có
async def test_no_projects_is_noop(...)
async def test_process_user_second_run_no_duplicates(...)
async def test_scan_adds_and_removes_on_filter_change(...)  # bộ lọc mới: mail A ra, mail B vào
async def test_scan_runs_in_batches(...)                 # batch=2 với 5 mail: more True rồi đủ 5
async def test_filter_change_mid_scan_restarts(...)      # tăng version giữa hai lô: scan_after_id về 0, kết quả theo bộ lọc mới
async def test_scan_covers_mail_with_smaller_id_via_thread_path(...)  # mail chưa gom id nhỏ hơn scan_after_id: process_user vẫn gán
async def test_assign_skips_project_deleted_after_rules_loaded(...)   # load_rules, xóa dự án, assign_messages không lỗi
async def test_user_isolation(...)                       # bộ lọc của A không gán mail của B
async def test_multiple_projects(...)                    # mail khớp 2 dự án có 2 dòng
async def test_delete_folder_mail_removes_links(...)
async def test_reset_scan_requires_state(...)
async def test_worker_logs_have_no_filter_values(caplog, ...)
```

`test_tasks.py`: `test_project_rules_sweep_defers_assign` (có người đang chờ thì job `mail:assign_user_projects` chạy xong, `applied_rules_version == rules_version`); `test_assign_job_defers_next_when_more` (monkeypatch `projects.assign_user`: lần đầu trả `AssignStats(1, more=True)`, lần sau `AssignStats(0, more=False)`; hàm được gọi hai lần).

`test_review.py`: `stats_report` có số mail đã gán dự án và không chứa tên dự án.

- [ ] **Step 2: Chạy test, mong đợi FAIL**.

- [ ] **Step 3: Viết `load_rules`, `assign_messages`**

`assign_messages` chia `message_ids` thành lô `ASSIGN_BATCH`; mỗi lô đọc `id, from_addr, to_addrs, cc_addrs, subject`, chạy `match_projects`, xóa dòng cũ của các mail đó rồi chèn lại. Câu chèn dùng `unnest` JOIN `projects p ON p.id = c.project_id AND p.user_id = :u`, kèm `ON CONFLICT DO NOTHING`, để dự án vừa bị xóa không gây lỗi FK.

- [ ] **Step 4: Gắn vào `rethread_user`** (`processing.py`)

Trước vòng ghi, lấy `new_ids = [r.id for r in rows if r.thread_id is None]`. Sau `UPDATE_MESSAGE_THREADS`, trong cùng transaction: nếu `new_ids` khác rỗng và `load_rules` có dự án thì gọi `assign_messages`.

- [ ] **Step 5: Viết `assign_batch`, `assign_user`, `pending_rule_users`, `reset_scan`**

`assign_batch` theo đúng 5 bước ở spec mục 5, mỗi lô một transaction giữ `USER_MAIL_LOCK`, đọc `rules_version` trước `load_rules`. `assign_user` theo mẫu `embed_user`. Log `assign user=%s messages=%d more=%s ms=%d`. `reset_scan` ghi `scan_version = -1`, `applied_rules_version = rules_version - 1`.

- [ ] **Step 6: Thêm task** vào `_blueprint` (`tasks/__init__.py`)

`project_rules_sweep` theo mẫu `apply_overrides` (cron `* * * * * */5`, `queueing_lock`). `assign_user_projects` bọc `sanitized`, còn `stats.more` thì `defer_assign`. `defer_assign` dùng `lock=user_lock(user_id)`, `queueing_lock=f"projects:{user_id}"`.

- [ ] **Step 7: CLI và thống kê**

`assign-projects --email <e>` thêm vào `MAIL_COMMANDS`, gọi `reset_scan` rồi `assign_user` với `budget_seconds=math.inf`. Thành công in `Người dùng id=<id>: đã xét <n> mail, <m> mail có dự án.`; chưa có dự án in `Người dùng id=<id>: chưa có dự án.`. `review.py` thêm `PROJECT_STATS` (đếm `DISTINCT message_id`), `stats_report` thêm một dòng. Sửa docstring đầu `cli.py`.

- [ ] **Step 8: Chạy test**

Run: `uv run --directory backend pytest tests/test_projects_db.py tests/test_processing.py tests/test_tasks.py tests/test_review.py -v`
Expected: PASS

- [ ] **Step 9: Commit** `feat: worker gán mail vào dự án theo bộ lọc`.

---

### Task 5: Tên dự án trên danh sách thread

**Files:**
- Modify: `backend/app/threads_view.py`, `backend/app/web_schemas.py:92-100`
- Test: `backend/tests/test_web_api.py`

**Interfaces:**
- Consumes: `ProjectRef`.
- Produces: `ThreadSummary.projects: list[ProjectRef]` (bắt buộc, không default); `async projects_of(session, thread_ids: list[int]) -> dict[int, list[ProjectRef]]`.

- [ ] **Step 1: Viết test thất bại**

```python
async def test_thread_list_shows_project_when_one_mail_matches(...)
async def test_thread_list_shows_all_projects_sorted_by_name(...)
async def test_thread_list_empty_projects_when_no_match(...)      # projects == []
async def test_thread_list_pagination_unchanged_with_projects(...)  # cursor, next_cursor như cũ
async def test_thread_list_hides_other_users_projects(...)
```

- [ ] **Step 2: Chạy test, mong đợi FAIL**.

- [ ] **Step 3: Viết `projects_of`** theo mẫu `folders_of`, một truy vấn riêng cho `thread_id` của trang: `SELECT DISTINCT` qua `messages` → `message_projects` → `projects`, join kèm `user_id`, sắp theo `p.name`. Gọi sau khi đã cắt trang trong `list_threads`.

- [ ] **Step 4: Kiểm tra truy vấn** bằng `EXPLAIN (ANALYZE, BUFFERS)` trên DB test có dữ liệu tổng hợp, xác nhận dùng `ix_messages_user_id_thread_id`. Không chạy trên DB dev.

- [ ] **Step 5: Chạy test** `tests/test_web_api.py`, mong đợi PASS.

- [ ] **Step 6: Commit** `feat: hiện dự án trong API danh sách thread`.

---

### Task 6: Giao diện web

**Files:**
- Create: `web/src/components/TagInput.tsx`, `web/src/components/TagInput.test.tsx`, `web/src/pages/ProjectsPage.tsx`, `web/src/pages/ProjectsPage.test.tsx`, `web/src/pages/ThreadListPage.test.tsx`
- Modify: `web/src/api/client.ts`, `web/src/api/hooks.ts`, `web/src/App.tsx`, `web/src/components/Layout.tsx`, `web/src/pages/ThreadListPage.tsx`, sinh lại `web/openapi.json`, `web/src/api/schema.ts`

**Interfaces:**
- Consumes: API Task 3, `ThreadSummary.projects` Task 5.
- Produces:
  - `client.ts`: kiểu `Project`, `ProjectList`, `ProjectBody`; `ApiError.fields: Record<string, string>` lấy từ `detail` dạng mảng của lỗi 422 (khóa là tên trường đầu tiên trong `loc`, bỏ `"body"`; bỏ tiền tố `Value error, `).
  - `hooks.ts`: `useProjects()` (`refetchInterval` 2 giây khi `assigning`), `useCreateProject()`, `useUpdateProject()`, `useDeleteProject()`. Mutation thành công thì invalidate `keys.projects` và khóa `["threads"]`.
  - `TagInput({ id, label, hint, addLabel, values, onChange, error, draftRef })`.

- [ ] **Step 1: Sinh lại client**

Run: `npm --prefix web run gen:api`
Expected: `web/src/api/schema.ts` có `/api/v1/projects`, `Project`, `ThreadSummary.projects`.

- [ ] **Step 2: Viết test thất bại**

`TagInput.test.tsx`: Enter thêm và không gửi form; dấu phẩy thêm; rời ô thêm phần đang gõ; Backspace ở ô trống bỏ giá trị cuối; dán `a@x.example, b@x.example\nc@x.example` thành 3 giá trị; nút bỏ có `aria-label`; không thêm trùng.

`ProjectsPage.test.tsx` (stub `fetch` như `AiSettingsPage.test.tsx`):

```ts
it("trống: hiện Chưa có dự án nào và nút Thêm dự án")
it("danh sách hiện 5 điều kiện lọc và Chưa có bộ lọc")
it("chọn dự án đưa id lên URL và hiện đúng dữ liệu")
it("thêm dự án gửi POST với đủ các trường")
it("gõ dở rồi bấm Lưu dự án: giá trị vẫn nằm trong body PUT")
it("nút Lưu dự án tắt khi chưa đổi gì")
it("project_name_taken hiện dưới ô tên")
it("lỗi 422 hiện theo từng ô")
it("cảnh báo Dự án chưa có bộ lọc nên chưa nhận mail nào")
it("assigning hiện Đang gán lại mail theo bộ lọc…")
it("xóa phải xác nhận, hủy thì không gọi DELETE")
it("đổi dự án khi chưa lưu thì hỏi trước")
it("?project=999 không tồn tại: không vỡ, hiện thông báo chọn dự án")
```

`ThreadListPage.test.tsx`: 2 tên hiện Badge; 3 tên hiện 2 Badge và `+1`, `title` đủ tên; không có dự án thì không có Badge.

- [ ] **Step 3: Chạy test, mong đợi FAIL**.

Run: `npm --prefix web run test`

- [ ] **Step 4: Viết `TagInput`, sửa `client.ts`, `hooks.ts`**

`draftRef` cho form lấy phần đang gõ lúc gửi, để giá trị chưa thêm vẫn được lưu kể cả khi nhấn Enter ở ô khác.

- [ ] **Step 5: Viết `ProjectsPage`** theo spec mục 7 và bố cục A trên canvas

Thêm route `/settings/projects` trong `App.tsx`, mục "Dự án" trước "Cài đặt AI" trong `Layout.tsx`. Dự án đang chọn đọc từ `?project=`; `new` là tạo mới. Dialog dùng `components/ui/dialog`. Nhắc worker sau 30 giây còn `assigning`, đặt trong callback `setTimeout`, không gọi `setState` đồng bộ trong `useEffect`. Không lưu dữ liệu vào storage. Mọi ô có `autoComplete="off"`.

- [ ] **Step 6: Sửa `ThreadListPage`**: Badge `variant="outline"` trước số mail, tối đa 2 tên, phần còn lại `+n`, đủ tên trong `title`. Tên dài cắt bằng `max-w-32 truncate`.

- [ ] **Step 7: Chạy kiểm tra web**

Run: `npm --prefix web run test ; npm --prefix web run lint ; npm --prefix web run build`
Expected: test PASS, lint và `tsc` không lỗi.

- [ ] **Step 8: Commit** `feat: thêm màn hình dự án và nhãn dự án trên danh sách thread`.

---

### Task 7: Tài liệu, chạy thử và rà soát bảo mật

**Files:**
- Modify: `docs/superpowers/specs/2026-10-31-project-settings-design.md`, `docs/superpowers/specs/README.md`, `docs/CHANGELOG.md`, `CLAUDE.md`, `backend/CLAUDE.md`, `web/CLAUDE.md`

- [ ] **Step 1: Chạy toàn bộ kiểm tra**

Run: `uv run --directory backend pytest ; uv run --directory backend ruff check . ; uv run --directory backend pyright ; npm --prefix web run test`
Expected: không test nào lỗi, Ruff và pyright sạch.

- [ ] **Step 2: Chạy thử đầu cuối trên DB test với profile giả**

Dùng `/run` hoặc chạy app trỏ `DATABASE_URL_*` sang `mailassist_test`, nạp `data/thunderbird-profile`. Tạo dự án có bộ lọc `*.example.*`, chờ worker gán, chụp `/settings/projects` và `/threads` bằng Playwright. Đối chiếu với bố cục A. Không mở DB dev.

- [ ] **Step 3: Rà soát bảo mật** bằng subagent `security-reviewer` trên toàn nhánh; sửa phát hiện và ghi vào mục "Sửa sau review" của spec.

- [ ] **Step 4: Cập nhật tài liệu**

- Spec 25: thêm `backend/app/db/locks.py` vào dòng thành phần, trạng thái `xong`, ghi phần sửa sau review.
- `docs/superpowers/specs/README.md`, `docs/CHANGELOG.md` ("Đã làm", "Đã xử lý sau review").
- `backend/CLAUDE.md`: mục "Cấu trúc" (bỏ "Dự kiến thêm"), "Lệnh" (`assign-projects`), bẫy khi gán dự án.
- `web/CLAUDE.md`: mục "Cấu trúc" thêm `TagInput`, `ProjectsPage`, các hook mới.
- `CLAUDE.md`: mục "Trạng thái hiện tại" chuyển spec 25 sang đã có, bỏ "Đang làm".

- [ ] **Step 5: Commit** `docs: cập nhật tài liệu cấu hình dự án`.
