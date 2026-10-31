import { X } from "lucide-react";
import {
  type ClipboardEvent,
  type KeyboardEvent,
  type RefObject,
  useId,
  useRef,
  useState,
} from "react";

import { Label } from "@/components/ui/label";
import { mergeTags } from "@/lib/tags";
import { cn } from "cn";

interface Props {
  id: string;
  label: string;
  hint?: string;
  /** Chữ gợi ý trong ô nhập, vd. "Thêm địa chỉ". */
  addLabel: string;
  values: string[];
  onChange: (values: string[]) => void;
  error?: string;
  /** Luôn giữ phần đang gõ chưa thêm, để form lấy lúc gửi (kể cả khi nhấn Enter ở ô khác). */
  draftRef: RefObject<string>;
  /** Báo khi phần đang gõ đổi (form dùng để biết có thay đổi chưa lưu). */
  onDraftChange?: (draft: string) => void;
}

const SPLIT = /[,\n\r]+/;

/** Ô nhiều giá trị: Enter hoặc dấu phẩy thêm, rời ô thì thêm phần đang gõ, dán nhiều giá trị được tách. */
export function TagInput({
  id,
  label,
  hint,
  addLabel,
  values,
  onChange,
  error,
  draftRef,
  onDraftChange,
}: Props) {
  const [draft, setDraftState] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const reactId = useId();
  const hintId = `${reactId}-hint`;
  const errorId = `${reactId}-error`;

  function setDraft(next: string) {
    draftRef.current = next;
    setDraftState(next);
    onDraftChange?.(next);
  }

  function commit(extra: string[]) {
    const merged = mergeTags(values, extra);
    if (merged.length !== values.length) onChange(merged);
  }

  function onInput(value: string) {
    if (!SPLIT.test(value)) {
      setDraft(value);
      return;
    }
    const parts = value.split(SPLIT);
    commit(parts.slice(0, -1));
    setDraft(parts[parts.length - 1]);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault(); // Enter trong ô không gửi form
      commit([draft]);
      setDraft("");
    } else if (event.key === "Backspace" && draft === "" && values.length > 0) {
      onChange(values.slice(0, -1));
    }
  }

  function onPaste(event: ClipboardEvent<HTMLInputElement>) {
    const text = event.clipboardData.getData("text");
    if (!SPLIT.test(text)) return;
    event.preventDefault();
    commit([draft, ...text.split(SPLIT)]);
    setDraft("");
  }

  function onBlur() {
    if (draft.trim()) commit([draft]);
    if (draft) setDraft("");
  }

  function remove(index: number) {
    onChange(values.filter((_, i) => i !== index));
    inputRef.current?.focus();
  }

  return (
    <div className="grid min-w-0 gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div
        className={cn(
          "flex min-h-9 flex-wrap items-center gap-1.5 rounded-md border border-input px-2 py-1 focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/50",
          error && "border-destructive",
        )}
      >
        {values.map((value, index) => (
          <span
            key={value}
            className="inline-flex max-w-full items-center gap-1 rounded-md bg-secondary py-0.5 pr-1 pl-2 text-sm"
          >
            <span className="min-w-0 break-all">{value}</span>
            <button
              type="button"
              aria-label={`Bỏ ${value}`}
              onClick={() => remove(index)}
              className="inline-flex size-5 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
            >
              <X className="size-3" aria-hidden="true" />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          id={id}
          value={draft}
          placeholder={addLabel}
          autoComplete="off"
          aria-invalid={error ? true : undefined}
          aria-describedby={
            [hint ? hintId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined
          }
          onChange={(e) => onInput(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onBlur={onBlur}
          className="h-6 min-w-40 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>
      {hint && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
