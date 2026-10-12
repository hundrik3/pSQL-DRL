# Completed disaster recovery exercises

Run interval (UTC): 2026-10-12T16:56:27.076445+00:00 to 2026-10-12T17:01:11.015308+00:00.

Overall result: **passed**. All three recovery scenarios and all six additional
checks completed. No planned, skipped or unrun checks are counted as successful.

| Exercise | RTO (seconds) | Observed RPO / rollback gap (seconds) | Omitted acknowledged orders |
| --- | ---: | ---: | --- |
| full-backup-restore | 20.650 | 0.000000 | 0 |
| source-volume-loss | 25.763 | 12.492706 | 25 |
| delete-and-pitr | 18.714 | 9.113017 | 25 intentionally omitted |

## Observed behavior

- Full backup: 25 orders and items recovered with identical content and constraint
  hashes; no acknowledged batch was missing.
- Source-volume loss: a post-backup batch archived to WAL survived. PostgreSQL was
  killed with SIGKILL and the original named volume was deleted and confirmed
  absent. Two batches (50 orders) recovered; the final deliberately unarchived
  acknowledged batch (25 orders) was missing. Its real commit timestamps define
  the reported RPO.
- Accidental deletion: the bad transaction removed batch 2 orders and cascading
  items, reducing orders to 25. A later valid batch committed. Timestamp PITR
  restored the pre-delete 50 orders/items with matching hashes and ledger batches
  1 and 2, and intentionally excluded batch 3. PostgreSQL logs confirm stopping
  before the bad commit and completing archive recovery.
- Corruption: changing bytes in global/pg_control was rejected with a CRC error.
  Removing that file was rejected as a manifest-listed file absent from disk.
  Removing required streamed WAL was rejected by pg_waldump/pg_verifybackup.
  Each diagnostic is preserved; all three verifier invocations returned nonzero.
- Retention: three full backups were created, two retained, and the oldest
  surviving full backup was restored and validated against current source data.
- Scheduling: the one-shot scheduler returned 0 with a running source and 1 after
  the source was stopped. Both outcomes were recorded. After restart the good
  backup still verified successfully.
- Safety: a specifically created empty volume carrying the wrong instance label
  was refused, confirmed still present, and then removed only as the test fixture.

## Compact evidence

- [Machine-readable scenario report](evidence/report.json): counts, SHA-256 content
  hashes, constraint hash, actual commit ledgers, source-volume absence and timings.
- [Environment and implementation hashes](evidence/environment.json).
- [Backup attempt records](evidence/backups.jsonl), including the deliberately
  failed attempt.
- [Scheduler exit-status records](evidence/scheduler.jsonl).
- [PITR server log](evidence/pitr.txt).
- [Corruption](evidence/corrupt-data.txt), [missing data](evidence/missing-data.txt),
  and [missing WAL](evidence/missing-wal.txt) diagnostics.

The separately exercised interactive Make sequence also passed: up, seed, check,
backup, verify, archive, restore, recovered-data check, schedule-once, down, up and
source-data check. Restart preserved the same data hashes. Test resources were
removed after completion; the separate interactive lab remains available.

## Scope and limitations

These measurements include backup verification, target creation, PostgreSQL
startup and business validation; source-loss RTO also includes source destruction.
They exclude incident detection, operator decision time, image download and backup
creation. The source-loss fault intentionally demonstrates nonzero data loss.
PITR intentionally rolls back later valid transactions and needs reconciliation.
See [the precise method](measurement.md) before interpreting the numbers.

Other isolated checks used the local Docker host during validation. This is a
small reproducible workload, not a production SLA. A colocated repository does not
protect against total computer loss; it provides neither off-host redundancy nor
independent backup authenticity. See [design decisions](decisions.md) and
[self-review](review.md). GitHub Actions evidence is recorded separately after
its actual completion.

## GitHub Actions validation

[Run 38282189905](https://github.com/hundrik3/pSQL-DRL/actions/runs/38282189905)
completed successfully for commit 00aa69dbcfd16207316ad41740432dbec54b0e70.
The runtime check, full fault suite, evidence upload and cleanup steps all
completed successfully. [Saved API evidence](evidence/github-actions.json) records
every step conclusion and artifact metadata. The workflow uses immutable Node.js
24 action commits; a prior Node.js deprecation annotation was resolved.

Raw hosted logs/artifact contents were not downloaded: the cloud egress policy
denied the Actions log host. This does not prevent API outcome verification or
access to the artifact in GitHub's UI. That destination has been saved in the
environment configuration draft for review; no hosted artifact RTO/RPO values
are claimed here. The detailed measurements above come from the actual local run.

An additional real foreground scheduler probe completed two interval-triggered
backup attempts with exit code 0, then was deliberately stopped with SIGINT.
[Timed scheduler evidence](evidence/scheduler-loop.json) records both attempts.

This documentation-only evidence update does not trigger another CI run: the
workflow ignores docs and README changes. The tested implementation is unchanged.
