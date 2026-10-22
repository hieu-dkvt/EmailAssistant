import { Loader2, Sparkles } from "lucide-react";
import { type FormEvent, Fragment, type ReactNode } from "react";
import { Link } from "react-router";

import { errorMessage, type AskResult, type AskSource } from "@/api/client";
import { useAsk, useFolders } from "@/api/hooks";
import { selectClass } from "@/components/FilterBar";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { localMidnight } from "@/lib/filters";
import { folderName, formatCount, formatDateTime, senderLabel } from "@/lib/format";
import { threadLink } from "@/lib/links";

const MAX_QUESTION = 1000;
const CITE = /\[(\d{1,3})\]/g;
const KIND_LABEL: Record<string, string> = {
  PERSON: "tên người",
  ORG: "công ty / tổ chức",
  EMAIL: "email",
  PHONE: "điện thoại",
  URL: "URL",
  ADDRESS: "địa chỉ",
  MONEY: "số tiền",
  CONTRACT: "số hợp đồng / đơn hàng",
};
const TASK_LABEL: Record<string, string> = {
  question_analysis: "Phân tích câu hỏi",
  answer: "Trả lời",
};

/** Câu trả lời dạng text; "[n]" thành link tới mail nguồn số n. Không render HTML. */
export function AnswerText({ text, sources }: { text: string; sources: AskSource[] }) {
  const byN = new Map(sources.map((s) => [s.n, s]));
  const parts: ReactNode[] = [];
  let pos = 0;
  for (const m of text.matchAll(CITE)) {
    const n = Number(m[1]);
    const src = byN.get(n);
    const to = src ? threadLink(src, "") : null;
    parts.push(text.slice(pos, m.index));
    parts.push(
      to ? (
        <Link
          key={`${m.index}`}
          to={to}
          className="text-primary hover:underline"
          title={src?.subject ?? ""}
        >
          [{n}]
        </Link>
      ) : (
        m[0]
      ),
    );
    pos = (m.index ?? 0) + m[0].length;
  }
  parts.push(text.slice(pos));
  return (
    <p className="text-sm leading-relaxed whitespace-pre-wrap">
      {parts.map((p, i) => (
        <Fragment key={i}>{p}</Fragment>
      ))}
    </p>
  );
}

function Sources({ sources }: { sources: AskSource[] }) {
  return (
    <ol className="divide-y rounded-lg border text-sm">
      {sources.map((s) => {
        const to = threadLink(s, "");
        const label = s.subject || "(không có tiêu đề)";
        return (
          <li key={s.n} className="flex flex-wrap items-baseline gap-2 px-3 py-2">
            <span className="w-8 shrink-0 text-muted-foreground">[{s.n}]</span>
            {to ? (
              <Link to={to} className="min-w-0 flex-1 truncate font-medium hover:underline">
                {label}
              </Link>
            ) : (
              <span className="min-w-0 flex-1 truncate font-medium">{label}</span>
            )}
            <span className="text-muted-foreground">
              {senderLabel(s.from_name, s.from_addr)} · {formatDateTime(s.sent_at)}
            </span>
            {s.cited && <Badge variant="secondary">được trích</Badge>}
          </li>
        );
      })}
    </ol>
  );
}

function SentToClaude({ result }: { result: AskResult }) {
  const counts = Object.entries(result.mask_counts);
  return (
    <details className="rounded-lg border p-3 text-sm">
      <summary className="cursor-pointer font-medium">
        Nội dung đã gửi Claude (đã che thông tin nhạy cảm)
      </summary>
      <p className="mt-2 text-muted-foreground">
        {counts.length
          ? "Đã che: " +
            counts.map(([k, n]) => `${formatCount(n)} ${KIND_LABEL[k] ?? k}`).join(", ") +
            ". Claude chỉ thấy nhãn như [PERSON_1]; câu trả lời được thay lại giá trị thật trên máy chủ của mình."
          : "Không có thông tin nào cần che."}{" "}
        Kiểm tra bên dưới nếu còn tên hoặc số liệu chưa được che.
      </p>
      {result.sent.map((s, i) => (
        <div key={i} className="mt-3">
          <p className="mb-1 font-medium">{TASK_LABEL[s.task] ?? s.task}</p>
          <pre className="max-h-96 overflow-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap">
            {s.content}
          </pre>
        </div>
      ))}
    </details>
  );
}

function Result({ result }: { result: AskResult }) {
  const plan = result.plan;
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Trả lời</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3">
          {result.insufficient && (
            <Alert>
              <AlertDescription>
                Các mail tìm được chưa đủ để trả lời chắc chắn. Kiểm tra lại nguồn hoặc hỏi cụ thể
                hơn (tên dự án, khoảng thời gian…).
              </AlertDescription>
            </Alert>
          )}
          <AnswerText text={result.answer} sources={result.sources} />
          <p className="text-xs text-muted-foreground">
            Câu trả lời dựa trên nội dung mail; mail có thể sai hoặc cố tình gây nhầm (kể cả chèn
            chỉ dẫn cho AI). Kiểm tra lại mail nguồn trước khi dùng.
          </p>
        </CardContent>
      </Card>
      {result.sources.length > 0 && (
        <section>
          <h2 className="mb-2 font-semibold">Mail nguồn ({result.sources.length})</h2>
          <Sources sources={result.sources} />
        </section>
      )}
      {plan && (
        <p className="text-sm text-muted-foreground">
          Đã tìm với:{" "}
          {[
            plan.search_queries.length ? `câu tìm "${plan.search_queries.join('", "')}"` : null,
            plan.keywords.length ? `từ khóa ${plan.keywords.join(", ")}` : null,
            plan.sender ? `người gửi "${plan.sender}"` : null,
            plan.sent_after ? `từ ${plan.sent_after}` : null,
            plan.sent_before ? `trước ${plan.sent_before}` : null,
          ]
            .filter(Boolean)
            .join(" · ") || "câu hỏi gốc"}
          {plan.filters_relaxed &&
            ". Không có mail nào khớp bộ lọc người gửi/ngày suy ra từ câu hỏi, nên đã tìm lại không dùng bộ lọc đó."}
        </p>
      )}
      <SentToClaude result={result} />
      {result.calls.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {result.calls
            .map(
              (c) =>
                `${TASK_LABEL[c.task] ?? c.task}: ${c.model}, ${formatCount(c.input_tokens)} token vào / ${formatCount(c.output_tokens)} token ra, ${(c.duration_ms / 1000).toFixed(1)} giây`,
            )
            .join(" · ")}
        </p>
      )}
    </div>
  );
}

export function AskPage() {
  const ask = useAsk();
  const folders = useFolders();

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const question = String(data.get("question") ?? "").trim();
    if (!question) return;
    const folder = String(data.get("folder") ?? "");
    const after = localMidnight(String(data.get("from") ?? ""));
    const before = localMidnight(String(data.get("to") ?? ""), 1);
    ask.mutate({
      question,
      ...(folder ? { folder_id: Number(folder) } : {}),
      ...(after ? { sent_after: after.toISOString() } : {}),
      ...(before ? { sent_before: before.toISOString() } : {}),
    });
  }

  return (
    <div className="grid gap-4">
      <div>
        <h1 className="text-lg font-semibold">Hỏi AI về mail</h1>
        <p className="text-sm text-muted-foreground">
          Hỏi bằng tiếng Việt, Nhật hoặc Anh. Hệ thống tìm các đoạn mail liên quan trong mail của
          bạn, che tên người, công ty, email, điện thoại, địa chỉ, số tiền, số hợp đồng rồi mới gửi
          cho Claude. Câu hỏi và câu trả lời không được lưu lại.
        </p>
      </div>
      <form onSubmit={submit} className="grid gap-3 rounded-lg border p-3" aria-label="Hỏi đáp">
        <div className="grid gap-1">
          <Label htmlFor="ask-q">Câu hỏi</Label>
          <textarea
            id="ask-q"
            name="question"
            required
            rows={3}
            maxLength={MAX_QUESTION}
            autoComplete="off"
            placeholder="Vd. Khách đã đồng ý báo giá sửa màn hình nhập kho chưa? Ai trả lời, khi nào?"
            className="rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          />
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1">
            <Label htmlFor="ask-folder">Folder</Label>
            <select id="ask-folder" name="folder" defaultValue="" className={selectClass}>
              <option value="">Tất cả folder</option>
              {folders.data?.map((f) => (
                <option key={f.id} value={f.id} title={f.folder_path}>
                  {folderName(f.folder_path)}
                </option>
              ))}
            </select>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="ask-from">Từ ngày</Label>
            <Input id="ask-from" name="from" type="date" className="w-40" />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="ask-to">Đến ngày</Label>
            <Input id="ask-to" name="to" type="date" className="w-40" />
          </div>
          <Button type="submit" disabled={ask.isPending}>
            {ask.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}
            Hỏi
          </Button>
        </div>
      </form>
      {ask.isPending && (
        <p className="text-sm text-muted-foreground" role="status">
          Đang tìm mail và hỏi Claude… (thường 10–60 giây)
        </p>
      )}
      {ask.error && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(ask.error)}</AlertDescription>
        </Alert>
      )}
      {ask.data && !ask.isPending && <Result result={ask.data} />}
    </div>
  );
}
