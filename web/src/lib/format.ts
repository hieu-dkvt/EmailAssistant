const dateTime = new Intl.DateTimeFormat("vi-VN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});
const dateOnly = new Intl.DateTimeFormat("vi-VN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});
const timeOnly = new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit" });

export function formatDateTime(iso: string | null | undefined): string {
  return iso ? dateTime.format(new Date(iso)) : "(không có ngày)";
}

export function formatDate(iso: string): string {
  return dateOnly.format(new Date(iso));
}

/** Hôm nay → giờ:phút; ngày khác → ngày/tháng/năm. */
export function formatShortDate(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toDateString() === now.toDateString() ? timeOnly.format(d) : dateOnly.format(d);
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** "ImapMail/imap.example.com/INBOX.sbd/ProjectA" → "ProjectA". */
export function folderName(path: string): string {
  const last = path.split(/[\\/]/).filter(Boolean).pop() ?? path;
  return last.replace(/\.sbd$/i, "");
}

export function senderLabel(name: string | null | undefined, addr: string | null | undefined) {
  return name?.trim() || addr || "(không rõ người gửi)";
}

export function formatCount(n: number): string {
  return n.toLocaleString("vi-VN");
}

/** Cùng quy tắc `is_inbox_path` của backend: đoạn cuối (tách theo `/`) là inbox, không phân biệt hoa thường. */
export function isInboxPath(path: string): boolean {
  return (path.split("/").pop() ?? "").toLowerCase() === "inbox";
}
