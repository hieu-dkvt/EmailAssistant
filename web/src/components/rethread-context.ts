import { createContext, useContext } from "react";

export interface RethreadState {
  waiting: boolean;
  slow: boolean; // chờ lâu: có thể worker chưa chạy
  /** Bắt đầu chờ worker áp dụng sửa tay; xong thì mở thread mới của mail (null: ở nguyên trang). */
  start: (messageId: number | null) => void;
}

export const RethreadContext = createContext<RethreadState>({
  waiting: false,
  slow: false,
  start: () => {},
});

export function useRethread(): RethreadState {
  return useContext(RethreadContext);
}
