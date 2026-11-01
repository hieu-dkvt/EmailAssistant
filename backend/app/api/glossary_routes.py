"""API glossary cho web (spec 2026-10-31-mail-translation-design mục 9).

Xác thực bằng phiên web (WebUserId; request ghi cần header X-PMM-Request). Thuật ngữ và bản dịch
có thể chứa tên khách: log chỉ có id và số lượng, lỗi nhập chỉ nêu số dòng và tên ô.
"""

import csv
import io
import logging
import unicodedata
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.ai.glossary import MAX_TERMS_PER_SCOPE, normalize_term
from app.api.deps import get_engine, read_body
from app.api.errors import ApiError, validation_error_response
from app.api.web_deps import WebUserId, json_body
from app.db.session import user_session
from app.schemas import MAX_BIGINT
from app.web_schemas import (
    ErrorBody,
    GlossaryImportInput,
    GlossaryImportResult,
    GlossaryInput,
    GlossaryItem,
    GlossaryPage,
)

log = logging.getLogger("app.api")
router = APIRouter(prefix="/api/v1")

MAX_IMPORT_BYTES = 1024 * 1024
MAX_IMPORT_LINES = 5_000
FORMULA_CHARS = "=+-@"
BOM = b"\xef\xbb\xbf"

Engine = Annotated[AsyncEngine, Depends(get_engine)]
GlossaryId = Annotated[int, Path(gt=0, le=MAX_BIGINT)]
ProjectQuery = Annotated[int | None, Query(gt=0, le=MAX_BIGINT)]
UNAUTHORIZED: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorBody, "description": "Chưa đăng nhập"}
}
SCOPED: dict[int | str, dict[str, Any]] = {
    **UNAUTHORIZED,
    404: {"model": ErrorBody, "description": "project_not_found"},
}
WRITE: dict[int | str, dict[str, Any]] = {
    **SCOPED,
    403: {"model": ErrorBody, "description": "Thiếu header X-PMM-Request"},
    404: {"model": ErrorBody, "description": "project_not_found, glossary_not_found"},
    409: {"model": ErrorBody, "description": "glossary_duplicate, glossary_limit"},
}
IMPORT: dict[int | str, dict[str, Any]] = {
    **WRITE,
    413: {"model": ErrorBody, "description": "glossary_import_too_large"},
    422: {"model": ErrorBody, "description": "glossary_import_too_many_lines"},
}

COLUMNS = "id, project_id, term, translation, note, created_at, updated_at"
SCOPE = "user_id = :u AND project_id IS NOT DISTINCT FROM CAST(:p AS bigint)"
PROJECT_EXISTS = text("SELECT 1 FROM projects WHERE id = :p AND user_id = :u")
COUNT_SCOPE = text(f"SELECT count(*) FROM glossary_terms WHERE {SCOPE}")
INSERT = text(
    "INSERT INTO glossary_terms (user_id, project_id, term, term_norm, translation, note) "
    f"VALUES (:u, :p, :term, :norm, :translation, :note) RETURNING {COLUMNS}"
)
UPDATE = text(
    "UPDATE glossary_terms SET term = :term, term_norm = :norm, translation = :translation, "
    "note = :note, updated_at = now() WHERE id = :id AND user_id = :u "
    f"RETURNING {COLUMNS}"
)
DELETE = text("DELETE FROM glossary_terms WHERE id = :id AND user_id = :u RETURNING id")
EXISTING = text(f"SELECT id, term_norm FROM glossary_terms WHERE {SCOPE}")
BULK_INSERT = text(
    "INSERT INTO glossary_terms (user_id, project_id, term, term_norm, translation, note) "
    "VALUES (:u, :p, :term, :norm, :translation, :note)"
)
BULK_UPDATE = text(
    "UPDATE glossary_terms SET term = :term, translation = :translation, note = :note, "
    "updated_at = now() WHERE id = :id AND user_id = :u"
)
FILTER = "AND (:q = '' OR position(:q IN term_norm) > 0 OR position(:qt IN lower(translation)) > 0)"
LIST = text(
    f"SELECT {COLUMNS} FROM glossary_terms WHERE {SCOPE} {FILTER} "
    "ORDER BY term_norm, id LIMIT :limit OFFSET :offset"
)
TOTAL = text(f"SELECT count(*) FROM glossary_terms WHERE {SCOPE} {FILTER}")
EXPORT = text(
    f"SELECT term, translation, note FROM glossary_terms WHERE {SCOPE} ORDER BY term_norm, id"
)


def _duplicate() -> ApiError:
    return ApiError(409, "Đã có thuật ngữ này", "glossary_duplicate")


def _limit() -> ApiError:
    return ApiError(409, f"Tối đa {MAX_TERMS_PER_SCOPE} thuật ngữ mỗi phạm vi", "glossary_limit")


def _is_duplicate(e: IntegrityError) -> bool:
    return getattr(e.orig, "sqlstate", None) == "23505"


async def _require_project(s: AsyncSession, user_id: int, project_id: int | None) -> None:
    if project_id is None:
        return
    if await s.scalar(PROJECT_EXISTS, {"p": project_id, "u": user_id}) is None:
        raise ApiError(404, "Không tìm thấy dự án", "project_not_found")


async def _parse[T: GlossaryInput | GlossaryImportInput](request: Request, model: type[T]) -> T:
    raw = await read_body(request)
    try:
        return model.model_validate_json(raw)
    except ValidationError as e:
        raise validation_error_response(e.errors()) from None


def _item(row: Any) -> GlossaryItem:
    return GlossaryItem(**row._mapping)


def _fold_query(q: str) -> str:
    return unicodedata.normalize("NFKC", q).casefold().strip()


@router.get("/glossary", response_model=GlossaryPage, responses=SCOPED)
async def get_glossary(
    user_id: WebUserId,
    engine: Engine,
    project_id: ProjectQuery = None,
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> GlossaryPage:
    params = {"u": user_id, "p": project_id, "q": normalize_term(q), "qt": _fold_query(q)}
    async with user_session(engine, user_id) as s:
        await _require_project(s, user_id, project_id)
        rows = await s.execute(LIST, {**params, "limit": limit, "offset": offset})
        total = await s.scalar(TOTAL, params) or 0
        return GlossaryPage(items=[_item(r) for r in rows], total=total)


@router.get("/glossary/export", responses=SCOPED)
async def export_glossary(
    user_id: WebUserId, engine: Engine, project_id: ProjectQuery = None
) -> Response:
    async with user_session(engine, user_id) as s:
        await _require_project(s, user_id, project_id)
        rows = (await s.execute(EXPORT, {"u": user_id, "p": project_id})).all()
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["term", "translation", "note"])
    for r in rows:
        writer.writerow([_escape(r.term), _escape(r.translation), _escape(r.note or "")])
    log.info("glossary-export user=%s count=%s", user_id, len(rows))
    return Response(
        content=BOM + out.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="glossary.csv"'},
    )


def _needs_escape(value: str) -> bool:
    """Bắt đầu bằng ký tự công thức, hoặc bằng chuỗi `'` rồi mới tới ký tự công thức. Xét trên NFKC:
    bảng tính có thể coi `＝ ＋ － ＠` như dạng thường."""
    head = value.lstrip("'")
    return bool(head) and unicodedata.normalize("NFKC", head[0])[0] in FORMULA_CHARS


def _escape(value: str) -> str:
    """Chống chèn công thức khi mở bằng bảng tính: thêm `'` phía trước. Giá trị vốn đã có dạng
    `'`+công thức cũng thêm, để nhập lại (bỏ đúng một `'`) ra giá trị gốc."""
    return "'" + value if _needs_escape(value) else value


def _unescape(value: str) -> str:
    return value[1:] if value[:1] == "'" and _needs_escape(value[1:]) else value


@router.post(
    "/glossary",
    response_model=GlossaryItem,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE,
    openapi_extra=json_body(GlossaryInput),
)
async def post_glossary(request: Request, user_id: WebUserId, engine: Engine) -> GlossaryItem:
    req = await _parse(request, GlossaryInput)
    params = {
        "u": user_id,
        "p": req.project_id,
        "term": req.term,
        "norm": normalize_term(req.term),
        "translation": req.translation,
        "note": req.note,
    }
    try:
        async with user_session(engine, user_id) as s:
            await _require_project(s, user_id, req.project_id)
            if (await s.scalar(COUNT_SCOPE, params) or 0) >= MAX_TERMS_PER_SCOPE:
                raise _limit()
            item = _item((await s.execute(INSERT, params)).one())
    except IntegrityError as e:
        if _is_duplicate(e):
            raise _duplicate() from None
        raise
    log.info("glossary-create user=%s id=%s", user_id, item.id)
    return item


@router.patch(
    "/glossary/{glossary_id}",
    response_model=GlossaryItem,
    responses=WRITE,
    openapi_extra=json_body(GlossaryInput),
)
async def patch_glossary(
    glossary_id: GlossaryId, request: Request, user_id: WebUserId, engine: Engine
) -> GlossaryItem:
    req = await _parse(request, GlossaryInput)
    params = {
        "id": glossary_id,
        "u": user_id,
        "term": req.term,
        "norm": normalize_term(req.term),
        "translation": req.translation,
        "note": req.note,
    }
    try:
        async with user_session(engine, user_id) as s:
            row = (await s.execute(UPDATE, params)).one_or_none()
    except IntegrityError as e:
        if _is_duplicate(e):
            raise _duplicate() from None
        raise
    if row is None:
        raise ApiError(404, "Không tìm thấy thuật ngữ", "glossary_not_found")
    log.info("glossary-update user=%s id=%s", user_id, glossary_id)
    return _item(row)


@router.delete("/glossary/{glossary_id}", status_code=status.HTTP_204_NO_CONTENT, responses=WRITE)
async def delete_glossary(glossary_id: GlossaryId, user_id: WebUserId, engine: Engine) -> Response:
    async with user_session(engine, user_id) as s:
        deleted = await s.scalar(DELETE, {"id": glossary_id, "u": user_id})
    if deleted is None:
        raise ApiError(404, "Không tìm thấy thuật ngữ", "glossary_not_found")
    log.info("glossary-delete user=%s id=%s", user_id, glossary_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- nhập ---


def _raw_rows(body: GlossaryImportInput) -> list[tuple[int, list[str]]]:
    """(số dòng, các ô) của từng dòng dữ liệu; dòng trống và dòng tiêu đề csv bị bỏ. BOM đầu text
    (file vừa xuất, đọc bằng utf-8) bị bỏ, không thì tiêu đề thành dữ liệu."""
    text_ = body.text.removeprefix("\ufeff")
    rows: list[tuple[int, list[str]]] = []
    if body.format == "lines":
        for n, line in enumerate(text_.splitlines(), start=1):
            if not line.strip():
                continue
            left, sep, right = line.partition("=")
            rows.append((n, [left, right] if sep else [line]))
        return rows
    reader = csv.reader(io.StringIO(text_))
    try:
        for cells in reader:
            if not any(c.strip() for c in cells):
                continue
            rows.append((reader.line_num, cells))
    except csv.Error:
        rows.append((reader.line_num, []))  # dòng hỏng: tính là không hợp lệ rồi dừng
    if rows and [c.strip().lower() for c in rows[0][1][:2]] == ["term", "translation"]:
        rows.pop(0)
    return rows


def _validate(cells: list[str]) -> tuple[GlossaryInput | None, str]:
    """Hàng hợp lệ hoặc tên ô lỗi (không bao giờ chứa giá trị)."""
    if not 2 <= len(cells) <= 3:
        return None, "định dạng"
    note = _unescape(cells[2]) if len(cells) == 3 else None
    try:
        row = GlossaryInput(
            term=_unescape(cells[0].strip()),
            translation=_unescape(cells[1].strip()),
            note=note,
        )
    except ValidationError as e:
        loc = e.errors()[0]["loc"]
        return None, str(loc[0]) if loc else "định dạng"
    return row, ""


async def _import(s: AsyncSession, user_id: int, body: GlossaryImportInput) -> GlossaryImportResult:
    await _require_project(s, user_id, body.project_id)
    scope = {"u": user_id, "p": body.project_id}
    existing = {r.term_norm: r.id for r in await s.execute(EXISTING, scope)}
    plan: dict[str, dict[str, Any]] = {}
    duplicate = invalid = 0
    first_error: str | None = None
    for line, cells in _raw_rows(body):
        row, bad = _validate(cells)
        if row is None:
            invalid += 1
            first_error = first_error or f"Dòng {line}: {bad}"
            continue
        norm = normalize_term(row.term)
        values = {"term": row.term, "translation": row.translation, "note": row.note}
        if norm in plan:
            duplicate += 1
            if body.on_conflict == "overwrite":
                plan[norm] = {**plan[norm], **values}
        elif norm in existing:
            duplicate += 1
            if body.on_conflict == "overwrite":
                plan[norm] = {"id": existing[norm], **values}
        else:
            plan[norm] = {"id": None, "norm": norm, **values}
    inserts = [{**v, "u": user_id, "p": body.project_id} for v in plan.values() if v["id"] is None]
    updates = [{**v, "u": user_id} for v in plan.values() if v["id"] is not None]
    if body.apply:
        if len(existing) + len(inserts) > MAX_TERMS_PER_SCOPE:
            raise _limit()
        if inserts:
            await s.execute(BULK_INSERT, inserts)
        if updates:
            await s.execute(BULK_UPDATE, updates)
    return GlossaryImportResult(
        added=len(inserts), duplicate=duplicate, invalid=invalid, first_error=first_error
    )


@router.post(
    "/glossary/import",
    response_model=GlossaryImportResult,
    responses=IMPORT,
    openapi_extra=json_body(GlossaryImportInput),
)
async def import_glossary(
    request: Request, user_id: WebUserId, engine: Engine
) -> GlossaryImportResult:
    body = await _parse(request, GlossaryImportInput)
    if len(body.text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ApiError(413, "Dữ liệu nhập quá lớn (tối đa 1 MiB)", "glossary_import_too_large")
    if len(body.text.splitlines()) > MAX_IMPORT_LINES:
        raise ApiError(422, f"Tối đa {MAX_IMPORT_LINES} dòng", "glossary_import_too_many_lines")
    try:
        async with user_session(engine, user_id) as s:
            result = await _import(s, user_id, body)
    except IntegrityError as e:
        if _is_duplicate(e):
            raise _duplicate() from None
        raise
    log.info(
        "glossary-import user=%s apply=%s added=%s duplicate=%s invalid=%s",
        user_id,
        body.apply,
        result.added,
        result.duplicate,
        result.invalid,
    )
    return result
