"""Dịch một mail: che → khớp glossary → gọi AI → kiểm tra → thay ngược (spec 26 mục 6, 10, 11).

Dùng chung cho nút dịch tay và job nền: mọi thứ nhận qua tham số, engine phải là role app_api
(đọc qua user_session, RLS). Không giữ transaction trong lúc chờ AI. Mọi text gửi đi qua một
Masker, che lỗi thì không gửi. Log chỉ có user, message_id, số lượng, ms.
"""

import asyncio
import hashlib
import json
import logging
import time
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.ai import people as people_mod
from app.ai.ask import MAX_ADDRESSES
from app.ai.client import CallLog, LlmClient, LlmError, call_task
from app.ai.glossary import (
    GlossaryEntry,
    GlossaryMatch,
    load_entries,
    match_glossary,
    merge_entries,
)
from app.ai.masking import LABEL_RE, MAX_MASK_INPUT_CHARS, Masker, cut_masked
from app.ai.models import TASK_TRANSLATE
from app.ai.names import companies_in
from app.ai.prompts import (
    TRANSLATE_PROMPT_VERSION,
    TRANSLATE_SYSTEM,
    TranslationOut,
    translate_message,
)
from app.ai.settings import user_models
from app.db.session import user_session
from app.embedding import MAX_SUBJECT_CHARS
from app.ml_client import MlClient
from app.threads_view import THREAD_MESSAGE_LIMIT
from app.web_schemas import (
    AutoState,
    MessageTranslationOut,
    ProjectRef,
    ThreadTranslationItem,
    ThreadTranslations,
)

log = logging.getLogger("app.ai")

MAX_BODY_CHARS = 12_000
MAX_CONTEXT_CHARS = 1_500
MAX_NOTES = 5
MAX_FULL_TEXT = 20_000  # toàn văn đọc để học tên công ty, như hỏi đáp
MAX_MASK_PASSES = 4
CHOOSE_BY_CONTEXT = "chọn theo ngữ cảnh"


class TranslateError(Exception):
    """Mail không dịch được: status_code, code cho web, detail cố định (không có nội dung mail)."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def _not_found() -> TranslateError:
    return TranslateError(404, "message_not_found", "Không có mail này")


@dataclass(frozen=True)
class TranslationResult:
    subject: str
    body: str
    notes: list[str]
    warnings: list[str]
    source_hash: bytes
    model: str
    prompt_version: int
    glossary_used: list[dict[str, str]]


_MAIL_COLUMNS = (
    "m.id, m.thread_id, m.subject, m.body_clean, m.sent_at, m.from_name, m.from_addr, "
    "m.to_addrs, m.cc_addrs, left(m.body_text, :cap) AS full_text"
)
READ_MAIL = text(f"SELECT {_MAIL_COLUMNS} FROM messages m WHERE m.id = :m AND m.user_id = :u")
PREVIOUS_MAIL = text(
    f"""
    SELECT {_MAIL_COLUMNS} FROM messages m
    WHERE m.thread_id = :t AND m.user_id = :u AND m.body_clean IS NOT NULL
      AND (m.sent_at, m.id) < (CAST(:at AS timestamptz), CAST(:m AS bigint))
    ORDER BY m.sent_at DESC, m.id DESC LIMIT 1
    """
)
UPSERT_TRANSLATION = text(
    """
    INSERT INTO message_translations (user_id, message_id, translated_subject, translated_body,
        notes, warnings, source_hash, model, prompt_version, glossary_used)
    VALUES (:u, :m, :subject, :body, CAST(:notes AS jsonb), CAST(:warnings AS jsonb), :hash,
        :model, :version, CAST(:glossary AS jsonb))
    ON CONFLICT (user_id, message_id) DO UPDATE SET
        translated_subject = excluded.translated_subject,
        translated_body = excluded.translated_body,
        notes = excluded.notes,
        warnings = excluded.warnings,
        source_hash = excluded.source_hash,
        model = excluded.model,
        prompt_version = excluded.prompt_version,
        glossary_used = excluded.glossary_used,
        updated_at = now()
    """
)
SAVED_TRANSLATIONS = text(
    """
    SELECT t.message_id, t.translated_subject, t.translated_body, t.notes, t.warnings,
           t.source_hash, t.model, t.prompt_version, t.glossary_used, t.created_at, t.updated_at,
           m.subject, m.body_clean
    FROM message_translations t
    JOIN messages m ON m.id = t.message_id AND m.user_id = t.user_id
    WHERE t.user_id = :u AND t.message_id = ANY(CAST(:ids AS bigint[]))
    """
)
USED_TERMS = text(
    """
    SELECT term, term_norm, translation, note, project_id FROM glossary_terms
    WHERE user_id = :u AND term_norm = ANY(CAST(:norms AS text[]))
    ORDER BY id
    """
)
MESSAGE_PROJECTS = text(
    "SELECT message_id, project_id FROM message_projects "
    "WHERE user_id = :u AND message_id = ANY(CAST(:ids AS bigint[]))"
)
THREAD_PROJECTS = text(
    """
    SELECT mp.message_id, p.id, p.name, p.processed_source_id IS NOT NULL AS has_folder
    FROM message_projects mp
    JOIN projects p ON p.id = mp.project_id AND p.user_id = mp.user_id
    JOIN messages m ON m.id = mp.message_id AND m.user_id = mp.user_id
    WHERE m.thread_id = :t AND mp.user_id = :u
    ORDER BY p.name, p.id
    """
)
THREAD_STATES = text(
    """
    SELECT m.id, a.status, a.error_code, a.move_note, f.folder_path AS target_folder
    FROM messages m
    LEFT JOIN message_auto_runs a ON a.message_id = m.id AND a.user_id = m.user_id
    LEFT JOIN mail_sources f ON f.id = a.target_source_id AND f.user_id = a.user_id
    WHERE m.thread_id = :t AND m.user_id = :u
    ORDER BY m.sent_at NULLS LAST, m.id
    LIMIT :limit
    """
)


def mail_hash(subject: str | None, body: str | None) -> bytes:
    """SHA-256 của subject + "\\0" + body trước khi che (toàn bộ, không phải phần đã cắt)."""
    return hashlib.sha256(f"{subject or ''}\0{body or ''}".encode()).digest()


def _glossary_line(term: str, translations: Sequence[str], note: str, conflict: bool) -> str:
    line = f"- {term} = {' | '.join(translations)}"
    if conflict:
        line += f" ({CHOOSE_BY_CONTEXT})"
    if note:
        line += f" (ghi chú: {' '.join(note.split())})"
    return line


def _participants(rows: Sequence[Any]) -> list[tuple[str | None, str | None]]:
    out: list[tuple[str | None, str | None]] = []
    for r in rows:
        out.append((r.from_name, r.from_addr))
        for group in (r.to_addrs or [], r.cc_addrs or []):
            for a in group[:MAX_ADDRESSES]:
                if isinstance(a, dict):
                    out.append((a.get("name"), a.get("addr")))
    return out


def learn_rows(masker: Masker, rows: Sequence[Any]) -> None:
    """Thêm vào từ điển che người gửi/nhận và tên công ty trong toàn văn (kể cả chữ ký đã cắt
    khỏi phần gửi đi) của các mail. Hàng cần cột from_name, from_addr, to_addrs, cc_addrs,
    full_text."""
    masker.add_entities(people_mod.entities_of(_participants(rows)))
    masker.add_entities(companies_in(r.full_text for r in rows if r.full_text))


async def build_masker(
    session: AsyncSession, ml: MlClient, user_id: int, rows: Sequence[Any]
) -> Masker:
    """Masker có từ điển dùng cho dịch mail.

    session phải là user_session của engine role app_api: correspondents dựa vào RLS, không lọc
    user_id trong câu truy vấn (role worker sẽ đọc lẫn người gửi/nhận của người khác).
    """
    masker = Masker(ml.ner)
    masker.add_entities(await people_mod.correspondents(session, user_id))
    learn_rows(masker, rows)
    return masker


async def _masked_glossary(masker: Masker, matches: Sequence[GlossaryMatch]) -> str:
    """Các dòng glossary đã che bằng cùng Masker. Bên gọi tự kiểm tra từ điển có mở rộng không."""
    values = [v for m in matches for v in (m.term, *m.translations, m.note or "")]
    masked = iter(await masker.mask(values) if values else [])
    lines: list[str] = []
    for m in matches:
        term = next(masked)
        translations = [next(masked) for _ in m.translations]
        lines.append(_glossary_line(term, translations, next(masked), m.conflict))
    return "\n".join(lines)


@dataclass(frozen=True)
class _MaskedMail:
    subject: str
    body: str
    context: str
    glossary: str
    matches: list[GlossaryMatch]
    truncated: bool


async def _mask_mail(
    masker: Masker, subject: str, body: str, context: str, entries: Sequence[GlossaryEntry]
) -> _MaskedMail:
    """Che mail, cắt theo giới hạn, khớp glossary trên đúng phần gửi đi, che glossary.

    Che glossary có thể dạy Masker tên mới. Khi đó mail đã che không còn đúng và mục glossary đã
    khớp có thể chứa chính tên đó: chạy lại cả bước này tới khi từ điển không đổi. Loại một mục
    lại nhường chỗ cho mục khác trong giới hạn 60, nên phải lặp. Không ổn định thì không gửi gì.
    """
    fields = (
        (subject, MAX_SUBJECT_CHARS),
        (body, MAX_BODY_CHARS),
        (context, MAX_CONTEXT_CHARS),
    )
    inputs = [value[:MAX_MASK_INPUT_CHARS] for value, _ in fields]
    for _ in range(MAX_MASK_PASSES):
        masked = await masker.mask(inputs)
        known = (masker.counts, masker.known_surfaces)
        (cut_subject, _), (cut_body, truncated), (cut_context, _) = (
            cut_masked(text_, limit, capped=len(value) > MAX_MASK_INPUT_CHARS)
            for text_, (value, limit) in zip(masked, fields, strict=True)
        )
        matches = await asyncio.to_thread(match_glossary, f"{cut_subject}\n{cut_body}", entries)
        glossary = await _masked_glossary(masker, matches)
        if (masker.counts, masker.known_surfaces) == known:
            return _MaskedMail(cut_subject, cut_body, cut_context, glossary, matches, truncated)
    raise TranslateError(
        500, "mask_unstable", "Không che ổn định được mail này, chưa gửi gì cho AI"
    )


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _labels(texts: Iterable[str]) -> set[str]:
    return {m.group(0) for t in texts for m in LABEL_RE.finditer(t)}


def check_translation(
    masked_inputs: Sequence[str],
    masked_outputs: Sequence[str],
    unmasked_body: str,
    matches: Sequence[GlossaryMatch],
    *,
    truncated: bool,
    other_sent: Sequence[str] = (),
    masked_notes: Sequence[str] = (),
) -> list[str]:
    """Mã cảnh báo của spec 26 mục 11. Không gọi AI, không tự dịch lại.

    masked_inputs, masked_outputs: subject và body. labels_missing chỉ so hai phần này, không tính
    notes. labels_unknown: nhãn trong kết quả (kể cả masked_notes) không có trong mọi thứ đã gửi,
    gồm other_sent (ngữ cảnh, glossary). So nhãn theo tập hợp; mục xung đột chấp nhận bản nào cũng
    được.
    """
    inputs, outputs = _labels(masked_inputs), _labels(masked_outputs)
    result = _fold(unmasked_body)
    warnings: list[str] = []
    if inputs - outputs:
        warnings.append("labels_missing")
    if (outputs | _labels(masked_notes)) - (inputs | _labels(other_sent)):
        warnings.append("labels_unknown")
    if any(not any(_fold(t) in result for t in m.translations) for m in matches):
        warnings.append("glossary_missing")
    if any(m.conflict for m in matches):
        warnings.append("glossary_conflict")
    if truncated:
        warnings.append("truncated")
    return warnings


def _term_key(translations: Iterable[str]) -> str:
    # Glossary không nhận ký tự điều khiển nên "\n" không lẫn với bản dịch.
    return "\n".join(sorted(translations))


def is_stale(row: Any, source_hash: bytes, current_terms: Mapping[str, str]) -> bool:
    """Bản dịch cũ: mail đổi, prompt đổi, hoặc một mục đã dùng đổi hay bị xóa.

    current_terms: term_norm → _term_key(các bản dịch hiện hành), xem current_term_keys. Mục mới
    thêm không làm bản dịch cũ (spec 26 mục 15).
    """
    if bytes(row.source_hash) != source_hash or row.prompt_version != TRANSLATE_PROMPT_VERSION:
        return True
    used: dict[str, set[str]] = {}
    for item in row.glossary_used:
        used.setdefault(item["term_norm"], set()).add(item["translation"])
    return any(current_terms.get(norm) != _term_key(tr) for norm, tr in used.items())


def current_term_keys(entries: Sequence[GlossaryEntry]) -> dict[str, str]:
    return {n: _term_key(m.translations) for n, m in merge_entries(entries).items()}


async def read_mail(session: AsyncSession, user_id: int, message_id: int) -> Any:
    row = (
        await session.execute(READ_MAIL, {"m": message_id, "u": user_id, "cap": MAX_FULL_TEXT})
    ).first()
    if row is None:
        raise _not_found()
    return row


def ensure_translatable(mail: Any) -> None:
    if mail.body_clean is None:
        raise TranslateError(409, "mail_not_ready", "Mail chưa được làm sạch, thử lại sau")
    if not (mail.subject or "").strip() and not mail.body_clean.strip():
        raise TranslateError(400, "nothing_to_translate", "Mail không có nội dung để dịch")


async def _previous_mail(session: AsyncSession, user_id: int, mail: Any) -> Any | None:
    if mail.thread_id is None or mail.sent_at is None:
        return None
    params = {
        "t": mail.thread_id,
        "u": user_id,
        "at": mail.sent_at,
        "m": mail.id,
        "cap": MAX_FULL_TEXT,
    }
    return (await session.execute(PREVIOUS_MAIL, params)).first()


async def produce_translation(
    engine: AsyncEngine,
    ml: MlClient,
    llm: LlmClient,
    user_id: int,
    message_id: int,
) -> TranslationResult:
    """Chạy luồng dịch, chưa lưu. Ném TranslateError, LlmError, MlUnavailable; không gửi gì nếu
    che lỗi. Không kiểm tra bản đã lưu: bên gọi quyết định có dịch lại không."""
    started = time.monotonic()
    async with user_session(engine, user_id) as s:
        mail = await read_mail(s, user_id, message_id)
        ensure_translatable(mail)
        previous = await _previous_mail(s, user_id, mail)
        models = await user_models(s)
        entries = await load_entries(s, user_id, message_id)
        rows = [mail] if previous is None else [mail, previous]
        masker = await build_masker(s, ml, user_id, rows)
    # Job không qua kiểm tra của route: tự kiểm tên model trước khi che và gọi AI.
    if TASK_TRANSLATE not in models:
        raise LlmError("ai_model_missing", 503, "Nhập tên model cho tác vụ dịch mail (Cài đặt AI)")

    context = previous.body_clean if previous is not None else ""
    masked = await _mask_mail(masker, mail.subject or "", mail.body_clean, context, entries)

    calls = CallLog()
    out = await call_task(
        engine,
        llm,
        user_id,
        TASK_TRANSLATE,
        models[TASK_TRANSLATE],
        system=TRANSLATE_SYSTEM,
        user=translate_message(masked.context, masked.glossary, masked.subject, masked.body),
        output_type=TranslationOut,
        log_to=calls,
    )
    if not (out.body if mail.body_clean.strip() else out.subject).strip():
        raise LlmError("ai_bad_output", 502, "AI trả bản dịch trống")

    def restore(value: str) -> str:
        return masker.unmask(value).replace("\x00", "")  # PostgreSQL không nhận NUL

    notes = [n for n in out.notes if n.strip()][:MAX_NOTES]
    result_subject, result_body = restore(out.subject), restore(out.body)
    warnings = check_translation(
        [masked.subject, masked.body],
        [out.subject, out.body],
        f"{result_subject}\n{result_body}",
        masked.matches,
        truncated=masked.truncated,
        other_sent=[masked.context, masked.glossary],
        masked_notes=notes,
    )
    log.info(
        "translate user=%s message_id=%s glossary=%d warnings=%d ms=%d",
        user_id,
        message_id,
        len(masked.matches),
        len(warnings),
        (time.monotonic() - started) * 1000,
    )
    return TranslationResult(
        subject=result_subject,
        body=result_body,
        notes=[restore(n) for n in notes],
        warnings=warnings,
        source_hash=mail_hash(mail.subject, mail.body_clean),
        model=calls.calls[-1].model,
        prompt_version=TRANSLATE_PROMPT_VERSION,
        glossary_used=[
            {"term_norm": m.term_norm, "translation": t}
            for m in masked.matches
            for t in m.translations
        ],
    )


async def save_translation(
    session: AsyncSession, user_id: int, message_id: int, r: TranslationResult
) -> None:
    """Upsert bản dịch. Mail đã bị xóa trong lúc chờ AI → TranslateError message_not_found."""
    params = {
        "u": user_id,
        "m": message_id,
        "subject": r.subject,
        "body": r.body,
        "notes": json.dumps(r.notes, ensure_ascii=False),
        "warnings": json.dumps(r.warnings),
        "hash": r.source_hash,
        "model": r.model,
        "version": r.prompt_version,
        "glossary": json.dumps(r.glossary_used, ensure_ascii=False),
    }
    try:
        await session.execute(UPSERT_TRANSLATION, params)
    except IntegrityError as e:
        if getattr(e.orig, "sqlstate", None) == "23503":
            raise _not_found() from None
        raise


async def _term_keys_by_message(
    session: AsyncSession, user_id: int, rows: Sequence[Any]
) -> dict[int, dict[str, str]]:
    """Thuật ngữ hiện hành của các mục đã dùng, theo từng mail (mục chung và mục của dự án chứa
    mail). Một lượt truy vấn cho cả thread: glossary có thể tới hàng nghìn mục."""
    used = {r.message_id: {i["term_norm"] for i in r.glossary_used} for r in rows}
    norms = sorted(set().union(*used.values())) if used else []
    if not norms:
        return {mid: {} for mid in used}
    by_norm: dict[str, list[GlossaryEntry]] = {}
    for r in await session.execute(USED_TERMS, {"u": user_id, "norms": norms}):
        entry = GlossaryEntry(r.term, r.term_norm, r.translation, r.note, r.project_id)
        by_norm.setdefault(r.term_norm, []).append(entry)
    projects: dict[int, set[int]] = {}
    for r in await session.execute(MESSAGE_PROJECTS, {"u": user_id, "ids": list(used)}):
        projects.setdefault(r.message_id, set()).add(r.project_id)
    return {
        mid: current_term_keys(
            [
                e
                for norm in terms
                for e in by_norm.get(norm, [])
                if e.project_id is None or e.project_id in projects.get(mid, ())
            ]
        )
        for mid, terms in used.items()
    }


async def saved_translations(
    session: AsyncSession, user_id: int, message_ids: Sequence[int]
) -> dict[int, MessageTranslationOut]:
    """Bản dịch đã lưu của các mail, kèm stale. Mail chưa dịch: không có khóa."""
    rows = (
        await session.execute(SAVED_TRANSLATIONS, {"u": user_id, "ids": list(message_ids)})
    ).all()
    terms = await _term_keys_by_message(session, user_id, rows)
    return {
        r.message_id: MessageTranslationOut(
            message_id=r.message_id,
            translated_subject=r.translated_subject,
            translated_body=r.translated_body,
            notes=r.notes,
            warnings=r.warnings,
            model=r.model,
            stale=is_stale(r, mail_hash(r.subject, r.body_clean), terms[r.message_id]),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    }


async def thread_translations(
    session: AsyncSession, user_id: int, thread_id: int
) -> ThreadTranslations | None:
    """Bản dịch và trạng thái tự động của từng mail, cùng thứ tự với GET /threads/{id}. Thread
    không có hoặc của người khác: None."""
    params = {"t": thread_id, "u": user_id, "limit": THREAD_MESSAGE_LIMIT}
    rows = (await session.execute(THREAD_STATES, params)).all()
    if not rows:
        return None
    saved = await saved_translations(session, user_id, [r.id for r in rows])
    projects: dict[int, list[ProjectRef]] = {}
    movable: dict[int, list[ProjectRef]] = {}
    for pr in (await session.execute(THREAD_PROJECTS, {"t": thread_id, "u": user_id})).all():
        ref = ProjectRef(id=pr.id, name=pr.name)
        projects.setdefault(pr.message_id, []).append(ref)
        if pr.has_folder:
            movable.setdefault(pr.message_id, []).append(ref)
    return ThreadTranslations(
        items=[
            ThreadTranslationItem(
                message_id=r.id,
                translation=saved.get(r.id),
                auto=AutoState(
                    status=r.status,
                    error_code=r.error_code,
                    move_note=r.move_note,
                    target_folder=r.target_folder,
                )
                if r.status is not None
                else None,
                projects=projects.get(r.id, []),
                move_projects=movable.get(r.id, []),
            )
            for r in rows
        ]
    )
