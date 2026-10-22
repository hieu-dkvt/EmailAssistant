import { Fragment } from "react";

import type { Segment } from "@/api/client";

/** Ghép các đoạn {text, hit} của API; đoạn khớp từ khóa hiện bằng <mark>. React tự escape text. */
export function Highlighted({ segments }: { segments: Segment[] }) {
  return (
    <>
      {segments.map((s, i) =>
        s.hit ? <mark key={i}>{s.text}</mark> : <Fragment key={i}>{s.text}</Fragment>,
      )}
    </>
  );
}
