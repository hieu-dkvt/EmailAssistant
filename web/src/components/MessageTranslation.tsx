import { Languages, TriangleAlert } from "lucide-react";
import { useState, type ReactNode } from "react";

import {
  ApiError,
  errorMessage,
  type ProjectRef,
  type ThreadMessage,
  type ThreadTranslationItem,
} from "@/api/client";
import { useAutoRetry, useMoveBack, useMoveMessage, useTranslateMessage } from "@/api/hooks";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { folderName } from "@/lib/format";

const ERROR_TEXT: Record<string, string> = {
  translate_in_progress: "Đang dịch một mail khác, thử lại sau ít phút.",
  mail_not_ready: "Mail chưa được làm sạch, chờ worker xử lý rồi thử lại.",
  nothing_to_translate: "Mail không có nội dung để dịch.",
  ml_unavailable: "Dịch vụ ml-service chưa chạy: bật ml-service rồi thử lại.",
  ai_not_configured: "Chưa cấu hình AI: vào Cài đặt AI để nhập nhà cung cấp.",
  ai_model_missing: "Chưa chọn model dịch: vào Cài đặt AI để chọn model cho tác vụ dịch.",
  move_invalid: "Dự án này không chứa mail hoặc chưa có folder đích.",
  move_not_ready: "Mail chưa được xử lý tự động nên chưa chuyển được.",
  auto_retry_invalid: "Mail không ở trạng thái lỗi nên không cần thử lại.",
};

const WARNING_TEXT: Record<string, string> = {
  labels_missing: "Bản dịch thiếu một số nhãn đã che (tên, số tiền…): kiểm tra lại với bản gốc.",
  labels_unknown: "Bản dịch có nhãn che không có trong bản gốc: kiểm tra lại.",
  glossary_missing: "Một số thuật ngữ glossary chưa xuất hiện đúng trong bản dịch.",
  glossary_conflict: "Glossary có thuật ngữ trùng nhau, bản dịch có thể dùng sai nghĩa.",
  truncated: "Mail quá dài nên chỉ dịch phần đầu.",
};

function errorText(error: unknown): string {
  if (error instanceof ApiError && error.code && ERROR_TEXT[error.code]) {
    return ERROR_TEXT[error.code];
  }
  return errorMessage(error);
}

function Failure({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <Alert variant="destructive">
      <AlertDescription>{errorText(error)}</AlertDescription>
    </Alert>
  );
}

function MovePicker({
  messageId,
  projects,
  ambiguous,
}: {
  messageId: number;
  projects: ProjectRef[];
  ambiguous: boolean;
}) {
  const move = useMoveMessage();
  const [projectId, setProjectId] = useState("");
  return (
    <div className="grid gap-1 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground">
          {ambiguous
            ? "Mail thuộc nhiều dự án có folder đích: chọn dự án để chuyển."
            : "Mail chưa được chuyển: chọn dự án để chuyển sang folder đích."}
        </span>
        <label htmlFor={`mv-${messageId}`} className="sr-only">
          Dự án
        </label>
        <select
          id={`mv-${messageId}`}
          className="h-8 rounded-md border border-input bg-transparent px-2 text-sm"
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
        >
          <option value="">Chọn dự án…</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <Button
          size="sm"
          variant="outline"
          disabled={!projectId || move.isPending}
          onClick={() => move.mutate({ id: messageId, projectId: Number(projectId) })}
        >
          Chuyển
        </Button>
      </div>
      <Failure error={move.error} />
    </div>
  );
}

/** Phần dịch của một thẻ mail: hai cột Bản gốc / Bản dịch, nút dịch, chuyển folder (spec 26 mục 12). */
export function MessageTranslation({
  message,
  item,
  original,
}: {
  message: ThreadMessage;
  /** Có `move_projects` (dự án của mail có folder đích): danh sách chọn khi chuyển mail. */
  item: ThreadTranslationItem | undefined;
  /** Nội dung bản gốc (do MessageCard dựng: làm sạch hoặc toàn văn). */
  original: ReactNode;
}) {
  const translate = useTranslateMessage();
  const moveBack = useMoveBack();
  const retry = useAutoRetry();
  const tr = item?.translation ?? null;
  const auto = item?.auto ?? null;
  const emptyMail = auto?.status === "done" && auto.error_code === "nothing_to_translate";
  const unmoved = auto?.status === "done" && !auto.target_folder;
  const moveProjects = item?.move_projects ?? [];

  const translateButton = (force: boolean) => (
    <Button
      variant="outline"
      size="sm"
      disabled={translate.isPending}
      onClick={() => translate.mutate({ id: message.id, force })}
    >
      <Languages />
      {translate.isPending ? "Đang dịch…" : force ? "Dịch lại" : "Dịch"}
    </Button>
  );

  return (
    <div className="grid gap-2">
      {tr ? (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="min-w-0">
            <div className="mb-1 text-xs font-medium text-muted-foreground">Bản gốc</div>
            {original}
          </div>
          <div className="min-w-0">
            <div className="mb-1 flex flex-wrap items-center gap-2 text-xs font-medium text-muted-foreground">
              Bản dịch
              {tr.stale && <Badge variant="outline">Bản dịch cũ</Badge>}
            </div>
            {tr.translated_subject && <div className="font-medium">{tr.translated_subject}</div>}
            <pre className="font-sans text-sm break-words whitespace-pre-wrap">
              {tr.translated_body}
            </pre>
            {tr.notes.length > 0 && (
              <div className="mt-2 text-xs text-muted-foreground">
                <div className="font-medium">Ghi chú sắc thái</div>
                <ul className="list-disc pl-4">
                  {tr.notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              </div>
            )}
            {tr.warnings.length > 0 && (
              <Alert className="mt-2" data-testid="translation-warnings">
                <TriangleAlert />
                <AlertDescription>
                  <ul className="list-disc pl-4">
                    {tr.warnings.map((w, i) => (
                      <li key={i}>{WARNING_TEXT[w] ?? "Bản dịch có điểm cần kiểm tra lại."}</li>
                    ))}
                  </ul>
                </AlertDescription>
              </Alert>
            )}
            {tr.stale && <div className="mt-2">{translateButton(true)}</div>}
          </div>
        </div>
      ) : (
        <>
          {original}
          {emptyMail ? (
            <p className="text-xs text-muted-foreground">Mail không có nội dung để dịch.</p>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              {translateButton(false)}
              {auto?.status === "retry" && (
                <span className="text-xs text-muted-foreground">
                  Đang chờ dịch lại tự động{auto.error_code ? ` (mã: ${auto.error_code})` : ""}.
                </span>
              )}
            </div>
          )}
        </>
      )}
      {auto?.status === "failed" && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-destructive">
            Dịch tự động lỗi{auto.error_code ? ` (mã: ${auto.error_code})` : ""}.
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={retry.isPending}
            onClick={() => retry.mutate(message.id)}
          >
            Thử lại
          </Button>
        </div>
      )}
      <Failure error={translate.error ?? retry.error} />
      {auto?.target_folder && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="secondary" title={auto.target_folder}>
            Đã chuyển tới {folderName(auto.target_folder)}
          </Badge>
          <Button
            variant="outline"
            size="sm"
            disabled={moveBack.isPending}
            onClick={() => moveBack.mutate(message.id)}
          >
            Chuyển lại Inbox
          </Button>
        </div>
      )}
      <Failure error={moveBack.error} />
      {unmoved && moveProjects.length > 0 && (
        <MovePicker
          messageId={message.id}
          projects={moveProjects}
          ambiguous={auto.move_note === "ambiguous"}
        />
      )}
      {unmoved && moveProjects.length === 0 && auto.move_note && (
        <p className="text-xs text-muted-foreground">
          Dự án của mail chưa có folder đích nên mail chưa được chuyển.
        </p>
      )}
    </div>
  );
}
