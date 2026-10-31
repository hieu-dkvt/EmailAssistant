import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProjectRef, ThreadSummary } from "@/api/client";

import { ThreadListPage } from "./ThreadListPage";

function thread(projects: ProjectRef[]): ThreadSummary {
  return {
    id: 1,
    subject: "Báo cáo tuần",
    message_count: 3,
    has_attachments: false,
    first_message_at: "2026-10-29T01:00:00Z",
    last_message_at: "2026-10-30T01:00:00Z",
    last_from_name: "Sender",
    last_from_addr: "sender@sample.example",
    projects,
  };
}

function renderList(projects: ProjectRef[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const body =
        path === "/api/v1/folders" ? [] : { items: [thread(projects)], next_cursor: null };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ThreadListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const badges = () => document.querySelectorAll('[data-slot="badge"][data-variant="outline"]');

afterEach(() => vi.unstubAllGlobals());

describe("ThreadListPage: nhãn dự án", () => {
  it("2 tên hiện 2 Badge", async () => {
    renderList([
      { id: 1, name: "Dự án A" },
      { id: 2, name: "Dự án B" },
    ]);
    expect(await screen.findByText("Dự án A")).toBeInTheDocument();
    expect(screen.getByText("Dự án B")).toBeInTheDocument();
    expect(badges()).toHaveLength(2);
    expect(screen.queryByText(/^\+\d/)).not.toBeInTheDocument();
  });

  it("3 tên hiện 2 Badge và +1, title đủ tên", async () => {
    renderList([
      { id: 1, name: "Dự án A" },
      { id: 2, name: "Dự án B" },
      { id: 3, name: "Dự án C" },
    ]);
    expect(await screen.findByText("+1")).toBeInTheDocument();
    expect(badges()).toHaveLength(2);
    expect(screen.queryByText("Dự án C")).not.toBeInTheDocument();
    expect(screen.getByTitle("Dự án A, Dự án B, Dự án C")).toBeInTheDocument();
  });

  it("không có dự án thì không có Badge nhãn", async () => {
    renderList([]);
    expect(await screen.findByText("Báo cáo tuần")).toBeInTheDocument();
    expect(badges()).toHaveLength(0);
  });
});
