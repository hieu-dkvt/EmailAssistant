import { useState, type FormEvent } from "react";

import { errorMessage, type ProjectRef } from "@/api/client";
import { useSaveGlossaryTerm } from "@/api/hooks";
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

/** Thêm một thuật ngữ vào glossary từ đoạn chữ bôi đen trong thẻ mail. */
export function AddGlossaryDialog({
  initialTerm,
  projects,
  onClose,
}: {
  initialTerm: string;
  /** Dự án của mail (để chọn phạm vi); rỗng thì chỉ có phạm vi chung. */
  projects: ProjectRef[];
  onClose: () => void;
}) {
  const save = useSaveGlossaryTerm();
  const [term, setTerm] = useState(initialTerm.slice(0, TERM_MAX));
  const [translation, setTranslation] = useState("");
  const [note, setNote] = useState("");
  const [scope, setScope] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate(
      {
        body: {
          project_id: scope ? Number(scope) : null,
          term,
          translation,
          note: note.trim() || null,
        },
      },
      { onSuccess: onClose },
    );
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-3">
          <DialogHeader>
            <DialogTitle>Thêm vào glossary</DialogTitle>
            <DialogDescription>
              Thuật ngữ sẽ được dùng cho các bản dịch sau. Bản dịch đã có không tự dịch lại.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-1">
            <Label htmlFor="gl-term">Thuật ngữ</Label>
            <Input
              id="gl-term"
              value={term}
              maxLength={TERM_MAX}
              autoComplete="off"
              onChange={(e) => setTerm(e.target.value)}
            />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="gl-translation">Bản dịch</Label>
            <Input
              id="gl-translation"
              value={translation}
              maxLength={TERM_MAX}
              autoComplete="off"
              onChange={(e) => setTranslation(e.target.value)}
            />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="gl-note">Ghi chú</Label>
            <Input
              id="gl-note"
              value={note}
              maxLength={NOTE_MAX}
              autoComplete="off"
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="gl-scope">Phạm vi</Label>
            <select
              id="gl-scope"
              className={SELECT_CLASS}
              value={scope}
              onChange={(e) => setScope(e.target.value)}
            >
              <option value="">Dùng chung</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          {save.error && (
            <Alert variant="destructive">
              <AlertDescription>{errorMessage(save.error)}</AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Hủy
            </Button>
            <Button type="submit" disabled={save.isPending || !term.trim() || !translation.trim()}>
              Lưu
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
