import { Link } from "react-router";

import { errorMessage, type MessageBrief, type OverrideInfo } from "@/api/client";
import { useDeleteOverride, useOverrides } from "@/api/hooks";
import { useRethread } from "@/components/rethread-context";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDateTime, senderLabel } from "@/lib/format";

function Brief({ m }: { m: MessageBrief }) {
  const label = m.subject || "(không có tiêu đề)";
  return (
    <span>
      {m.thread_id !== null ? (
        <Link to={`/threads/${m.thread_id}#m-${m.id}`} className="font-medium hover:underline">
          {label}
        </Link>
      ) : (
        <span className="font-medium">{label}</span>
      )}
      <span className="text-muted-foreground">
        {" "}
        · {senderLabel(m.from_name, m.from_addr)} · {formatDateTime(m.sent_at)}
      </span>
    </span>
  );
}

function Row({ o }: { o: OverrideInfo }) {
  const remove = useDeleteOverride();
  const rethread = useRethread();
  return (
    <li className="flex flex-wrap items-center gap-3 px-3 py-2.5 text-sm">
      <Badge variant={o.action === "split" ? "secondary" : "outline"}>
        {o.action === "split" ? "Tách" : "Gộp"}
      </Badge>
      <div className="grid min-w-0 flex-1 gap-0.5">
        <Brief m={o.message} />
        {o.target && (
          <span className="text-muted-foreground">
            vào thread của: <Brief m={o.target} />
          </span>
        )}
      </div>
      <span className="text-muted-foreground">{formatDateTime(o.created_at)}</span>
      <Button
        variant="outline"
        size="sm"
        disabled={remove.isPending || rethread.waiting}
        onClick={() => remove.mutate(o.id, { onSuccess: () => rethread.start(null) })}
      >
        Hoàn tác
      </Button>
      {remove.error && <span className="text-destructive">{errorMessage(remove.error)}</span>}
    </li>
  );
}

export function OverridesPage() {
  const overrides = useOverrides();
  return (
    <div>
      <h1 className="mb-1 text-lg font-semibold">Sửa tay</h1>
      <p className="mb-3 text-sm text-muted-foreground">
        Các lần tách/gộp thread bằng tay. Thuật toán gom thread luôn áp dụng lại các sửa tay này.
      </p>
      {overrides.error && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(overrides.error)}</AlertDescription>
        </Alert>
      )}
      {overrides.data && overrides.data.length === 0 && (
        <p className="text-sm text-muted-foreground">Chưa có sửa tay nào.</p>
      )}
      {overrides.data && overrides.data.length > 0 && (
        <ul className="divide-y rounded-lg border">
          {overrides.data.map((o) => (
            <Row key={o.id} o={o} />
          ))}
        </ul>
      )}
    </div>
  );
}
