import { Paperclip } from "lucide-react";
import { Link, useSearchParams } from "react-router";

import { errorMessage } from "@/api/client";
import { useThreads } from "@/api/hooks";
import { FilterBar } from "@/components/FilterBar";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { hasFilters, readFilters, toApiFilters } from "@/lib/filters";
import { formatShortDate, senderLabel } from "@/lib/format";

export function ThreadListPage() {
  const [params] = useSearchParams();
  const filters = readFilters(params);
  const threads = useThreads(toApiFilters(filters));
  const items = threads.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div>
      <h1 className="mb-3 text-lg font-semibold">Thread</h1>
      <FilterBar />
      {threads.error && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(threads.error)}</AlertDescription>
        </Alert>
      )}
      {threads.isPending && <p className="text-sm text-muted-foreground">Đang tải…</p>}
      {threads.isSuccess && items.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {hasFilters(filters)
            ? "Không có thread nào khớp bộ lọc."
            : "Chưa có thread nào. Đồng bộ mail bằng collector rồi chờ worker xử lý."}
        </p>
      )}
      <ul className="divide-y rounded-lg border">
        {items.map((t) => (
          <li key={t.id}>
            <Link
              to={`/threads/${t.id}`}
              className="flex items-center gap-3 px-3 py-2.5 hover:bg-accent"
            >
              <span className="w-44 shrink-0 truncate text-sm text-muted-foreground">
                {senderLabel(t.last_from_name, t.last_from_addr)}
              </span>
              <span className="min-w-0 flex-1 truncate">{t.subject || "(không có tiêu đề)"}</span>
              {t.message_count > 1 && <Badge variant="secondary">{t.message_count}</Badge>}
              {t.has_attachments && (
                <Paperclip className="size-4 text-muted-foreground" aria-label="Có file đính kèm" />
              )}
              <span className="w-24 shrink-0 text-right text-sm text-muted-foreground">
                {formatShortDate(t.last_message_at)}
              </span>
            </Link>
          </li>
        ))}
      </ul>
      {threads.hasNextPage && (
        <div className="mt-3 flex justify-center">
          <Button
            variant="outline"
            onClick={() => threads.fetchNextPage()}
            disabled={threads.isFetchingNextPage}
          >
            {threads.isFetchingNextPage ? "Đang tải…" : "Tải thêm"}
          </Button>
        </div>
      )}
    </div>
  );
}
