FROM postgres:18.6

# The official postgres:18.6 image can run archive_command. It does not include
# the pinned pgBackRest binary. This build adds that checked release tarball.
# It does not embed secrets.

USER root

RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      gcc \
      libbz2-dev \
      liblz4-dev \
      libpq-dev \
      libssl-dev \
      libxml2-dev \
      libzstd-dev \
      meson \
      ninja-build \
      pkg-config \
      python3-minimal \
      zlib1g-dev \
      libbz2-1.0 \
      liblz4-1 \
      libxml2 \
      libzstd1 \
      zlib1g; \
    curl -fsSL -o /tmp/pgbackrest.tar.gz \
      "https://github.com/pgbackrest/pgbackrest/releases/download/release/2.60.0/pgbackrest-2.60.0.tar.gz"; \
    echo "6d3ee1b5defdadc85228969b4be804096f5e65e54707bb95c7c4c4ce41f5d007  /tmp/pgbackrest.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/pgbackrest.tar.gz -C /tmp; \
    meson setup /tmp/pgbackrest-2.60.0/build /tmp/pgbackrest-2.60.0; \
    ninja -C /tmp/pgbackrest-2.60.0/build; \
    meson install -C /tmp/pgbackrest-2.60.0/build; \
    pgbackrest version | grep -qx "pgBackRest 2.60.0"; \
    if ldd /usr/local/bin/pgbackrest | grep -q "not found"; then exit 1; fi; \
    test "$(id -u postgres)" = "999"; \
    mkdir -p /var/spool/pgbackrest /var/log/pgbackrest /var/lib/pgbackrest \
      /var/lib/gym-backup-metrics /usr/local/lib/gym; \
    chown postgres:postgres /var/spool/pgbackrest /var/log/pgbackrest \
      /var/lib/pgbackrest /var/lib/gym-backup-metrics; \
    chmod 0750 /var/spool/pgbackrest /var/log/pgbackrest /var/lib/pgbackrest; \
    chmod 0755 /var/lib/gym-backup-metrics; \
    apt-get purge -y \
      curl \
      gcc \
      libbz2-dev \
      liblz4-dev \
      libpq-dev \
      libssl-dev \
      libxml2-dev \
      libzstd-dev \
      meson \
      ninja-build \
      pkg-config \
      zlib1g-dev; \
    rm -rf /var/lib/apt/lists/* /tmp/pgbackrest.tar.gz /tmp/pgbackrest-2.60.0; \
    rm -f /usr/local/bin/gosu

COPY backup_policy.py /usr/local/lib/gym/backup_policy.py
COPY bin/gym-backup /usr/local/bin/gym-backup
COPY bin/gym-archive-push /usr/local/bin/gym-archive-push
COPY bin/gym-postgres-entrypoint /usr/local/bin/gym-postgres-entrypoint

RUN chmod 0755 /usr/local/bin/gym-backup /usr/local/bin/gym-archive-push /usr/local/bin/gym-postgres-entrypoint \
    && chmod 0644 /usr/local/lib/gym/backup_policy.py \
    && ln -s gym-archive-push /usr/local/bin/pgbackrest-archive-push

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/usr/local/lib/gym \
    PGBACKREST_CONFIG=/run/pgbackrest/pgbackrest.conf

USER postgres
STOPSIGNAL SIGINT
ENTRYPOINT ["/usr/local/bin/gym-postgres-entrypoint"]
CMD ["postgres"]
