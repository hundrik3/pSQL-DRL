# Recovery runbook

## Prepare and identify the lab

Requirements: Linux or Docker Desktop in Linux-container mode, Docker Engine,
Docker Compose v2, Python 3.10+, GNU Make, and about 1 GB of free disk for this small
dataset. No Python packages, PostgreSQL host installation or cloud credentials.
Run commands from the repository root. On a fresh clone, scripts are readable
by the container because Git restores the committed file modes.

    make up
    make seed
    make backup
    make verify
    make status

The controller prints backup IDs. State is in .local/state.json; do not delete it
while resources exist. It identifies the exact project and ownership labels needed
to remove resources safely. Each seed creates 25 deterministic paid orders with
one item per order and a ledger batch in one committed transaction.

## Restore to a fresh server

    make restore

This destroys only the previous recovery target, not the source or repository.
It selects the newest retained full backup and replays available archived WAL.
Inspect the printed row counts, content hashes, constraint hash and consistency
checks. A command finishing does not prove an arbitrary business recovery point:
compare with independently recorded expectations or run the automated exercises.

For a specific backup, or just the base-backup consistency point:

    make verify BACKUP=20261012T120000Z-0123abcd
    make restore BACKUP=20261012T120000Z-0123abcd
    python3 scripts/lab.py restore --backup 20261012T120000Z-0123abcd --base-only

The example ID is illustrative; use an ID actually printed by make backup.
New IDs include microseconds before Z; older IDs remain accepted. Failed backup
barriers do not publish an ID for selection. An empty PITR target or combining
--target with --base-only is rejected before creating or changing lab resources.

## Recover an accidental deletion

1. Stop application writes. Preserve the source and logs; do not overwrite it.
2. Determine a UTC timestamp after the wanted transactions and before the bad
   transaction committed. Select a retained full backup completed before it.
3. Confirm the archive contains the complete WAL sequence through the bad commit.
   A backup manifest validates only WAL needed to make the base backup consistent;
   it does not validate every later archived segment.
4. Restore into the separate recovery volume:

       make pitr BACKUP=<actual-backup-id> TARGET='2026-10-12T12:00:00.123456+00:00'

5. Confirm PostgreSQL's recovery log reached the target and promoted. Validate
   restored orders, ledger batches, content hashes, foreign keys and totals against
   the expected state. The automatic PITR exercise performs these checks and
   retains the recovery log.
6. Keep application traffic stopped until the recovered state is approved.
   Client cutover, replaying wanted later transactions and re-establishing backup
   for the promoted timeline are manual follow-up work outside this lab.

Do not remove recovery.signal to bypass an unreachable target. Investigate missing
segments, an unsuitable backup, incorrect timezone or a target beyond available
WAL. The command fails instead of treating an unachieved PITR target as success.

## Exercise complete loss of source data

    make test

This creates its own project and does not erase the interactive lab. The exercise
archives one post-backup batch, disables archiving, acknowledges another batch,
kills PostgreSQL with SIGKILL, removes the exact source named volume and confirms
its absence. Only then does it restore to a new recovery volume. The archived
batch must survive; the unarchived batch must be absent. The report records both
commit timestamps and the measured loss. Tests remove their own Docker resources
even when a check fails; logs and JSON reports remain under results/.

## Schedule and detect failures

Foreground local loop, wait one hour between completed attempts (stop with Ctrl-C):

    make schedule INTERVAL=3600

One-shot entry point for cron:

    make schedule-once

Example crontab; replace the path with your clone path and use an absolute Python
path if your cron PATH lacks python3:

    PATH=/usr/local/bin:/usr/bin:/bin
    0 * * * * cd /path/to/pSQL-DRL && /usr/bin/python3 scripts/lab.py schedule --once >> /path/to/pSQL-DRL/results/cron.log 2>&1

Do not run both a cron entry and a foreground scheduler for the same instance.
The lock rejects concurrent operations instead of allowing overlapping backup,
retention and destructive restore. Each attempt writes backups.jsonl; scheduler
also writes scheduler.jsonl with the actual subprocess exit code. --once returns
nonzero on failure so a supervisor can alert. The automatic exercise stops the
source, invokes the same scheduler entry point, requires failure, restarts the
source and verifies a previously successful backup remains usable.

## Diagnose

- Startup failure: inspect the exact project with make status and Docker logs.
  The controller includes the tail of startup logs on failure.
- Backup failure: inspect results/<project>/backups.jsonl and scheduler.jsonl.
  Partial directories cannot be selected for restore. Do not delete the last good
  backups to make space without another verified recovery path.
- Verification failure: quarantine/investigate the bad copy. Use an earlier
  independently verified full backup and its required WAL. Do not disable
  checksums or skip pg_verifybackup to produce a passing report.
- Docker unavailable: start your local Docker daemon. Do not replace actual
  integration checks with a syntax-only success claim.
- Wrong ownership labels or identity: the controller refuses destructive actions.
  Inspect the cause; do not rename foreign resources to bypass the safety gate.

## Stop and fully remove resources

    make down                 # Stop lab containers, preserve volumes and backups.
    make clean                # Delete this interactive lab's containers, network and three volumes.

Remove your cron entry or stop the foreground scheduler before cleanup. To remove
local secrets, state and generated evidence too, after successful make clean and
after all tests have finished:

    rm -rf .local results/psqldrl_*

This removes only paths inside this clone. No command performs a Docker-wide prune.
Never remove .local first: the controller would lose its resource identities.
