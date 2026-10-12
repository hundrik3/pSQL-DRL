# Design decisions

This lab was designed from PostgreSQL's documented physical backup and recovery
primitives. It does not depend on a copied lab, a cloud account, or a hosted service.

## Native physical backups

Use PostgreSQL 17.11 and its matching client binaries, pinned by image tag and
immutable multi-platform digest. PostgreSQL 17 is a supported major version.
Review new minor releases and explicitly update the tag and digest together;
digest pinning prevents silent updates, but also prevents automatic security fixes.

The database uses data-page checksums. A full backup uses
pg_basebackup -Fp -X stream --checkpoint=fast --manifest-checksums=SHA256.
The streamed WAL makes the base backup independently consistent. A hidden partial
directory becomes selectable only after pg_verifybackup succeeds. No running
database files are copied as a substitute for an online backup.

The manifest is stored alongside its backup. This detects accidental corruption
and missing files, not malicious replacement of both the backup and its manifest.
There is no independent signed catalog, immutable store or second trust domain.

pgBackRest is a good production alternative: it provides mature retention,
compression, encryption, parallelism, incremental/differential backups and
repository management. Native tools were chosen here to keep dependencies minimal
and make the WAL/recovery mechanism visible. This deliberately narrow controller
does not claim to replace pgBackRest. Logical pg_dump backups are convenient for
portable table-level restores, but cannot provide physical WAL-based PITR.

## WAL and durability

archive_mode=on and wal_level=replica enable continuous archiving. The archive
script copies a segment to a temporary name, flushes it, atomically publishes a
hard link, and flushes the directory. An existing identical file is accepted;
an existing different file is rejected. Both segment and backup-history files
must be archived. A nonzero result makes PostgreSQL retry rather than discard WAL.

archive_timeout=60s limits segment-switch delay during ongoing activity, not a
guaranteed RPO. Storage errors, archiver backlog, downtime and the last open
segment still matter. Exercises explicitly force a segment switch and wait for
that exact archive file before claiming durability. Recovery cannot obtain WAL
from the original database: it mounts only a full backup and the archive.

## Retention and recovery scope

Keep the two newest verified full backups. Before dropping old full backups,
verify both survivors. Remove only timeline-1 WAL segments older than the start
segment of the oldest retained backup. Keep backup/timeline history files.
Retention runs after successful backup and archive-barrier checks; failed
scheduled attempts do not expire valid backups. Partial failed backups are not
selectable and remain for investigation until cleanup (they can consume disk).

This lab supports one source cluster and original timeline 1. A restored server
promotes onto a new timeline with archiving disabled. It is a validation target,
not an automated replacement primary. Reusing a repository for an unrelated new
cluster or implementing multi-primary/timeline retention is out of scope.
The test starts a new empty source and repository for the PITR exercise.

Neither source cluster is allowed to use the other's repository. The normal
interactive commands preserve the source and repository until explicit cleanup.

Restore always verifies the selected full backup, removes only the lab's previous
recovery volume, creates an empty one, copies the backup, writes recovery.signal
and recovery settings, then waits for writable PostgreSQL and business validation.
Default restore replays available archive WAL; --base-only stops at the earliest
consistent point. PITR uses an explicit timezone-qualified target timestamp and
promotes only after PostgreSQL reaches it. Missing WAL needed for an explicit
target causes startup to fail; a finite archive without an explicit target can
stop earlier, which is why expected business state must also be checked.

## Isolation and secret handling

Each interactive lab has a UUID; each test gets a separate UUID and Compose
project. Containers, networks and named volumes carry ownership labels. Destructive
paths check those labels, exact volume roles and attached mounts. Source deletion
also checks the database identity row. Resource names and backup identifiers are
validated before interpolation. No user-supplied SQL host, arbitrary volume name,
host port, global prune or external database is accepted.

The repository is bound to PostgreSQL's system identifier as well as the lab UUID.
Reinitializing a source with the same lab UUID cannot silently mix an unrelated
cluster's backups or WAL into the existing repository. Startup waits for the final
PID-1 PostgreSQL process, not the image's temporary initialization server.

A random password is generated in .local/db_password, mode 0600, and mounted as a
Compose secret. No password is put in Compose, Git, command arguments or reports.
The backup process reads it inside the container and runs pg_basebackup as postgres.
Administrative SQL runs through the container's local socket. Local trust and
superuser use are deliberate lab simplifications; do not expose this configuration
as a production service. The Docker daemon is a privileged trust boundary.
Compose secrets on a local Docker host are file mounts, not an encrypted vault.

## Scheduling and CI

The local scheduler calls the same backup entry point, uses a per-instance
nonblocking lock and writes JSONL exit-status records. Cron can use --once;
a foreground loop is available for machines without cron. No email/webhook,
external API, or paid monitoring service is needed. Inspect failed records and
process status; a loop continuing to run does not mean its backups are succeeding.

GitHub Actions uses a hosted Linux runner, a read-only repository token, pinned
actions, and the same make test command. Evidence artifacts expire after seven
days. Public-repository standard hosted runners are free; private-repository
included minutes depend on the account plan. There are no paid runner types or
external services, but disable the workflow if your account has no free allowance.

## Official sources

- [PostgreSQL continuous archiving and PITR](https://www.postgresql.org/docs/17/continuous-archiving.html)
- [pg_basebackup](https://www.postgresql.org/docs/17/app-pgbasebackup.html)
- [pg_verifybackup and its limitations](https://www.postgresql.org/docs/17/app-pgverifybackup.html)
- [Recovery target configuration](https://www.postgresql.org/docs/17/runtime-config-wal.html#RUNTIME-CONFIG-WAL-RECOVERY-TARGET)
- [PostgreSQL version policy](https://www.postgresql.org/support/versioning/)
- [Official PostgreSQL documentation source inspected during implementation](https://github.com/postgres/postgres/tree/REL_17_STABLE/doc/src/sgml)
- [Docker Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/)
- [Official PostgreSQL Docker image](https://hub.docker.com/_/postgres)
- [GitHub Actions workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- [pgBackRest](https://pgbackrest.org/user-guide.html)
