import { Download, Trash2, Upload } from "lucide-react";
import { type ChangeEvent, type FormEvent, useState } from "react";

import { errorMessage, type GlossaryImportBody, type GlossaryItem } from "@/api/client";
import {
  GLOSSARY_PAGE,
  useDeleteGlossaryTerm,
  useExportGlossary,
  useGlossary,
  useImportGlossary,
  useSaveGlossaryTerm,
} from "@/api/hooks";
import { Alert, AlertDescription } from "@/components/ui/alert";
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

// Giới hạn khớp spec 26 mục 9 (server cũng kiểm tra).
const TERM_MAX = 200;
const NOTE_MAX = 500;
const SELECT_CLASS = "h-9 rounded-md border border-input bg-transparent px-3 text-sm";

function Row({ item, projectId }: { item: GlossaryItem; projectId: number | null }) {
  const save = useSaveGlossaryTerm();
  const remove = useDeleteGlossaryTerm();
  const [term, setTerm] = useState(item.term);
  const [translation, setTranslation] = useState(item.translation);
  const [note, setNote] = useState(item.note ?? "");
  const [seen, setSeen] = useState(item);

  // Dữ liệu server đổi (sau khi lưu hoặc tải lại): chỉ đồng bộ ô khi người dùng chưa gõ gì khác
  // so với bản đã thấy, để không mất ký tự đang gõ và tiêu điểm (hàng không bị dựng lại).
  if (seen !== item) {
    const wasDirty =
      term !== seen.term || translation !== seen.translation || note !== (seen.note ?? "");
    setSeen(item);
    if (!wasDirty) {
      setTerm(item.term);
      setTranslation(item.translation);
      setNote(item.note ?? "");
    }
  }

  const dirty =
    term !== item.term || translation !== item.translation || note !== (item.note ?? "");

  // Rời ô thì lưu (PATCH). Lưu xong, danh sách tải lại; hàng giữ nguyên (key theo id) và đồng bộ lại nếu không có thay đổi dở.
  function commit() {
    if (!dirty || save.isPending) return;
    save.mutate({
      id: item.id,
      body: { project_id: projectId, term, translation, note: note.trim() || null },
    });
  }

  const error = save.error ?? remove.error;
  return (
    <tr className="border-t align-top">
      <td className="py-1.5 pr-2">
        <Input
          aria-label={`Thuật ngữ: ${item.term}`}
          value={term}
          maxLength={TERM_MAX}
          autoComplete="off"
          aria-invalid={save.error ? true : undefined}
          onChange={(e) => setTerm(e.target.value)}
          onBlur={commit}
        />
        {error && <p className="mt-1 text-xs text-destructive">{errorMessage(error)}</p>}
      </td>
      <td className="py-1.5 pr-2">
        <Input
          aria-label={`Bản dịch: ${item.term}`}
          value={translation}
          maxLength={TERM_MAX}
          autoComplete="off"
          onChange={(e) => setTranslation(e.target.value)}
          onBlur={commit}
        />
      </td>
      <td className="py-1.5 pr-2">
        <Input
          aria-label={`Ghi chú: ${item.term}`}
          value={note}
          maxLength={NOTE_MAX}
          autoComplete="off"
          onChange={(e) => setNote(e.target.value)}
          onBlur={commit}
        />
      </td>
      <td className="py-1.5">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={`Xóa ${item.term}`}
          disabled={remove.isPending}
          onClick={() => remove.mutate(item.id)}
        >
          <Trash2 />
        </Button>
      </td>
    </tr>
  );
}

function AddForm({ projectId }: { projectId: number | null }) {
  const save = useSaveGlossaryTerm();
  const [term, setTerm] = useState("");
  const [translation, setTranslation] = useState("");
  const [note, setNote] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (save.isPending) return;
    save.mutate(
      {
        body: {
          project_id: projectId,
          term: term.trim(),
          translation: translation.trim(),
          note: note.trim() || null,
        },
      },
      {
        onSuccess: () => {
          setTerm("");
          setTranslation("");
          setNote("");
        },
      },
    );
  }

  return (
    <form onSubmit={submit} aria-label="Thêm thuật ngữ" className="grid gap-2">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="glossary-new-term">Thuật ngữ mới</Label>
          <Input
            id="glossary-new-term"
            value={term}
            maxLength={TERM_MAX}
            autoComplete="off"
            onChange={(e) => setTerm(e.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="glossary-new-translation">Bản dịch mới</Label>
          <Input
            id="glossary-new-translation"
            value={translation}
            maxLength={TERM_MAX}
            autoComplete="off"
            onChange={(e) => setTranslation(e.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="glossary-new-note">Ghi chú mới</Label>
          <Input
            id="glossary-new-note"
            value={note}
            maxLength={NOTE_MAX}
            autoComplete="off"
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={!term.trim() || !translation.trim() || save.isPending}>
          Thêm
        </Button>
        {save.error && <p className="text-sm text-destructive">{errorMessage(save.error)}</p>}
      </div>
    </form>
  );
}

interface Preview {
  key: string;
  added: number;
  duplicate: number;
  invalid: number;
  firstError: string | null;
}

function ImportDialog({ projectId, onClose }: { projectId: number | null; onClose: () => void }) {
  const run = useImportGlossary();
  const [text, setText] = useState("");
  const [format, setFormat] = useState<GlossaryImportBody["format"]>("lines");
  const [onConflict, setOnConflict] = useState<"skip" | "overwrite">("skip");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [applied, setApplied] = useState(false);

  const key = JSON.stringify([format, onConflict, text]);
  const fresh = preview?.key === key;

  function send(apply: boolean) {
    run.mutate(
      { project_id: projectId, text, format, on_conflict: onConflict, apply },
      {
        onSuccess: (r) => {
          setPreview({
            key,
            added: r.added,
            duplicate: r.duplicate,
            invalid: r.invalid,
            firstError: r.first_error,
          });
          setApplied(apply);
        },
      },
    );
  }

  async function pickFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setText(await file.text());
    setFormat("csv");
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nhập thuật ngữ</DialogTitle>
          <DialogDescription>
            Xem trước số mục sẽ thêm trước khi ghi. Tối đa 1 MiB và 5.000 dòng.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="flex flex-wrap gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="glossary-import-format">Định dạng</Label>
              <select
                id="glossary-import-format"
                className={SELECT_CLASS}
                value={format}
                onChange={(e) => setFormat(e.target.value as GlossaryImportBody["format"])}
              >
                <option value="lines">Từng dòng (thuật ngữ = bản dịch)</option>
                <option value="csv">CSV</option>
              </select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="glossary-import-conflict">Khi trùng</Label>
              <select
                id="glossary-import-conflict"
                className={SELECT_CLASS}
                value={onConflict}
                onChange={(e) => setOnConflict(e.target.value as "skip" | "overwrite")}
              >
                <option value="skip">Bỏ qua</option>
                <option value="overwrite">Ghi đè</option>
              </select>
            </div>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="glossary-import-text">Dữ liệu nhập</Label>
            <textarea
              id="glossary-import-text"
              className="min-h-40 rounded-md border border-input bg-transparent px-3 py-2 font-mono text-sm"
              value={text}
              autoComplete="off"
              spellCheck={false}
              placeholder={
                format === "csv" ? "term,translation,note" : "結合テスト = test tích hợp"
              }
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="glossary-import-file">Hoặc chọn file CSV</Label>
            <input
              id="glossary-import-file"
              type="file"
              accept=".csv,text/csv,text/plain"
              className="text-sm"
              onChange={(e) => void pickFile(e)}
            />
          </div>
          {run.error && (
            <Alert variant="destructive">
              <AlertDescription>{errorMessage(run.error)}</AlertDescription>
            </Alert>
          )}
          {fresh && preview && (
            <div role="status" className="grid gap-1 text-sm">
              {applied && <p className="font-medium">Đã ghi vào glossary.</p>}
              <p>{`Thêm mới ${preview.added}, trùng ${preview.duplicate}, lỗi ${preview.invalid}`}</p>
              {preview.firstError && <p className="text-destructive">{preview.firstError}</p>}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Đóng
          </Button>
          <Button
            variant="outline"
            disabled={!text.trim() || run.isPending}
            onClick={() => send(false)}
          >
            Xem trước
          </Button>
          <Button disabled={!fresh || applied || run.isPending} onClick={() => send(true)}>
            Ghi
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Bảng thuật ngữ của một phạm vi (null: glossary chung). Dựng lại (key) khi đổi phạm vi. */
export function GlossaryTable({ projectId }: { projectId: number | null }) {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [importing, setImporting] = useState(false);
  const list = useGlossary(projectId, q.trim(), offset);
  const exporter = useExportGlossary();

  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const last = Math.min(offset + GLOSSARY_PAGE, total);

  function download() {
    exporter.mutate(projectId, {
      onSuccess: (blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "glossary.csv";
        link.click();
        URL.revokeObjectURL(url);
      },
    });
  }

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid min-w-48 flex-1 gap-1.5">
          <Label htmlFor="glossary-q">Tìm thuật ngữ</Label>
          <Input
            id="glossary-q"
            type="search"
            value={q}
            maxLength={200}
            autoComplete="off"
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
          />
        </div>
        <Button type="button" variant="outline" onClick={() => setImporting(true)}>
          <Upload />
          Nhập
        </Button>
        <Button type="button" variant="outline" onClick={download} disabled={exporter.isPending}>
          <Download />
          Xuất CSV
        </Button>
      </div>
      {exporter.error && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(exporter.error)}</AlertDescription>
        </Alert>
      )}
      <AddForm projectId={projectId} />
      {list.error && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(list.error)}</AlertDescription>
        </Alert>
      )}
      {list.isPending && <p className="text-sm text-muted-foreground">Đang tải…</p>}
      {list.data && items.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {q.trim() ? "Không có thuật ngữ khớp" : "Chưa có thuật ngữ nào"}
        </p>
      )}
      {items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground">
                <th className="pb-1 font-medium">Thuật ngữ</th>
                <th className="pb-1 font-medium">Bản dịch</th>
                <th className="pb-1 font-medium">Ghi chú</th>
                <th className="w-12" />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <Row key={item.id} item={item} projectId={projectId} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {total > 0 && (
        <div className="flex items-center gap-3 text-sm">
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label="Trang trước"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - GLOSSARY_PAGE))}
          >
            Trước
          </Button>
          <span>{`${offset + 1}–${last} / ${total}`}</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label="Trang sau"
            disabled={offset + GLOSSARY_PAGE >= total}
            onClick={() => setOffset(offset + GLOSSARY_PAGE)}
          >
            Sau
          </Button>
        </div>
      )}
      {importing && <ImportDialog projectId={projectId} onClose={() => setImporting(false)} />}
    </div>
  );
}
