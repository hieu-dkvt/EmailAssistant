import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AiProvider, AiSettings } from "@/api/client";

import { AiSettingsPage } from "./AiSettingsPage";

function settings(configured: boolean, model: string | null = null): AiSettings {
  return {
    configured,
    tasks: [
      {
        task: "answer",
        label: "Trả lời câu hỏi",
        description: "Đọc mail và trả lời.",
        model,
      },
    ],
  };
}

function provider(custom: AiProvider["custom"]): AiProvider {
  return { secret_key_ready: true, custom };
}

function custom(over: Partial<NonNullable<AiProvider["custom"]>> = {}) {
  return {
    protocol: "openai" as const,
    base_url: "http://gateway.example.test:20128/v1",
    api_key_set: true,
    api_key_hint: "abcd",
    public_host: false,
    ...over,
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const EMPTY_USAGE = { since: "2026-10-01T00:00:00Z", rows: [], total_calls: 0 };

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AiSettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("AiSettingsPage", () => {
  it("hiện ô tên model cho từng tác vụ, không còn ba cấp và thẻ che thông tin", async () => {
    const puts: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const path = new URL(request.url).pathname;
        if (path === "/api/v1/ai/provider") return json(200, provider(null));
        if (path === "/api/v1/ai/usage") return json(200, EMPTY_USAGE);
        if (request.method === "PUT") {
          const body = (await request.json()) as { models: { answer: string } };
          puts.push(body);
          return json(200, settings(false, body.models.answer));
        }
        return json(200, settings(false));
      }),
    );
    renderPage();
    expect(await screen.findByText(/Chưa cấu hình AI/)).toBeInTheDocument();
    expect(await screen.findByText("Tháng này chưa gọi AI lần nào.")).toBeInTheDocument();
    expect(screen.queryByText(/Che thông tin/)).toBeNull();
    expect(screen.queryByLabelText("Giao thức")).toBeNull();
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.queryByText(/Ước tính/)).toBeNull();
    expect(screen.queryByText(/USD|\$/)).toBeNull();

    const model = screen.getByLabelText("Tên model");
    expect(model).toHaveValue("");
    const save = screen.getByRole("button", { name: "Lưu" });
    expect(save).toBeDisabled();
    const user = userEvent.setup();
    await user.type(model, "internal/qwen-72b");
    await user.click(save);
    expect(await screen.findByText("Đã lưu.")).toBeInTheDocument();
    expect(puts).toEqual([{ models: { answer: "internal/qwen-72b" } }]);
  });

  it("lưu nhà cung cấp, không hiện lại API key", async () => {
    const calls: { method: string; path: string; body: unknown }[] = [];
    let saved: AiProvider["custom"] = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const path = new URL(request.url).pathname;
        const body = request.method === "GET" ? null : await request.json().catch(() => null);
        calls.push({ method: request.method, path, body });
        if (path === "/api/v1/ai/usage") return json(200, EMPTY_USAGE);
        if (path === "/api/v1/ai/provider" && request.method === "PUT") {
          saved = custom({ base_url: "http://10.0.0.5:20128/v1" });
          return json(200, provider(saved));
        }
        if (path === "/api/v1/ai/provider") return json(200, provider(saved));
        return json(200, settings(saved !== null, "internal/qwen-72b"));
      }),
    );
    renderPage();
    const user = userEvent.setup();
    const url = await screen.findByLabelText("URL gốc");
    await user.type(url, "http://10.0.0.5:20128/v1");
    await user.type(screen.getByLabelText("API key"), "sk-internal-secret-abcd");
    await user.click(screen.getByRole("button", { name: "Lưu nhà cung cấp" }));
    expect(await screen.findByText("Đã lưu nhà cung cấp.")).toBeInTheDocument();
    const put = calls.find((c) => c.method === "PUT" && c.path === "/api/v1/ai/provider");
    expect(put?.body).toEqual({
      protocol: "openai",
      base_url: "http://10.0.0.5:20128/v1",
      api_key: "sk-internal-secret-abcd",
    });
    // Ô key được xóa và API không trả key về: không còn chỗ nào trên trang chứa key.
    expect(screen.getByLabelText("API key")).toHaveValue("");
    expect(document.body.innerHTML).not.toContain("sk-internal-secret-abcd");
    expect(screen.getByLabelText("API key")).toHaveAttribute(
      "placeholder",
      expect.stringContaining("abcd"),
    );
    expect(await screen.findByLabelText("Tên model")).toHaveValue("internal/qwen-72b");
  });

  describe("cảnh báo gửi mail không che", () => {
    function stubProvider(publicHost: boolean) {
      vi.stubGlobal(
        "fetch",
        vi.fn(async (request: Request) => {
          const path = new URL(request.url).pathname;
          if (path === "/api/v1/ai/usage") return json(200, EMPTY_USAGE);
          if (path === "/api/v1/ai/provider") {
            return json(200, provider(custom({ public_host: publicHost })));
          }
          return json(200, settings(true));
        }),
      );
    }

    it("luôn báo mail không che sẽ gửi tới host", async () => {
      stubProvider(false);
      renderPage();
      const note = await screen.findByText(/sẽ gửi tới/);
      expect(note).toHaveTextContent("Mail không che sẽ gửi tới gateway.example.test:20128.");
      expect(screen.getByText("không che").tagName).toBe("STRONG");
      expect(screen.queryByText(/NDA|đã che/)).toBeNull();
    });

    it("cảnh báo khi public_host là true", async () => {
      stubProvider(true);
      renderPage();
      expect(
        await screen.findByText("Đây là địa chỉ công cộng, mail sẽ ra ngoài mạng nội bộ."),
      ).toBeInTheDocument();
    });

    it("không cảnh báo công cộng khi public_host là false", async () => {
      stubProvider(false);
      renderPage();
      await screen.findByText(/sẽ gửi tới/);
      expect(screen.queryByText(/địa chỉ công cộng/)).toBeNull();
    });

    it("đổi URL thì bỏ cảnh báo công cộng của địa chỉ đã lưu", async () => {
      stubProvider(true);
      renderPage();
      await screen.findByText(/địa chỉ công cộng/);
      const url = screen.getByLabelText("URL gốc");
      await userEvent.setup().clear(url);
      await userEvent.setup().type(url, "http://10.0.0.5/v1");
      expect(screen.queryByText(/địa chỉ công cộng/)).toBeNull();
      expect(screen.getByText(/sẽ gửi tới/)).toHaveTextContent("10.0.0.5");
    });

    it("đổi URL thì yêu cầu nhập lại API key", async () => {
      stubProvider(false);
      renderPage();
      await screen.findByText(/sẽ gửi tới/);
      const key = screen.getByLabelText("API key");
      expect(key).toHaveAttribute("placeholder", expect.stringMatching(/để trống để giữ nguyên/));
      expect(screen.queryByText(/cần nhập lại API key/)).toBeNull();
      const url = screen.getByLabelText("URL gốc");
      await userEvent.setup().clear(url);
      await userEvent.setup().type(url, "http://10.0.0.5/v1");
      expect(key).toHaveAttribute("placeholder", "Dán API key");
      expect(screen.getByText(/Đổi URL thì cần nhập lại API key/)).toBeInTheDocument();
    });
  });
});
