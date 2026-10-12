# Follow-up requirements audit

The initial successful restores were genuine. The audit also found edge-case
faults that those original checks did not exercise. The implementation was fixed
and the real recovery suite expanded. Original evidence is preserved separately.

## Findings and corrections

- **Backup publication before WAL confirmation:** reproduced on a real PostgreSQL
  instance by injecting a controller barrier failure after real pg_basebackup and
  pg_verifybackup. The original controller reported failure but still published a
  selectable backup. Publication now happens only after the durability barrier;
  a rejected attempt stays under a hidden partial name. The regression uses the
  same explicitly documented injection; it is not claimed as a physical disk outage.
- **Ordering:** second-resolution names relied on random suffixes within the same
  second. New IDs contain microseconds, and parsed timestamp ordering supports
  existing IDs. Backwards-moving clocks remain outside this simple retention policy.
- **Volume inspection and mount safety:** Docker errors no longer count as volume
  absence. Recovery requires precisely its own data volume and repository at the
  expected destinations. A valid ownership label cannot permit a source mount in
  the recovery server.
- **Lost-source startup:** startup refuses before creating a new primary when a
  bound archive remains but its source volume is gone. Use restore instead.
- **PITR input and unreachable target:** an empty target or conflicting target modes
  is rejected before state creation. A real future target is tested and must produce
  PostgreSQL's explicit unreachable-target failure.
- **Durability and failure classification:** identical archived files are flushed
  again on retry; different contents are rejected. Corruption checks require the
  actual pg_verifybackup diagnostic and exit code, not just any process failure.
- **Report truthfulness:** cleanup finishes before the final report is written;
  cleanup failures make the run failed and retain the earlier exercise error.
- **WAL retention coverage:** restore from the oldest retained backup now replays
  a batch created after all full backups. Matching complete content proves the
  retained archive chain is usable; expired segments are checked as absent.

## Executed validation

Ten Python standard-library regression tests passed. These mock error preconditions
and validate safety/reporting gates; they do not substitute for database exercises.
Three real Docker recovery scenarios and eleven additional checks also passed:

| Exercise | RTO (seconds) | Observed RPO / rollback gap (seconds) | Omitted orders |
| --- | ---: | ---: | ---: |
| full-backup-restore | 20.505 | 0.000000 | 0 |
| source-volume-loss | 29.123 | 13.373168 | 25 |
| delete-and-pitr | 21.848 | 9.765600 | 25 intentionally omitted |

The source-volume loss again omitted exactly 25 deliberately unarchived orders.
PITR recovered the wanted orders and intentionally omitted the later batch. Its
number is a rollback window, not unexpected archive loss. RTO includes recovery
and business validation; see the existing measurement definition.

| Additional check | Result |
| --- | --- |
| missing-source-startup-refused | passed |
| unreachable-pitr-refused | passed |
| archive-idempotency-and-no-overwrite | passed |
| corrupt-data | passed |
| missing-data | passed |
| missing-wal | passed |
| archive-barrier-failure-unpublished | passed |
| retention-and-oldest-restore | passed |
| scheduled-backup-and-failure | passed |
| foreign-resource-refused | passed |
| resource-cleanup | passed |

The test project's containers, network and named volumes were independently checked
as absent after completion. The existing interactive source was restarted without
clearing it: its original 25 orders, content hashes and legacy backup were preserved.
The original CI artifact was downloaded and its successful report inspected.

## Requirement mapping

| Original requirement | Evidence |
| --- | --- |
| Supported PostgreSQL pinned in Compose | PostgreSQL 17.11 and immutable image digest; real container startup |
| Domain model and test generator | Customers, products, orders, items and commit ledger; complete content hashes |
| Full backup, WAL, retention and verification | Native physical backup, SHA-256 manifest, archive contract and two-backup retention |
| Independent restore | Empty target volume; source volume removed and confirmed absent before restore |
| Timestamp PITR | Deleted orders/items returned, later batch omitted, explicit recovery-stop log |
| Automated fault scenarios | Full restore, source loss, DELETE/PITR and corrupt/incomplete backup checks |
| Integrity beyond startup | Counts, content SHA-256, constraint definitions, actual rejected writes, totals and ledger |
| RTO/RPO | Monotonic operation timing and actual PostgreSQL commit timestamps; saved report |
| Local scheduling and failed execution | Shared one-shot scheduler succeeds, stopped source fails; initial timed-loop proof preserved |
| Free GitHub Actions | Hosted Ubuntu workflow runs unit gates and real Docker exercises; new outcome recorded after completion |

## Evidence and remaining limits

[Machine-readable report](evidence/audit-20261012/report.json),
[unit-test transcript](evidence/audit-20261012/unit-tests.txt),
[implementation metadata](evidence/audit-20261012/metadata.json),
[original defect reproduction](evidence/audit-20261012/prior-publication-defect.json),
[backup attempt records](evidence/audit-20261012/backups.jsonl), and
[unreachable-target diagnostic](evidence/audit-20261012/unreachable-pitr.txt) are
compact records of executed operations. No database files, WAL or credentials
are included. The injected controller outage is labelled explicitly.

The same-host backup limitation, one-original-timeline scope, small workload,
colocated manifest and manual client cutover remain intentional lab limits.
RTO/RPO observations are not production guarantees. Historic evidence still
records the initial run; the audit directory describes this expanded version.
