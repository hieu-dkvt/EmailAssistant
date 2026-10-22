import { Merge } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router";

import { ApiError, errorMessage } from "@/api/client";
import { useThread } from "@/api/hooks";
import { MergeDialog } from "@/components/MergeDialog";
import { MessageCard } from "@/components/MessageCard";
import { SimilarThreads } from "@/components/SimilarThreads";
import { useRethread } from "@/components/rethread-context";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/format";

export function ThreadPage() {
  const { id } = useParams();
  const threadId = Number(id);
  const [params] = useSearchParams();
  const q = params.get("q")?.trim() ?? "";
  const location = useLocation();
  const thread = useThread(threadId, q);
  const rethread = useRethread();
  const [merging, setMerging] = useState(false);

  // Mở từ kết quả search (#m-<id>): cuộn tới mail đó sau khi tải xong.
  useEffect(() => {
    if (!thread.data || !location.hash) return;
    document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: "start" });
  }, [thread.data, location.hash]);

  if (!Number.isInteger(threadId) || threadId <= 0) {
    return <p className="text-sm">Đường dẫn thread không hợp lệ.</p>;
  }
  if (thread.isPending) return <p className="text-sm text-muted-foreground">Đang tải…</p>;
  if (thread.error) {
    const notFound = thread.error instanceof ApiError && thread.error.status === 404;
    return (
      <Alert variant={notFound ? "default" : "destructive"}>
        <AlertDescription>
          {notFound
            ? "Không có thread này (có thể thread đã được gom lại với thread khác)."
            : errorMessage(thread.error)}{" "}
          <Link to="/threads" className="underline">
            Về danh sách thread
          </Link>
        </AlertDescription>
      </Alert>
    );
  }

  const t = thread.data;
  const root = t.messages[0];
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-semibold break-words">{t.subject || "(không có tiêu đề)"}</h1>
          <p className="text-sm text-muted-foreground">
            {t.message_count} mail · {formatDateTime(t.first_message_at)} →{" "}
            {formatDateTime(t.last_message_at)}
          </p>
        </div>
        {root && (
          <Button variant="outline" disabled={rethread.waiting} onClick={() => setMerging(true)}>
            <Merge />
            Gộp vào thread khác…
          </Button>
        )}
      </div>
      {t.truncated && (
        <p className="text-sm text-muted-foreground">
          Thread quá dài: chỉ hiện {t.messages.length} mail đầu.
        </p>
      )}
      {t.messages.map((m) => (
        <MessageCard key={m.id} message={m} q={q} />
      ))}
      {root && <SimilarThreads threadId={t.id} rootMessageId={root.id} />}
      {merging && root && (
        <MergeDialog threadId={t.id} rootMessageId={root.id} onClose={() => setMerging(false)} />
      )}
    </div>
  );
}
