# Implementation self-review

The review focused on destructive operations, recovery independence, result
accuracy, secret handling and failure propagation. All instructions and code were
written for this repository; PostgreSQL's official documentation informed the
backup/WAL design.

## Findings addressed

- Container initialization needs readable init scripts and a repository volume
  owned by postgres before startup. Script executable bits are committed and the
  controller prepares the labelled volume before starting the server.
- PostgreSQL archives backup-history files as well as segment files. Filename
  validation accepts their safe suffixes while rejecting paths; archiving still
  refuses to replace a different existing file.
- pg_switch_wal need not create an archive for an empty segment. The barrier emits
  a named restore-point record first, captures its segment, switches WAL and waits
  for that exact durable archive file.
- A temporary initialization server can answer queries and then shut down. The
  startup probe waits for the final PID-1 PostgreSQL server before accepting SQL
  readiness and the lab/cluster identity.
- A lab UUID alone does not distinguish two separately initialized PostgreSQL
  clusters. The repository also records the actual system identifier and rejects
  mismatched source/recovery clusters.
- Backup uses the secret file inside the container; root reads the file, while
  gosu runs the backup client as postgres. Password contents never enter process
  command arguments, tracked files or evidence.
- Volume deletion checks exact names, Compose roles, project/instance labels and
  attached mounts. Source deletion also queries the database identity. A real
  empty fixture with mismatched ownership tests refusal without using someone's
  database or deleting a foreign volume.
- An inherited LAB_ID cannot override the controller's validated identity. Compose
  receives the controller's value explicitly. Backup IDs and PITR timestamps are
  validated before constructing commands/configuration.
- Recovery mounts only the repository and a freshly created target volume. The
  source-volume-loss exercise checks physical volume absence before restoration.
- Latest-timestamp RPO alone could hide a missing intermediate batch. Reports also
  compare acknowledged/recovered batch IDs and complete business-table hashes.
  PITR's intentionally omitted later batch is checked separately.
- The scheduler preserves the backup subprocess's actual status and records both
  successful and failed attempts. Failed attempts do not trigger retention.
- GitHub Actions uses the same real exercise command, restricted token permissions,
  immutable action references and current-run evidence artifacts.

## Accepted limits

This is a small original-timeline lab, not a production backup manager. A
colocated manifest cannot establish authenticity against a malicious writer.
Same-host copies cannot survive total host loss. A finite archive can allow an
untargeted restore to finish before wanted commits; expected-content validation
and the commit ledger are therefore essential. An explicit unreachable PITR
target must not be bypassed. The lab provides no automatic client cutover,
off-host redundancy, managed alert delivery or promoted-timeline backup continuity.

Generated backup data, WAL, volume content, credentials and bulky test outputs are
ignored. Only selected small, reviewed evidence is copied into docs/evidence/.
Failed intermediate development runs remain local and are not presented as
successful portfolio results.

## Follow-up audit

The [requirements audit](audit.md) records subsequently reproduced edge cases,
their corrections, ten regression gates and the expanded real Docker suite.
The initial report is historical evidence; it does not cover these new failure paths.
