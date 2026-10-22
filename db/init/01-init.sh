#!/bin/bash
# Chạy một lần khi volume dữ liệu được tạo (docker-entrypoint-initdb.d), bằng superuser.
# Tạo role, database và extension. Bảng, policy và GRANT nằm trong migration Alembic.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_pw="$APP_OWNER_PASSWORD" \
  -v api_pw="$APP_API_PASSWORD" \
  -v worker_pw="$APP_WORKER_PASSWORD" <<'SQL'
CREATE ROLE app_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE app_api LOGIN NOBYPASSRLS PASSWORD :'api_pw';
CREATE ROLE app_worker LOGIN BYPASSRLS PASSWORD :'worker_pw';
CREATE DATABASE mailassist OWNER app_owner ENCODING 'UTF8' TEMPLATE template0;
CREATE DATABASE mailassist_test OWNER app_owner ENCODING 'UTF8' TEMPLATE template0;
SQL

# pgroonga và vector cần superuser để tạo. Sau đó thu hồi hàm quản trị PGroonga khỏi PUBLIC.
for db in mailassist mailassist_test; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<'SQL'
CREATE EXTENSION IF NOT EXISTS pgroonga;
CREATE EXTENSION IF NOT EXISTS vector;
SQL
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" \
    -f /opt/pm-mail-assistant/sql/revoke-pgroonga-admin.sql
done
