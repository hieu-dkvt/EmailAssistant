import { Loader2, Plus } from "lucide-react";
import { type FormEvent, type Ref, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import {
  ApiError,
  errorMessage,
  type FolderInfo,
  type Project,
  type ProjectBody,
} from "@/api/client";
import {
  useCreateProject,
  useDeleteProject,
  useFolders,
  useProjects,
  useUpdateProject,
} from "@/api/hooks";
import { GlossaryTable } from "@/components/GlossaryTable";
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
import { folderName, formatDate, isInboxPath } from "@/lib/format";
import { mergeTags } from "@/lib/tags";
import { cn } from "cn";

const TAG_KEYS = [
  "filter_from",
  "filter_to",
  "filter_cc",
  "filter_subject",
  "github_mail",
  "backlog_mail",
  "customer_domains",
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
  processedSourceId: number | null;
  texts: Record<TextKey, string>;
}

// Giới hạn độ dài khớp spec 25 mục 6 (server cũng kiểm tra).
const URL_MAX = 2000;
const PATH_MAX = 1000;
const KEY_MAX = 200;

const NOT_USED = "Chưa dùng, lưu sẵn cho giai đoạn sau";

function fromProject(p: Project | null): FormState {
  return {
    name: p?.name ?? "",
    tags: Object.fromEntries(TAG_KEYS.map((k) => [k, p?.[k] ?? []])) as FormState["tags"],
    processedSourceId: p?.processed_source_id ?? null,
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
  return {
    name: state.name.trim(),
    ...tags,
    ...texts,
    processed_source_id: state.processedSourceId,
  };
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
  maxLength,
}: {
  id: string;
  label: string;
  maxLength: number;
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
        maxLength={maxLength}
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

function TranslateSection({
  project,
  folderId,
  onFolder,
  domains,
  error,
}: {
  project: Project | null;
  folderId: number | null;
  onFolder: (id: number | null) => void;
  domains: React.ReactNode;
  error?: string;
}) {
  const folders = useFolders();
  // Không cho chọn Inbox (backend cũng chặn); nhóm theo tài khoản, giữ thứ tự API trả về.
  const groups = new Map<string, FolderInfo[]>();
  for (const f of folders.data ?? []) {
    if (isInboxPath(f.folder_path)) continue;
    groups.set(f.account, [...(groups.get(f.account) ?? []), f]);
  }
  const known = [...groups.values()].some((list) => list.some((f) => f.id === folderId));
  return (
    <Section
      title="Dịch tự động"
      hint="Mail khách gửi (theo domain) được dịch sang tiếng Việt. Folder để trống thì mail vẫn được dịch nhưng không chuyển."
    >
      {domains}
      <div className="grid min-w-0 gap-1.5">
        <Label htmlFor="project-processed-source">Folder mail dự án</Label>
        <select
          id="project-processed-source"
          className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
          value={folderId === null ? "" : String(folderId)}
          aria-invalid={error ? true : undefined}
          onChange={(e) => onFolder(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">Không chuyển folder</option>
          {folderId !== null && !known && folders.data && (
            <option value={String(folderId)}>Folder đã chọn (không có trong danh sách)</option>
          )}
          {[...groups].map(([account, list]) => (
            <optgroup key={account} label={account}>
              {list.map((f) => (
                <option key={f.id} value={String(f.id)}>
                  {folderName(f.folder_path)}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <p className="text-xs text-muted-foreground">
          Chỉ chọn được folder đã đồng bộ (tick ở collector rồi đồng bộ). Không chọn Inbox.
        </p>
        {error && <p className="text-xs text-destructive">{error}</p>}
      </div>
      <p className="text-sm text-muted-foreground">
        {project?.ai_since
          ? `Dịch mail từ ngày ${formatDate(project.ai_since)}`
          : "Mốc sẽ được đặt khi lưu domain đầu tiên (14 ngày trước)"}
      </p>
    </Section>
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
    customer_domains: useRef(""),
  };

  const dirty =
    JSON.stringify(toBody(state, drafts)) !== JSON.stringify(toBody(fromProject(project), {}));
  useImperativeHandle(handle, () => ({ isDirty: () => dirty }), [dirty]);

  const pending = create.isPending || update.isPending;
  const apiError = error instanceof ApiError ? error : null;
  const errors: Record<string, string> = { ...apiError?.fields };
  if (apiError?.code === "project_name_taken") errors.name = apiError.message;
  if (apiError?.code === "project_folder_invalid") errors.processed_source_id = apiError.message;
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
  function text(key: TextKey, label: string, maxLength: number) {
    return (
      <TextField
        id={`project-${key}`}
        label={label}
        value={state.texts[key]}
        onChange={setText(key)}
        error={errors[key]}
        maxLength={maxLength}
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
    // mutateAsync: callback của mutate bị bỏ khi form đã gỡ (dự án rời cache); lỗi hiện qua remove.error.
    remove.mutateAsync(project.id).then(onDeleted, () => undefined);
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

        <TranslateSection
          project={project}
          folderId={state.processedSourceId}
          onFolder={(id) => setState((s) => ({ ...s, processedSourceId: id }))}
          domains={tag(
            "customer_domains",
            "Domain mail khách",
            "Thêm domain",
            "Mail từ domain này (và domain con) được dịch tự động. Để trống thì dự án không dịch tự động.",
          )}
          error={errors.processed_source_id}
        />

        <Section title="Tài liệu khách hàng" note={NOT_USED}>
          <div className={GRID}>
            {text("docs_repo_url", "Repo URL lưu tài liệu", URL_MAX)}
            {text("docs_local_path", "Folder local dự án", PATH_MAX)}
          </div>
        </Section>

        <Section title="GitHub review" note={NOT_USED}>
          {tag("github_mail", "Mail GitHub", "Thêm địa chỉ", "Địa chỉ gửi thông báo GitHub.")}
          <div className={GRID}>
            {text("review_repo_url", "GitHub khách review", URL_MAX)}
            {text("review_local_path", "Git local của dự án", PATH_MAX)}
          </div>
        </Section>

        <Section title="Backlog và Redmine" note={NOT_USED}>
          {tag("backlog_mail", "Mail Backlog", "Thêm địa chỉ", "Địa chỉ gửi thông báo Backlog.")}
          <div className={GRID}>
            {text("backlog_space_url", "URL space Backlog", URL_MAX)}
            {text("backlog_project_key", "Project key Backlog", KEY_MAX)}
            {text("redmine_url", "URL Redmine", URL_MAX)}
            {text("redmine_project", "Project Redmine", KEY_MAX)}
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
              {`Xóa dự án ${project?.name ?? ""}? Mail không bị xóa, chỉ bỏ liên kết với dự án. Glossary của dự án cũng bị xóa theo.`}
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
  const [tab, setTab] = useState<"config" | "glossary">("config");
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
    setTab("config");
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
  // Gate theo data (không theo isSuccess): refetch nền lỗi vẫn giữ data, form đang sửa không bị gỡ.
  if (projects.data) {
    if (isNew || selected) {
      right = (
        <>
          <h2 className="sr-only">{selected ? selected.name : "Dự án mới"}</h2>
          {selected && (
            <div role="tablist" aria-label="Mục của dự án" className="mb-4 flex gap-1 border-b">
              {(
                [
                  ["config", "Cấu hình"],
                  ["glossary", "Glossary"],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={tab === id}
                  onClick={() => setTab(id)}
                  className={cn(
                    "-mb-px border-b-2 px-3 py-2 text-sm focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
                    tab === id
                      ? "border-primary font-medium"
                      : "border-transparent text-muted-foreground",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          {/* Form luôn được dựng (chỉ ẩn) để tab Glossary không làm mất phần đang sửa. */}
          <div hidden={tab !== "config" || undefined}>
            <ProjectForm
              key={`${isNew ? "new" : selected?.id}:${formVersion}`}
              project={isNew ? null : (selected ?? null)}
              handle={formHandle}
              onSaved={onSaved}
              onDeleted={() => go(null, true)}
            />
          </div>
          {selected && tab === "glossary" && (
            <GlossaryTable key={selected.id} projectId={selected.id} />
          )}
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
