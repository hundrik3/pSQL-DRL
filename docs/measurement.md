# Measuring recovery, not assuming it

Every exercise records UTC wall-clock timestamps for audit and PostgreSQL commit
timestamps for data-loss measurement. RTO uses Python's monotonic clock so a wall
clock correction cannot make recovery look faster or slower.

## RTO

For a full-backup restore, the clock starts immediately before backup verification
and target creation. For source-volume loss, it starts before killing the source
and deleting its volume. For PITR, it starts after the fault has been committed
and the needed WAL archived, immediately before restore. Detection and operator
decision time, installing Docker, pulling the image and generating the backup are
not included. These are recovery-operation measurements, not full incident RTOs.

The clock ends only after PostgreSQL is writable, the lab identity matches,
all five business-table snapshots match expected counts and SHA-256 content
hashes, schema constraint definitions match, order totals are consistent, negative
constraint writes fail with the required PostgreSQL SQLSTATEs, and the recovered
commit ledger has been read. Startup alone is insufficient.

The implementation copies into a new empty volume on every restore. The crash
scenario additionally verifies that the source named volume is absent. The
recovery server and restore helper never mount the source volume.

## RPO and observed loss

Each workload batch inserts 25 orders and their items plus one immutable ledger
row in a single transaction. After COMMIT returns, its actual PostgreSQL commit
timestamp is obtained with pg_xact_commit_timestamp(xmin). track_commit_timestamp
is enabled on both source and recovered servers. Missing timestamps fail the
exercise instead of substituting client start times.

Observed RPO in seconds is:

    max(0, latest acknowledged batch commit - latest recovered batch commit)

The report also records both ledgers, missing acknowledged batch IDs and the
number of omitted orders. A zero timestamp gap is accepted only alongside exact
content checks. This is a finite workload measurement; it is not a general bound
on recovery of arbitrarily concurrent transactions. The batch-ID comparison
exposes missing intermediate commits which a latest-timestamp metric alone could
hide. Non-workload transactions such as the deliberate DELETE and validation
probes are excluded from the batch-ledger definition.

The crash exercise makes loss observable: archive a post-backup batch, freeze the
archiver, then commit another batch before SIGKILL and deleting the source volume.
Exactly the final unarchived batch must be missing. RPO therefore reflects a
real acknowledged transaction lost through a real volume deletion.

PITR is different: a valid later batch is intentionally omitted to return to the
state before the erroneous DELETE. The report identifies this batch separately
as intentional_rollback_batches. Its timestamp gap is the rollback window, not an
archiver failure. Wanted orders deleted by the fault must reappear with identical
content. Later valid transactions require a separate reconciliation procedure.

## Evidence and reproducibility

make test uses a new project on every invocation, writes results/<project>/report.json,
backup/scheduler JSONL records and concise failure/PITR logs. Reports record a
top-level passed or failed status; checks that have not run are not listed as
successful. An expected verifier/scheduler failure is a passed negative test only
if the underlying command actually returns the expected failure. Corruption
checks require pg_verifybackup's own matching diagnostic and exit status, not a
generic Docker error. The source is restarted and
the good backup is rechecked after the scheduler-failure exercise.
The final report is written after resource cleanup. Cleanup errors mark the run
failed and are recorded separately from any preceding exercise error.
make test also runs ten standard-library regression tests before the Docker
exercises; mocked safety/error-path tests are distinct from actual restore checks.

The tracked evidence is a selected completed run under docs/evidence/, not live
backup data. New runs remain ignored; review and explicitly copy only small,
non-secret reports if updating portfolio evidence. CI uploads only its current
ephemeral workspace's results as an artifact. No data directories, backup manifests,
WAL segments, password files or container volume contents are committed.

The workload is intentionally small, on an otherwise lightly loaded local Docker
host. Hardware, Docker storage, cache state, image availability, workload size and
WAL replay length affect timing. No lab result is a production SLA or RPO guarantee.
Other isolated lab smoke tests also used the Docker host during local validation;
these were not controlled production-load benchmarks.
