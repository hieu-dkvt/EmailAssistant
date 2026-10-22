import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, CSRF_HEADER, setUnauthorizedHandler, toApiError, unwrap } from "./client";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
});

describe("client API", () => {
  it("request ghi có header chống CSRF, request đọc thì không", async () => {
    const fetchMock = vi.fn(async (request: Request) => {
      void request;
      return new Response(null, { status: 204 });
    });
    vi.stubGlobal("fetch", fetchMock);
    await api.GET("/api/v1/processing");
    await api.DELETE("/api/v1/thread-overrides/{override_id}", {
      params: { path: { override_id: 5 } },
    });
    const [get, del] = fetchMock.mock.calls.map(([r]) => r);
    expect(get.headers.get(CSRF_HEADER)).toBeNull();
    expect(del.method).toBe("DELETE");
    expect(del.headers.get(CSRF_HEADER)).toBe("1");
    expect(new URL(del.url).pathname).toBe("/api/v1/thread-overrides/5");
  });

  it("401 lúc đang dùng gọi handler, trừ API phiên", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(401, { detail: "Chưa đăng nhập", code: "not_authenticated" })),
    );
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    await expect(unwrap(api.GET("/api/v1/session"))).rejects.toMatchObject({ status: 401 });
    expect(handler).not.toHaveBeenCalled();
    const err = await unwrap(api.GET("/api/v1/folders")).catch((e: unknown) => e);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({
      status: 401,
      code: "not_authenticated",
      message: "Chưa đăng nhập",
    });
  });

  it("thông điệp lỗi", () => {
    expect(toApiError(400, { detail: "Cú pháp sai", code: "x" })).toMatchObject({
      message: "Cú pháp sai",
      code: "x",
    });
    expect(toApiError(422, { detail: [{ loc: ["q"] }] }).message).toBe(
      "Dữ liệu gửi lên không hợp lệ",
    );
    expect(toApiError(500, undefined)).toMatchObject({ message: "Lỗi máy chủ (500)", code: null });
  });
});
