"""Khóa advisory dùng chung giữa các bước xử lý mail của worker."""

from sqlalchemy import text

# Khóa theo người dùng: worker và CLI không làm sạch / gom thread / gán dự án chồng nhau cho cùng
# một người. Truyền :u là chuỗi.
USER_MAIL_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended('process-mail:' || :u, 0))")
