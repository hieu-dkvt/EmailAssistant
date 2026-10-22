import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CSRF_HEADER } from "@/api/client";

import { LoginPage } from "./LoginPage";

const ME = { id: 1, email: "pm@vn.example.com", display_name: "PM" };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderLogin(next: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/login?next=${encodeURIComponent(next)}`]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/threads/:id" element={<p>TRANG THREAD</p>} />
          <Route path="/threads" element={<p>DANH SÁCH</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("LoginPage", () => {
  it("gửi email, mật khẩu kèm header rồi về trang trước đó", async () => {
    const posts: Request[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        if (request.method === "POST") {
          posts.push(request.clone());
          return json(200, ME);
        }
        return json(401, { detail: "Chưa đăng nhập", code: "not_authenticated" });
      }),
    );
    renderLogin("/threads/5?q=見積");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "pm@vn.example.com");
    await user.type(screen.getByLabelText("Mật khẩu"), "mat-khau-dai-du");
    await user.click(screen.getByRole("button", { name: "Đăng nhập" }));
    expect(await screen.findByText("TRANG THREAD")).toBeInTheDocument();
    expect(posts).toHaveLength(1);
    expect(new URL(posts[0].url).pathname).toBe("/api/v1/session");
    expect(posts[0].headers.get(CSRF_HEADER)).toBe("1");
    expect(await posts[0].json()).toEqual({
      email: "pm@vn.example.com",
      password: "mat-khau-dai-du",
    });
  });

  it("hiện thông điệp lỗi của API, không rời trang", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) =>
        request.method === "POST"
          ? json(401, { detail: "Email hoặc mật khẩu không đúng", code: "invalid_credentials" })
          : json(401, { detail: "Chưa đăng nhập", code: "not_authenticated" }),
      ),
    );
    renderLogin("https://evil.example.com");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Email"), "pm@vn.example.com");
    await user.type(screen.getByLabelText("Mật khẩu"), "sai-mat-khau-roi");
    await user.click(screen.getByRole("button", { name: "Đăng nhập" }));
    expect(await screen.findByText("Email hoặc mật khẩu không đúng")).toBeInTheDocument();
    expect(screen.queryByText("TRANG THREAD")).toBeNull();
  });

  it("đã đăng nhập thì về trang next (chỉ đường dẫn nội bộ)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(200, ME)),
    );
    renderLogin("//evil.example.com");
    expect(await screen.findByText("DANH SÁCH")).toBeInTheDocument();
  });
});
