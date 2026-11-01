// Client gọi API, kiểu lấy từ schema.ts (sinh từ OpenAPI của backend: npm run gen:api).
// Không viết tay kiểu dữ liệu hay đường dẫn API.
import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type Segment = Schemas["Segment"];
export type Me = Schemas["Me"];
export type FolderInfo = Schemas["FolderInfo"];
export type FolderRef = Schemas["FolderRef"];
export type ThreadSummary = Schemas["ThreadSummary"];
export type ThreadDetail = Schemas["ThreadDetail"];
export type ThreadMessage = Schemas["ThreadMessage"];
export type SearchHit = Schemas["SearchHit"];
export type SearchResult = Schemas["SearchResult"];
export type OverrideInfo = Schemas["OverrideInfo"];
export type MessageBrief = Schemas["MessageBrief"];
export type TextPart = Schemas["TextPart"];
export type LoginBody = NonNullable<
  paths["/api/v1/session"]["post"]["requestBody"]
>["content"]["application/json"];
export type OverrideBody = NonNullable<
  paths["/api/v1/thread-overrides"]["post"]["requestBody"]
>["content"]["application/json"];
export type SimilarThread = Schemas["SimilarThread"];
export type AskResult = Schemas["AskResult"];
export type AskSource = Schemas["AskSource"];
export type AiSettings = Schemas["AiSettings"];
export type AiUsage = Schemas["AiUsage"];
export type AiProvider = Schemas["AiProvider"];
export type AiProviderTest = Schemas["AiProviderTest"];
export type AiProviderBody = NonNullable<
  paths["/api/v1/ai/provider"]["put"]["requestBody"]
>["content"]["application/json"];
export type Project = Schemas["Project"];
export type ProjectList = Schemas["ProjectList"];
export type ProjectRef = Schemas["ProjectRef"];
export type ProjectBody = NonNullable<
  paths["/api/v1/projects"]["post"]["requestBody"]
>["content"]["application/json"];
export type ThreadTranslationItem = Schemas["ThreadTranslationItem"];
export type MessageTranslation = Schemas["MessageTranslationOut"];
export type GlossaryItem = Schemas["GlossaryItem"];
export type GlossaryBody = NonNullable<
  paths["/api/v1/glossary"]["post"]["requestBody"]
>["content"]["application/json"];
export type GlossaryImportBody = NonNullable<
  paths["/api/v1/glossary/import"]["post"]["requestBody"]
>["content"]["application/json"];
export type Tier = Schemas["AiTierInfo"]["tier"];
export type AskBody = NonNullable<
  paths["/api/v1/ask"]["post"]["requestBody"]
>["content"]["application/json"];
export type AiSettingsBody = NonNullable<
  paths["/api/v1/ai/settings"]["put"]["requestBody"]
>["content"]["application/json"];

/** Request ghi phải có header này (chống CSRF, spec 13 mục 5). */
export const CSRF_HEADER = "X-PMM-Request";

/** Lỗi từ API: mã HTTP, mã lỗi riêng của backend (nếu có) và thông điệp cho người dùng. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  /** Lỗi 422 theo từng trường: tên trường → thông điệp (đã bỏ tiền tố "Value error, "). */
  readonly fields: Record<string, string>;

  constructor(
    status: number,
    message: string,
    code: string | null = null,
    fields: Record<string, string> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

let unauthorizedHandler: (() => void) | null = null;

/** Gọi khi API trả 401 lúc đang dùng (phiên hết hạn, bị thu hồi). */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export const middleware: Middleware = {
  onRequest({ request }) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      request.headers.set(CSRF_HEADER, "1");
    }
    return request;
  },
  onResponse({ request, response }) {
    // /api/v1/session tự xử lý 401 (đăng nhập sai, kiểm tra phiên).
    if (response.status === 401 && !new URL(request.url).pathname.startsWith("/api/v1/session")) {
      unauthorizedHandler?.();
    }
    return response;
  },
};

export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "",
  credentials: "same-origin",
  // Lấy fetch lúc gọi (không giữ bản lúc tạo client): test thay được fetch.
  fetch: (request: Request) => globalThis.fetch(request),
});
api.use(middleware);

interface Result<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** Trả data khi thành công, ngược lại ném ApiError. */
export async function unwrap<T>(promise: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await promise;
  if (response.ok) {
    return data as T;
  }
  throw toApiError(response.status, error);
}

export function toApiError(status: number, error: unknown): ApiError {
  const body = (typeof error === "object" && error !== null ? error : {}) as {
    detail?: unknown;
    code?: unknown;
  };
  let message: string;
  if (typeof body.detail === "string") {
    message = body.detail;
  } else if (status === 422) {
    message = "Dữ liệu gửi lên không hợp lệ";
  } else {
    message = `Lỗi máy chủ (${status})`;
  }
  return new ApiError(
    status,
    message,
    typeof body.code === "string" ? body.code : null,
    fieldErrors(body.detail),
  );
}

/** `detail` dạng mảng của lỗi 422: khóa là tên trường đầu tiên trong `loc` (bỏ "body"). */
function fieldErrors(detail: unknown): Record<string, string> {
  const fields: Record<string, string> = {};
  if (!Array.isArray(detail)) return fields;
  for (const item of detail) {
    if (typeof item !== "object" || item === null) continue;
    const { loc, msg } = item as { loc?: unknown; msg?: unknown };
    if (!Array.isArray(loc) || typeof msg !== "string") continue;
    const path = loc[0] === "body" ? loc.slice(1) : loc;
    const field = path[0];
    if (typeof field !== "string" || field in fields) continue;
    fields[field] = msg.replace(/^Value error, /, "");
  }
  return fields;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  return "Không kết nối được máy chủ";
}
