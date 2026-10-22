import { describe, expect, it } from "vitest";

import {
  hasFilters,
  localMidnight,
  readFilters,
  safeNext,
  toApiFilters,
  writeFilters,
} from "./filters";

describe("bộ lọc trên URL", () => {
  it("đọc và ghi lại đúng, bỏ giá trị hỏng", () => {
    const params = new URLSearchParams(
      "q=見積&folder=3&sender=%20tanaka%20&from=2026-09-01&to=2026-09-30&att=1&page=2",
    );
    const f = readFilters(params);
    expect(f).toEqual({
      folder: "3",
      sender: "tanaka",
      from: "2026-09-01",
      to: "2026-09-30",
      att: "1",
    });
    const back = writeFilters(new URLSearchParams("q=見積"), f);
    expect(back.get("q")).toBe("見積");
    expect(readFilters(back)).toEqual(f);
    expect(readFilters(new URLSearchParams("folder=abc&from=1/9/2026&att=yes"))).toEqual({});
    expect(writeFilters(params, {}).toString()).toBe("q=%E8%A6%8B%E7%A9%8D&page=2");
    expect(hasFilters({})).toBe(false);
    expect(hasFilters({ att: "0" })).toBe(true);
  });

  it("đổi ngày theo giờ của trình duyệt, 'đến ngày' tính cả ngày đó", () => {
    const api = toApiFilters({ folder: "3", from: "2026-09-01", to: "2026-09-30", att: "0" });
    expect(api).toEqual({
      folder_id: 3,
      sent_after: new Date(2026, 8, 1).toISOString(),
      sent_before: new Date(2026, 9, 1).toISOString(),
      has_attachments: false,
    });
    expect(localMidnight("2026-12-31", 1)?.getFullYear()).toBe(2027);
    expect(localMidnight("31/12/2026")).toBeNull();
  });
});

describe("safeNext", () => {
  it("chỉ nhận đường dẫn nội bộ", () => {
    expect(safeNext("/threads/5?q=x#m-1")).toBe("/threads/5?q=x#m-1");
    expect(safeNext("/threads/5?q=%E8%A6%8B")).toBe("/threads/5?q=%E8%A6%8B");
    for (const bad of [
      null,
      "",
      "https://evil.example.com",
      "//evil.example.com",
      "/\\evil.example.com",
      "/\t/evil.example.com",
      "/\n/evil.example.com",
      "/.//evil.example.com",
      "x",
    ]) {
      expect(safeNext(bad)).toBe("/threads");
    }
  });
});
