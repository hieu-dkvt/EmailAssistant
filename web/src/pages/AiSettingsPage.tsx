import { type FormEvent, useState } from "react";

import { errorMessage, type AiSettings, type Tier } from "@/api/client";
import { useAiSettings, useAiUsage, useSaveAiSettings } from "@/api/hooks";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatCount, formatDateTime } from "@/lib/format";
import { cn } from "cn";

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 4,
});

function price(n: number): string {
  return `$${n.toFixed(2)}`;
}

/** Chọn cấp model cho từng tác vụ (spec 22). Lưu cả bảng một lần. */
function TierForm({ settings }: { settings: AiSettings }) {
  const save = useSaveAiSettings();
  const [chosen, setChosen] = useState<Record<string, Tier>>(() =>
    Object.fromEntries(settings.tasks.map((t) => [t.task, t.tier])),
  );
  const changed = settings.tasks.some((t) => chosen[t.task] !== t.tier);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save.mutate({ tiers: chosen });
  }

  return (
    <form onSubmit={submit} className="grid gap-4" aria-label="Model theo tác vụ">
      {settings.tasks.map((task) => (
        <fieldset key={task.task} className="grid gap-2 rounded-lg border p-3">
          <legend className="px-1 font-medium">{task.label}</legend>
          <p className="text-sm text-muted-foreground">{task.description}</p>
          <div className="grid gap-2 sm:grid-cols-3">
            {settings.tiers.map((tier) => {
              const id = `tier-${task.task}-${tier.tier}`;
              const active = chosen[task.task] === tier.tier;
              return (
                <label
                  key={tier.tier}
                  htmlFor={id}
                  className={cn(
                    "grid cursor-pointer gap-0.5 rounded-md border p-2 text-sm",
                    active && "border-primary bg-secondary",
                  )}
                >
                  <span className="flex items-center gap-2 font-medium">
                    <input
                      id={id}
                      type="radio"
                      name={task.task}
                      value={tier.tier}
                      checked={active}
                      onChange={() => setChosen({ ...chosen, [task.task]: tier.tier })}
                    />
                    {tier.label}
                    {task.default_tier === tier.tier && (
                      <span className="text-xs font-normal text-muted-foreground">(mặc định)</span>
                    )}
                  </span>
                  <span className="text-xs text-muted-foreground">{tier.model}</span>
                  <span className="text-xs text-muted-foreground">
                    {price(tier.input_price)} vào / {price(tier.output_price)} ra mỗi 1 triệu token
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>
      ))}
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={!changed || save.isPending}>
          Lưu
        </Button>
        {save.isSuccess && !changed && (
          <span className="text-sm text-muted-foreground" role="status">
            Đã lưu.
          </span>
        )}
      </div>
      {save.error && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(save.error)}</AlertDescription>
        </Alert>
      )}
    </form>
  );
}

function Usage() {
  const usage = useAiUsage();
  if (usage.isPending) return <p className="text-sm text-muted-foreground">Đang tải…</p>;
  if (usage.error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{errorMessage(usage.error)}</AlertDescription>
      </Alert>
    );
  }
  const u = usage.data;
  if (u.rows.length === 0) {
    return <p className="text-sm text-muted-foreground">Tháng này chưa gọi Claude lần nào.</p>;
  }
  return (
    <div className="grid gap-2 text-sm">
      <table className="w-full text-left">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-1 font-normal">Model</th>
            <th className="py-1 text-right font-normal">Lần gọi</th>
            <th className="py-1 text-right font-normal">Token vào</th>
            <th className="py-1 text-right font-normal">Token ra</th>
            <th className="py-1 text-right font-normal">Ước tính</th>
          </tr>
        </thead>
        <tbody>
          {u.rows.map((r) => (
            <tr key={r.model} className="border-t">
              <td className="py-1">{r.model}</td>
              <td className="py-1 text-right">{formatCount(r.calls)}</td>
              <td className="py-1 text-right">{formatCount(r.input_tokens)}</td>
              <td className="py-1 text-right">{formatCount(r.output_tokens)}</td>
              <td className="py-1 text-right">
                {r.estimated_usd === null ? "—" : usd.format(r.estimated_usd)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-muted-foreground">
        Tổng {formatCount(u.total_calls)} lần gọi, khoảng {usd.format(u.total_estimated_usd)} từ{" "}
        {formatDateTime(u.since)}. Ước tính theo bảng giá cố định, số tiền thật xem trên hóa đơn
        Anthropic.
      </p>
    </div>
  );
}

export function AiSettingsPage() {
  const settings = useAiSettings();
  if (settings.isPending) return <p className="text-sm text-muted-foreground">Đang tải…</p>;
  if (settings.error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{errorMessage(settings.error)}</AlertDescription>
      </Alert>
    );
  }
  const s = settings.data;
  return (
    <div className="grid gap-4">
      <h1 className="text-lg font-semibold">Cài đặt AI</h1>
      {!s.configured && (
        <Alert>
          <AlertDescription>
            Chưa cấu hình Claude: đặt biến môi trường <code>ANTHROPIC_API_KEY</code> cho backend rồi
            chạy lại <code>python -m app.run</code>. Chức năng hỏi đáp chưa dùng được.
          </AlertDescription>
        </Alert>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Model theo tác vụ</CardTitle>
          <CardDescription>
            Haiku nhanh và rẻ, Sonnet cân bằng, Opus trả lời tốt nhất với câu hỏi khó nhưng đắt
            nhất. Cài đặt này chỉ áp dụng cho tài khoản của bạn.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <TierForm settings={s} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Che thông tin trước khi gửi Claude</CardTitle>
          <CardDescription>
            Luôn bật theo yêu cầu NDA của khách, không tắt được. Các nhóm thông tin bị thay bằng
            nhãn giả:
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="list-disc pl-5 text-sm">
            {s.masking.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Sử dụng tháng này</CardTitle>
        </CardHeader>
        <CardContent>
          <Usage />
        </CardContent>
      </Card>
    </div>
  );
}
