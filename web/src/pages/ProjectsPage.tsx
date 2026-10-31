import { Loader2, Plus } from "lucide-react";
import { type FormEvent, type Ref, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import { ApiError, errorMessage, type Project, type ProjectBody } from "@/api/client";
import { useCreateProject, useDeleteProject, useProjects, useUpdateProject } from "@/api/hooks";
import { TagInput } from "@/components/TagInput";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { mergeTags } from "@/lib/tags";
import { cn } from "cn";

const TAG_KEYS = [
  "filter_from",
  "filter_to",
  "filter_cc",
  "filter_subject",
  "github_mail",
  "backlog_mail",
] as const;
const TEXT_KEYS = [
  "docs_repo_url",
  "docs_local_path",
  "review_repo_url",
  "review_local_path",
  "backlog_space_url",
  "backlog_project_key",
  "redmine_url",
  "redmine_project",
] as const;
type TagKey = (typeof TAG_KEYS)[number];
type TextKey = (typeof TEXT_KEYS)[number];

interface FormState {
  name: string;
  tags: Record<TagKey, string[]>;
  texts: Record<TextKey, string>;
}

const NOT_USED = "Chưa dùng, lưu sẵn cho giai đoạn sau";

function fromProject(p: Project | null): FormState {
  return {
    name: p?.name ?? "",
    tags: Object.fromEntries(TAG_KEYS.map((k) => [k, p?.[k] ?? []])) as FormState["tags"],
    texts: Object.fromEntries(TEXT_KEYS.map((k) => [k, p?.[k] ?? ""])) as FormState["texts"],
  };
}

/** Giá trị gửi lên: phần đang gõ (`drafts`) được tính như đã thêm; ô văn bản trống thành null. */
function toBody(state: FormState, drafts: Partial<Record<TagKey, string>>): ProjectBody {
  const tags = Object.fromEntries(
    TAG_KEYS.map((k) => [k, mergeTags(state.tags[k], [drafts[k] ?? ""])]),
  ) as Record<TagKey, string[]>;
  const texts = Object.fromEntries(
    TEXT_KEYS.map((k) => [k, state.texts[k].trim() || null]),
  ) as Record<TextKey, string | null>;
  return { name: state.name.trim(), ...tags, ...texts };
}

function filterCount(
  p: Pick<Project, "filter_from" | "filter_to" | "filter_cc" | "filter_subject">,
) {
  return p.filter_from.length + p.filter_to.length + p.filter_cc.length + p.filter_subject.length;
}

interface FormHandle {
  isDirty: () => boolean;
}

function TextField({
  id,
  label,
  value,
  onChange,
  error,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
}) {
  return (
    <div className="grid min-w-0 gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        value={value}
        maxLength={500}
        autoComplete="off"
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      {error && (
        <p id={`${id}-error`} className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

function Section({
  title,
  note,
  hint,
  children,
}: {
  title: string;
  note?: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="grid gap-4 border-t py-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">{title}</h3>
        {note && <Badge variant="secondary">{note}</Badge>}
      </div>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {children}
    </section>
  );
}

const GRID = "grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-4";

interface FormProps {
  /** null: dự án mới. */
  project: Project | null;
  handle: Ref<FormHandle>;
  onSaved: (id: number) => void;
  onDeleted: () => void;
}

function ProjectForm({ project, handle, onSaved, onDeleted }: FormProps) {
  const create = useCreateProject();
  const update = useUpdateProject();
  const remove = useDeleteProject();
  const [state, setState] = useState(() => fromProject(project));
  const [drafts, setDrafts] = useState<Partial<Record<TagKey, string>>>({});
  const [error, setError] = useState<unknown>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const draftRefs = {
    filter_from: useRef(""),
    filter_to: useRef(""),
    filter_cc: useRef(""),
    filter_subject: useRef(""),
    github_mail: useRef(""),
    backlog_mail: useRef(""),
  };

  const dirty =
    JSON.stringify(toBody(state, drafts)) !== JSON.stringify(toBody(fromProject(project), {}));
  useImperativeHandle(handle, () => ({ isDirty: () => dirty }), [dirty]);

  const pending = create.isPending || update.isPending;
  const apiError = error instanceof ApiError ? error : null;
  const errors: Record<string, string> = { ...apiError?.fields };
  if (apiError?.code === "project_name_taken") errors.name = apiError.message;
  const showAlert = error !== null && Object.keys(errors).length === 0;

  const shownFilters = TAG_KEYS.slice(0, 4).reduce(
    (n, k) => n + mergeTags(state.tags[k], [drafts[k] ?? ""]).length,
    0,
  );

  function setTags(key: TagKey) {
    return (values: string[]) => setState((s) => ({ ...s, tags: { ...s.tags, [key]: values } }));
  }
  function setText(key: TextKey) {
    return (value: string) => setState((s) => ({ ...s, texts: { ...s.texts, [key]: value } }));
  }
  function tag(key: TagKey, label: string, addLabel: string, hint?: string) {
    return (
      <TagInput
        id={`project-${key}`}
        label={label}
        hint={hint}
        addLabel={addLabel}
        values={state.tags[key]}
        onChange={setTags(key)}
        error={errors[key]}
        draftRef={draftRefs[key]}
        onDraftChange={(d) => setDrafts((prev) => ({ ...prev, [key]: d }))}
      />
    );
  }
  function text(key: TextKey, label: string) {
    return (
      <TextField
        id={`project-${key}`}
        label={label}
        value={state.texts[key]}
        onChange={setText(key)}
        error={errors[key]}
      />
    );
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    // Lấy phần đang gõ từ ref (không chờ render): bấm Lưu hay Enter ở ô khác đều không mất giá trị.
    const typed = Object.fromEntries(TAG_KEYS.map((k) => [k, draftRefs[k].current]));
    const body = toBody(state, typed);
    setError(null);
    if (project) {
      update.mutate(
        { id: project.id, body },
        { onSuccess: (p) => onSaved(p.id), onError: setError },
      );
    } else {
      create.mutate(body, { onSuccess: (p) => onSaved(p.id), onError: setError });
    }
  }

  function doDelete() {
    if (!project) return;
    remove.mutate(project.id, { onSuccess: onDeleted });
  }

  return (
    <>
      <form onSubmit={submit} aria-label="Thông tin dự án" noValidate>
        <div className="grid gap-1.5 pb-5">
          <Label htmlFor="project-name">Tên dự án</Label>
          <Input
            id="project-name"
            value={state.name}
            maxLength={200}
            autoComplete="off"
            aria-invalid={errors.name ? true : undefined}
            aria-describedby={errors.name ? "project-name-error" : undefined}
            onChange={(e) => setState((s) => ({ ...s, name: e.target.value }))}
          />
          {errors.name && (
            <p id="project-name-error" className="text-xs text-destructive">
              {errors.name}
            </p>
          )}
        </div>

        <Section
          title="Bộ lọc mail"
          hint="Mail thuộc dự án khi khớp ít nhất một ô From, To, CC. Nếu ô Subject có giá trị, subject của mail cũng phải khớp. Khớp theo kiểu có chứa, không phân biệt hoa thường."
        >
          {shownFilters === 0 && (
            <Alert role="status">
              <AlertDescription>Dự án chưa có bộ lọc nên chưa nhận mail nào</AlertDescription>
            </Alert>
          )}
          <div className={GRID}>
            {tag("filter_from", "From", "Thêm địa chỉ", "Địa chỉ hoặc tên miền người gửi.")}
            {tag("filter_to", "To", "Thêm địa chỉ", "Địa chỉ hoặc tên miền người nhận.")}
          </div>
          <div className={GRID}>
            {tag("filter_cc", "CC", "Thêm địa chỉ", "Địa chỉ hoặc tên miền ở CC.")}
            {tag(
              "filter_subject",
              "Subject",
              "Thêm chuỗi",
              "Chuỗi có trong subject. Để trống thì không xét subject.",
            )}
          </div>
        </Section>

        <Section title="Tài liệu khách hàng" note={NOT_USED}>
          <div className={GRID}>
            {text("docs_repo_url", "Repo URL lưu tài liệu")}
            {text("docs_local_path", "Folder local dự án")}
          </div>
        </Section>

        <Section title="GitHub review" note={NOT_USED}>
          {tag("github_mail", "Mail GitHub", "Thêm địa chỉ", "Địa chỉ gửi thông báo GitHub.")}
          <div className={GRID}>
            {text("review_repo_url", "GitHub khách review")}
            {text("review_local_path", "Git local của dự án")}
          </div>
        </Section>

        <Section title="Backlog và Redmine" note={NOT_USED}>
          {tag("backlog_mail", "Mail Backlog", "Thêm địa chỉ", "Địa chỉ gửi thông báo Backlog.")}
          <div className={GRID}>
            {text("backlog_space_url", "URL space Backlog")}
            {text("backlog_project_key", "Project key Backlog")}
            {text("redmine_url", "URL Redmine")}
            {text("redmine_project", "Project Redmine")}
          </div>
        </Section>

        {showAlert && (
          <Alert variant="destructive" className="mb-4">
            <AlertDescription>{errorMessage(error)}</AlertDescription>
          </Alert>
        )}
        <div className="flex flex-wrap items-center gap-3 border-t pt-5">
          <Button type="submit" disabled={!dirty || pending}>
            Lưu dự án
          </Button>
          {project && (
            <Button type="button" variant="outline" onClick={() => setConfirmDelete(true)}>
              Xóa dự án
            </Button>
          )}
        </div>
      </form>

      <Dialog
        open={confirmDelete}
        onOpenChange={(open) => {
          if (!open) setConfirmDelete(false);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Xóa dự án</DialogTitle>
            <DialogDescription>
              {`Xóa dự án ${project?.name ?? ""}? Mail không bị xóa, chỉ bỏ liên kết với dự án.`}
            </DialogDescription>
          </DialogHeader>
          {remove.error && (
            <Alert variant="destructive">
              <AlertDescription>{errorMessage(remove.error)}</AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(false)}>
              Hủy
            </Button>
            <Button variant="destructive" onClick={doDelete} disabled={remove.isPending}>
              Xóa
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** Worker đang gán lại mail theo bộ lọc; sau 30 giây vẫn chưa xong thì nhắc kiểm tra worker. */
function AssigningNotice() {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), 30_000);
    return () => clearTimeout(timer);
  }, []);
  return (
    <div className="mb-4 rounded-lg border bg-secondary px-4 py-2 text-sm" role="status">
      <Loader2 className="mr-2 inline size-4 animate-spin" aria-hidden="true" />
      <span>Đang gán lại mail theo bộ lọc…</span>
      {slow && (
        <span className="text-muted-foreground">
          {" "}
          Vẫn đang chờ: worker có đang chạy không (<code>python -m app.worker</code>)?
        </span>
      )}
    </div>
  );
}

export function ProjectsPage() {
  const [params, setParams] = useSearchParams();
  const projects = useProjects();
  const formHandle = useRef<FormHandle>(null);
  const [formVersion, setFormVersion] = useState(0);
  const [saved, setSaved] = useState(false);
  const [leaveTo, setLeaveTo] = useState<{ target: string | null } | null>(null);

  const raw = params.get("project");
  const isNew = raw === "new";
  const selectedId = raw && /^\d+$/.test(raw) ? Number(raw) : null;
  const items = projects.data?.items ?? [];
  const selected = selectedId === null ? undefined : items.find((p) => p.id === selectedId);

  function go(target: string | null, replace = false) {
    const next = new URLSearchParams(params);
    if (target) next.set("project", target);
    else next.delete("project");
    setSaved(false);
    setParams(next, { replace });
  }

  function choose(target: string) {
    if (target === raw) return;
    if (formHandle.current?.isDirty()) setLeaveTo({ target });
    else go(target);
  }

  function onSaved(id: number) {
    setSaved(true);
    setFormVersion((v) => v + 1);
    const next = new URLSearchParams(params);
    next.set("project", String(id));
    setParams(next, { replace: true });
  }

  let right: React.ReactNode = null;
  if (projects.isSuccess) {
    if (isNew || selected) {
      right = (
        <>
          <h2 className="sr-only">{selected ? selected.name : "Dự án mới"}</h2>
          <ProjectForm
            key={`${isNew ? "new" : selected?.id}:${formVersion}`}
            project={isNew ? null : (selected ?? null)}
            handle={formHandle}
            onSaved={onSaved}
            onDeleted={() => go(null, true)}
          />
          {saved && (
            <p className="mt-3 text-sm text-muted-foreground" role="status">
              Đã lưu.
            </p>
          )}
        </>
      );
    } else if (items.length > 0 || selectedId !== null) {
      right = (
        <p className="text-sm text-muted-foreground">
          {selectedId !== null ? "Không tìm thấy dự án này. " : ""}
          Chọn một dự án ở bên trái hoặc thêm dự án mới.
        </p>
      );
    }
  }

  return (
    <div>
      {projects.data?.assigning && <AssigningNotice />}
      {projects.error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{errorMessage(projects.error)}</AlertDescription>
        </Alert>
      )}
      <div className="flex flex-wrap items-start gap-6">
        <aside className="flex min-w-0 max-w-full flex-1 basis-64 flex-col gap-3 sm:max-w-72">
          <div className="flex items-center justify-between gap-2">
            <h1 className="text-lg font-semibold">Dự án</h1>
            {items.length > 0 && (
              <Button variant="outline" onClick={() => choose("new")}>
                <Plus />
                Thêm dự án
              </Button>
            )}
          </div>
          {projects.isPending && <p className="text-sm text-muted-foreground">Đang tải…</p>}
          {projects.isSuccess && items.length === 0 && (
            <div className="grid gap-3 rounded-lg border p-4">
              <p className="text-sm">Chưa có dự án nào</p>
              <Button variant="outline" onClick={() => choose("new")}>
                <Plus />
                Thêm dự án
              </Button>
            </div>
          )}
          {items.length > 0 && (
            <nav aria-label="Danh sách dự án" className="grid gap-0.5">
              {items.map((p) => {
                const active = p.id === selectedId && !isNew;
                const n = filterCount(p);
                return (
                  <button
                    key={p.id}
                    type="button"
                    aria-current={active ? "true" : undefined}
                    onClick={() => choose(String(p.id))}
                    className={cn(
                      "grid w-full gap-0.5 rounded-md px-2.5 py-2 text-left hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
                      active && "bg-secondary",
                    )}
                  >
                    <span className={cn("break-words text-sm", active ? "font-medium" : "")}>
                      {p.name}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {n > 0 ? `${n} điều kiện lọc` : "Chưa có bộ lọc"}
                    </span>
                  </button>
                );
              })}
            </nav>
          )}
          <p className="text-xs text-muted-foreground">
            Danh sách này chỉ áp dụng cho tài khoản của bạn.
          </p>
        </aside>
        <div className="min-w-0 grow-[999] basis-[560px]">
          {right && <div className="rounded-xl border px-6 py-5">{right}</div>}
        </div>
      </div>

      <Dialog open={leaveTo !== null} onOpenChange={(open) => !open && setLeaveTo(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Bỏ thay đổi chưa lưu?</DialogTitle>
            <DialogDescription>
              Dự án đang sửa có thay đổi chưa lưu. Chuyển sang mục khác sẽ mất các thay đổi này.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setLeaveTo(null)}>
              Ở lại
            </Button>
            <Button
              onClick={() => {
                const target = leaveTo?.target ?? null;
                setLeaveTo(null);
                go(target);
              }}
            >
              Bỏ thay đổi
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
