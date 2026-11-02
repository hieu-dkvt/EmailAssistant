"""Đo quét chính xác và recall của HNSW trên vector tổng hợp có cụm (spec 27 mục 4, 13).

Chạy từ thư mục gốc (cần Docker DB; KHÔNG chạy cùng lúc với pytest vì cùng dùng database test):

    uv run --directory backend python ../eval/vector_bench.py [--chunks 100000] [--out FILE]

Chỉ chạy trên database test mailassist_test (từ chối database khác). Không đọc, không ghi dữ liệu
mail: tạo schema `bench` riêng (messages, chunks) với vector tổng hợp có cụm, chuẩn hóa, xóa ở cuối.

1. Quét chính xác (CTE MATERIALIZED, cùng dạng với exact_chunks_sql) ở 5.000, 10.000, 20.000 đoạn:
   thời gian từng lần và EXPLAIN ANALYZE.
2. Recall@120 và thời gian của index HNSW (m = 16) với ef_construction 64 và 128, ef_search 40,
   120, 200 (đối chứng: recall phải tăng theo ef_search), so với quét chính xác toàn bộ vector.

Vector có cụm: 1.000 tâm ngẫu nhiên cộng nhiễu, chuẩn hóa (cosine nội cụm khoảng 0,7); truy vấn là
tâm ngẫu nhiên cộng nhiễu mới. Không có nội dung mail. Câu đo quét chính xác đơn giản hơn câu thật
(không RLS, không điều kiện model/cleaner_version, phạm vi chọn theo khoảng id) nên số thật cao hơn.
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

# Script nằm ngoài backend/: thêm backend vào sys.path để import tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import Connection, create_engine, text  # noqa: E402

from tests.dbutil import TEST_DB, url_for_test_db  # noqa: E402

DIM = 1024
PER_MESSAGE = 3  # đoạn mỗi mail
BATCH = 5_000
QUERIES = 20
TOP = 120
SCOPES = (5_000, 10_000, 20_000)
TIMING_RUNS = 5

CLUSTERS = 1_000
NOISE = 0.66  # biên độ nhiễu so với tâm (tâm: (random() - 0.5)); cosine nội cụm khoảng 0,7
EF_SEARCH = (40, 120, 200)

# Vector có cụm: tâm ngẫu nhiên (bench.centroids) cộng nhiễu, chuẩn hóa. :cid chọn tâm.
CLUSTERED_VECTOR = (
    "SELECT CAST(l2_normalize(CAST(array_agg(t.x + (random() - 0.5) * "
    f"{NOISE} ORDER BY t.o) AS vector({DIM}))) AS halfvec({DIM})) "
    "FROM bench.centroids cen, unnest(cen.arr) WITH ORDINALITY t(x, o) WHERE cen.id = "
)
# cid giả ngẫu nhiên xác định theo chỉ số dòng (tránh phụ thuộc vào cách planner xử lý random()).
CID = f"(1 + (CAST({{i}} AS bigint) * 2654435761) % {CLUSTERS})"

EXACT_SCAN = """
WITH scope AS MATERIALIZED (
  SELECT c.message_id, c.chunk_no, c.embedding <=> CAST(:qv AS halfvec(1024)) AS distance
  FROM bench.chunks c
  JOIN bench.messages m ON m.id = c.message_id
  WHERE m.id <= :max_id
)
SELECT * FROM scope ORDER BY distance, message_id, chunk_no LIMIT 300
"""


def load(conn: Connection, total: int) -> None:
    conn.execute(text("DROP SCHEMA IF EXISTS bench CASCADE"))
    conn.execute(text("CREATE SCHEMA bench"))
    conn.execute(text("CREATE TABLE bench.messages (id integer PRIMARY KEY)"))
    conn.execute(
        text(
            "CREATE TABLE bench.chunks (message_id integer NOT NULL, chunk_no integer NOT NULL, "
            f"embedding halfvec({DIM}) NOT NULL, PRIMARY KEY (message_id, chunk_no))"
        )
    )
    conn.execute(
        text("INSERT INTO bench.messages SELECT g FROM generate_series(1, :n) g"),
        {"n": total // PER_MESSAGE + 1},
    )
    conn.execute(text("SELECT setseed(0.42)"))
    conn.execute(text("CREATE TABLE bench.centroids (id integer PRIMARY KEY, arr real[] NOT NULL)"))
    conn.execute(
        text(
            "INSERT INTO bench.centroids SELECT g, (SELECT array_agg(random() - 0.5) "
            f"FROM generate_series(1, {DIM}) WHERE g = g) FROM generate_series(1, {CLUSTERS}) g"
        )
    )
    for start in range(0, total, BATCH):
        n = min(BATCH, total - start)
        conn.execute(
            text(
                "INSERT INTO bench.chunks (message_id, chunk_no, embedding) "
                f"SELECT (i / {PER_MESSAGE}) + 1, i % {PER_MESSAGE}, v.e "
                "FROM generate_series(CAST(:a AS integer), CAST(:b AS integer)) i "
                f"CROSS JOIN LATERAL ({CLUSTERED_VECTOR}{CID.format(i='i')}) v(e)"
            ),
            {"a": start, "b": start + n - 1},
        )
    conn.execute(text("ANALYZE bench.chunks"))
    conn.execute(text("ANALYZE bench.messages"))


def query_vectors(conn: Connection) -> list[str]:
    """Truy vấn giống dữ liệu: một tâm ngẫu nhiên cộng nhiễu mới."""
    return [
        str(
            conn.scalar(
                text(f"SELECT CAST(({CLUSTERED_VECTOR}{CID.format(i=1000 * (q + 1) + 7)}) AS text)")
            )
        )
        for q in range(QUERIES)
    ]


def bench_exact(conn: Connection, qvs: list[str], total: int) -> list[str]:
    lines = [
        "| đoạn trong phạm vi | trung vị (ms) | nhỏ nhất (ms) | lớn nhất (ms) |",
        "|---|---|---|---|",
    ]
    plans: list[str] = []
    for scope in SCOPES:
        if scope > total:
            continue
        params = {"qv": qvs[0], "max_id": scope // PER_MESSAGE}
        times = []
        for k in range(TIMING_RUNS):
            params["qv"] = qvs[k % len(qvs)]
            t0 = time.perf_counter()
            conn.execute(text(EXACT_SCAN), params).fetchall()
            times.append((time.perf_counter() - t0) * 1000)
        lines.append(
            f"| {scope} | {statistics.median(times):.0f} | {min(times):.0f} | {max(times):.0f} |"
        )
        plan = "\n".join(conn.scalars(text("EXPLAIN (ANALYZE, COSTS OFF) " + EXACT_SCAN), params))
        plans.append(f"-- EXPLAIN ANALYZE, {scope} đoạn\n{plan}")
    return [*lines, "", *plans]


def recall(
    conn: Connection, qvs: list[str], ef_construction: int
) -> tuple[float, dict[int, tuple[float, float]]]:
    """Trả (thời gian build, {ef_search: (recall@TOP, trung vị ms của câu dùng index)})."""
    conn.execute(text("DROP INDEX IF EXISTS bench.ix_bench_embedding"))
    t0 = time.perf_counter()
    conn.execute(
        text(
            "CREATE INDEX ix_bench_embedding ON bench.chunks USING hnsw "
            f"(embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = {ef_construction})"
        )
    )
    build = time.perf_counter() - t0
    nearest = (
        "SELECT message_id, chunk_no FROM bench.chunks "
        f"ORDER BY embedding <=> CAST(:qv AS halfvec(1024)) LIMIT {TOP}"
    )
    conn.execute(text("SET hnsw.iterative_scan = relaxed_order"))
    conn.execute(text("SET enable_indexscan = off"))
    conn.execute(text("SET enable_bitmapscan = off"))
    truths = [{tuple(r) for r in conn.execute(text(nearest), {"qv": qv})} for qv in qvs]
    conn.execute(text("SET enable_indexscan = on"))
    conn.execute(text("SET enable_bitmapscan = on"))
    out: dict[int, tuple[float, float]] = {}
    for ef in EF_SEARCH:
        conn.execute(text(f"SET hnsw.ef_search = {ef}"))
        scores, times = [], []
        for qv, truth in zip(qvs, truths, strict=True):
            t0 = time.perf_counter()
            got = {tuple(r) for r in conn.execute(text(nearest), {"qv": qv})}
            times.append((time.perf_counter() - t0) * 1000)
            scores.append(len(truth & got) / TOP)
        out[ef] = (statistics.mean(scores), statistics.median(times))
    conn.execute(text("RESET enable_indexscan"))
    conn.execute(text("RESET enable_bitmapscan"))
    return build, out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chunks", type=int, default=100_000)
    ap.add_argument("--out")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    url = url_for_test_db("owner")
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    out: list[str] = []
    with engine.connect() as conn:
        db = conn.scalar(text("SELECT current_database()"))
        if db != TEST_DB:
            sys.exit(f"Chỉ chạy trên {TEST_DB}, đang ở {db}")
        try:
            t0 = time.perf_counter()
            load(conn, args.chunks)
            out.append(f"Nạp {args.chunks} vector: {time.perf_counter() - t0:.0f} giây")
            qvs = query_vectors(conn)
            conn.execute(text("SET max_parallel_workers_per_gather = 0"))
            out += ["", "## Quét chính xác (không index, không song song)", ""]
            out += bench_exact(conn, qvs, args.chunks)
            conn.execute(text("RESET max_parallel_workers_per_gather"))
            conn.execute(text("SET maintenance_work_mem = '1GB'"))
            out += [
                "",
                f"## Recall@{TOP} của HNSW (m = 16, {QUERIES} truy vấn, vector có cụm)",
                "",
                "| ef_construction | ef_search | recall@120 | trung vị câu dùng index (ms) |"
                " build (giây) |",
                "|---|---|---|---|---|",
            ]
            for efc in (64, 128):
                build, res = recall(conn, qvs, efc)
                for ef, (r, ms) in res.items():
                    out.append(f"| {efc} | {ef} | {r:.3f} | {ms:.1f} | {build:.0f} |")
                    print(out[-1], flush=True)
        finally:
            conn.execute(text("DROP SCHEMA IF EXISTS bench CASCADE"))
    report = "\n".join(out)
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
