#!/usr/bin/env python3
"""Isolated PostgreSQL backup/recovery controller; Python standard library only."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
IMAGE = "postgres:17.11-bookworm@sha256:3645570cccdfa447589da9f57dd740faa29b30938e861289a5574b6ca6b03826"
BACKUP_PATTERN = r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}"

def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def run(args, data=None, check=True, timeout=180, env=None):
    result = subprocess.run(args, input=data, text=True, capture_output=True, timeout=timeout, cwd=ROOT, env=env)
    if check and result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}{result.stderr}")
    return result

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)

class Lab:
    def __init__(self, state_path=None):
        LOCAL.mkdir(mode=0o700, exist_ok=True)
        password = LOCAL / "db_password"
        if not password.exists():
            fd = os.open(password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_urlsafe(32) + "\n")
        self.state_path = state_path or LOCAL / "state.json"
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
        else:
            identifier = str(uuid.uuid4())
            self.state = {"id": identifier, "project": "psqldrl_" + identifier.replace("-", "")}
            write_json(self.state_path, self.state)
        if (str(uuid.UUID(self.state["id"])) != self.state["id"] or
                self.state["project"] != "psqldrl_" + self.state["id"].replace("-", "")):
            raise RuntimeError("Invalid local resource identity")
        self.project = self.state["project"]
        self.env_path = self.state_path.with_suffix(".env")
        self.env_path.write_text("LAB_ID=" + self.state["id"] + "\n")
        self.log_dir = ROOT / "results" / self.project
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.backup_log = self.log_dir / "backups.jsonl"

    def compose(self, *args, check=True):
        return run(["docker", "compose", "--project-name", self.project, "--env-file",
                    str(self.env_path), "-f", str(ROOT / "compose.yaml"), *args], check=check,
                   env=dict(os.environ, LAB_ID=self.state["id"]))

    def assert_labels(self, labels):
        if (labels.get("io.psqldrl.managed") != "true" or
                labels.get("io.psqldrl.instance") != self.state["id"] or
                labels.get("com.docker.compose.project") != self.project):
            raise RuntimeError("Resource ownership check failed; refusing destructive action")

    def volume(self, role, required=True):
        if role not in ("primary", "recovery", "repository"):
            raise RuntimeError("Unknown volume role")
        name = self.project + "_" + role
        result = run(["docker", "volume", "inspect", name], check=False)
        if result.returncode:
            if required:
                raise RuntimeError("Required lab volume missing: " + name)
            return None
        info = json.loads(result.stdout)[0]
        self.assert_labels(info.get("Labels") or {})
        if info["Labels"].get("com.docker.compose.volume") != role:
            raise RuntimeError("Volume role mismatch")
        return name

    def container(self, service):
        ids = self.compose("ps", "-a", "-q", service).stdout.split()
        if len(ids) != 1:
            raise RuntimeError("Expected one lab container: " + service)
        info = json.loads(run(["docker", "container", "inspect", ids[0]]).stdout)[0]
        self.assert_labels(info["Config"].get("Labels") or {})
        if info["Config"]["Labels"].get("com.docker.compose.service") != service:
            raise RuntimeError("Container service mismatch")
        for mount in info["Mounts"]:
            if mount["Type"] == "volume":
                names = {self.project + "_" + r: r for r in ("primary", "recovery", "repository")}
                if mount["Name"] not in names:
                    raise RuntimeError("Foreign volume attached to lab container")
                self.volume(names[mount["Name"]])
        return ids[0]

    def sql(self, statement, service="db", check=True):
        return run(["docker", "exec", "-i", "-u", "postgres", self.container(service), "psql",
                    "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "shop"],
                   data=statement, check=check)

    def identity(self, service="db", bind=False):
        if self.sql("SELECT id FROM lab_identity;", service).stdout.strip() != self.state["id"]:
            raise RuntimeError("Database lab identity mismatch")
        cluster = self.sql("SELECT system_identifier FROM pg_control_system();", service).stdout.strip()
        if not re.fullmatch(r"[0-9]+", cluster):
            raise RuntimeError("Invalid PostgreSQL cluster identity")
        saved = self.tool("if [ -f /repository/cluster-id ]; then cat /repository/cluster-id; fi").stdout.strip()
        if not saved and bind:
            self.tool(f"printf '%s\\n' '{cluster}' > /repository/cluster-id", writable=True)
        elif saved != cluster:
            raise RuntimeError("Repository belongs to a different PostgreSQL cluster; preserve it and investigate")

    def wait(self, service="db", bind=False):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            info = json.loads(run(["docker", "container", "inspect", self.container(service)]).stdout)[0]
            if info["State"]["Status"] == "exited":
                raise RuntimeError("Database exited during startup\n" +
                                   self.compose("logs", "--tail", "80", service, check=False).stdout)
            # The image briefly runs a socket-only server during initdb. It is
            # not the final PID-1 server and may shut down between SQL probes.
            process = run(["docker", "exec", self.container(service), "cat", "/proc/1/comm"], check=False)
            if process.stdout.strip() != "postgres":
                time.sleep(0.5)
                continue
            response = self.sql("SELECT NOT pg_is_in_recovery();", service, check=False)
            if response.returncode == 0 and response.stdout.strip() == "t":
                self.identity(service, bind=bind)
                return
            time.sleep(0.5)
        raise RuntimeError("Database did not become writable\n" +
                           self.compose("logs", "--tail", "80", service, check=False).stdout)

    def tool(self, script, writable=False, recovery=False, check=True):
        args = ["docker", "run", "--rm", "--network", "none", "--label",
                "io.psqldrl.managed=true", "--label", "io.psqldrl.instance=" + self.state["id"],
                "--mount", f"type=volume,src={self.volume('repository')},dst=/repository" +
                ("" if writable else ",readonly")]
        if recovery:
            args += ["--mount", f"type=volume,src={self.volume('recovery')},dst=/restore"]
        return run([*args, IMAGE, "sh", "-ceu", script], check=check)

    def up(self):
        for role in ("primary", "recovery", "repository"):
            self.volume(role, required=False)
        self.compose("create", "db")
        self.tool('mkdir -p /repository/backups /repository/wal /repository/failed; '
                  'if [ -f /repository/lab-id ]; then '
                  f'test "$(cat /repository/lab-id)" = "{self.state["id"]}"; '
                  f'else printf "%s\\n" "{self.state["id"]}" > /repository/lab-id; fi; '
                  'chown -R postgres:postgres /repository; chmod 700 /repository', writable=True)
        self.compose("up", "-d", "db")
        self.wait(bind=True)

    def seed(self, count=25):
        self.identity()
        batch = int(self.sql("SELECT COALESCE(max(id),0)+1 FROM batches;").stdout.strip())
        first = batch * 1000
        self.sql(f"""BEGIN;
INSERT INTO batches(id) VALUES ({batch});
INSERT INTO orders
SELECT {first}+n, (n % 20)+1, {batch}, 'paid', (1+(n%3))*(100+((n%10)+1)*25)
FROM generate_series(1,{count}) n;
INSERT INTO order_items
SELECT {first}+n, (n%10)+1, 1+(n%3), 100+((n%10)+1)*25
FROM generate_series(1,{count}) n;
COMMIT;""")
        return self.ledger()

    def ledger(self, service="db"):
        value = self.sql("""SELECT COALESCE(json_agg(x ORDER BY id),'[]') FROM
(SELECT id, pg_xact_commit_timestamp(xmin) AS committed_at FROM batches) x;""", service)
        ledger = json.loads(value.stdout)
        if any(row["committed_at"] is None for row in ledger):
            raise RuntimeError("Missing commit timestamps; cannot measure RPO")
        return ledger

    def snapshot(self, service="db"):
        self.identity(service)
        tables = {"customers": "id", "products": "id", "batches": "id",
                  "orders": "id", "order_items": "order_id, product_id"}
        snapshot = {}
        for table, ordering in tables.items():
            value = self.sql(f"SELECT COALESCE(json_agg(t ORDER BY {ordering}),'[]') FROM {table} t;", service).stdout
            rows = json.loads(value)
            canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
            snapshot[table] = {"rows": len(rows), "sha256": hashlib.sha256(canonical.encode()).hexdigest()}
        definitions = self.sql("""SELECT conrelid::regclass::text || ':' || conname || ':' ||
pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace='public'::regnamespace
ORDER BY conrelid::regclass::text, conname;""", service).stdout
        snapshot["constraints_sha256"] = hashlib.sha256(definitions.encode()).hexdigest()
        mismatches = self.sql("""SELECT count(*) FROM orders o LEFT JOIN
(SELECT order_id, sum(quantity::bigint * unit_price_cents) total FROM order_items GROUP BY order_id) i
ON i.order_id=o.id WHERE i.total IS DISTINCT FROM o.total_cents;""", service)
        if mismatches.stdout.strip() != "0":
            raise RuntimeError("Order totals are inconsistent")
        self.sql("""DO $$ BEGIN
 BEGIN INSERT INTO customers VALUES (-1, 'customer-1@example.invalid');
 RAISE EXCEPTION 'Unique constraint missing'; EXCEPTION WHEN unique_violation THEN NULL; END;
 BEGIN INSERT INTO customers VALUES (-1, NULL);
 RAISE EXCEPTION 'NOT NULL missing'; EXCEPTION WHEN not_null_violation THEN NULL; END;
 BEGIN INSERT INTO orders VALUES (-1, 999999, 1, 'paid', 1);
 RAISE EXCEPTION 'Foreign key missing'; EXCEPTION WHEN foreign_key_violation THEN NULL; END;
 BEGIN INSERT INTO orders VALUES (-1, 1, 1, 'invalid', 1);
 RAISE EXCEPTION 'Status check missing'; EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN INSERT INTO orders VALUES (-1, 1, 1, 'paid', -1);
 RAISE EXCEPTION 'Amount check missing'; EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN INSERT INTO order_items VALUES (1001, 1, 0, 100);
 RAISE EXCEPTION 'Quantity check missing'; EXCEPTION WHEN check_violation THEN NULL; END;
END $$;""", service)
        return snapshot

    def archive(self):
        # An empty current segment need not switch. Emit a WAL record first so the
        # durability barrier refers to a segment that actually exists and can archive.
        segment = self.sql("SELECT pg_walfile_name(pg_create_restore_point('lab_archive_barrier'));").stdout.strip()
        if not re.fullmatch(r"[A-F0-9]{24}", segment):
            raise RuntimeError("Invalid WAL segment")
        self.sql("SELECT pg_switch_wal();")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.tool(f"test -f /repository/wal/{segment}", check=False).returncode == 0:
                return segment
            time.sleep(0.5)
        raise RuntimeError("WAL archive did not reach the requested durability barrier")

    def backup_ids(self):
        value = self.tool('find /repository/backups -mindepth 1 -maxdepth 1 -type d -printf "%f\\n"').stdout
        return sorted(x for x in value.splitlines() if re.fullmatch(BACKUP_PATTERN, x))

    def backup_id(self, identifier=None):
        ids = self.backup_ids()
        identifier = identifier or (ids[-1] if ids else None)
        if not identifier or not re.fullmatch(BACKUP_PATTERN, identifier) or identifier not in ids:
            raise RuntimeError("No matching complete backup; run make backup")
        return identifier

    def verify(self, identifier=None):
        identifier = self.backup_id(identifier)
        return self.tool(f"pg_verifybackup /repository/backups/{identifier}").stdout.strip()

    def backup(self):
        started = time.monotonic()
        identifier = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)
        record = {"backup": identifier, "started_at": utc(), "status": "failed"}
        try:
            self.identity()
            self.volume("repository")
            temporary = "/repository/backups/.partial-" + identifier
            command = ('umask 077; export PGPASSWORD="$(cat /run/secrets/db_password)"; '
                       f"gosu postgres pg_basebackup -h 127.0.0.1 -U postgres -D {temporary} "
                       "-Fp -X stream --checkpoint=fast --manifest-checksums=SHA256 "
                       f"--label={identifier}; pg_verifybackup {temporary}; "
                       f"mv {temporary} /repository/backups/{identifier}; sync -f /repository/backups")
            result = run(["docker", "exec", "-u", "0", self.container("db"), "sh", "-ceu", command])
            record["basebackup_seconds"] = round(time.monotonic() - started, 3)
            record["verification"] = result.stdout.strip()
            self.archive()
            self.retain()
            record["status"] = "passed"
            return identifier
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            record["finished_at"] = utc()
            with self.backup_log.open("a") as stream:
                stream.write(json.dumps(record) + "\n")

    def retain(self):
        ids = self.backup_ids()
        if len(ids) <= 2:
            return
        keep = ids[-2:]
        for identifier in keep:
            self.verify(identifier)
        label = self.tool(f"cat /repository/backups/{keep[0]}/backup_label").stdout
        segment = re.search(r"\(file ([A-F0-9]{24})\)", label).group(1)
        if segment[:8] != "00000001":
            raise RuntimeError("Retention supports only original timeline 1")
        for identifier in ids[:-2]:
            self.tool(f"rm -rf /repository/backups/{identifier}", writable=True)
        self.tool(f'''for path in /repository/wal/00000001????????????????; do
test -f "$path" || continue
name=$(basename "$path")
if [ "$name" \\< "{segment}" ]; then rm -- "$path"; fi
done''', writable=True)

    def remove_service(self, service, role, kill=False):
        if {"db": "primary", "recovery": "recovery"}.get(service) != role:
            raise RuntimeError("Service and volume role do not match")
        if self.compose("ps", "-a", "-q", service).stdout.strip():
            container = self.container(service)
            if service == "db":
                self.identity()
            if kill:
                run(["docker", "kill", "--signal", "KILL", container])
            else:
                self.compose("stop", service)
            self.compose("rm", "-f", service)
        name = self.volume(role, required=False)
        if name:
            run(["docker", "volume", "rm", name])

    def restore(self, identifier=None, target=None, base_only=False):
        identifier = self.backup_id(identifier)
        self.verify(identifier)
        if target:
            parsed = dt.datetime.fromisoformat(target)
            if parsed.tzinfo is None:
                raise RuntimeError("PITR target must include a timezone")
            target = parsed.astimezone(dt.timezone.utc).isoformat()
        self.remove_service("recovery", "recovery")
        self.compose("--profile", "recovery", "create", "recovery")
        self.volume("recovery")
        settings = ["restore_command = 'cp /repository/wal/%f %p'", "recovery_target_timeline = '1'"]
        if target:
            settings += [f"recovery_target_time = '{target}'", "recovery_target_inclusive = 'true'",
                         "recovery_target_action = 'promote'"]
        elif base_only:
            settings += ["recovery_target = 'immediate'", "recovery_target_action = 'promote'"]
        config = "\n".join(settings) + "\n"
        self.tool(f'''test -z "$(ls -A /restore)"
cp -a /repository/backups/{identifier}/. /restore/
rm -f /restore/backup_manifest /restore/postmaster.pid
cat > /restore/postgresql.auto.conf <<'RECOVERY_CONFIG'
{config}RECOVERY_CONFIG
touch /restore/recovery.signal
chown -R postgres:postgres /restore
chmod 700 /restore''', recovery=True)
        self.compose("--profile", "recovery", "up", "-d", "recovery")
        self.wait("recovery")
        return identifier

    def assert_snapshot(self, expected):
        actual = self.snapshot("recovery")
        if actual != expected:
            raise RuntimeError("Recovered content or constraints mismatch\n" +
                               json.dumps({"expected": expected, "actual": actual}, indent=2))
        return actual

    def measure(self, name, started, expected, acknowledged, intentional=None):
        actual = self.assert_snapshot(expected)
        recovered = self.ledger("recovery")
        missing = sorted({x["id"] for x in acknowledged} - {x["id"] for x in recovered})
        if not recovered:
            raise RuntimeError("No recovered business commits")
        latest_ack = max(dt.datetime.fromisoformat(x["committed_at"]) for x in acknowledged)
        latest_recovered = max(dt.datetime.fromisoformat(x["committed_at"]) for x in recovered)
        return {"scenario": name, "status": "passed", "rto_seconds": round(time.monotonic() - started, 3),
                "rpo_seconds": round(max(0, (latest_ack - latest_recovered).total_seconds()), 6),
                "missing_acknowledged_batches": missing, "lost_orders": len(missing) * 25,
                "intentional_rollback_batches": intentional or [], "acknowledged_commits": acknowledged,
                "recovered_commits": recovered, "integrity": actual}

    def clean(self):
        ids = run(["docker", "ps", "-aq", "--filter",
                   "label=com.docker.compose.project=" + self.project]).stdout.split()
        for identifier in ids:
            info = json.loads(run(["docker", "container", "inspect", identifier]).stdout)[0]
            self.assert_labels(info["Config"].get("Labels") or {})
            service = info["Config"]["Labels"].get("com.docker.compose.service")
            if service not in ("db", "recovery"):
                raise RuntimeError("Unknown container in project")
            self.container(service)
        networks = run(["docker", "network", "ls", "-q", "--filter",
                        "label=com.docker.compose.project=" + self.project]).stdout.split()
        for identifier in networks:
            self.assert_labels(json.loads(run(["docker", "network", "inspect", identifier]).stdout)[0].get("Labels") or {})
        volumes = [self.volume(role, required=False) for role in ("primary", "recovery", "repository")]
        self.compose("--profile", "recovery", "down", "--remove-orphans")
        for name in volumes:
            if name:
                run(["docker", "volume", "rm", name])

    def schedule_once(self):
        result = run([sys.executable, str(Path(__file__).resolve()), "--state", str(self.state_path),
                      "backup"], check=False)
        with (self.log_dir / "scheduler.jsonl").open("a") as stream:
            stream.write(json.dumps({"time": utc(), "exit_code": result.returncode,
                                     "stdout": result.stdout[-2000:], "stderr": result.stderr[-4000:]}) + "\n")
        return result.returncode

    def safety_negative_check(self):
        # Create an expendable fixture with mismatched ownership, then prove the
        # controller refuses it and leaves it intact. Never use a real foreign volume.
        foreign = Lab(LOCAL / "runs" / (uuid.uuid4().hex + ".json"))
        name = foreign.project + "_primary"
        if run(["docker", "volume", "inspect", name], check=False).returncode == 0:
            raise RuntimeError("Safety fixture name already exists")
        labels = {"io.psqldrl.managed": "true", "io.psqldrl.instance": self.state["id"],
                  "io.psqldrl.fixture": self.state["id"],
                  "com.docker.compose.project": foreign.project,
                  "com.docker.compose.volume": "primary"}
        arguments = ["docker", "volume", "create"]
        for key, value in labels.items():
            arguments += ["--label", key + "=" + value]
        run([*arguments, name])
        try:
            try:
                foreign.volume("primary")
            except RuntimeError as error:
                if "ownership check failed" not in str(error):
                    raise
            else:
                raise RuntimeError("Foreign resource passed safety gate")
            info = json.loads(run(["docker", "volume", "inspect", name]).stdout)[0]
            if info.get("Labels") != labels:
                raise RuntimeError("Safety fixture identity changed")
        finally:
            info = json.loads(run(["docker", "volume", "inspect", name]).stdout)[0]
            if info.get("Labels") == labels:
                run(["docker", "volume", "rm", name])

    def test(self):
        report = {"started_at": utc(), "image": IMAGE, "project": self.project,
                  "scenarios": [], "checks": [], "status": "running"}
        try:
            print("Exercise: full backup and independent restore", flush=True)
            self.up()
            self.seed()
            baseline, acknowledged = self.snapshot(), self.ledger()
            first = self.backup()
            started = time.monotonic()
            self.restore(first, base_only=True)
            report["scenarios"].append(self.measure("full-backup-restore", started, baseline, acknowledged))

            print("Exercise: crash and source-volume removal", flush=True)
            self.seed()
            archived_state, archived_commits = self.snapshot(), self.ledger()
            barrier = self.archive()
            self.sql("ALTER SYSTEM SET archive_command = '/bin/false'; SELECT pg_reload_conf();")
            self.seed()
            acknowledged = self.ledger()
            started = time.monotonic()
            self.remove_service("db", "primary", kill=True)
            if self.volume("primary", required=False) is not None:
                raise RuntimeError("Source volume still exists")
            self.restore(first)
            measured = self.measure("source-volume-loss", started, archived_state, acknowledged)
            if measured["missing_acknowledged_batches"] != [acknowledged[-1]["id"]]:
                raise RuntimeError("Expected exactly one lost unarchived batch")
            measured.update(last_archived_segment=barrier, source_volume_absent=True,
                            archived_batch_count=len(archived_commits))
            report["scenarios"].append(measured)

            print("Exercise: accidental deletion and timestamp PITR", flush=True)
            self.clean()
            self.up()
            self.seed()
            pitr_backup = self.backup()
            self.seed()
            before_delete, before_commits = self.snapshot(), self.ledger()
            target = self.sql("SELECT clock_timestamp();").stdout.strip()
            time.sleep(0.1)
            self.sql("DELETE FROM orders WHERE batch_id=2;")
            deleted = int(self.sql("SELECT count(*) FROM orders;").stdout.strip())
            if deleted != 25:
                raise RuntimeError("Delete fault did not execute")
            self.seed()
            after_commits = self.ledger()
            self.archive()
            started = time.monotonic()
            self.restore(pitr_backup, target=target)
            measured = self.measure("delete-and-pitr", started, before_delete, after_commits,
                                    intentional=[after_commits[-1]["id"]])
            if measured["missing_acknowledged_batches"] != [after_commits[-1]["id"]]:
                raise RuntimeError("PITR omitted unexpected acknowledged batches")
            measured.update(target_time=target, orders_after_fault=deleted,
                            orders_after_recovery=before_delete["orders"]["rows"],
                            expected_commits_before_delete=before_commits)
            logs = self.compose("logs", "--no-color", "recovery").stdout
            if "recovery stopping before commit" not in logs or "archive recovery complete" not in logs:
                raise RuntimeError("PITR stop not confirmed by PostgreSQL logs")
            (self.log_dir / "pitr.log").write_text(logs)
            report["scenarios"].append(measured)

            print("Exercise: damaged and incomplete copies", flush=True)
            for mode in ("corrupt-data", "missing-data", "missing-wal"):
                path = "/repository/failed/" + mode
                self.tool(f"mkdir -p {path}; cp -a /repository/backups/{pitr_backup}/. {path}/", writable=True)
                if mode == "corrupt-data":
                    damage = f"printf BAD | dd of={path}/global/pg_control bs=1 seek=128 conv=notrunc status=none"
                elif mode == "missing-data":
                    damage = f"rm {path}/global/pg_control"
                else:
                    damage = f"find {path}/pg_wal -maxdepth 1 -type f -delete"
                self.tool(damage, writable=True)
                result = self.tool(f"pg_verifybackup {path}", check=False)
                diagnostic = result.stdout + result.stderr
                (self.log_dir / (mode + ".log")).write_text(diagnostic)
                if result.returncode == 0:
                    raise RuntimeError("Damaged backup incorrectly accepted: " + mode)
                report["checks"].append({"name": mode, "status": "passed",
                                         "verifier_exit_code": result.returncode, "diagnostic": diagnostic.strip()[-1200:]})
                self.tool(f"rm -rf {path}", writable=True)
            self.verify(pitr_backup)

            self.backup()
            self.backup()
            ids = self.backup_ids()
            if len(ids) != 2 or pitr_backup in ids:
                raise RuntimeError("Retention did not keep exactly two full backups")
            expected = self.snapshot()
            self.restore(ids[0], base_only=True)
            self.assert_snapshot(expected)
            report["checks"].append({"name": "retention-and-oldest-restore", "status": "passed", "retained_full_backups": len(ids)})

            success = self.schedule_once()
            if success != 0:
                raise RuntimeError("Scheduled backup failed unexpectedly")
            self.container("db")
            self.compose("stop", "db")
            failure = self.schedule_once()
            if failure == 0:
                raise RuntimeError("Scheduled backup failure returned success")
            self.compose("up", "-d", "db")
            self.wait()
            self.verify()
            report["checks"].append({"name": "scheduled-backup-and-failure", "status": "passed",
                                     "success_exit_code": success, "failure_exit_code": failure})
            self.safety_negative_check()
            report["checks"].append({"name": "foreign-resource-refused", "status": "passed",
                                     "fixture_volume_preserved_on_refusal": True})
            report["status"] = "passed"
        except Exception as error:
            report["status"], report["error"] = "failed", str(error)
            raise
        finally:
            report["finished_at"] = utc()
            write_json(self.log_dir / "report.json", report)
            print("Evidence: " + str((self.log_dir / "report.json").relative_to(ROOT)))
        return report

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("command", choices=["up", "seed", "backup", "verify", "restore", "test", "check", "archive",
                                           "schedule", "status", "down", "clean"])
    parser.add_argument("--backup")
    parser.add_argument("--target")
    parser.add_argument("--base-only", action="store_true")
    parser.add_argument("--service", choices=["db", "recovery"], default="db")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=3600)
    parser.add_argument("--confirm-lab-only", action="store_true")
    args = parser.parse_args()
    state = LOCAL / "runs" / (uuid.uuid4().hex + ".json") if args.command == "test" else args.state
    lab = Lab(state)
    if args.command == "schedule":
        if args.interval < 1:
            parser.error("Interval must be positive")
        while True:
            code = lab.schedule_once()
            if args.once:
                return code
            print(f"{utc()} scheduled backup exit={code}", flush=True)
            time.sleep(args.interval)
    with lab.state_path.with_suffix(".lock").open("w") as lock:
        if args.command != "test":
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another operation owns this lab instance")
        if args.command == "up":
            lab.up()
        elif args.command == "seed":
            print(json.dumps(lab.seed(), indent=2))
        elif args.command == "backup":
            print(lab.backup())
        elif args.command == "verify":
            print(lab.verify(args.backup))
        elif args.command == "check":
            print(json.dumps(lab.snapshot(args.service), indent=2))
        elif args.command == "archive":
            print(lab.archive())
        elif args.command == "restore":
            print(lab.restore(args.backup, args.target, args.base_only))
            print(json.dumps(lab.snapshot("recovery"), indent=2))
        elif args.command == "test":
            try:
                print(json.dumps(lab.test(), indent=2))
            finally:
                lab.clean()
        elif args.command == "status":
            print(lab.compose("--profile", "recovery", "ps", "-a").stdout)
        elif args.command == "down":
            for service in ("db", "recovery"):
                if lab.compose("ps", "-a", "-q", service).stdout.strip():
                    lab.container(service)
            lab.compose("--profile", "recovery", "stop")
        elif args.command == "clean":
            if not args.confirm_lab_only:
                parser.error("Use --confirm-lab-only to delete this lab's named volumes")
            lab.clean()
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
