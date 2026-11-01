import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AiSettings } from "@/api/client";

import { AiSettingsPage } from "./AiSettingsPage";

const TIERS: AiSettings["tiers"] = [
  { tier: "haiku", label: "Haiku", model: "claude-haiku-5-5", input_price: 0.1, output_price: 0.5 },
  { tier: "sonnet", label: "Sonnet", model: "claude-sonnet-5-5", input_price: 2, output_price: 10 },
  { tier: "opus", label: "Opus", model: "claude-opus-5-5", input_price: 4, output_price: 20 },
];

function settings(answer: "haiku" | "sonnet" | "opus", custom = false): AiSettings {
  return {
    configured: custom,
    custom_provider: custom,
    tasks: [
      {
        task: "answer",
        label: "Trả lời câu hỏi",
        description: "Đọc mail và trả lời.",
        tier: answer,
        default_tier: "sonnet",
        model: custom ? "anthropic/claude-sonnet-5-5" : null,
      },
    ],
    tiers: TIERS,
    masking: ["Tên người", "Số tiền, số hợp đồng / đơn hàng"],
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("AiSettingsPage", () => {
  it("chọn cấp model cho tác vụ rồi lưu bằng PUT", async () => {
    const puts: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const path = new URL(request.url).pathname;
        if (path === "/api/v1/ai/provider") {
          return json(200, { configured_env: false, secret_key_ready: true, custom: null });
        }
        if (path === "/api/v1/ai/usage") {
          return json(200, {
            since: "2026-10-01T00:00:00Z",
            rows: [],
            total_calls: 0,
            total_estimated_usd: 0,
          });
        }
        if (request.method === "PUT") {
          const body = (await request.json()) as { tiers: { answer: "opus" } };
          puts.push(body);
          return json(200, settings(body.tiers.answer));
        }
        return json(200, settings("sonnet"));
      }),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <AiSettingsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/Chưa cấu hình AI/)).toBeInTheDocument();
    expect(screen.getByText("Tên người")).toBeInTheDocument();
    expect(await screen.findByText("Tháng này chưa gọi AI lần nào.")).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Lưu" });
    expect(save).toBeDisabled();
    const user = userEvent.setup();
    await user.click(screen.getByLabelText(/Opus/));
    await user.click(save);
    expect(await screen.findByText("Đã lưu.")).toBeInTheDocument();
    expect(puts).toEqual([{ tiers: { answer: "opus" } }]);
  });

  it("lưu nhà cung cấp tùy chỉnh, không hiện lại API key và cảnh báo NDA theo host", async () => {
    const calls: { method: string; path: string; body: unknown }[] = [];
    let custom: unknown = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const path = new URL(request.url).pathname;
        const body = request.method === "GET" ? null : await request.json().catch(() => null);
        calls.push({ method: request.method, path, body });
        if (path === "/api/v1/ai/usage") {
          return json(200, {
            since: "2026-10-01T00:00:00Z",
            rows: [],
            total_calls: 0,
            total_estimated_usd: 0,
          });
        }
        if (path === "/api/v1/ai/provider" && request.method === "PUT") {
          custom = {
            protocol: "openai",
            base_url: "https://openrouter.ai/api/v1",
            api_key_set: true,
            api_key_hint: "abcd",
          };
          return json(200, { configured_env: false, secret_key_ready: true, custom });
        }
        if (path === "/api/v1/ai/provider") {
          return json(200, { configured_env: false, secret_key_ready: true, custom });
        }
        return json(200, settings("sonnet", custom !== null));
      }),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <AiSettingsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    const url = await screen.findByLabelText("URL gốc");
    await user.type(url, "https://openrouter.ai/api/v1");
    expect(screen.getByText("openrouter.ai")).toBeInTheDocument();
    await user.type(screen.getByLabelText("API key"), "sk-or-secret-abcd");
    await user.click(screen.getByRole("button", { name: "Lưu nhà cung cấp" }));
    expect(await screen.findByText("Đã lưu nhà cung cấp.")).toBeInTheDocument();
    const put = calls.find((c) => c.method === "PUT" && c.path === "/api/v1/ai/provider");
    expect(put?.body).toEqual({
      protocol: "openai",
      base_url: "https://openrouter.ai/api/v1",
      api_key: "sk-or-secret-abcd",
    });
    // Ô key được xóa và API không trả key về: không còn chỗ nào trên trang chứa key.
    expect(screen.getByLabelText("API key")).toHaveValue("");
    expect(document.body.innerHTML).not.toContain("sk-or-secret-abcd");
    expect(screen.getByLabelText("API key")).toHaveAttribute(
      "placeholder",
      expect.stringContaining("abcd"),
    );
    // Có provider tùy chỉnh: nhập tên model thay vì chọn cấp.
    const model = await screen.findByLabelText("Tên model");
    expect(model).toHaveValue("anthropic/claude-sonnet-5-5");
    expect(screen.queryByLabelText(/Opus/)).not.toBeInTheDocument();
  });
});
