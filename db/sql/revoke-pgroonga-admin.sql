-- Hàm quản trị của PGroonga mặc định cho PUBLIC EXECUTE. Chúng chạy lệnh Groonga thô trên
-- dữ liệu index (không qua RLS), hoặc khóa ghi / cắt WAL của index. Chỉ superuser được gọi.
-- Chạy bằng superuser trong từng database có extension pgroonga (script init gọi file này;
-- volume tạo trước khi có file thì chạy tay: xem backend/CLAUDE.md).
DO $$
DECLARE
  f regprocedure;
BEGIN
  FOR f IN
    SELECT p.oid::regprocedure
    FROM pg_proc p
    WHERE p.proname IN (
      'pgroonga_command',
      'pgroonga_set_writable',
      'pgroonga_wal_truncate',
      'pgroonga_wal_apply',
      'pgroonga_flush',
      'pgroonga_vacuum'
    )
  LOOP
    EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', f);
  END LOOP;
END $$;
