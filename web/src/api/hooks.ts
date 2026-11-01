// Truy vấn API bằng TanStack Query. Cache chỉ nằm trong bộ nhớ (không persist): nội dung mail
// không được ghi xuống storage của trình duyệt.
import { useEffect, useRef } from "react";
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import type { ApiFilters } from "@/lib/filters";

import {
  api,
  toApiError,
  unwrap,
  type AiProviderBody,
  type AiSettingsBody,
  type AskBody,
  type GlossaryBody,
  type GlossaryImportBody,
  type LoginBody,
  type Me,
  type OverrideBody,
  type Project,
  type ProjectBody,
  type ProjectList,
} from "./client";

export const keys = {
  me: ["me"] as const,
  folders: ["folders"] as const,
  threads: (f: ApiFilters) => ["threads", f] as const,
  search: (p: SearchParams) => ["search", p] as const,
  thread: (id: number, q: string) => ["thread", id, q] as const,
  similar: (id: number) => ["similar", id] as const,
  full: (id: number, q: string) => ["full", id, q] as const,
  processing: ["processing"] as const,
  overrides: ["overrides"] as const,
  aiSettings: ["aiSettings"] as const,
  aiUsage: ["aiUsage"] as const,
  aiProvider: ["aiProvider"] as const,
  projects: ["projects"] as const,
  threadTranslations: (id: number) => ["threadTranslations", id] as const,
};

export interface SearchParams extends ApiFilters {
  q?: string;
  sort?: "date" | "relevance";
  mode?: "keyword" | "hybrid";
  offset?: number;
  limit?: number;
}

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: () => unwrap(api.GET("/api/v1/session")),
    retry: false,
    staleTime: 60_000,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LoginBody) => unwrap(api.POST("/api/v1/session", { body })),
    onSuccess: (me: Me) => qc.setQueryData(keys.me, me),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/session")),
    // Xóa toàn bộ cache (có nội dung mail) khỏi bộ nhớ.
    onSettled: () => qc.clear(),
  });
}

export function useFolders() {
  return useQuery({
    queryKey: keys.folders,
    queryFn: () => unwrap(api.GET("/api/v1/folders")),
    staleTime: 5 * 60_000,
  });
}

export function useThreads(filters: ApiFilters) {
  return useInfiniteQuery({
    queryKey: keys.threads(filters),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/threads", {
          params: { query: { ...filters, cursor: pageParam ?? undefined, limit: 50 } },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
}

export function useSearch(params: SearchParams, enabled = true) {
  return useQuery({
    queryKey: keys.search(params),
    queryFn: () => unwrap(api.GET("/api/v1/search", { params: { query: params } })),
    enabled,
    placeholderData: keepPreviousData,
    retry: false,
  });
}

export function useThread(id: number, q: string) {
  return useQuery({
    queryKey: keys.thread(id, q),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/threads/{thread_id}", {
          params: { path: { thread_id: id }, query: q ? { q } : {} },
        }),
      ),
    retry: false,
  });
}

export function useMessageFull(id: number, q: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.full(id, q),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/messages/{message_id}/full", {
          params: { path: { message_id: id }, query: q ? { q } : {} },
        }),
      ),
    enabled,
  });
}

/** Trạng thái xử lý; hỏi mỗi 2 giây khi đang chờ gom lại, mỗi 30 giây lúc bình thường. */
export function useProcessing(fast: boolean) {
  return useQuery({
    queryKey: keys.processing,
    queryFn: () => unwrap(api.GET("/api/v1/processing")),
    refetchInterval: fast ? 2000 : 30_000,
  });
}

export async function fetchMessageRef(id: number) {
  return unwrap(api.GET("/api/v1/messages/{message_id}", { params: { path: { message_id: id } } }));
}

export function useOverrides() {
  return useQuery({
    queryKey: keys.overrides,
    queryFn: () => unwrap(api.GET("/api/v1/thread-overrides")),
  });
}

export function useCreateOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: OverrideBody) => unwrap(api.POST("/api/v1/thread-overrides", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.processing }),
  });
}

/** Thread có nội dung gần giống (embedding), gợi ý gộp (spec 23). */
export function useSimilarThreads(id: number, enabled: boolean) {
  return useQuery({
    queryKey: keys.similar(id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/threads/{thread_id}/similar", { params: { path: { thread_id: id } } }),
      ),
    enabled,
    retry: false,
    staleTime: 60_000,
  });
}

/** Hỏi đáp AI (spec 21). Kết quả chỉ nằm trong state của mutation, không lưu ở đâu khác. */
export function useAsk() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AskBody) => unwrap(api.POST("/api/v1/ask", { body })),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.aiUsage }),
  });
}

export function useAiSettings() {
  return useQuery({
    queryKey: keys.aiSettings,
    queryFn: () => unwrap(api.GET("/api/v1/ai/settings")),
  });
}

export function useSaveAiSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AiSettingsBody) => unwrap(api.PUT("/api/v1/ai/settings", { body })),
    onSuccess: (data) => qc.setQueryData(keys.aiSettings, data),
  });
}

export function useAiUsage() {
  return useQuery({
    queryKey: keys.aiUsage,
    queryFn: () => unwrap(api.GET("/api/v1/ai/usage")),
  });
}

export function useDeleteOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(
        api.DELETE("/api/v1/thread-overrides/{override_id}", {
          params: { path: { override_id: id } },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.processing }),
  });
}

/** Nhà cung cấp AI tùy chỉnh (spec 24). Không bao giờ nhận lại API key, chỉ "đã đặt" và 4 ký tự cuối. */
export function useAiProvider() {
  return useQuery({
    queryKey: keys.aiProvider,
    queryFn: () => unwrap(api.GET("/api/v1/ai/provider")),
  });
}

function useRefreshAiProvider() {
  const qc = useQueryClient();
  return (data: unknown) => {
    qc.setQueryData(keys.aiProvider, data);
    return qc.invalidateQueries({ queryKey: keys.aiSettings });
  };
}

export function useSaveAiProvider() {
  const refresh = useRefreshAiProvider();
  return useMutation({
    mutationFn: (body: AiProviderBody) => unwrap(api.PUT("/api/v1/ai/provider", { body })),
    onSuccess: refresh,
  });
}

export function useDeleteAiProvider() {
  const refresh = useRefreshAiProvider();
  return useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/ai/provider")),
    onSuccess: refresh,
  });
}

export function useTestAiProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/ai/provider/test")),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.aiUsage }),
  });
}

/** Dự án và bộ lọc gán mail (spec 2026-10-31). Hỏi mỗi 2 giây khi worker đang gán lại mail. */
export function useProjects() {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: keys.projects,
    queryFn: () => unwrap(api.GET("/api/v1/projects")),
    refetchInterval: (query) => (query.state.data?.assigning ? 2000 : false),
  });
  // Worker gán xong (assigning true -> false): nhãn dự án trên danh sách thread đã cũ, tải lại.
  const assigning = query.data?.assigning;
  const wasAssigning = useRef(false);
  useEffect(() => {
    if (wasAssigning.current && assigning === false) {
      void qc.invalidateQueries({ queryKey: ["threads"] });
    }
    if (assigning !== undefined) wasAssigning.current = assigning;
  }, [assigning, qc]);
  return query;
}

/** Sau khi lưu: cập nhật danh sách ngay, đánh dấu danh sách thread (nhãn dự án) là cũ. */
function useProjectChanged() {
  const qc = useQueryClient();
  return async (change: (list: ProjectList) => ProjectList) => {
    qc.setQueryData<ProjectList>(keys.projects, (old) => (old ? change(old) : old));
    await Promise.all([
      qc.invalidateQueries({ queryKey: keys.projects }),
      qc.invalidateQueries({ queryKey: ["threads"] }),
    ]);
  };
}

const byName = (a: Project, b: Project) => a.name.localeCompare(b.name);

export function useCreateProject() {
  const changed = useProjectChanged();
  return useMutation({
    mutationFn: (body: ProjectBody) => unwrap(api.POST("/api/v1/projects", { body })),
    onSuccess: (p) => changed((l) => ({ ...l, items: [...l.items, p].sort(byName) })),
  });
}

export function useUpdateProject() {
  const changed = useProjectChanged();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ProjectBody }) =>
      unwrap(
        api.PUT("/api/v1/projects/{project_id}", { params: { path: { project_id: id } }, body }),
      ),
    onSuccess: (p) =>
      changed((l) => ({ ...l, items: l.items.map((x) => (x.id === p.id ? p : x)).sort(byName) })),
  });
}

export function useDeleteProject() {
  const changed = useProjectChanged();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(api.DELETE("/api/v1/projects/{project_id}", { params: { path: { project_id: id } } })),
    onSuccess: (_data, id) => changed((l) => ({ ...l, items: l.items.filter((x) => x.id !== id) })),
  });
}

// --- Glossary (spec 26 mục 9) ---

export const GLOSSARY_PAGE = 50;

/** projectId null: glossary chung. */
export function useGlossary(projectId: number | null, q: string, offset: number) {
  return useQuery({
    queryKey: ["glossary", projectId, q, offset] as const,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/glossary", {
          params: {
            query: {
              project_id: projectId ?? undefined,
              q: q || undefined,
              limit: GLOSSARY_PAGE,
              offset,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

/** Thêm (không có id) hoặc sửa (có id) một thuật ngữ; PATCH bỏ qua project_id. */
export function useSaveGlossaryTerm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id?: number; body: GlossaryBody }) =>
      id === undefined
        ? unwrap(api.POST("/api/v1/glossary", { body }))
        : unwrap(
            api.PATCH("/api/v1/glossary/{glossary_id}", {
              params: { path: { glossary_id: id } },
              body,
            }),
          ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["glossary"] }),
  });
}

export function useDeleteGlossaryTerm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(
        api.DELETE("/api/v1/glossary/{glossary_id}", { params: { path: { glossary_id: id } } }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["glossary"] }),
  });
}

/** apply=false chỉ xem trước; apply=true ghi (sau đó danh sách được tải lại). */
export function useImportGlossary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GlossaryImportBody) => unwrap(api.POST("/api/v1/glossary/import", { body })),
    onSuccess: (_result, body) => {
      if (body.apply) return qc.invalidateQueries({ queryKey: ["glossary"] });
    },
  });
}

/** Tải CSV về bộ nhớ (Blob); không lưu vào storage trình duyệt. */
export function useExportGlossary() {
  return useMutation({
    mutationFn: async (projectId: number | null) => {
      const { data, error, response } = await api.GET("/api/v1/glossary/export", {
        params: { query: { project_id: projectId ?? undefined } },
        parseAs: "blob",
      });
      if (!response.ok) throw toApiError(response.status, error);
      return data as unknown as Blob;
    },
  });
}

// --- Dịch mail (spec 26 mục 10, 12) ---

export function useThreadTranslations(threadId: number) {
  return useQuery({
    queryKey: keys.threadTranslations(threadId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/threads/{thread_id}/translations", {
          params: { path: { thread_id: threadId } },
        }),
      ),
    retry: false,
  });
}

/** Sau thao tác dịch/chuyển: tải lại bản dịch, folder và danh sách thread (folder hiệu lực đổi). */
function useTranslationChanged() {
  const qc = useQueryClient();
  return () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ["threadTranslations"] }),
      // Badge folder trên thẻ mail (folder hiệu lực) nằm trong chi tiết thread.
      qc.invalidateQueries({ queryKey: ["thread"] }),
      qc.invalidateQueries({ queryKey: keys.folders }),
      qc.invalidateQueries({ queryKey: ["threads"] }),
    ]);
}

export function useTranslateMessage() {
  const changed = useTranslationChanged();
  return useMutation({
    mutationFn: ({ id, force }: { id: number; force: boolean }) =>
      unwrap(
        api.POST("/api/v1/messages/{message_id}/translate", {
          params: { path: { message_id: id } },
          body: { force },
        }),
      ),
    onSuccess: changed,
  });
}

export function useMoveBack() {
  const changed = useTranslationChanged();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(
        api.POST("/api/v1/messages/{message_id}/move-back", {
          params: { path: { message_id: id } },
        }),
      ),
    onSuccess: changed,
  });
}

export function useMoveMessage() {
  const changed = useTranslationChanged();
  return useMutation({
    mutationFn: ({ id, projectId }: { id: number; projectId: number }) =>
      unwrap(
        api.POST("/api/v1/messages/{message_id}/move", {
          params: { path: { message_id: id } },
          body: { project_id: projectId },
        }),
      ),
    onSuccess: changed,
  });
}

export function useAutoRetry() {
  const changed = useTranslationChanged();
  return useMutation({
    mutationFn: (id: number) =>
      unwrap(
        api.POST("/api/v1/messages/{message_id}/auto-retry", {
          params: { path: { message_id: id } },
        }),
      ),
    onSuccess: changed,
  });
}
