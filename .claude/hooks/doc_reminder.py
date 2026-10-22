"""Hook nhắc cập nhật tài liệu theo .claude/rules/documentation.md.

Gọi từ .claude/settings.json với một tham số:
  track  (PostToolUse, Edit|Write|MultiEdit|NotebookEdit): ghi lại đường dẫn file vừa sửa.
  reset  (UserPromptSubmit): bắt đầu lượt mới, xóa danh sách.
  check  (Stop): lượt có sửa code mà không sửa tài liệu nào thì chặn dừng một lần, nhắc Claude.

Chỉ đọc đường dẫn file trong input của hook, không đọc nội dung file hay transcript.
Danh sách được lưu theo session_id trong thư mục tạm của hệ điều hành.
Sửa quy tắc phân loại thì cập nhật luôn bảng cấu hình trong CLAUDE.md và rule tài liệu.
"""

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

STATE_DIR = Path(tempfile.gettempdir()) / "pmmail-doc-reminder"
STATE_MAX_AGE = 2 * 24 * 3600

CODE_SUFFIXES = {
    ".py",
    ".go",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".html",
    ".sql",
    ".sh",
    ".ps1",
    ".json",
    ".toml",
    ".yml",
    ".yaml",
    ".ini",
    ".mod",
    ".mako",
}
CODE_NAMES = {"dockerfile"}
# Không tính là code hay tài liệu: dữ liệu sinh ra, mail mẫu, ghi chép làm việc, thiết lập cá nhân.
IGNORED_PREFIXES = (
    "data/thunderbird-profile/",
    "data/expected_threads.json",
    "samples/",
    ".superpowers/",
    ".claude/settings.local.json",
    "claude.local.md",
)
# Thay đổi chỉ nằm trong test không cần tài liệu (.claude/rules/documentation.md).
TEST_PATTERN = re.compile(r"(^|/)(tests?|testdata)/|_test\.go$|(^|/)(test_[^/]*|conftest)\.py$")


def project_root(payload: dict) -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()).resolve()


def state_file(payload: dict) -> Path:
    session = re.sub(r"[^A-Za-z0-9_.-]", "_", str(payload.get("session_id") or "unknown"))
    return STATE_DIR / f"{session}.json"


def relative(path: str, root: Path) -> str | None:
    """Đường dẫn tương đối kiểu posix, chữ thường; None nếu nằm ngoài dự án."""
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    try:
        rel = os.path.relpath(os.path.normcase(p.resolve()), os.path.normcase(root))
    except ValueError:  # khác ổ đĩa
        return None
    if rel.startswith(".."):
        return None
    return PurePosixPath(*Path(rel).parts).as_posix().lower()


def classify(rel: str) -> str | None:
    """'doc', 'code' hoặc None (bỏ qua)."""
    if rel.startswith(IGNORED_PREFIXES) or TEST_PATTERN.search(rel):
        return None
    name = PurePosixPath(rel).name
    if name.endswith(".md"):
        return "doc"
    if PurePosixPath(rel).suffix in CODE_SUFFIXES or name in CODE_NAMES:
        return "code"
    return None


def load(path: Path) -> list[str]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def track(payload: dict) -> None:
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not file_path:
        return
    rel = relative(file_path, project_root(payload))
    if rel is None:
        return
    path = state_file(payload)
    files = load(path)
    if rel not in files:
        files.append(rel)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(files), encoding="utf-8")


def reset(payload: dict) -> None:
    state_file(payload).unlink(missing_ok=True)
    now = time.time()
    for old in STATE_DIR.glob("*.json") if STATE_DIR.exists() else ():
        try:
            if now - old.stat().st_mtime > STATE_MAX_AGE:
                old.unlink()
        except OSError:
            pass


def check(payload: dict) -> None:
    if payload.get("stop_hook_active"):  # đã nhắc một lần trong lượt này
        return
    files = load(state_file(payload))
    kinds = {f: classify(f) for f in files}
    code = [f for f, k in kinds.items() if k == "code"]
    if not code or "doc" in kinds.values():
        return
    shown = ", ".join(code[:10]) + (f" và {len(code) - 10} file khác" if len(code) > 10 else "")
    reason = (
        f"Lượt này đã sửa code ({shown}) nhưng chưa sửa tài liệu nào. "
        "Theo .claude/rules/documentation.md: cập nhật spec, danh mục spec "
        "(docs/superpowers/specs/README.md), docs/CHANGELOG.md và CLAUDE.md liên quan. "
        'Nếu thay đổi này không cần tài liệu thì ghi rõ "không cần cập nhật tài liệu" '
        "kèm lý do trong câu trả lời cuối."
    )
    print(json.dumps({"decision": "block", "reason": reason}))


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    try:
        {"track": track, "reset": reset, "check": check}.get(mode, lambda _: None)(payload)
    except OSError:  # không ghi/đọc được file trạng thái: bỏ qua, không làm hỏng lượt làm việc
        return


if __name__ == "__main__":
    main()
