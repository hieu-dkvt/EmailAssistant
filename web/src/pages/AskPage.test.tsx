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
    { task: "question_analysis", content: "Today: 2026-10-20\nQuestion: [PERSON_1]さんの見積" },
    { task: "answer", content: "<question>[PERSON_1]さんの見積</question>" },
  ],
  mask_counts: { PERSON: 1, MONEY: 2 },
  calls: [
    {
      task: "question_analysis",
      model: "claude-haiku-5-5",
      status: "ok",
      input_tokens: 120,
      output_tokens: 30,
      duration_ms: 1500,
    },
  ],
};

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
  it("gửi câu hỏi kèm header chống CSRF và hiện câu trả lời, nguồn, nội dung đã gửi", async () => {
    const posts: Request[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        if (request.method === "POST") {
          posts.push(request.clone());
          return json(200, RESULT);
        }
        return json(200, []); // /folders
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
    expect(screen.getByText(/Đã che: 1 tên người, 2 số tiền/)).toBeInTheDocument();
    expect(screen.getByText(/Question: \[PERSON_1\]さんの見積/)).toBeInTheDocument();
  });

  it("hiện lỗi của backend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) =>
        request.method === "POST"
          ? json(503, { detail: "Chưa cấu hình Claude", code: "ai_not_configured" })
          : json(200, []),
      ),
    );
    renderAt(<AskPage />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Câu hỏi"), "x");
    await user.click(screen.getByRole("button", { name: "Hỏi" }));
    expect(await screen.findByText("Chưa cấu hình Claude")).toBeInTheDocument();
  });
});
