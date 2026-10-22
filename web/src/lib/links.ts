/** Link mở thread của một mail, cuộn tới mail đó và giữ tô từ khóa; null khi chưa gom thread. */
export function threadLink(
  hit: { thread_id: number | null; message_id: number },
  q: string,
): string | null {
  if (hit.thread_id === null) return null;
  const query = q ? `?${new URLSearchParams({ q }).toString()}` : "";
  return `/threads/${hit.thread_id}${query}#m-${hit.message_id}`;
}
