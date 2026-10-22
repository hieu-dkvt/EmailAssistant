import { FileText, Paperclip, Scissors } from "lucide-react";
import { useState } from "react";

import { errorMessage, type ThreadMessage } from "@/api/client";
import { useCreateOverride, useMessageFull } from "@/api/hooks";
import { Highlighted } from "@/components/Highlighted";
import { useRethread } from "@/components/rethread-context";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { folderName, formatDateTime, formatSize, senderLabel } from "@/lib/format";
import { cn } from "cn";

const PART_LABEL = { quote: "Trích dẫn", signature: "Chữ ký" } as const;

function FullText({ messageId, q }: { messageId: number; q: string }) {
  const full = useMessageFull(messageId, q, true);
  if (full.isPending) return <p className="text-sm text-muted-foreground">Đang tải toàn văn…</p>;
  if (full.error) return <p className="text-sm text-destructive">{errorMessage(full.error)}</p>;
  return (
    <div className="grid gap-1">
      {full.data.parts.map((part, i) =>
        part.kind === "body" ? (
          <pre key={i} className="font-sans text-sm break-words whitespace-pre-wrap">
            <Highlighted segments={part.segments} />
          </pre>
        ) : (
          <div key={i} className="border-l-2 pl-3 text-muted-foreground">
            <div className="text-xs">{PART_LABEL[part.kind]} (đã ẩn trong bản làm sạch)</div>
            <pre className="font-sans text-sm break-words whitespace-pre-wrap">
              <Highlighted segments={part.segments} />
            </pre>
          </div>
        ),
      )}
    </div>
  );
}

function addressList(list: ThreadMessage["to"]): string {
  return list.map((a) => (a.name ? `${a.name} <${a.addr}>` : a.addr)).join(", ");
}

function SplitDialog({ message, onClose }: { message: ThreadMessage; onClose: () => void }) {
  const create = useCreateOverride();
  const rethread = useRethread();
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Tách thành thread riêng?</DialogTitle>
          <DialogDescription>
            Mail này và các mail trả lời nó sẽ thành một thread riêng. Có thể hoàn tác ở trang "Sửa
            tay".
          </DialogDescription>
        </DialogHeader>
        {create.error && (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage(create.error)}</AlertDescription>
          </Alert>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Hủy
          </Button>
          <Button
            disabled={create.isPending}
            onClick={() =>
              create.mutate(
                { action: "split", message_id: message.id },
                {
                  onSuccess: () => {
                    onClose();
                    rethread.start(message.id);
                  },
                },
              )
            }
          >
            Tách
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function MessageCard({ message, q }: { message: ThreadMessage; q: string }) {
  const [showFull, setShowFull] = useState(false);
  const [splitting, setSplitting] = useState(false);
  const rethread = useRethread();
  return (
    <article id={`m-${message.id}`} className="scroll-mt-20 rounded-lg border p-4 target:ring-2">
      <header className="mb-2 grid gap-0.5 text-sm">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="font-medium">{senderLabel(message.from_name, message.from_addr)}</span>
          {message.from_name && message.from_addr && (
            <span className="text-muted-foreground">&lt;{message.from_addr}&gt;</span>
          )}
          <span className="ml-auto text-muted-foreground">{formatDateTime(message.sent_at)}</span>
        </div>
        {message.to.length > 0 && (
          <div className="text-muted-foreground">Đến: {addressList(message.to)}</div>
        )}
        {message.cc.length > 0 && (
          <div className="text-muted-foreground">Cc: {addressList(message.cc)}</div>
        )}
        <div className="font-medium">
          <Highlighted segments={message.subject} />
        </div>
        <div className="flex flex-wrap gap-1">
          {message.folders.map((f) => (
            <Badge key={f.id} variant="outline" title={f.path}>
              {folderName(f.path)}
            </Badge>
          ))}
        </div>
      </header>
      {message.attachments.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-2 text-sm" aria-label="File đính kèm">
          {message.attachments.map((a, i) => (
            <li key={i} className="flex items-center gap-1 rounded-md bg-secondary px-2 py-0.5">
              <Paperclip className="size-3.5" />
              {a.filename || "(không tên)"}
              <span className="text-muted-foreground">({formatSize(a.size)})</span>
            </li>
          ))}
        </ul>
      )}
      {!message.cleaned && (
        <p className="mb-1 text-xs text-muted-foreground">
          Mail chưa được làm sạch (đang chờ worker): hiện nội dung đầy đủ.
        </p>
      )}
      {showFull ? (
        <FullText messageId={message.id} q={q} />
      ) : (
        <pre className="font-sans text-sm break-words whitespace-pre-wrap">
          {message.body.length ? (
            <Highlighted segments={message.body} />
          ) : (
            <span className="text-muted-foreground">(không có nội dung mới)</span>
          )}
        </pre>
      )}
      {message.body_truncated && !showFull && (
        <p className="mt-1 text-xs text-muted-foreground">
          Nội dung dài, chỉ hiện phần đầu: bấm "Toàn văn" để đọc hết.
        </p>
      )}
      <footer className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={() => setShowFull((v) => !v)}>
          <FileText />
          {showFull ? "Bản làm sạch" : "Toàn văn"}
        </Button>
        {message.cut_count > 0 && !showFull && (
          <span className="text-xs text-muted-foreground">
            Đã ẩn {message.cut_count} đoạn trích dẫn/chữ ký
          </span>
        )}
        {message.thread_method !== "root" && (
          <Button
            variant="ghost"
            size="sm"
            className={cn("ml-auto")}
            disabled={rethread.waiting}
            onClick={() => setSplitting(true)}
          >
            <Scissors />
            Tách từ mail này
          </Button>
        )}
      </footer>
      {splitting && <SplitDialog message={message} onClose={() => setSplitting(false)} />}
    </article>
  );
}
