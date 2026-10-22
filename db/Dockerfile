# PostgreSQL 16 + pgvector + PGroonga cho PM Mail Assistant.
FROM pgvector/pgvector:0.8.0-pg16

# Version được ghim (kiểm tra bằng apt-cache madison trong log build). Để trống thì cài bản mới nhất.
ARG PGROONGA_VERSION=4.0.6-1

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates wget; \
    . /etc/os-release; \
    wget -q -O /tmp/groonga-apt-source.deb \
      "https://packages.groonga.org/debian/groonga-apt-source-latest-${VERSION_CODENAME}.deb"; \
    apt-get install -y --no-install-recommends /tmp/groonga-apt-source.deb; \
    apt-get update; \
    apt-cache madison postgresql-16-pgdg-pgroonga; \
    apt-get install -y --no-install-recommends \
      "postgresql-16-pgdg-pgroonga${PGROONGA_VERSION:+=${PGROONGA_VERSION}}"; \
    apt-get purge -y --auto-remove wget; \
    rm -rf /var/lib/apt/lists/* /tmp/groonga-apt-source.deb

# Script tạo role, database, extension; chạy một lần khi volume dữ liệu mới được tạo.
COPY sql/ /opt/pm-mail-assistant/sql/
COPY --chmod=755 init/ /docker-entrypoint-initdb.d/
