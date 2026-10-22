"""Gom thread cho mail của MỘT người dùng (spec 2026-10-17-threading-design.md mục 4).

Hàm thuần, không I/O, không dùng AI. Cùng dữ liệu thì cùng kết quả (mail xét theo thứ tự
(sent_at, id), sent_at null xếp cuối). Người gọi chịu trách nhiệm chỉ đưa vào mail của một người.
"""

import base64
import binascii
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from app.mail.subject import normalize_subject

THREADER_VERSION = 1
SUBJECT_WINDOW = timedelta(days=60)
THREAD_INDEX_HEADER_BYTES = 22

type Method = Literal["root", "references", "thread_index", "subject", "merge"]


@dataclass(frozen=True)
class MailRow:
    id: int
    message_id: str | None
    in_reply_to: str | None
    references: tuple[str, ...]
    thread_index: str | None
    subject: str | None
    from_addr: str | None
    recipients: frozenset[str]  # To + Cc, viết thường
    sent_at: datetime | None


@dataclass(frozen=True)
class Override:
    action: Literal["split", "merge"]
    message_id: int
    target_message_id: int | None = None


@dataclass
class ThreadingResult:
    groups: list[list[int]]  # id mail của từng thread, theo thứ tự (sent_at, id)
    parent: dict[int, int | None]  # thread_parent_id
    method: dict[int, Method]


def thread_index_key(value: str | None) -> bytes | None:
    """22 byte đầu của Thread-Index (ID cuộc hội thoại của Outlook); hỏng hoặc ngắn → None."""
    if not value:
        return None
    s = "".join(value.split())
    try:
        raw = base64.b64decode(s + "=" * (-len(s) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw[:THREAD_INDEX_HEADER_BYTES] if len(raw) >= THREAD_INDEX_HEADER_BYTES else None


# --- lớp 1: container JWZ ---


@dataclass(eq=False)
class _Container:
    key: str
    row: MailRow | None = None
    parent: "_Container | None" = None
    protected: bool = False  # mail bị split: không ai đặt cha cho nó được


def _would_loop(parent: _Container, child: _Container) -> bool:
    node: _Container | None = parent
    while node is not None:
        if node is child:
            return True
        node = node.parent
    return False


def _order_key(row: MailRow) -> tuple[bool, datetime, int]:
    return (row.sent_at is None, row.sent_at or datetime.min, row.id)


def _references(row: MailRow, own_key: str) -> list[str]:
    refs = [r.lower() for r in row.references if r]
    irt = row.in_reply_to.lower() if row.in_reply_to else None
    if irt:
        # In-Reply-To chỉ đúng mail cha trực tiếp: luôn đứng cuối.
        refs = [r for r in refs if r != irt] + [irt]
    seen: set[str] = set()
    out: list[str] = []
    for r in refs:
        if r != own_key and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _build_containers(rows: Sequence[MailRow], split_ids: set[int]) -> dict[int, _Container]:
    containers: dict[str, _Container] = {}
    own: dict[int, _Container] = {}
    for row in rows:
        key = row.message_id.lower() if row.message_id else f"#{row.id}"
        if key in containers and containers[key].row is not None:
            key = f"#{row.id}"  # Message-ID trùng trong một người dùng (không xảy ra sau ingest)
        c = containers.setdefault(key, _Container(key))
        c.row = row
        c.protected = row.id in split_ids
        own[row.id] = c

    for row in rows:
        c = own[row.id]
        prev: _Container | None = None
        for key in _references(row, c.key):
            rc = containers.setdefault(key, _Container(key))
            if (
                prev is not None
                and rc.parent is None
                and rc is not c
                and not rc.protected
                and not _would_loop(prev, rc)
            ):
                rc.parent = prev
            prev = rc
        if c.protected:
            c.parent = None
        elif prev is not None and not _would_loop(prev, c):
            c.parent = prev  # header của chính mail ghi đè cha do mail khác đặt
    return own


def _root(c: _Container) -> _Container:
    while c.parent is not None:
        c = c.parent
    return c


def _nearest_message_ancestor(c: _Container) -> int | None:
    node = c.parent
    while node is not None:
        if node.row is not None:
            return node.row.id
        node = node.parent
    return None


# --- nhóm: union-find trên cây lớp 1 ---


@dataclass(eq=False)
class _Group:
    first: MailRow  # mail sớm nhất
    rank: int  # thứ tự của mail sớm nhất
    base_tree: int  # cây chứa mail sớm nhất
    trees: list[int]
    participants: set[str] = field(default_factory=set)
    dates: list[datetime] = field(default_factory=list)  # tăng dần
    subjects: set[str] = field(default_factory=set)


class _Groups:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}
        self.info: dict[int, _Group] = {}
        self.tree_method: dict[int, Method] = {}

    def add(self, tree: int, group: _Group) -> None:
        self.parent[tree] = tree
        self.info[tree] = group

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int, method: Method) -> int:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        ga, gb = self.info[ra], self.info[rb]
        keep, lose = (ra, rb) if ga.rank <= gb.rank else (rb, ra)
        gk, gl = self.info[keep], self.info.pop(lose)
        self.parent[lose] = keep
        self.tree_method.setdefault(gl.base_tree, method)
        gk.trees.extend(gl.trees)
        gk.participants |= gl.participants
        gk.dates = sorted(gk.dates + gl.dates)
        gk.subjects |= gl.subjects
        return keep


def build_threads(rows: Iterable[MailRow], overrides: Sequence[Override] = ()) -> ThreadingResult:
    """Gom mail của một người dùng. overrides theo thứ tự created_at, id."""
    order = sorted(rows, key=_order_key)
    rank = {row.id: i for i, row in enumerate(order)}
    split_ids = {o.message_id for o in overrides if o.action == "split"}
    own = _build_containers(order, split_ids)

    # Lớp 1: mỗi cây container là một nhóm. Id cây = id của container gốc (theo id Python).
    tree_of: dict[int, int] = {}
    tree_rows: dict[int, list[MailRow]] = {}
    protected_trees: set[int] = set()
    for row in order:
        root = _root(own[row.id])
        tid = id(root)
        tree_of[row.id] = tid
        tree_rows.setdefault(tid, []).append(row)
        if root.protected:
            protected_trees.add(tid)

    groups = _Groups()
    for tid, members in tree_rows.items():
        first = members[0]  # members theo thứ tự order
        g = _Group(first=first, rank=rank[first.id], base_tree=tid, trees=[tid])
        for row in members:
            g.participants.update(_participants(row))
            if row.sent_at is not None:
                g.dates.append(row.sent_at)
            g.subjects.add(normalize_subject(row.subject).text)
        g.dates.sort()
        groups.add(tid, g)

    # Lớp 2: Thread-Index.
    by_key: dict[bytes, int] = {}
    for row in order:
        tid = tree_of[row.id]
        key = thread_index_key(row.thread_index)
        if key is None or tid in protected_trees:
            continue
        if key in by_key:
            groups.union(by_key[key], tid, "thread_index")
        else:
            by_key[key] = tid

    # Lớp 3: subject + người tham gia + thời gian.
    by_subject: dict[str, list[int]] = {}
    for root in sorted({groups.find(t) for t in tree_rows}, key=lambda r: groups.info[r].rank):
        g = groups.info[root]
        target = None if root in protected_trees else _subject_target(groups, g, by_subject)
        if target is not None:
            root = groups.union(target, root, "subject")
        for s in g.subjects:
            if s:
                by_subject.setdefault(s, []).append(root)

    # Sửa tay: merge.
    for o in overrides:
        if o.action != "merge" or o.target_message_id is None:
            continue
        source, target = tree_of.get(o.message_id), tree_of.get(o.target_message_id)
        if source is not None and target is not None:
            groups.union(target, source, "merge")

    members_of: dict[int, list[int]] = {}
    for row in order:
        members_of.setdefault(groups.find(tree_of[row.id]), []).append(row.id)
    result = ThreadingResult(groups=[], parent={}, method={})
    for root in sorted(members_of, key=lambda r: groups.info[r].rank):
        g = groups.info[root]
        result.groups.append(members_of[root])
        for mid in members_of[root]:
            tid = tree_of[mid]
            if mid == g.first.id:
                result.method[mid] = "root"
            elif tid == g.base_tree:
                result.method[mid] = "references"
            else:
                result.method[mid] = groups.tree_method[tid]
            result.parent[mid] = _nearest_message_ancestor(own[mid])
    return result


def _participants(row: MailRow) -> set[str]:
    return set(row.recipients) | ({row.from_addr} if row.from_addr else set())


def _subject_target(groups: _Groups, g: _Group, by_subject: dict[str, list[int]]) -> int | None:
    first = g.first
    subject = normalize_subject(first.subject)
    if (
        subject.outer is None
        or not subject.text
        or first.sent_at is None
        or first.from_addr is None
    ):
        return None
    sent_at = first.sent_at
    best: tuple[datetime, int, int] | None = None  # (ngày gần nhất, -id mail sớm nhất, root)
    for candidate in {groups.find(r) for r in by_subject.get(subject.text, ())}:
        d = groups.info[candidate]
        i = bisect_right(d.dates, sent_at)
        if i == 0 or sent_at - d.dates[i - 1] > SUBJECT_WINDOW:
            continue
        if first.from_addr not in d.participants or not (first.recipients & d.participants):
            continue
        key = (d.dates[i - 1], -d.first.id, candidate)
        if best is None or key[:2] > best[:2]:
            best = key
    return best[2] if best is not None else None


def assign_thread_ids(
    groups: Sequence[Sequence[int]], old_thread_of: Mapping[int, int | None]
) -> list[int | None]:
    """Ánh xạ nhóm mới sang id thread cũ theo số mail chung (spec mục 4.6). None = thread mới."""
    pairs: list[tuple[int, int, int]] = []  # (số mail chung, id thread cũ, chỉ số nhóm)
    for gi, members in enumerate(groups):
        counts = Counter(t for m in members if (t := old_thread_of.get(m)) is not None)
        pairs.extend((n, old, gi) for old, n in counts.items())
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    assigned: list[int | None] = [None] * len(groups)
    used: set[int] = set()
    for _, old, gi in pairs:
        if assigned[gi] is None and old not in used:
            assigned[gi] = old
            used.add(old)
    return assigned
