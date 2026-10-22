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

function settings(answer: "haiku" | "sonnet" | "opus"): AiSettings {
  return {
    configured: false,
    tasks: [
      {
        task: "answer",
        label: "Trả lời câu hỏi",
        description: "Đọc mail và trả lời.",
        tier: answer,
        default_tier: "sonnet",
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
    expect(await screen.findByText(/Chưa cấu hình Claude/)).toBeInTheDocument();
    expect(screen.getByText("Tên người")).toBeInTheDocument();
    expect(await screen.findByText("Tháng này chưa gọi Claude lần nào.")).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Lưu" });
    expect(save).toBeDisabled();
    const user = userEvent.setup();
    await user.click(screen.getByLabelText(/Opus/));
    await user.click(save);
    expect(await screen.findByText("Đã lưu.")).toBeInTheDocument();
    expect(puts).toEqual([{ tiers: { answer: "opus" } }]);
  });
});
