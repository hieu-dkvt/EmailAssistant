import { TriangleAlert } from "lucide-react";
import { type FormEvent, useState } from "react";

import { errorMessage, type AiSettings } from "@/api/client";
import {
  useAiProvider,
  useAiSettings,
  useAiUsage,
  useDeleteAiProvider,
  useSaveAiProvider,
  useSaveAiSettings,
  useTestAiProvider,
} from "@/api/hooks";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatCount, formatDateTime } from "@/lib/format";

/** Nhập tên model (theo cách gọi của gateway) cho từng tác vụ. Lưu cả bảng một lần. */
function ModelForm({ settings }: { settings: AiSettings }) {
  const save = useSaveAiSettings();
  const [models, setModels] = useState<Record<string, string>>(() =>
    Object.fromEntries(settings.tasks.map((t) => [t.task, t.model ?? ""])),
  );
  const changed = settings.tasks.some((t) => models[t.task].trim() !== (t.model ?? ""));

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save.mutate({ models });
  }

  return (
    <form onSubmit={submit} className="grid gap-4" aria-label="Model theo tác vụ">
      {settings.tasks.map((task) => (
        <fieldset key={task.task} className="grid gap-2 rounded-lg border p-3">
          <legend className="px-1 font-medium">{task.label}</legend>
          <p className="text-sm text-muted-foreground">{task.description}</p>
          <div className="grid gap-1">
            <Label htmlFor={`model-${task.task}`}>Tên model</Label>
            <Input
              id={`model-${task.task}`}
              value={models[task.task]}
              maxLength={200}
              placeholder="tên model theo nhà cung cấp"
              autoComplete="off"
              onChange={(e) => setModels({ ...models, [task.task]: e.target.value })}
            />
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

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return "";
  }
}

/** Nhà cung cấp AI: gateway model nội bộ kiểu 9router (spec 24, 28). API key chỉ đi một chiều. */
function ProviderForm() {
  const provider = useAiProvider();
  const save = useSaveAiProvider();
  const remove = useDeleteAiProvider();
  const test = useTestAiProvider();
  const [protocol, setProtocol] = useState<"anthropic" | "openai" | null>(null);
  const [baseUrl, setBaseUrl] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");

  if (provider.isPending) return <p className="text-sm text-muted-foreground">Đang tải…</p>;
  if (provider.error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{errorMessage(provider.error)}</AlertDescription>
      </Alert>
    );
  }
  const p = provider.data;
  const shownProtocol = protocol ?? p.custom?.protocol ?? "openai";
  const shownUrl = baseUrl ?? p.custom?.base_url ?? "";
  const host = hostOf(shownUrl);
  // public_host do backend tính cho địa chỉ đã lưu: sửa URL trong ô thì chưa biết, bỏ cảnh báo.
  const publicHost = !!p.custom?.public_host && shownUrl.trim() === p.custom.base_url;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save.mutate(
      { protocol: shownProtocol, base_url: shownUrl.trim(), api_key: apiKey.trim() || null },
      {
        onSuccess: () => {
          setApiKey("");
          setProtocol(null);
          setBaseUrl(null);
        },
      },
    );
  }

  return (
    <form onSubmit={submit} className="grid gap-3" aria-label="Nhà cung cấp AI">
      {!p.secret_key_ready && (
        <Alert>
          <AlertDescription>
            Backend chưa có <code>PMM_SECRET_KEY</code> nên chưa lưu được API key. Chạy{" "}
            <code>python -m app.cli gen-secret-key</code>, đặt kết quả vào biến môi trường{" "}
            <code>PMM_SECRET_KEY</code> rồi chạy lại backend.
          </AlertDescription>
        </Alert>
      )}
      <div className="grid gap-1">
        <Label htmlFor="provider-protocol">Giao thức</Label>
        <select
          id="provider-protocol"
          className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
          value={shownProtocol}
          onChange={(e) => setProtocol(e.target.value as "anthropic" | "openai")}
        >
          <option value="openai">Tương thích OpenAI (9router…)</option>
          <option value="anthropic">Anthropic (Messages API)</option>
        </select>
      </div>
      <div className="grid gap-1">
        <Label htmlFor="provider-url">URL gốc</Label>
        <Input
          id="provider-url"
          value={shownUrl}
          placeholder="http://10.0.0.5:20128/v1"
          autoComplete="off"
          onChange={(e) => setBaseUrl(e.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          Địa chỉ gateway model nội bộ, <code>http://</code> hoặc <code>https://</code>.
        </p>
      </div>
      <div className="grid gap-1">
        <Label htmlFor="provider-key">API key</Label>
        <Input
          id="provider-key"
          type="password"
          value={apiKey}
          autoComplete="new-password"
          placeholder={
            p.custom?.api_key_set
              ? `Đã đặt (…${p.custom.api_key_hint ?? ""}), để trống để giữ nguyên`
              : "Dán API key"
          }
          onChange={(e) => setApiKey(e.target.value)}
        />
        {p.custom && !p.custom.api_key_set && (
          <p className="text-xs text-destructive">
            Không đọc được key đã lưu (có thể đã đổi <code>PMM_SECRET_KEY</code>): nhập lại key.
          </p>
        )}
      </div>
      {host && (
        <Alert>
          <AlertDescription>
            Mail <strong>không che</strong> sẽ gửi tới <code>{host}</code>.
          </AlertDescription>
        </Alert>
      )}
      {publicHost && (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertDescription>
            Đây là địa chỉ công cộng, mail sẽ ra ngoài mạng nội bộ.
          </AlertDescription>
        </Alert>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={!p.secret_key_ready || !shownUrl.trim() || save.isPending}>
          Lưu nhà cung cấp
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={!p.custom || test.isPending}
          onClick={() => test.mutate()}
        >
          Kiểm tra kết nối
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={!p.custom || remove.isPending}
          onClick={() => remove.mutate()}
        >
          Xóa
        </Button>
        {save.isSuccess && (
          <span className="text-sm text-muted-foreground" role="status">
            Đã lưu nhà cung cấp.
          </span>
        )}
        {test.data && (
          <span className="text-sm text-muted-foreground" role="status">
            Kết nối được ({test.data.model}, {test.data.duration_ms} ms).
          </span>
        )}
      </div>
      {[save.error, test.error, remove.error].map(
        (error, i) =>
          error && (
            <Alert key={i} variant="destructive">
              <AlertDescription>{errorMessage(error)}</AlertDescription>
            </Alert>
          ),
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
    return <p className="text-sm text-muted-foreground">Tháng này chưa gọi AI lần nào.</p>;
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
          </tr>
        </thead>
        <tbody>
          {u.rows.map((r) => (
            <tr key={r.model} className="border-t">
              <td className="py-1">{r.model}</td>
              <td className="py-1 text-right">{formatCount(r.calls)}</td>
              <td className="py-1 text-right">{formatCount(r.input_tokens)}</td>
              <td className="py-1 text-right">{formatCount(r.output_tokens)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-muted-foreground">
        Tổng {formatCount(u.total_calls)} lần gọi từ {formatDateTime(u.since)}.
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
            Chưa cấu hình AI: nhập nhà cung cấp ở khối bên dưới. Hỏi đáp và dịch mail chưa dùng
            được.
          </AlertDescription>
        </Alert>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Nhà cung cấp AI</CardTitle>
          <CardDescription>
            Gateway model nội bộ (kiểu 9router). API key được mã hóa khi lưu và không bao giờ hiển
            thị lại. Chỉ áp dụng cho tài khoản của bạn.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ProviderForm />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Model theo tác vụ</CardTitle>
          <CardDescription>
            Nhập tên model theo cách gọi của gateway cho từng tác vụ. Cài đặt này chỉ áp dụng cho
            tài khoản của bạn.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ModelForm settings={s} />
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
