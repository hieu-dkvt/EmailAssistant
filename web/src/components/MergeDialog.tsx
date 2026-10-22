import { type FormEvent, useState } from "react";

import { errorMessage, type SearchHit } from "@/api/client";
import { useCreateOverride, useSearch } from "@/api/hooks";
import { Highlighted } from "@/components/Highlighted";
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
import { Input } from "@/components/ui/input";
import { formatDateTime, senderLabel } from "@/lib/format";
import { cn } from "cn";

interface Props {
  threadId: number;
  rootMessageId: number;
  onClose: () => void;
}

/** Chọn một mail thuộc thread khác (bằng search) rồi gộp thread đang xem vào thread đó. */
export function MergeDialog({ threadId, rootMessageId, onClose }: Props) {
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<SearchHit | null>(null);
  const search = useSearch({ q, limit: 20 }, q.length > 0);
  const create = useCreateOverride();
  const rethread = useRethread();

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPicked(null);
    setQ(String(new FormData(event.currentTarget).get("q") ?? "").trim());
  }

  function merge() {
    if (!picked) return;
    create.mutate(
      { action: "merge", message_id: rootMessageId, target_message_id: picked.message_id },
      {
        onSuccess: () => {
          onClose();
          rethread.start(rootMessageId);
        },
      },
    );
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Gộp vào thread khác</DialogTitle>
          <DialogDescription>
            Tìm một mail thuộc thread đích. Toàn bộ thread đang xem sẽ được gộp vào thread chứa mail
            đó. Có thể hoàn tác ở trang "Sửa tay".
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="flex gap-2">
          <Input
            name="q"
            autoComplete="off"
            placeholder="Từ khóa của thread đích…"
            aria-label="Tìm thread đích"
          />
          <Button type="submit" variant="outline">
            Tìm
          </Button>
        </form>
        {search.error && (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage(search.error)}</AlertDescription>
          </Alert>
        )}
        <ul className="max-h-80 divide-y overflow-y-auto rounded-md border" aria-label="Kết quả">
          {search.data?.items.map((hit) => {
            const same = hit.thread_id === threadId;
            const disabled = same || hit.thread_id === null;
            return (
              <li key={hit.message_id}>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => setPicked(hit)}
                  aria-pressed={picked?.message_id === hit.message_id}
                  className={cn(
                    "block w-full px-3 py-2 text-left text-sm hover:bg-accent disabled:opacity-50",
                    picked?.message_id === hit.message_id && "bg-secondary",
                  )}
                >
                  <div className="truncate font-medium">
                    <Highlighted segments={hit.subject} />
                  </div>
                  <div className="text-muted-foreground">
                    {senderLabel(hit.from_name, hit.from_addr)} · {formatDateTime(hit.sent_at)}
                    {same && " · cùng thread đang xem"}
                  </div>
                </button>
              </li>
            );
          })}
          {search.data && search.data.items.length === 0 && (
            <li className="px-3 py-2 text-sm text-muted-foreground">Không có kết quả.</li>
          )}
        </ul>
        {create.error && (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage(create.error)}</AlertDescription>
          </Alert>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Hủy
          </Button>
          <Button disabled={!picked || create.isPending} onClick={merge}>
            Gộp vào thread này
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
