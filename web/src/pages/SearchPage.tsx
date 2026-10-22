import { Paperclip } from "lucide-react";
import { Link, useSearchParams } from "react-router";

import { errorMessage, type SearchHit } from "@/api/client";
import { useSearch, type SearchParams } from "@/api/hooks";
import { FilterBar, selectClass } from "@/components/FilterBar";
import { Highlighted } from "@/components/Highlighted";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { hasFilters, readFilters, toApiFilters } from "@/lib/filters";
import { folderName, formatCount, formatDateTime, senderLabel } from "@/lib/format";
import { threadLink } from "@/lib/links";

const PAGE_SIZE = 50;
const HYBRID_MAX = 200;
const MATCH_LABEL: Record<NonNullable<SearchHit["match"]>, string> = {
  keyword: "từ khóa",
  semantic: "ngữ nghĩa",
  both: "từ khóa + ngữ nghĩa",
};

function Hit({ hit, q }: { hit: SearchHit; q: string }) {
  const to = threadLink(hit, q);
  const body = (
    <>
      <div className="flex items-baseline gap-2">
        <span className="min-w-0 flex-1 truncate font-medium">
          {hit.subject.length ? <Highlighted segments={hit.subject} /> : "(không có tiêu đề)"}
        </span>
        <span className="shrink-0 text-sm text-muted-foreground">
          {formatDateTime(hit.sent_at)}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <span>{senderLabel(hit.from_name, hit.from_addr)}</span>
        {hit.folders.map((f) => (
          <Badge key={f.id} variant="outline" title={f.path}>
            {folderName(f.path)}
          </Badge>
        ))}
        {hit.thread_message_count !== null && hit.thread_message_count > 1 && (
          <span>· thread {hit.thread_message_count} mail</span>
        )}
        {hit.has_attachments && <Paperclip className="size-3.5" aria-label="Có file đính kèm" />}
        {hit.match && <Badge variant="secondary">{MATCH_LABEL[hit.match]}</Badge>}
      </div>
      {hit.snippet.length > 0 && (
        <p className="mt-1 line-clamp-2 text-sm">
          <Highlighted segments={hit.snippet} />
        </p>
      )}
    </>
  );
  return (
    <li>
      {to ? (
        <Link to={to} className="block px-3 py-2.5 hover:bg-accent">
          {body}
        </Link>
      ) : (
        <div className="px-3 py-2.5" title="Mail đang chờ gom thread">
          {body}
        </div>
      )}
    </li>
  );
}

export function SearchPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q")?.trim() ?? "";
  const filters = readFilters(params);
  const hybrid = params.get("mode") === "hybrid";
  const sort = params.get("sort") === "relevance" && q ? "relevance" : "date";
  const page = Math.max(0, Number.parseInt(params.get("page") ?? "0", 10) || 0);
  // Ngữ nghĩa cần có câu tìm kiếm; từ khóa thì chỉ lọc cũng được.
  const enabled = hybrid ? Boolean(q) : Boolean(q) || hasFilters(filters);
  const query: SearchParams = {
    ...toApiFilters(filters),
    ...(q ? { q } : {}),
    ...(hybrid ? { mode: "hybrid" as const } : { sort }),
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };
  const search = useSearch(query, enabled);
  const result = search.data;
  const pages = result ? Math.ceil(result.total / PAGE_SIZE) : 0;
  const maxOffset = hybrid ? HYBRID_MAX : 10_000;

  function set(key: string, value: string | null) {
    const p = new URLSearchParams(params);
    if (value === null) p.delete(key);
    else p.set(key, value);
    if (key !== "page") p.delete("page");
    setParams(p);
  }

  function setMode(next: "keyword" | "hybrid") {
    const p = new URLSearchParams(params);
    if (next === "hybrid") {
      p.set("mode", "hybrid");
      p.delete("sort");
    } else p.delete("mode");
    p.delete("page");
    setParams(p);
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">Kết quả tìm kiếm</h1>
        <div className="flex rounded-md border p-0.5" role="group" aria-label="Cách tìm">
          <Button
            size="sm"
            variant={hybrid ? "ghost" : "secondary"}
            aria-pressed={!hybrid}
            onClick={() => setMode("keyword")}
          >
            Từ khóa
          </Button>
          <Button
            size="sm"
            variant={hybrid ? "secondary" : "ghost"}
            aria-pressed={hybrid}
            onClick={() => setMode("hybrid")}
          >
            Ngữ nghĩa
          </Button>
        </div>
      </div>
      <FilterBar />
      {!enabled && !hybrid && (
        <p className="text-sm text-muted-foreground">
          Gõ từ khóa ở ô tìm kiếm phía trên. Nhiều từ cách nhau bằng khoảng trắng: mail phải có đủ
          các từ. <code>OR</code> (viết hoa) giữa hai từ: có một trong hai. <code>-từ</code>: không
          có từ đó. <code>"cụm từ"</code>: tìm nguyên cụm.
        </p>
      )}
      {!enabled && hybrid && (
        <p className="text-sm text-muted-foreground">
          Gõ câu mô tả điều cần tìm, bằng tiếng Việt, Nhật hoặc Anh (vd. "khách hỏi lại báo giá
          Project B"). Kết quả xếp theo độ gần nghĩa (kể cả mail khác ngôn ngữ); mail chứa đủ các từ
          bạn gõ (như tìm từ khóa) được ưu tiên thêm.
        </p>
      )}
      {search.error && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(search.error)}</AlertDescription>
        </Alert>
      )}
      {enabled && search.isPending && <p className="text-sm text-muted-foreground">Đang tìm…</p>}
      {result && (
        <>
          <div className="mb-2 flex items-center gap-3 text-sm">
            <span>
              {result.total_capped ? "Hơn " : ""}
              {formatCount(result.total)} kết quả
              {hybrid && " (xếp theo độ liên quan)"}
            </span>
            {hybrid && (result.embedding_pending ?? 0) > 0 && (
              <span className="text-muted-foreground">
                · còn{" "}
                {(result.embedding_pending ?? 0) > 10_000
                  ? "hơn 10.000"
                  : formatCount(result.embedding_pending ?? 0)}{" "}
                mail chưa xử lý cho tìm kiếm ngữ nghĩa (chỉ tìm được bằng từ khóa)
              </span>
            )}
            {q && !hybrid && (
              <select
                aria-label="Sắp xếp"
                value={sort}
                onChange={(e) => set("sort", e.target.value === "relevance" ? "relevance" : null)}
                className={selectClass}
              >
                <option value="date">Mới nhất trước</option>
                <option value="relevance">Liên quan nhất trước</option>
              </select>
            )}
          </div>
          <ul className="divide-y rounded-lg border">
            {result.items.map((hit) => (
              <Hit key={hit.message_id} hit={hit} q={q} />
            ))}
          </ul>
          {pages > 1 && (
            <div className="mt-3 flex items-center justify-center gap-3 text-sm">
              <Button
                variant="outline"
                size="sm"
                disabled={page === 0}
                onClick={() => set("page", String(page - 1))}
              >
                Trang trước
              </Button>
              <span>
                Trang {page + 1}/{pages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page + 1 >= pages || (page + 1) * PAGE_SIZE > maxOffset}
                onClick={() => set("page", String(page + 1))}
              >
                Trang sau
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
