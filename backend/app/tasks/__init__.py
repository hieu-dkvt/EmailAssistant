"""Job nền Procrastinate (spec 2026-10-17-worker-design.md mục 3). Chạy worker: python -m app.worker

Task nằm trong Blueprint tạo mới cho mỗi App (add_tasks_from đổi tên task ngay trên Blueprint, nên
một Blueprint chỉ gắn được vào một App; test dựng App riêng trỏ tới DB test). Engine SQLAlchemy
của worker (role app_worker) đi vào task qua additional_context["engine"].
Log chỉ có user_id và số lượng. Lỗi trong task được thay bằng ProcessingError (raise ... from
None) trước khi tới Procrastinate, vì Procrastinate log cả traceback của lỗi.
"""

import logging
import time

from procrastinate import App, Blueprint, JobContext, PsycopgConnector
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from app import auto_translate, embedding, processing, projects
from app.ai.provider import LlmResolver
from app.config import database_url
from app.ml_client import EmbeddingModelMismatch, MlClient, MlUnavailable
from app.safe_errors import sanitized

log = logging.getLogger("app.tasks")

NAMESPACE = "mail"
PROCESS_USER_MAIL = f"{NAMESPACE}:process_user_mail"
SWEEP = f"{NAMESPACE}:sweep"
APPLY_OVERRIDES = f"{NAMESPACE}:apply_overrides"
EMBED_SWEEP = f"{NAMESPACE}:embed_sweep"
EMBED_USER = f"{NAMESPACE}:embed_user"
PROJECT_RULES_SWEEP = f"{NAMESPACE}:project_rules_sweep"
ASSIGN_USER_PROJECTS = f"{NAMESPACE}:assign_user_projects"
TRANSLATE_SWEEP = f"{NAMESPACE}:translate_sweep"
TRANSLATE_USER = f"{NAMESPACE}:translate_user"
TRANSLATE_QUEUE = "translate"


def conninfo(url: str | None = None) -> str:
    """URL SQLAlchemy (postgresql+psycopg://…) của role worker → conninfo của libpq."""
    sa_url = make_url(url or database_url("worker")).set(drivername="postgresql")
    return sa_url.render_as_string(hide_password=False)


def create_app(url: str | None = None) -> App:
    connector = PsycopgConnector(
        conninfo=conninfo(url),
        # Bảng job ở schema riêng (migration 0008).
        kwargs={"options": "-c search_path=procrastinate"},
    )
    app = App(connector=connector)
    app.add_tasks_from(_blueprint(), namespace=NAMESPACE)
    return app


def _blueprint() -> Blueprint:
    bp = Blueprint()

    @bp.task(name="process_user_mail", pass_context=True)
    async def process_user_mail(context: JobContext, user_id: int) -> None:
        try:
            result = await processing.process_user(_engine(context), user_id)
        except Exception as e:
            raise sanitized(f"process_user_mail user={user_id}", e) from None
        # Mail vừa làm sạch được embed ngay, không chờ embed_sweep của phút sau.
        if result.clean.cleaned and _ml(context) is not None:
            await defer_embed(context.app, user_id)

    # Embedding (spec 2026-10-19-mail-embedding-design mục 5). Job ngắn (EMBED_JOB_SECONDS) tự
    # đẩy job tiếp theo: worker chạy một job tại một thời điểm, sửa tay thread không phải chờ lâu.
    @bp.periodic(cron="* * * * *", periodic_id="embed_sweep")
    @bp.task(name="embed_sweep", pass_context=True, queueing_lock="embed_sweep")
    async def embed_sweep(context: JobContext, timestamp: int) -> int:
        if _ml(context) is None:
            return 0
        try:
            users = await embedding.pending_users(_engine(context))
        except Exception as e:
            raise sanitized("embed_sweep", e) from None
        deferred = 0
        for user_id in users:
            deferred += await defer_embed(context.app, user_id)
        return deferred

    @bp.task(name="embed_user", pass_context=True)
    async def embed_user(context: JobContext, user_id: int) -> int:
        ml = _ml(context)
        if ml is None:
            return 0
        try:
            stats = await embedding.embed_user(_engine(context), ml, user_id)
        except MlUnavailable:
            log.warning("embed user=%s ml-service unavailable", user_id)
            return 0
        except EmbeddingModelMismatch as e:
            log.error("embed user=%s model mismatch: %s", user_id, e)
            return 0
        except Exception as e:
            raise sanitized(f"embed_user user={user_id}", e) from None
        if stats.more:
            await defer_embed(context.app, user_id)
        return stats.messages

    @bp.periodic(cron="* * * * *", periodic_id="sweep")
    @bp.task(name="sweep", pass_context=True, queueing_lock="sweep")
    async def sweep(context: JobContext, timestamp: int) -> int:
        try:
            return await defer_pending(context.app, _engine(context))
        except Exception as e:
            raise sanitized("sweep", e) from None

    # Sửa tay từ web có hiệu lực trong vài giây (spec 2026-10-18-thread-api mục 6). Chỉ đọc bảng
    # nhỏ user_thread_state; sweep (truy vấn nặng hơn) vẫn mỗi phút. Cron 6 trường: giây ở cuối.
    @bp.periodic(cron="* * * * * */5", periodic_id="apply_overrides")
    @bp.task(name="apply_overrides", pass_context=True, queueing_lock="apply_overrides")
    async def apply_overrides(context: JobContext, timestamp: int) -> int:
        try:
            users = await processing.pending_override_users(_engine(context))
            return await defer_for_users(context.app, users)
        except Exception as e:
            raise sanitized("apply_overrides", e) from None

    # Gán mail vào dự án khi sửa bộ lọc (spec 25 mục 5): task 5 giây chỉ đọc bảng nhỏ
    # user_project_state; việc quét lại nằm ở job riêng theo người dùng, chạy theo lô.
    @bp.periodic(cron="* * * * * */5", periodic_id="project_rules_sweep")
    @bp.task(name="project_rules_sweep", pass_context=True, queueing_lock="project_rules_sweep")
    async def project_rules_sweep(context: JobContext, timestamp: int) -> int:
        try:
            users = await projects.pending_rule_users(_engine(context))
        except Exception as e:
            raise sanitized("project_rules_sweep", e) from None
        deferred = 0
        for user_id in users:
            deferred += await defer_assign(context.app, user_id)
        return deferred

    @bp.task(name="assign_user_projects", pass_context=True)
    async def assign_user_projects(context: JobContext, user_id: int) -> int:
        try:
            stats = await projects.assign_user(_engine(context), user_id)
        except Exception as e:
            raise sanitized(f"assign_user_projects user={user_id}", e) from None
        if stats.more:
            await defer_assign(context.app, user_id)
        return stats.messages

    # Dịch tự động (spec 26 mục 5; không cần ml-service, spec 28). translate_user chờ AI tới 120
    # giây nên chạy ở queue riêng; translate_sweep chỉ quét và đẩy job, ở queue default để không
    # phải chờ một job dịch dài.
    @bp.periodic(cron="* * * * *", periodic_id="translate_sweep")
    @bp.task(name="translate_sweep", pass_context=True, queueing_lock="translate_sweep")
    async def translate_sweep(context: JobContext, timestamp: int) -> int:
        if not _can_translate(context):
            return 0
        try:
            users = await auto_translate.pending_translate_users(_engine(context))
        except Exception as e:
            raise sanitized("translate_sweep", e) from None
        deferred = 0
        for user_id in users:
            deferred += await defer_translate(context.app, user_id)
        return deferred

    @bp.task(name="translate_user", pass_context=True, queue=TRANSLATE_QUEUE)
    async def translate_user(context: JobContext, user_id: int) -> bool:
        if not _can_translate(context):
            return False
        try:
            stats = await auto_translate.translate_next(
                _api_engine(context), _resolver(context), user_id
            )
        except Exception as e:
            raise sanitized(f"translate_user user={user_id}", e) from None
        if stats.more:
            await defer_translate(context.app, user_id)
        return stats.handled

    return bp


def user_lock(user_id: int) -> str:
    return f"user:{user_id}"


def _engine(context: JobContext) -> AsyncEngine:
    return context.additional_context["engine"]


def _ml(context: JobContext) -> MlClient | None:
    """Client ml-service; None khi worker chạy không có embedding (vd. test cũ)."""
    return context.additional_context.get("ml")


def _api_engine(context: JobContext) -> AsyncEngine:
    """Engine role app_api: job dịch đọc, ghi dữ liệu mail qua user_session (spec 26 mục 5)."""
    return context.additional_context["api_engine"]


def _resolver(context: JobContext) -> LlmResolver:
    return context.additional_context["resolver"]


def _can_translate(context: JobContext) -> bool:
    """Dịch không cần ml-service (spec 28): chỉ cần bộ chọn nhà cung cấp AI."""
    return "resolver" in context.additional_context


def embed_lock(user_id: int) -> str:
    return f"embed:{user_id}"


async def defer_embed(app: App, user_id: int) -> int:
    """Đẩy embed_user cho người dùng; đã có job chờ thì bỏ qua. Trả 1 nếu đã đẩy."""
    try:
        await app.configure_task(EMBED_USER, queueing_lock=embed_lock(user_id)).defer_async(
            user_id=user_id
        )
        return 1
    except AlreadyEnqueued:
        return 0


async def defer_assign(app: App, user_id: int) -> int:
    """Đẩy assign_user_projects cho người dùng; đã có job chờ thì bỏ qua. Trả 1 nếu đã đẩy."""
    try:
        await app.configure_task(
            ASSIGN_USER_PROJECTS, lock=user_lock(user_id), queueing_lock=f"projects:{user_id}"
        ).defer_async(user_id=user_id)
        return 1
    except AlreadyEnqueued:
        return 0


async def defer_translate(app: App, user_id: int) -> int:
    """Đẩy translate_user cho người dùng; đã có job chờ thì bỏ qua. Trả 1 nếu đã đẩy."""
    try:
        await app.configure_task(
            TRANSLATE_USER, queue=TRANSLATE_QUEUE, queueing_lock=f"translate:{user_id}"
        ).defer_async(user_id=user_id)
        return 1
    except AlreadyEnqueued:
        return 0


async def defer_pending(app: App, engine: AsyncEngine) -> int:
    """Đẩy process_user_mail cho mỗi người dùng có việc tồn; đã có job chờ thì bỏ qua."""
    users = await processing.pending_users(engine)
    deferred = await defer_for_users(app, users)
    if users:
        log.info("sweep pending_users=%d deferred=%d", len(users), deferred)
    return deferred


async def defer_for_users(app: App, users: list[int]) -> int:
    deferred = 0
    for user_id in users:
        lock = user_lock(user_id)
        try:
            await app.configure_task(PROCESS_USER_MAIL, lock=lock, queueing_lock=lock).defer_async(
                user_id=user_id
            )
            deferred += 1
        except AlreadyEnqueued:
            pass
    return deferred


async def defer_sweep_now(app: App) -> None:
    """Quét ngay khi worker khởi động, không chờ tới phút kế tiếp."""
    try:
        await app.configure_task(SWEEP, queueing_lock="sweep").defer_async(
            timestamp=int(time.time())
        )
    except AlreadyEnqueued:
        pass
