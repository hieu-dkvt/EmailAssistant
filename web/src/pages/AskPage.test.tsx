import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CSRF_HEADER, type AskResult } from "@/api/client";

import { AnswerText, AskPage } from "./AskPage";

const SOURCES: AskResult["sources"] = [
  {
    n: 1,
    message_id: 11,
    thread_id: 7,
    subject: "お見積りの送付",
    from_name: "田中 健二",
    from_addr: "tanaka@client-a.example.jp",
    sent_at: "2026-09-01T00:00:00Z",
    cited: true,
  },
  {
    n: 2,
    message_id: 12,
    thread_id: null,
    subject: "お知らせ",
    from_name: null,
    from_addr: "info@client-a.example.jp",
    sent_at: null,
    cited: false,
  },
];

const RESULT: AskResult = {
  answer: "田中様が見積を送りました [1]. Xem thêm [2] và [9].",
  insufficient: false,
  sources: SOURCES,
  plan: {
    search_queries: ["見積の金額"],
    keywords: ["見積"],
    sender: null,
    sent_after: null,
    sent_before: null,
    filters_relaxed: false,
  },
  sent: [
    { task: "question_analysis", content: "Today: 2026-10-20\nQuestion: 田中さんの見積" },
    { task: "answer", content: "<question>田中さんの見積</question>" },
  ],
  calls: [
    {
      task: "question_analysis",
      model: "local/qwen3-8b",
      status: "ok",
      input_tokens: 120,
      output_tokens: 30,
      duration_ms: 1500,
    },
  ],
};

const PROJECTS = [
  { id: 7, name: "Dự án A" },
  { id: 8, name: "Dự án B" },
];

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderAt(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/ask"]}>
        <Routes>
          <Route path="/ask" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function getBody(request: Request): Response {
  return new URL(request.url).pathname.endsWith("/projects")
    ? json(200, { items: PROJECTS, assigning: false })
    : json(200, []); // /folders
}

afterEach(() => vi.unstubAllGlobals());

describe("AnswerText", () => {
  it("biến [n] thành link tới thread của nguồn, giữ nguyên số không có nguồn hoặc chưa gom", () => {
    renderAt(<AnswerText text={RESULT.answer} sources={SOURCES} />);
    const link = screen.getByRole("link", { name: "[1]" });
    expect(link).toHaveAttribute("href", "/threads/7#m-11");
    expect(screen.queryByRole("link", { name: "[2]" })).toBeNull();
    expect(screen.getByText(/Xem thêm \[2\] và \[9\]\./)).toBeInTheDocument();
  });
});

describe("AskPage", () => {
  it("không khẳng định model là nội bộ: ô URL ở Cài đặt AI nhận mọi địa chỉ", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => getBody(request)),
    );
    renderAt(<AskPage />);
    expect(screen.getByText(/gửi tới nhà cung cấp AI đã cấu hình/)).toBeInTheDocument();
    expect(screen.getByText(/không được lưu lại/)).toBeInTheDocument();
    expect(screen.queryByText(/nội bộ/)).toBeNull();
  });

  it("gửi câu hỏi kèm header chống CSRF và hiện câu trả lời, nguồn, nội dung đã gửi", async () => {
    const posts: Request[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        if (request.method === "POST") {
          posts.push(request.clone());
          return json(200, RESULT);
        }
        return getBody(request);
      }),
    );
    renderAt(<AskPage />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Câu hỏi"), "Khách gửi báo giá bao nhiêu?");
    await user.click(screen.getByRole("button", { name: "Hỏi" }));
    expect(await screen.findByText("Mail nguồn (2)")).toBeInTheDocument();
    expect(posts).toHaveLength(1);
    expect(new URL(posts[0].url).pathname).toBe("/api/v1/ask");
    expect(posts[0].headers.get(CSRF_HEADER)).toBe("1");
    expect(await posts[0].json()).toEqual({ question: "Khách gửi báo giá bao nhiêu?" });
    expect(screen.getByText("được trích")).toBeInTheDocument();
    expect(screen.getByText(/Question: 田中さんの見積/)).toBeInTheDocument();
  });

  it("AskPage không còn chữ đã che", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) =>
        request.method === "POST" ? json(200, RESULT) : getBody(request),
      ),
    );
    renderAt(<AskPage />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Câu hỏi"), "x");
    await user.click(screen.getByRole("button", { name: "Hỏi" }));
    expect(await screen.findByText("Mail nguồn (2)")).toBeInTheDocument();
    expect(screen.getByText("Nội dung đã gửi cho AI")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/đã che|che thông tin|\bche\b|\[PERSON_1\]/i);
  });

  it("hiện lỗi của backend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) =>
        request.method === "POST"
          ? json(503, { detail: "Chưa cấu hình AI", code: "ai_not_configured" })
          : getBody(request),
      ),
    );
    renderAt(<AskPage />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Câu hỏi"), "x");
    await user.click(screen.getByRole("button", { name: "Hỏi" }));
    expect(await screen.findByText("Chưa cấu hình AI")).toBeInTheDocument();
  });

  it("gửi project_id khi chọn dự án, không gửi khi chọn Tất cả dự án", async () => {
    const posts: Request[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        if (request.method === "POST") {
          posts.push(request.clone());
          return json(200, RESULT);
        }
        return getBody(request);
      }),
    );
    renderAt(<AskPage />);
    const user = userEvent.setup();
    const select = screen.getByLabelText("Dự án");
    expect(screen.getByRole("option", { name: "Tất cả dự án" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Câu hỏi"), "x");
    await user.click(screen.getByRole("button", { name: "Hỏi" }));
    expect(await screen.findByText("Mail nguồn (2)")).toBeInTheDocument();
    expect(await posts[0].json()).toEqual({ question: "x" });

    await screen.findByRole("option", { name: "Dự án A" });
    await user.selectOptions(select, "7");
    await user.click(screen.getByRole("button", { name: "Hỏi" }));
    await vi.waitFor(() => expect(posts).toHaveLength(2));
    expect(await posts[1].json()).toEqual({ question: "x", project_id: 7 });
  });

  it("hiện lỗi project_not_found", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) =>
        request.method === "POST"
          ? json(404, { detail: "Không tìm thấy dự án", code: "project_not_found" })
          : getBody(request),
      ),
    );
    renderAt(<AskPage />);
    const user = userEvent.setup();
    await screen.findByRole("option", { name: "Dự án B" });
    await user.selectOptions(screen.getByLabelText("Dự án"), "8");
    await user.type(screen.getByLabelText("Câu hỏi"), "x");
    await user.click(screen.getByRole("button", { name: "Hỏi" }));
    expect(await screen.findByText("Không tìm thấy dự án")).toBeInTheDocument();
  });
});
