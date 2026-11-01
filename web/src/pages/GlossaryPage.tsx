import { useSearchParams } from "react-router";

import { errorMessage } from "@/api/client";
import { useProjects } from "@/api/hooks";
import { GlossaryTable } from "@/components/GlossaryTable";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";

/** /settings/glossary: glossary chung hoặc của một dự án (?project=id). */
export function GlossaryPage() {
  const [params, setParams] = useSearchParams();
  const projects = useProjects();
  const raw = params.get("project");
  const items = projects.data?.items ?? [];
  // Id không có trong danh sách dự án (đã xóa, của người khác) thì quay về glossary chung.
  const selected = raw && /^\d+$/.test(raw) ? items.find((p) => p.id === Number(raw)) : undefined;
  const projectId = selected?.id ?? null;
  // Chờ danh sách dự án rồi mới tải bảng, để không gọi API với id chưa kiểm tra.
  const ready = raw === null || projects.data !== undefined || projects.error !== null;

  function choose(value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set("project", value);
    else next.delete("project");
    setParams(next);
  }

  return (
    <div className="grid gap-4">
      <h1 className="text-lg font-semibold">Glossary</h1>
      <p className="text-sm text-muted-foreground">
        Thuật ngữ gửi kèm cho AI khi dịch mail. Mục của dự án thắng mục chung khi trùng thuật ngữ.
      </p>
      {projects.error && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(projects.error)}</AlertDescription>
        </Alert>
      )}
      <div className="grid max-w-sm gap-1.5">
        <Label htmlFor="glossary-scope">Phạm vi</Label>
        <select
          id="glossary-scope"
          className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
          value={projectId === null ? "" : String(projectId)}
          onChange={(e) => choose(e.target.value)}
        >
          <option value="">Glossary chung</option>
          {items.map((p) => (
            <option key={p.id} value={String(p.id)}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      {ready && <GlossaryTable key={projectId ?? "common"} projectId={projectId} />}
    </div>
  );
}
