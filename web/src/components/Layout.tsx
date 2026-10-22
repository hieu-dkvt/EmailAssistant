import { Loader2, LogOut, Search } from "lucide-react";
import { type FormEvent } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate, useSearchParams } from "react-router";

import type { Me } from "@/api/client";
import { useLogout, useProcessing } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useRethread } from "@/components/rethread-context";
import { readFilters, writeFilters } from "@/lib/filters";
import { formatCount } from "@/lib/format";
import { cn } from "cn";

const UNPROCESSED_CAP = 10_001;

function navClass({ isActive }: { isActive: boolean }) {
  return cn(
    "rounded-md px-3 py-1.5 text-sm",
    isActive ? "bg-secondary font-medium" : "text-muted-foreground hover:text-foreground",
  );
}

function ProcessingStatus() {
  const rethread = useRethread();
  const status = useProcessing(rethread.waiting);
  const n = status.data?.unprocessed_messages ?? 0;
  if (rethread.waiting) return null; // hiện ở dải thông báo bên dưới
  if (n === 0) return null;
  return (
    <span className="flex items-center gap-1 text-xs text-muted-foreground" role="status">
      <Loader2 className="size-3 animate-spin" />
      Đang xử lý {n >= UNPROCESSED_CAP ? "hơn 10.000" : formatCount(n)} mail mới
    </span>
  );
}

export function Layout({ me }: { me: Me }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const logout = useLogout();
  const rethread = useRethread();
  const onSearchPage = location.pathname === "/search";

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const q = String(new FormData(event.currentTarget).get("q") ?? "").trim();
    // Giữ bộ lọc đang chọn khi tìm từ danh sách thread hoặc trang kết quả.
    const keep = location.pathname === "/threads" || onSearchPage;
    const next = writeFilters(new URLSearchParams(), keep ? readFilters(params) : {});
    if (q) next.set("q", q);
    // Đang ở chế độ ngữ nghĩa thì tìm tiếp bằng ngữ nghĩa.
    if (onSearchPage && params.get("mode") === "hybrid") next.set("mode", "hybrid");
    navigate(`/search?${next.toString()}`);
  }

  async function doLogout() {
    await logout.mutateAsync().catch(() => undefined);
    navigate("/login", { replace: true });
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4">
          <Link to="/threads" className="shrink-0 font-semibold">
            PM Mail Assistant
          </Link>
          <form onSubmit={submitSearch} className="relative max-w-md flex-1" role="search">
            <Search className="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
            <Input
              key={onSearchPage ? (params.get("q") ?? "") : "other"}
              name="q"
              autoComplete="off"
              defaultValue={onSearchPage ? (params.get("q") ?? "") : ""}
              placeholder="Tìm mail (Nhật, Việt, Anh)…"
              aria-label="Tìm mail"
              className="pl-8"
            />
          </form>
          <nav className="flex items-center gap-1">
            <NavLink to="/threads" className={navClass}>
              Thread
            </NavLink>
            <NavLink to="/overrides" className={navClass}>
              Sửa tay
            </NavLink>
            <NavLink to="/ask" className={navClass}>
              Hỏi AI
            </NavLink>
            <NavLink to="/settings/ai" className={navClass}>
              Cài đặt AI
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <ProcessingStatus />
            <span className="hidden text-sm text-muted-foreground sm:inline" title={me.email}>
              {me.display_name}
            </span>
            <Button variant="ghost" size="sm" onClick={doLogout} disabled={logout.isPending}>
              <LogOut />
              Đăng xuất
            </Button>
          </div>
        </div>
        {rethread.waiting && (
          <div
            className="border-t bg-secondary px-4 py-2 text-center text-sm"
            role="status"
            aria-live="polite"
          >
            <Loader2 className="mr-2 inline size-4 animate-spin" />
            Đang gom lại thread…
            {rethread.slow && (
              <span className="text-muted-foreground">
                {" "}
                Vẫn đang chờ: worker có đang chạy không (<code>python -m app.worker</code>)?
              </span>
            )}
          </div>
        )}
      </header>
      <main className="mx-auto max-w-6xl px-4 py-4">
        <Outlet />
      </main>
    </div>
  );
}
