import { useState } from "react";
import { Link } from "react-router";

import { errorMessage, type SimilarThread } from "@/api/client";
import { useCreateOverride, useSimilarThreads } from "@/api/hooks";
import { useRethread } from "@/components/rethread-context";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatDateTime } from "@/lib/format";

const NO_SUBJECT = "(không có tiêu đề)";

interface Props {
  threadId: number;
  rootMessageId: number;
}

/** Thread có nội dung gần giống (lớp 4 gom thread, spec 23): gợi ý, người dùng tự quyết gộp. */
export function SimilarThreads({ threadId, rootMessageId }: Props) {
  const rethread = useRethread();
  const similar = useSimilarThreads(threadId, !rethread.waiting);
  const create = useCreateOverride();
  const [target, setTarget] = useState<SimilarThread | null>(null);

  if (similar.error || !similar.data) return null;
  if (similar.data.pending) {
    return (
      <p className="text-xs text-muted-foreground">
        Thread chưa được xử lý cho tìm kiếm ngữ nghĩa nên chưa có gợi ý thread liên quan.
      </p>
    );
  }
  if (similar.data.items.length === 0) return null;

  function merge() {
    if (!target) return;
    create.mutate(
      { action: "merge", message_id: rootMessageId, target_message_id: target.message_id },
      {
        onSuccess: () => {
          setTarget(null);
          rethread.start(rootMessageId);
        },
      },
    );
  }

  return (
    <section className="rounded-lg border p-3" aria-label="Thread có thể liên quan">
      <h2 className="mb-1 font-semibold">Thread có thể liên quan</h2>
      <p className="mb-2 text-xs text-muted-foreground">
        Nội dung gần giống thread này (so bằng embedding). Nếu là cùng một trao đổi bị tách (vd.
        khách gửi mail mới thay vì trả lời), có thể gộp; hoàn tác ở trang "Sửa tay".
      </p>
      <ul className="divide-y text-sm">
        {similar.data.items.map((t) => (
          <li key={t.thread_id} className="flex flex-wrap items-center gap-2 py-2">
            <Link
              to={`/threads/${t.thread_id}`}
              className="min-w-0 flex-1 truncate hover:underline"
            >
              {t.subject || NO_SUBJECT}
            </Link>
            <span className="text-muted-foreground">
              {t.message_count} mail · {formatDateTime(t.last_message_at)} · giống{" "}
              {Math.round(t.score * 100)}%
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={rethread.waiting}
              onClick={() => setTarget(t)}
            >
              Gộp vào thread này
            </Button>
          </li>
        ))}
      </ul>
      {target && (
        <Dialog open onOpenChange={(open) => !open && setTarget(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Gộp thread</DialogTitle>
              <DialogDescription>
                Toàn bộ thread đang xem sẽ được gộp vào thread "{target.subject || NO_SUBJECT}". Có
                thể hoàn tác ở trang "Sửa tay".
              </DialogDescription>
            </DialogHeader>
            {create.error && (
              <Alert variant="destructive">
                <AlertDescription>{errorMessage(create.error)}</AlertDescription>
              </Alert>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setTarget(null)}>
                Hủy
              </Button>
              <Button disabled={create.isPending} onClick={merge}>
                Gộp
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </section>
  );
}
