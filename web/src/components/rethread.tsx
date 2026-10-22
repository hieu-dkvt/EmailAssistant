// Chờ worker áp dụng sửa tay (tách/gộp/hoàn tác) rồi mở thread mới của mail (spec 16 mục 5).
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api, ApiError, unwrap } from "@/api/client";
import { fetchMessageRef } from "@/api/hooks";

import { RethreadContext } from "./rethread-context";

const POLL_MS = 2000;
const SLOW_AFTER_MS = 30_000;
const REFRESHED = ["threads", "thread", "search", "overrides", "processing"];

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function RethreadProvider({ children }: { children: React.ReactNode }) {
  const [waiting, setWaiting] = useState(false);
  const [slow, setSlow] = useState(false);
  const run = useRef(0);
  const qc = useQueryClient();
  const navigate = useNavigate();

  // Rời trang (đăng xuất, đóng): dừng vòng chờ.
  useEffect(() => () => void run.current++, []);

  const start = useCallback(
    (messageId: number | null) => {
      const id = ++run.current;
      const startedAt = Date.now();
      setWaiting(true);
      setSlow(false);
      void (async () => {
        // Hỏi trạng thái mới (không dùng cache): sửa tay vừa gửi đã nằm trong DB.
        while (run.current === id) {
          try {
            const status = await unwrap(api.GET("/api/v1/processing"));
            if (!status.rethread_pending) break;
          } catch (e) {
            if (e instanceof ApiError && (e.status === 401 || e.status === 403)) return;
          }
          if (Date.now() - startedAt > SLOW_AFTER_MS) setSlow(true);
          await sleep(POLL_MS);
        }
        if (run.current !== id) return;
        let navigated = false;
        if (messageId !== null) {
          try {
            const ref = await fetchMessageRef(messageId);
            if (ref.thread_id !== null) {
              navigate(`/threads/${ref.thread_id}`, { replace: true });
              navigated = true;
            }
          } catch {
            // Mail đã bị xóa khỏi server trong lúc chờ: ở nguyên trang.
          }
        }
        // Đã chuyển trang: chỉ đánh dấu cache cũ (trang mới tự tải lại khi mở). Không tải lại
        // ngay thread đang hiện: nó có thể đã bị xóa khi gộp (404).
        await qc.invalidateQueries({
          predicate: (q) => REFRESHED.includes(String(q.queryKey[0])),
          refetchType: navigated ? "none" : "active",
        });
        if (run.current === id) {
          setWaiting(false);
          setSlow(false);
        }
      })();
    },
    [qc, navigate],
  );

  const value = useMemo(() => ({ waiting, slow, start }), [waiting, slow, start]);
  return <RethreadContext.Provider value={value}>{children}</RethreadContext.Provider>;
}
