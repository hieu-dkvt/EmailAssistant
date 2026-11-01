import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FolderInfo, Project, ProjectList } from "@/api/client";

import { ProjectsPage } from "./ProjectsPage";

function project(over: Partial<Project> & Pick<Project, "id" | "name">): Project {
  return {
    filter_from: [],
    filter_to: [],
    filter_cc: [],
    filter_subject: [],
    docs_repo_url: null,
    docs_local_path: null,
    github_mail: [],
    review_repo_url: null,
    review_local_path: null,
    backlog_mail: [],
    backlog_space_url: null,
    backlog_project_key: null,
    redmine_url: null,
    redmine_project: null,
    customer_domains: [],
    processed_source_id: null,
    ai_since: null,
    created_at: "2026-10-31T00:00:00Z",
    updated_at: "2026-10-31T00:00:00Z",
    ...over,
  };
}

function folder(id: number, folder_path: string, account: string): FolderInfo {
  return { id, folder_path, account, kind: "imap", message_count: 5, last_scan_at: null };
}

const FOLDERS = [
  folder(10, "ImapMail/imap.example.com/INBOX", "imap.example.com"),
  folder(11, "ImapMail/imap.example.com/INBOX.sbd/SampleA", "imap.example.com"),
  folder(12, "Mail/Local Folders/Inbox", "Local Folders"),
  folder(13, "Mail/Local Folders/Archive", "Local Folders"),
];

const A = project({
  id: 1,
  name: "Cổng khách hàng Sample A",
  filter_from: ["@sample-a.example.jp"],
  filter_to: ["@sample-a.example.jp"],
  filter_cc: ["@sample-a.example.jp"],
  filter_subject: ["[SAMPLEA-", "【サンプルA】"],
  github_mail: ["notifications@github.example"],
});
const B = project({ id: 2, name: "Bảo trì Sample B" });

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Call {
  method: string;
  path: string;
  body: Record<string, unknown> | null;
}

/** Stub fetch: danh sách dự án giữ trong bộ nhớ, ghi lại các lệnh ghi. */
function stub(
  items: Project[],
  opts: {
    assigning?: boolean;
    folders?: FolderInfo[];
    reply?: (call: Call) => Response | null;
  } = {},
) {
  const calls: Call[] = [];
  let list = [...items];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const method = request.method;
      const body =
        method === "GET" || method === "DELETE"
          ? null
          : ((await request.json()) as Record<string, unknown>);
      const call = { method, path, body };
      if (method !== "GET") calls.push(call);
      if (path === "/api/v1/folders") return json(200, opts.folders ?? FOLDERS);
      // Độ trễ như mạng thật: stub tức thì che lỗi callback bị bỏ khi form đã gỡ (xóa dự án).
      await new Promise((resolve) => setTimeout(resolve, 20));
      const custom = opts.reply?.(call);
      if (custom) return custom;
      if (method === "GET") {
        return json(200, { items: list, assigning: opts.assigning ?? false } satisfies ProjectList);
      }
      if (method === "POST") {
        const p = project({ id: 9, ...(body as object), name: String(body?.name) });
        list = [...list, p];
        return json(201, p);
      }
      const id = Number(path.split("/").pop());
      if (method === "PUT") {
        const p = project({ id, ...(body as object), name: String(body?.name) });
        list = list.map((x) => (x.id === id ? p : x));
        return json(200, p);
      }
      list = list.filter((x) => x.id !== id);
      return new Response(null, { status: 204 });
    }),
  );
  return calls;
}

function Where() {
  const loc = useLocation();
  return <output data-testid="where">{loc.pathname + loc.search}</output>;
}

function renderPage(url = "/settings/projects") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[url]}>
        <ProjectsPage />
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("ProjectsPage", () => {
  it("trống: hiện Chưa có dự án nào và nút Thêm dự án", async () => {
    stub([]);
    renderPage();
    expect(await screen.findByText("Chưa có dự án nào")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Thêm dự án" })).toBeInTheDocument();
  });

  it("danh sách hiện 5 điều kiện lọc và Chưa có bộ lọc", async () => {
    stub([A, B]);
    renderPage();
    const list = await screen.findByRole("navigation", { name: "Danh sách dự án" });
    expect(within(list).getByText("5 điều kiện lọc")).toBeInTheDocument();
    expect(within(list).getByText("Chưa có bộ lọc")).toBeInTheDocument();
    expect(screen.getByText("Danh sách này chỉ áp dụng cho tài khoản của bạn.")).toBeVisible();
  });

  it("chọn dự án đưa id lên URL và hiện đúng dữ liệu", async () => {
    stub([A, B]);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Cổng khách hàng Sample A/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("/settings/projects?project=1");
    expect(screen.getByLabelText("Tên dự án")).toHaveValue("Cổng khách hàng Sample A");
    expect(screen.getByRole("button", { name: "Bỏ [SAMPLEA-" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Bỏ notifications@github.example" })).toBeVisible();
  });

  it("thêm dự án gửi POST với đủ các trường", async () => {
    const calls = stub([B]);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Thêm dự án" }));
    expect(screen.getByTestId("where")).toHaveTextContent("?project=new");
    await userEvent.type(screen.getByLabelText("Tên dự án"), "Dự án mới");
    await userEvent.type(screen.getByLabelText("From"), "@new.example.jp{Enter}");
    await userEvent.type(screen.getByLabelText("URL Redmine"), "https://redmine.example.jp");
    await userEvent.click(screen.getByRole("button", { name: "Lưu dự án" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].method).toBe("POST");
    expect(calls[0].body).toEqual({
      name: "Dự án mới",
      filter_from: ["@new.example.jp"],
      filter_to: [],
      filter_cc: [],
      filter_subject: [],
      docs_repo_url: null,
      docs_local_path: null,
      github_mail: [],
      review_repo_url: null,
      review_local_path: null,
      backlog_mail: [],
      backlog_space_url: null,
      backlog_project_key: null,
      redmine_url: "https://redmine.example.jp",
      redmine_project: null,
      customer_domains: [],
      processed_source_id: null,
    });
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("?project=9"));
  });

  it("gõ dở rồi bấm Lưu dự án: giá trị vẫn nằm trong body PUT", async () => {
    const calls = stub([A]);
    renderPage("/settings/projects?project=1");
    await userEvent.type(await screen.findByLabelText("To"), "@late.example.jp");
    await userEvent.click(screen.getByRole("button", { name: "Lưu dự án" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].method).toBe("PUT");
    expect(calls[0].path).toBe("/api/v1/projects/1");
    expect(calls[0].body?.filter_to).toEqual(["@sample-a.example.jp", "@late.example.jp"]);
  });

  it("gõ dở ở một ô rồi Enter ở ô tên: vẫn lưu phần đang gõ", async () => {
    const calls = stub([A]);
    renderPage("/settings/projects?project=1");
    await userEvent.type(await screen.findByLabelText("CC"), "@cc.example.jp");
    await userEvent.type(screen.getByLabelText("Tên dự án"), " 2{Enter}");
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body?.filter_cc).toEqual(["@sample-a.example.jp", "@cc.example.jp"]);
  });

  it("nút Lưu dự án tắt khi chưa đổi gì", async () => {
    stub([A]);
    renderPage("/settings/projects?project=1");
    const save = await screen.findByRole("button", { name: "Lưu dự án" });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Tên dự án"), "x");
    expect(save).toBeEnabled();
    await userEvent.type(screen.getByLabelText("Tên dự án"), "{Backspace}");
    expect(save).toBeDisabled();
  });

  it("project_name_taken hiện dưới ô tên", async () => {
    stub([A, B], {
      reply: (c) =>
        c.method === "PUT"
          ? json(409, { detail: "Đã có dự án trùng tên", code: "project_name_taken" })
          : null,
    });
    renderPage("/settings/projects?project=1");
    await userEvent.type(await screen.findByLabelText("Tên dự án"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Lưu dự án" }));
    const name = screen.getByLabelText("Tên dự án");
    await waitFor(() => expect(name).toBeInvalid());
    expect(screen.getByText("Đã có dự án trùng tên")).toBeInTheDocument();
  });

  it("lỗi 422 hiện theo từng ô", async () => {
    stub([A], {
      reply: (c) =>
        c.method === "PUT"
          ? json(422, {
              detail: [
                {
                  loc: ["filter_from", 0],
                  msg: "Value error, Giá trị quá dài",
                  type: "value_error",
                },
                { loc: ["body", "redmine_url"], msg: "Value error, URL không hợp lệ", type: "x" },
              ],
            })
          : null,
    });
    renderPage("/settings/projects?project=1");
    await userEvent.type(await screen.findByLabelText("Tên dự án"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Lưu dự án" }));
    expect(await screen.findByText("Giá trị quá dài")).toBeInTheDocument();
    expect(screen.getByText("URL không hợp lệ")).toBeInTheDocument();
    expect(screen.getByLabelText("URL Redmine")).toBeInvalid();
    expect(screen.getByLabelText("From")).toBeInvalid();
  });

  it("cảnh báo Dự án chưa có bộ lọc nên chưa nhận mail nào", async () => {
    stub([B]);
    renderPage("/settings/projects?project=2");
    expect(
      await screen.findByText("Dự án chưa có bộ lọc nên chưa nhận mail nào"),
    ).toBeInTheDocument();
  });

  it("không cảnh báo khi đã có bộ lọc", async () => {
    stub([A]);
    renderPage("/settings/projects?project=1");
    await screen.findByLabelText("Tên dự án");
    expect(screen.queryByText(/chưa có bộ lọc nên/)).not.toBeInTheDocument();
  });

  it("assigning hiện Đang gán lại mail theo bộ lọc…", async () => {
    stub([A], { assigning: true });
    renderPage();
    expect(await screen.findByText("Đang gán lại mail theo bộ lọc…")).toBeInTheDocument();
    expect(screen.queryByText(/python -m app.worker/)).not.toBeInTheDocument();
  });

  it("assigning xong: danh sách thread được đánh dấu cũ", async () => {
    const spy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    try {
      let gets = 0;
      stub([A], {
        reply: (call) =>
          call.method === "GET" ? json(200, { items: [A], assigning: ++gets === 1 }) : null,
      });
      renderPage();
      await screen.findByText("Đang gán lại mail theo bộ lọc…");
      await waitFor(
        () => expect(spy).toHaveBeenCalledWith(expect.objectContaining({ queryKey: ["threads"] })),
        { timeout: 4000 },
      );
      expect(screen.queryByText("Đang gán lại mail theo bộ lọc…")).not.toBeInTheDocument();
    } finally {
      spy.mockRestore();
    }
  });

  it("nhắc worker khi assigning quá 30 giây", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      stub([A], { assigning: true });
      renderPage();
      await screen.findByText("Đang gán lại mail theo bộ lọc…");
      await vi.advanceTimersByTimeAsync(31_000);
      expect(await screen.findByText(/python -m app.worker/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("xóa phải xác nhận, hủy thì không gọi DELETE", async () => {
    const calls = stub([A, B]);
    renderPage("/settings/projects?project=1");
    await userEvent.click(await screen.findByRole("button", { name: "Xóa dự án" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(
        "Xóa dự án Cổng khách hàng Sample A? Mail không bị xóa, chỉ bỏ liên kết với dự án. Glossary của dự án cũng bị xóa theo.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Hủy" }));
    expect(calls).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Xóa dự án" }));
    await userEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Xóa" }),
    );
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({ method: "DELETE", path: "/api/v1/projects/1" });
    await waitFor(() => expect(screen.getByTestId("where")).not.toHaveTextContent("project="));
  });

  it("đổi dự án khi chưa lưu thì hỏi trước", async () => {
    stub([A, B]);
    renderPage("/settings/projects?project=1");
    await userEvent.type(await screen.findByLabelText("Tên dự án"), "x");
    await userEvent.click(screen.getByRole("button", { name: /Bảo trì Sample B/ }));
    const dialog = await screen.findByRole("dialog");
    expect(screen.getByTestId("where")).toHaveTextContent("project=1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Ở lại" }));
    expect(screen.getByLabelText("Tên dự án")).toHaveValue("Cổng khách hàng Sample Ax");
    await userEvent.click(screen.getByRole("button", { name: /Bảo trì Sample B/ }));
    await userEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Bỏ thay đổi" }),
    );
    expect(screen.getByTestId("where")).toHaveTextContent("project=2");
    expect(screen.getByLabelText("Tên dự án")).toHaveValue("Bảo trì Sample B");
  });

  it("refetch nền lỗi: form đang sửa không bị gỡ", async () => {
    let gets = 0;
    stub([A, B], {
      reply: (call) => (call.method === "GET" && ++gets > 1 ? json(500, { detail: "x" }) : null),
    });
    renderPage("/settings/projects?project=1");
    const name = await screen.findByLabelText("Tên dự án");
    await userEvent.type(name, "x");
    window.dispatchEvent(new Event("visibilitychange"));
    document.dispatchEvent(new Event("visibilitychange"));
    await waitFor(() => expect(gets).toBeGreaterThan(1));
    await screen.findByRole("alert");
    expect(screen.getByLabelText("Tên dự án")).toHaveValue("Cổng khách hàng Sample Ax");
  });

  it("?project=999 không tồn tại: không vỡ, hiện thông báo chọn dự án", async () => {
    stub([A, B]);
    renderPage("/settings/projects?project=999");
    expect(await screen.findByText(/Chọn một dự án/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Bảo trì Sample B/ })).toBeInTheDocument();
    expect(screen.queryByLabelText("Tên dự án")).not.toBeInTheDocument();
  });

  it("lưu domain đang gõ dở", async () => {
    const calls = stub([A]);
    renderPage("/settings/projects?project=1");
    await userEvent.type(await screen.findByLabelText("Domain mail khách"), "@Sample-A.example.jp");
    await userEvent.click(screen.getByRole("button", { name: "Lưu dự án" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body?.customer_domains).toEqual(["@Sample-A.example.jp"]);
  });

  it("lỗi domain hiện đúng ô", async () => {
    stub([A], {
      reply: (c) =>
        c.method === "PUT"
          ? json(422, {
              detail: [
                { loc: ["body", "customer_domains"], msg: "Value error, Domain sai", type: "x" },
              ],
            })
          : null,
    });
    renderPage("/settings/projects?project=1");
    await userEvent.type(await screen.findByLabelText("Domain mail khách"), "x{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Lưu dự án" }));
    expect(await screen.findByText("Domain sai")).toBeInTheDocument();
    expect(screen.getByLabelText("Domain mail khách")).toBeInvalid();
    expect(screen.getByLabelText("From")).not.toBeInvalid();
  });

  it("chọn folder gửi processed_source_id", async () => {
    const calls = stub([A]);
    renderPage("/settings/projects?project=1");
    const select = await screen.findByRole("combobox", { name: "Folder mail dự án" });
    await within(select).findByRole("option", { name: "SampleA" });
    await userEvent.selectOptions(select, "SampleA");
    await userEvent.click(screen.getByRole("button", { name: "Lưu dự án" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body?.processed_source_id).toBe(11);
  });

  it("chọn lại Không chuyển folder gửi null", async () => {
    const calls = stub([project({ id: 3, name: "C", processed_source_id: 11 })]);
    renderPage("/settings/projects?project=3");
    const select = await screen.findByRole("combobox", { name: "Folder mail dự án" });
    await waitFor(() => expect(select).toHaveValue("11"));
    await userEvent.selectOptions(select, "");
    await userEvent.click(screen.getByRole("button", { name: "Lưu dự án" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body?.processed_source_id).toBeNull();
  });

  it("danh sách folder không có Inbox, nhóm theo tài khoản", async () => {
    stub([A]);
    renderPage("/settings/projects?project=1");
    const select = await screen.findByRole("combobox", { name: "Folder mail dự án" });
    await within(select).findByRole("option", { name: "SampleA" });
    const names = within(select)
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(names).toEqual(["Không chuyển folder", "SampleA", "Archive"]);
    const groups = within(select)
      .getAllByRole("group")
      .map((g) => g.getAttribute("label"));
    expect(groups).toEqual(["imap.example.com", "Local Folders"]);
  });

  it("project_folder_invalid hiện dưới ô folder", async () => {
    stub([A], {
      reply: (c) =>
        c.method === "PUT"
          ? json(422, { detail: "Folder không hợp lệ", code: "project_folder_invalid" })
          : null,
    });
    renderPage("/settings/projects?project=1");
    const select = await screen.findByRole("combobox", { name: "Folder mail dự án" });
    await within(select).findByRole("option", { name: "SampleA" });
    await userEvent.selectOptions(select, "SampleA");
    await userEvent.click(screen.getByRole("button", { name: "Lưu dự án" }));
    expect(await screen.findByText("Folder không hợp lệ")).toBeInTheDocument();
    expect(select).toBeInvalid();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("hiện ngày mốc chỉ đọc", async () => {
    stub([project({ id: 3, name: "C", ai_since: "2026-10-17T12:00:00Z" })]);
    renderPage("/settings/projects?project=3");
    expect(await screen.findByText("Dịch mail từ ngày 17/10/2026")).toBeInTheDocument();
  });

  it("chưa có mốc thì nói rõ khi nào đặt", async () => {
    stub([B]);
    renderPage("/settings/projects?project=2");
    expect(
      await screen.findByText("Mốc sẽ được đặt khi lưu domain đầu tiên (14 ngày trước)"),
    ).toBeInTheDocument();
  });

  it("tab Glossary chỉ hiện với dự án đã lưu", async () => {
    stub([A]);
    renderPage("/settings/projects?project=1");
    expect(await screen.findByRole("tab", { name: "Glossary" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Thêm dự án" }));
    await screen.findByLabelText("Tên dự án");
    expect(screen.queryByRole("tab", { name: "Glossary" })).not.toBeInTheDocument();
  });

  it("hộp thoại xóa nói rõ glossary bị xóa theo", async () => {
    stub([A]);
    renderPage("/settings/projects?project=1");
    await userEvent.click(await screen.findByRole("button", { name: "Xóa dự án" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent(
      /Glossary của dự án cũng bị xóa theo/,
    );
  });
});
