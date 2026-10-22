import { describe, expect, it } from "vitest";

import type { SearchHit } from "@/api/client";

import { folderName, formatShortDate, formatSize, senderLabel } from "./format";
import { threadLink } from "./links";

describe("định dạng", () => {
  it("tên folder lấy phần cuối đường dẫn", () => {
    expect(folderName("ImapMail/imap.example.com/INBOX.sbd/ProjectA")).toBe("ProjectA");
    expect(folderName("Mail/Local Folders/Inbox")).toBe("Inbox");
    expect(folderName("Mail\\Local Folders\\Archive.sbd")).toBe("Archive");
  });

  it("kích thước, người gửi, ngày", () => {
    expect(formatSize(500)).toBe("500 B");
    expect(formatSize(12345)).toBe("12 KB");
    expect(formatSize(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(senderLabel("田中 健二", "t@a.example.jp")).toBe("田中 健二");
    expect(senderLabel(" ", "t@a.example.jp")).toBe("t@a.example.jp");
    expect(senderLabel(null, null)).toBe("(không rõ người gửi)");
    expect(formatShortDate(null)).toBe("—");
    const now = new Date(2026, 9, 18, 15, 0);
    expect(formatShortDate(new Date(2026, 9, 18, 9, 5).toISOString(), now)).toContain("09:05");
  });
});

describe("threadLink", () => {
  const hit = { message_id: 7, thread_id: 3 } as SearchHit;
  it("mở thread, cuộn tới mail, giữ từ khóa", () => {
    expect(threadLink(hit, "見積 OR 請求")).toBe(
      "/threads/3?q=%E8%A6%8B%E7%A9%8D+OR+%E8%AB%8B%E6%B1%82#m-7",
    );
    expect(threadLink(hit, "")).toBe("/threads/3#m-7");
    expect(threadLink({ ...hit, thread_id: null }, "x")).toBeNull();
  });
});
