#!/bin/sh
set -eu
umask 077
mkdir -p /repository/wal /repository/backups /repository/failed
chown -R postgres:postgres /repository
printf '\nhost replication postgres 127.0.0.1/32 scram-sha-256\n' >> "$PGDATA/pg_hba.conf"
psql -v ON_ERROR_STOP=1 -U postgres -d shop -v lab_id="$LAB_ID" -f /lab/schema.sql
