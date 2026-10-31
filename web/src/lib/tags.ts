/** Thêm các giá trị mới vào cuối, bỏ rỗng và trùng (không phân biệt hoa thường). */
export function mergeTags(values: string[], extra: string[]): string[] {
  const out = [...values];
  const seen = new Set(out.map((v) => v.toLowerCase()));
  for (const raw of extra) {
    const v = raw.trim();
    if (v && !seen.has(v.toLowerCase())) {
      seen.add(v.toLowerCase());
      out.push(v);
    }
  }
  return out;
}
