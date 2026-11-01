import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Schemas } from "@/api/client";

import { GlossaryPage } from "./GlossaryPage";

type Item = Schemas["GlossaryItem"];

function item(id: number, term: string, translation: string, project_id: number | null = null) {
  return {
    id,
    project_id,
    term,
    translation,
    note: null,
    created_at: "2026-10-31T00:00:00Z",
    updated_at: "2026-10-31T00:00:00Z",
  } satisfies Item;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Call {
  method: string;
  path: string;
  query: URLSearchParams;
  body: Record<string, unknown> | null;
}

const PROJECT = {
  id: 1,
  name: "Dự án Sample A",
  filter_from: [],
  filter_to: [],
  filter_cc: [],
  filter_subject: [],
  github_mail: [],
  backlog_mail: [],
  customer_domains: [],
  processed_source_id: null,
  ai_since: null,
  created_at: "2026-10-31T00:00:00Z",
  updated_at: "2026-10-31T00:00:00Z",
};

/** Stub fetch: ghi lại mọi request glossary (kể cả GET); `reply` chen vào trước. */
function stub(
  items: Item[],
  opts: { total?: number; reply?: (call: Call) => Response | null } = {},
) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const method = request.method;
      if (url.pathname === "/api/v1/projects") {
        return json(200, { items: [PROJECT], assigning: false });
      }
      const body =
        method === "GET" || method === "DELETE"
          ? null
          : ((await request.json()) as Record<string, unknown>);
      const call = { method, path: url.pathname, query: url.searchParams, body };
      calls.push(call);
      const custom = opts.reply?.(call);
      if (custom) return custom;
      if (url.pathname === "/api/v1/glossary/export") {
        return new Response("term,translation,note\r\n", {
          headers: { "Content-Type": "text/csv; charset=utf-8" },
        });
      }
      if (method === "GET") return json(200, { items, total: opts.total ?? items.length });
      if (url.pathname.endsWith("/import")) {
        return json(200, { added: 2, duplicate: 1, invalid: 0, first_error: null });
      }
      if (method === "DELETE") return new Response(null, { status: 204 });
      const id = Number(url.pathname.split("/").pop());
      return json(method === "POST" ? 201 : 200, {
        ...item(Number.isNaN(id) ? 99 : id, String(body?.term), String(body?.translation)),
        note: body?.note ?? null,
      });
    }),
  );
  return calls;
}

function renderPage(url = "/settings/glossary") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[url]}>
        <GlossaryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const lists = (calls: Call[]) =>
  calls.filter((c) => c.method === "GET" && c.path === "/api/v1/glossary");

afterEach(() => vi.unstubAllGlobals());

describe("GlossaryPage", () => {
  it("hiện bảng glossary chung và chọn được phạm vi dự án", async () => {
    const calls = stub([item(1, "API", "API")]);
    renderPage();
    expect(await screen.findByLabelText("Thuật ngữ: API")).toHaveValue("API");
    expect(lists(calls)[0].query.has("project_id")).toBe(false);
    await userEvent.selectOptions(screen.getByLabelText("Phạm vi"), "Dự án Sample A");
    await waitFor(() => expect(lists(calls).at(-1)?.query.get("project_id")).toBe("1"));
  });

  it("tìm kiếm", async () => {
    const calls = stub([item(1, "API", "API")]);
    renderPage();
    await userEvent.type(await screen.findByLabelText("Tìm thuật ngữ"), "テスト");
    await waitFor(() => expect(lists(calls).at(-1)?.query.get("q")).toBe("テスト"));
    expect(lists(calls).at(-1)?.query.get("offset")).toBe("0");
  });

  it("sửa trực tiếp một ô", async () => {
    const calls = stub([item(7, "結合テスト", "kiểm thử tích hợp", 1)]);
    renderPage("/settings/glossary?project=1");
    const cell = await screen.findByLabelText("Bản dịch: 結合テスト");
    await userEvent.clear(cell);
    await userEvent.type(cell, "test tích hợp");
    await userEvent.tab();
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.path).toBe("/api/v1/glossary/7");
    expect(patch?.body).toEqual({
      project_id: 1,
      term: "結合テスト",
      translation: "test tích hợp",
      note: null,
    });
  });

  it("lưu xong hàng không bị dựng lại: tab sang ô kế, gõ tiếp vẫn giữ tiêu điểm và chữ", async () => {
    const rows = [item(7, "API", "API")];
    const calls = stub(rows, {
      reply: (c) => {
        if (c.method !== "PATCH") return null;
        rows[0] = {
          ...rows[0],
          term: String(c.body?.term),
          translation: String(c.body?.translation),
          updated_at: "2026-11-01T00:00:00Z",
        };
        return json(200, rows[0]);
      },
    });
    renderPage();
    const term = await screen.findByLabelText("Thuật ngữ: API");
    await userEvent.type(term, "2");
    await userEvent.tab();
    const translation = screen.getByRole("textbox", { name: /^Bản dịch: / });
    expect(translation).toHaveFocus();
    await userEvent.type(translation, "xyz");
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    // Chờ lần tải lại sau khi lưu.
    await waitFor(() => expect(screen.getByLabelText("Thuật ngữ: API2")).toHaveValue("API2"));
    const after = screen.getByRole("textbox", { name: /^Bản dịch: / });
    expect(after).toBe(translation);
    expect(after).toHaveFocus();
    expect(after).toHaveValue("APIxyz");
  });

  it("không đổi gì thì rời ô không gọi PATCH", async () => {
    const calls = stub([item(7, "API", "API")]);
    renderPage();
    await userEvent.click(await screen.findByLabelText("Bản dịch: API"));
    await userEvent.tab();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("trùng thuật ngữ hiện thông báo", async () => {
    stub([item(7, "API", "API")], {
      reply: (c) =>
        c.method === "PATCH" || c.method === "POST"
          ? json(409, { detail: "Đã có thuật ngữ này", code: "glossary_duplicate" })
          : null,
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText("Thuật ngữ mới"), "Bug");
    await userEvent.type(screen.getByLabelText("Bản dịch mới"), "Lỗi");
    await userEvent.click(screen.getByRole("button", { name: "Thêm" }));
    expect(await screen.findByText("Đã có thuật ngữ này")).toBeInTheDocument();
  });

  it("thêm thuật ngữ gửi POST kèm phạm vi", async () => {
    const calls = stub([]);
    renderPage("/settings/glossary?project=1");
    await userEvent.type(await screen.findByLabelText("Thuật ngữ mới"), "Bug");
    await userEvent.type(screen.getByLabelText("Bản dịch mới"), "Lỗi");
    await userEvent.type(screen.getByLabelText("Ghi chú mới"), "trong bối cảnh test");
    await userEvent.click(screen.getByRole("button", { name: "Thêm" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      project_id: 1,
      term: "Bug",
      translation: "Lỗi",
      note: "trong bối cảnh test",
    });
  });

  it("xóa gửi DELETE", async () => {
    const calls = stub([item(7, "API", "API")]);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Xóa API" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(calls.find((c) => c.method === "DELETE")?.path).toBe("/api/v1/glossary/7");
  });

  it("nhập: xem trước rồi mới ghi", async () => {
    const calls = stub([]);
    renderPage("/settings/glossary?project=1");
    await userEvent.click(await screen.findByRole("button", { name: "Nhập" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.selectOptions(within(dialog).getByLabelText("Định dạng"), "CSV");
    await userEvent.type(within(dialog).getByLabelText("Dữ liệu nhập"), "Bug,Lỗi,");
    const write = within(dialog).getByRole("button", { name: "Ghi" });
    expect(write).toBeDisabled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Xem trước" }));
    expect(await within(dialog).findByText("Thêm mới 2, trùng 1, lỗi 0")).toBeInTheDocument();
    const imports = () => calls.filter((c) => c.path.endsWith("/import"));
    expect(imports()).toHaveLength(1);
    expect(imports()[0].body).toMatchObject({ project_id: 1, format: "csv", apply: false });
    await userEvent.click(write);
    await waitFor(() => expect(imports()).toHaveLength(2));
    expect(imports()[1].body).toMatchObject({ apply: true, on_conflict: "skip" });
  });

  it("sửa dữ liệu sau khi xem trước thì phải xem trước lại", async () => {
    stub([]);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Nhập" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Dữ liệu nhập"), "a1 = b");
    await userEvent.click(within(dialog).getByRole("button", { name: "Xem trước" }));
    await within(dialog).findByText("Thêm mới 2, trùng 1, lỗi 0");
    expect(within(dialog).getByRole("button", { name: "Ghi" })).toBeEnabled();
    await userEvent.type(within(dialog).getByLabelText("Dữ liệu nhập"), "x");
    expect(within(dialog).getByRole("button", { name: "Ghi" })).toBeDisabled();
  });

  it("hiện dòng lỗi đầu tiên của bản xem trước", async () => {
    stub([], {
      reply: (c) =>
        c.path.endsWith("/import")
          ? json(200, { added: 0, duplicate: 0, invalid: 1, first_error: "Dòng 3: term" })
          : null,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Nhập" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Dữ liệu nhập"), "x");
    await userEvent.click(within(dialog).getByRole("button", { name: "Xem trước" }));
    expect(await within(dialog).findByText("Dòng 3: term")).toBeInTheDocument();
  });

  it("dán nhiều dòng: dạng term = translation", async () => {
    const calls = stub([]);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Nhập" }));
    const dialog = await screen.findByRole("dialog");
    const area = within(dialog).getByLabelText("Dữ liệu nhập");
    await userEvent.click(area);
    await userEvent.paste("Bug = Lỗi\nAPI = API");
    await userEvent.selectOptions(within(dialog).getByLabelText("Khi trùng"), "Ghi đè");
    await userEvent.click(within(dialog).getByRole("button", { name: "Xem trước" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/import"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/import"))?.body).toEqual({
      project_id: null,
      text: "Bug = Lỗi\nAPI = API",
      format: "lines",
      on_conflict: "overwrite",
      apply: false,
    });
  });

  it("lỗi nhập quá lớn hiện thông báo", async () => {
    stub([], {
      reply: (c) =>
        c.path.endsWith("/import")
          ? json(413, { detail: "Dữ liệu nhập quá lớn (tối đa 1 MiB)", code: "x" })
          : null,
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Nhập" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Dữ liệu nhập"), "a");
    await userEvent.click(within(dialog).getByRole("button", { name: "Xem trước" }));
    expect(await within(dialog).findByText(/Dữ liệu nhập quá lớn/)).toBeInTheDocument();
  });

  it("xuất CSV tải file qua GET có phiên", async () => {
    const calls = stub([item(1, "API", "API")]);
    const create = vi.fn(() => "blob:fake");
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    try {
      renderPage("/settings/glossary?project=1");
      await userEvent.click(await screen.findByRole("button", { name: "Xuất CSV" }));
      await waitFor(() => expect(click).toHaveBeenCalled());
      const exp = calls.find((c) => c.path === "/api/v1/glossary/export");
      expect(exp?.method).toBe("GET");
      expect(exp?.query.get("project_id")).toBe("1");
      expect(create).toHaveBeenCalled();
      expect(revoke).toHaveBeenCalledWith("blob:fake");
    } finally {
      click.mockRestore();
      Reflect.deleteProperty(URL, "createObjectURL");
      Reflect.deleteProperty(URL, "revokeObjectURL");
    }
  });

  it("phân trang", async () => {
    const calls = stub([item(1, "API", "API")], { total: 120 });
    renderPage();
    expect(await screen.findByText("1–50 / 120")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Trang trước" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Trang sau" }));
    await waitFor(() => expect(lists(calls).at(-1)?.query.get("offset")).toBe("50"));
    expect(await screen.findByText("51–100 / 120")).toBeInTheDocument();
  });
});
