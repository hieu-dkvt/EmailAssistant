import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ThreadMessage, ThreadTranslationItem } from "@/api/client";

import { useThread } from "@/api/hooks";

import { MessageCard } from "./MessageCard";
import { MessageTranslation } from "./MessageTranslation";

const MESSAGE: ThreadMessage = {
  id: 5,
  thread_parent_id: null,
  thread_method: "root",
  sent_at: "2026-10-31T01:00:00Z",
  from_name: "Sample Sender",
  from_addr: "sender@sample-a.example.jp",
  to: [],
  cc: [],
  subject: [{ text: "件名", hit: false }],
  body: [{ text: "原文の本文です", hit: false }],
  cleaned: true,
  cut_count: 0,
  body_truncated: false,
  has_attachments: false,
  attachments: [],
  folders: [],
};

const PROJECTS = [
  { id: 1, name: "Dự án A" },
  { id: 2, name: "Dự án B" },
];

function translation(over: Partial<NonNullable<ThreadTranslationItem["translation"]>> = {}) {
  return {
    message_id: 5,
    translated_subject: "Tiêu đề dịch",
    translated_body: "Nội dung dịch",
    notes: [],
    warnings: [],
    model: "m",
    stale: false,
    created_at: "2026-10-31T01:00:00Z",
    updated_at: "2026-10-31T01:00:00Z",
    ...over,
  };
}

function item(over: Partial<ThreadTranslationItem> = {}): ThreadTranslationItem {
  return {
    message_id: 5,
    translation: null,
    auto: null,
    projects: PROJECTS,
    move_projects: PROJECTS,
    ...over,
  };
}

function auto(over: Partial<NonNullable<ThreadTranslationItem["auto"]>> = {}) {
  return {
    status: "done" as const,
    error_code: null,
    move_note: null,
    target_folder: null,
    ...over,
  };
}

interface Call {
  method: string;
  path: string;
  body: Record<string, unknown> | null;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stub(reply: (call: Call) => Response | null = () => null) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const body =
        request.method === "GET" ? null : ((await request.json().catch(() => null)) as never);
      const call = { method: request.method, path, body };
      if (request.method !== "GET") calls.push(call);
      const custom = reply(call);
      if (custom) return custom;
      if (path === "/api/v1/folders") return json(200, []);
      if (request.method === "GET") return json(200, { items: [] });
      if (path.endsWith("/auto-retry")) return new Response(null, { status: 204 });
      return json(200, { message_id: 5, folder_id: null, move_note: null });
    }),
  );
  return calls;
}

function wrap(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function show(it: ThreadTranslationItem | undefined) {
  return wrap(
    <MessageTranslation message={MESSAGE} item={it} original={<pre>原文の本文です</pre>} />,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.getSelection()?.removeAllRanges();
});

describe("MessageTranslation", () => {
  it("hiện hai cột khi có bản dịch", () => {
    stub();
    const { container } = show(item({ translation: translation() }));
    expect(container.querySelector(".md\\:grid-cols-2")).not.toBeNull();
    expect(screen.getByText("Bản gốc")).toBeInTheDocument();
    expect(screen.getByText("Bản dịch")).toBeInTheDocument();
    expect(screen.getByText("原文の本文です")).toBeInTheDocument();
    expect(screen.getByText("Tiêu đề dịch")).toBeInTheDocument();
    expect(screen.getByText("Nội dung dịch")).toBeInTheDocument();
  });

  it("nội dung HTML hiện nguyên dạng chữ", () => {
    stub();
    const { container } = show(
      item({ translation: translation({ translated_body: "<b>x</b> <script>1</script>" }) }),
    );
    expect(container.querySelector("pre b")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("<b>x</b> <script>1</script>")).toBeInTheDocument();
  });

  it("ghi chú sắc thái", () => {
    stub();
    show(item({ translation: translation({ notes: ["Khách dùng kính ngữ", "Từ mơ hồ"] }) }));
    expect(screen.getByText("Ghi chú sắc thái")).toBeInTheDocument();
    expect(screen.getByText("Khách dùng kính ngữ")).toBeInTheDocument();
    expect(screen.getByText("Từ mơ hồ")).toBeInTheDocument();
  });

  it("từng cảnh báo có câu tiếng Việt", () => {
    stub();
    const codes = [
      "labels_missing",
      "labels_unknown",
      "glossary_missing",
      "glossary_conflict",
      "truncated",
    ];
    show(item({ translation: translation({ warnings: [...codes, "mã_lạ"] }) }));
    const strip = screen.getByTestId("translation-warnings");
    const items = within(strip).getAllByRole("listitem");
    expect(items).toHaveLength(6);
    for (const li of items) {
      expect(li.textContent).not.toMatch(/labels_|glossary_|truncated|mã_lạ/);
      expect((li.textContent ?? "").length).toBeGreaterThan(10);
    }
  });

  it("bản dịch cũ có labels_missing vẫn hiện cảnh báo", () => {
    stub();
    show(item({ translation: translation({ warnings: ["labels_missing", "labels_unknown"] }) }));
    const items = within(screen.getByTestId("translation-warnings")).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent(/thiếu một số nhãn/);
    expect(items[1]).toHaveTextContent(/nhãn che không có trong bản gốc/);
  });

  it("thông báo ml_unavailable không nói về che thông tin", async () => {
    stub((call) =>
      call.path.endsWith("/translate") ? json(503, { detail: "x", code: "ml_unavailable" }) : null,
    );
    show(item());
    await userEvent.setup().click(screen.getByRole("button", { name: "Dịch" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/ml-service/);
    expect(alert.textContent).not.toMatch(/che/);
  });

  it("hết giờ chờ model có thông báo riêng", async () => {
    stub((call) =>
      call.path.endsWith("/translate")
        ? json(503, { detail: "chi tiết kỹ thuật", code: "ai_timeout" })
        : null,
    );
    show(item());
    await userEvent.setup().click(screen.getByRole("button", { name: "Dịch" }));
    expect(await screen.findByText(/Model trả lời quá lâu/)).toBeInTheDocument();
    expect(screen.queryByText("chi tiết kỹ thuật")).toBeNull();
  });

  it("bản dịch cũ có nút Dịch lại", async () => {
    const calls = stub();
    show(item({ translation: translation({ stale: true }) }));
    expect(screen.getByText("Bản dịch cũ")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Dịch lại" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].path).toBe("/api/v1/messages/5/translate");
    expect(calls[0].body).toEqual({ force: true });
  });

  it("bản dịch mới không có nút Dịch lại", () => {
    stub();
    show(item({ translation: translation() }));
    expect(screen.queryByRole("button", { name: "Dịch lại" })).toBeNull();
    expect(screen.queryByText("Bản dịch cũ")).toBeNull();
  });

  it("mail chưa có bản dịch có nút Dịch", async () => {
    const calls = stub();
    show(item());
    await userEvent.setup().click(screen.getByRole("button", { name: "Dịch" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ force: false });
  });

  it("chưa có dữ liệu bản dịch của thread vẫn có nút Dịch", () => {
    stub();
    show(undefined);
    expect(screen.getByRole("button", { name: "Dịch" })).toBeInTheDocument();
  });

  it("đang dịch khóa nút", async () => {
    let release: (r: Response) => void = () => {};
    stub((call) =>
      call.path.endsWith("/translate")
        ? (new Promise<Response>((r) => (release = r)) as unknown as Response)
        : null,
    );
    show(item());
    await userEvent.setup().click(screen.getByRole("button", { name: "Dịch" }));
    expect(await screen.findByRole("button", { name: "Đang dịch…" })).toBeDisabled();
    release(json(200, translation()));
    await waitFor(() => expect(screen.getByRole("button", { name: "Dịch" })).toBeEnabled());
  });

  it("lỗi hiện thông báo theo mã", async () => {
    stub((call) =>
      call.path.endsWith("/translate")
        ? json(503, { detail: "chi tiết kỹ thuật", code: "ai_not_configured" })
        : null,
    );
    show(item());
    await userEvent.setup().click(screen.getByRole("button", { name: "Dịch" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/Cài đặt AI/);
    expect(alert.textContent).not.toMatch(/chi tiết kỹ thuật/);
  });

  it("nothing_to_translate trên mail đã xử lý: không phải lỗi, không có nút Dịch", () => {
    stub();
    show(item({ auto: auto({ error_code: "nothing_to_translate" }) }));
    expect(screen.getByText(/Mail không có nội dung để dịch/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dịch" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("đã chuyển hiện folder đích và Chuyển lại Inbox", async () => {
    const calls = stub();
    show(
      item({
        translation: translation(),
        auto: auto({ target_folder: "ImapMail/imap.example.com/INBOX.sbd/SampleA" }),
      }),
    );
    expect(screen.getByText(/Đã chuyển tới SampleA/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Chuyển lại Inbox" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].path).toBe("/api/v1/messages/5/move-back");
  });

  it("ambiguous cho chọn dự án", async () => {
    const calls = stub();
    show(item({ translation: translation(), auto: auto({ move_note: "ambiguous" }) }));
    const user = userEvent.setup();
    expect(screen.getByRole("button", { name: "Chuyển" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Dự án"), "2");
    await user.click(screen.getByRole("button", { name: "Chuyển" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].path).toBe("/api/v1/messages/5/move");
    expect(calls[0].body).toEqual({ project_id: 2 });
  });

  it("no_folder báo dự án chưa có folder", () => {
    stub();
    show(
      item({
        translation: translation(),
        auto: auto({ move_note: "no_folder" }),
        move_projects: [],
      }),
    );
    expect(screen.getByText(/chưa có folder đích/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Chuyển" })).toBeNull();
  });

  it("no_folder mà dự án đã có folder thì cho chuyển, không báo thiếu folder", async () => {
    const calls = stub();
    show(
      item({
        translation: translation(),
        auto: auto({ move_note: "no_folder" }),
        move_projects: [PROJECTS[0]],
      }),
    );
    expect(screen.queryByText(/chưa có folder đích/)).toBeNull();
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Dự án"), "1");
    await user.click(screen.getByRole("button", { name: "Chuyển" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].path).toBe("/api/v1/messages/5/move");
    expect(calls[0].body).toEqual({ project_id: 1 });
  });

  it("sau Chuyển lại Inbox vẫn chọn lại được dự án", () => {
    stub();
    show(item({ translation: translation(), auto: auto() }));
    expect(screen.getByLabelText("Dự án")).toBeInTheDocument();
  });

  it("không chọn dự án khi mail đã có đích, chưa xong, hoặc không dự án nào có folder", () => {
    stub();
    const cases = [
      item({ auto: auto({ target_folder: "Mail/Local Folders/SampleA" }) }),
      item({ auto: auto({ status: "retry" }) }),
      item({ auto: auto({ status: "failed" }) }),
      item({ auto: null }),
      item({ auto: auto(), move_projects: [] }),
    ];
    for (const it of cases) {
      const { unmount } = show(it);
      expect(screen.queryByLabelText("Dự án")).toBeNull();
      unmount();
    }
  });

  it("Chuyển lại Inbox dùng nút viền", () => {
    stub();
    show(item({ auto: auto({ target_folder: "Mail/Local Folders/SampleA" }) }));
    expect(screen.getByRole("button", { name: "Chuyển lại Inbox" })).toHaveAttribute(
      "data-variant",
      "outline",
    );
  });

  it("failed hiện mã lỗi và Thử lại", async () => {
    const calls = stub();
    show(item({ auto: auto({ status: "failed", error_code: "ai_bad_output" }) }));
    expect(screen.getByText(/ai_bad_output/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Thử lại" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].path).toBe("/api/v1/messages/5/auto-retry");
  });

  it("retry hiện đang chờ thử lại, không có nút Thử lại", () => {
    stub();
    show(item({ auto: auto({ status: "retry", error_code: "ai_bad_request" }) }));
    expect(screen.getByText(/Đang chờ dịch lại tự động/)).toBeInTheDocument();
    expect(screen.getByText(/ai_bad_request/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Thử lại" })).toBeNull();
  });
});

function ThreadProbe() {
  useThread(1, "");
  return null;
}

describe("MessageTranslation sau sửa review", () => {
  it("chuyển folder tải lại chi tiết thread (badge folder hiệu lực)", async () => {
    let threadGets = 0;
    stub();
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", (request: Request) => {
      if (new URL(request.url).pathname === "/api/v1/threads/1") {
        threadGets += 1;
        return Promise.resolve(json(200, {}));
      }
      return base(request);
    });
    wrap(
      <>
        <ThreadProbe />
        <MessageTranslation
          message={MESSAGE}
          item={item({
            translation: translation(),
            auto: auto({ target_folder: "Mail/Local Folders/SampleA" }),
          })}
          original={<pre>x</pre>}
        />
      </>,
    );
    await waitFor(() => expect(threadGets).toBe(1));
    await userEvent.setup().click(screen.getByRole("button", { name: "Chuyển lại Inbox" }));
    await waitFor(() => expect(threadGets).toBe(2));
  });

  it("lỗi chuyển hiện thông báo", async () => {
    stub((call) =>
      call.path.endsWith("/move") ? json(422, { detail: "kỹ thuật", code: "move_invalid" }) : null,
    );
    show(item({ auto: auto({ move_note: "ambiguous" }) }));
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Dự án"), "1");
    await user.click(screen.getByRole("button", { name: "Chuyển" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/chưa có folder đích/);
  });

  it("translate_in_progress hiện câu tiếng Việt", async () => {
    stub((call) =>
      call.path.endsWith("/translate")
        ? json(409, { detail: "busy", code: "translate_in_progress" })
        : null,
    );
    show(item());
    await userEvent.setup().click(screen.getByRole("button", { name: "Dịch" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/Đang dịch một mail khác/);
    expect(alert.textContent).not.toMatch(/busy/);
  });

  it("failed vẫn hiện Thử lại khi đã có bản dịch tay", () => {
    stub();
    show(
      item({
        translation: translation(),
        auto: auto({ status: "failed", error_code: "ai_bad_output" }),
      }),
    );
    expect(screen.getByText(/ai_bad_output/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Thử lại" })).toBeInTheDocument();
  });

  it("ambiguous chỉ liệt kê dự án của mail có folder", () => {
    stub();
    show(item({ auto: auto({ move_note: "ambiguous" }), move_projects: [PROJECTS[1]] }));
    const options = within(screen.getByLabelText("Dự án")).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["Chọn dự án…", "Dự án B"]);
  });
});

describe("MessageCard", () => {
  it("bỏ vùng chọn thì nút Thêm vào glossary biến mất", async () => {
    stub();
    wrap(<MessageCard message={MESSAGE} q="" translation={item()} />);
    const text = screen.getByText("原文の本文です");
    const range = document.createRange();
    range.selectNodeContents(text);
    window.getSelection()?.addRange(range);
    fireEvent.mouseUp(text);
    expect(await screen.findByRole("button", { name: "Thêm vào glossary" })).toBeInTheDocument();
    window.getSelection()?.removeAllRanges();
    fireEvent(document, new Event("selectionchange"));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Thêm vào glossary" })).toBeNull(),
    );
  });

  it("bôi đen trong thẻ mở hộp thoại thêm glossary với đoạn đã chọn", async () => {
    const calls = stub();
    wrap(<MessageCard message={MESSAGE} q="" translation={item()} />);
    const user = userEvent.setup();
    expect(screen.queryByRole("button", { name: "Thêm vào glossary" })).toBeNull();

    const text = screen.getByText("原文の本文です");
    const range = document.createRange();
    range.selectNodeContents(text);
    window.getSelection()?.addRange(range);
    fireEvent.mouseUp(text);

    await user.click(await screen.findByRole("button", { name: "Thêm vào glossary" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Thuật ngữ")).toHaveValue("原文の本文です");
    await user.type(within(dialog).getByLabelText("Bản dịch"), "nội dung chính");
    await user.selectOptions(within(dialog).getByLabelText("Phạm vi"), "1");
    await user.click(within(dialog).getByRole("button", { name: "Lưu" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/glossary")).toBe(true));
    const post = calls.find((c) => c.path === "/api/v1/glossary");
    expect(post?.body).toMatchObject({
      project_id: 1,
      term: "原文の本文です",
      translation: "nội dung chính",
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByRole("button", { name: "Thêm vào glossary" })).toBeNull();
  });
});
