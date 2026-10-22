// Bộ lọc nằm trên URL (dán link mở lại được), không lưu vào storage của trình duyệt.

export interface Filters {
  folder?: string;
  sender?: string;
  from?: string; // YYYY-MM-DD theo giờ của trình duyệt
  to?: string; // YYYY-MM-DD, tính cả ngày này
  att?: "1" | "0";
}

/** Tham số lọc gửi cho API (tên theo OpenAPI của backend). */
export interface ApiFilters {
  folder_id?: number;
  sender?: string;
  sent_after?: string;
  sent_before?: string;
  has_attachments?: boolean;
}

export const FILTER_KEYS = ["folder", "sender", "from", "to", "att"] as const;
const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

export function readFilters(params: URLSearchParams): Filters {
  const f: Filters = {};
  const folder = params.get("folder");
  if (folder && /^\d+$/.test(folder)) f.folder = folder;
  const sender = params.get("sender")?.trim();
  if (sender) f.sender = sender;
  const from = params.get("from");
  if (from && DATE_RE.test(from)) f.from = from;
  const to = params.get("to");
  if (to && DATE_RE.test(to)) f.to = to;
  const att = params.get("att");
  if (att === "1" || att === "0") f.att = att;
  return f;
}

/** Bản sao của params với bộ lọc mới (giữ các tham số khác như q, sort). */
export function writeFilters(params: URLSearchParams, f: Filters): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const key of FILTER_KEYS) {
    const value = f[key];
    if (value) next.set(key, value);
    else next.delete(key);
  }
  return next;
}

export function hasFilters(f: Filters): boolean {
  return FILTER_KEYS.some((k) => Boolean(f[k]));
}

/** Nửa đêm (giờ trình duyệt) của ngày YYYY-MM-DD, cộng thêm `addDays` ngày. */
export function localMidnight(day: string, addDays = 0): Date | null {
  const m = DATE_RE.exec(day);
  if (!m) return null;
  const date = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + addDays);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function toApiFilters(f: Filters): ApiFilters {
  const out: ApiFilters = {};
  if (f.folder) out.folder_id = Number(f.folder);
  if (f.sender) out.sender = f.sender;
  const after = f.from ? localMidnight(f.from) : null;
  if (after) out.sent_after = after.toISOString();
  const before = f.to ? localMidnight(f.to, 1) : null;
  if (before) out.sent_before = before.toISOString();
  if (f.att) out.has_attachments = f.att === "1";
  return out;
}

const SAFE_BASE = "http://safe-next.invalid";

/** Đường dẫn quay lại sau khi đăng nhập: chỉ nhận đường dẫn nội bộ.
 *
 * Phân tích bằng URL (như trình duyệt): trình duyệt bỏ tab/xuống dòng, nên "/\t/evil.example.com"
 * thành "//evil.example.com" — so origin sau khi phân tích chặn được cả các dạng này. */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/")) return "/threads";
  let url: URL;
  try {
    url = new URL(next, SAFE_BASE);
  } catch {
    return "/threads";
  }
  if (url.origin !== SAFE_BASE || url.pathname.startsWith("//")) return "/threads";
  return url.pathname + url.search + url.hash;
}
