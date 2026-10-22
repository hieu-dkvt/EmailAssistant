import { type FormEvent } from "react";
import { useSearchParams } from "react-router";

import { useFolders } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { hasFilters, readFilters, writeFilters, type Filters } from "@/lib/filters";
import { folderName } from "@/lib/format";

export const selectClass =
  "h-9 rounded-md border border-input bg-background px-2 text-sm shadow-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50";

/** Bộ lọc folder, người gửi, khoảng ngày, file đính kèm. Áp dụng khi bấm "Lọc" (ghi lên URL). */
export function FilterBar() {
  const [params, setParams] = useSearchParams();
  const filters = readFilters(params);
  const folders = useFolders();

  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const get = (k: string) => String(data.get(k) ?? "").trim() || undefined;
    const next: Filters = {
      folder: get("folder"),
      sender: get("sender"),
      from: get("from"),
      to: get("to"),
      att: get("att") as Filters["att"],
    };
    const p = writeFilters(params, next);
    p.delete("page");
    setParams(p);
  }

  function clear() {
    const p = writeFilters(params, {});
    p.delete("page");
    setParams(p);
  }

  // key theo URL: khi URL đổi (bấm link, Xóa lọc) thì ô nhập lấy lại giá trị từ URL.
  return (
    <form
      key={params.toString()}
      onSubmit={apply}
      className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border p-3"
      aria-label="Bộ lọc"
    >
      <div className="grid gap-1">
        <Label htmlFor="f-folder">Folder</Label>
        <select
          id="f-folder"
          name="folder"
          defaultValue={filters.folder ?? ""}
          className={selectClass}
        >
          <option value="">Tất cả folder</option>
          {folders.data?.map((f) => (
            <option key={f.id} value={f.id} title={f.folder_path}>
              {folderName(f.folder_path)} ({f.message_count})
            </option>
          ))}
        </select>
      </div>
      <div className="grid gap-1">
        <Label htmlFor="f-sender">Người gửi</Label>
        <Input
          id="f-sender"
          name="sender"
          autoComplete="off"
          defaultValue={filters.sender ?? ""}
          placeholder="tên hoặc địa chỉ"
          className="w-48"
          maxLength={200}
        />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="f-from">Từ ngày</Label>
        <Input
          id="f-from"
          name="from"
          type="date"
          defaultValue={filters.from ?? ""}
          className="w-40"
        />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="f-to">Đến ngày</Label>
        <Input id="f-to" name="to" type="date" defaultValue={filters.to ?? ""} className="w-40" />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="f-att">Đính kèm</Label>
        <select id="f-att" name="att" defaultValue={filters.att ?? ""} className={selectClass}>
          <option value="">Tất cả</option>
          <option value="1">Có file đính kèm</option>
          <option value="0">Không có file</option>
        </select>
      </div>
      <Button type="submit">Lọc</Button>
      {hasFilters(filters) && (
        <Button type="button" variant="ghost" onClick={clear}>
          Xóa lọc
        </Button>
      )}
    </form>
  );
}
