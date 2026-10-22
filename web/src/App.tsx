import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router";

import { ApiError, errorMessage, setUnauthorizedHandler } from "@/api/client";
import { keys, useMe } from "@/api/hooks";
import { Layout } from "@/components/Layout";
import { RethreadProvider } from "@/components/rethread";
import { AiSettingsPage } from "@/pages/AiSettingsPage";
import { AskPage } from "@/pages/AskPage";
import { LoginPage } from "@/pages/LoginPage";
import { OverridesPage } from "@/pages/OverridesPage";
import { SearchPage } from "@/pages/SearchPage";
import { ThreadListPage } from "@/pages/ThreadListPage";
import { ThreadPage } from "@/pages/ThreadPage";

/** Trang cần đăng nhập: chưa có phiên → /login?next=<trang đang mở>. */
function RequireAuth() {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) return <p className="p-4 text-sm text-muted-foreground">Đang tải…</p>;
  if (me.error) {
    if (me.error instanceof ApiError && me.error.status === 401) {
      const next = encodeURIComponent(location.pathname + location.search + location.hash);
      return <Navigate to={`/login?next=${next}`} replace />;
    }
    return <p className="p-4 text-sm text-destructive">{errorMessage(me.error)}</p>;
  }
  return (
    <RethreadProvider>
      <Layout me={me.data} />
    </RethreadProvider>
  );
}

export function App() {
  const qc = useQueryClient();

  useEffect(() => {
    // Phiên hết hạn giữa chừng: bỏ cache (có nội dung mail) và hỏi lại phiên → về /login.
    setUnauthorizedHandler(() => {
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== keys.me[0] });
      void qc.resetQueries({ queryKey: keys.me });
    });
    return () => setUnauthorizedHandler(null);
  }, [qc]);

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route path="/" element={<Navigate to="/threads" replace />} />
        <Route path="/threads" element={<ThreadListPage />} />
        <Route path="/threads/:id" element={<ThreadPage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/overrides" element={<OverridesPage />} />
        <Route path="/ask" element={<AskPage />} />
        <Route path="/settings/ai" element={<AiSettingsPage />} />
        <Route path="*" element={<p className="text-sm">Không có trang này.</p>} />
      </Route>
    </Routes>
  );
}
