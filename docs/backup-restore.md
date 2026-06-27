# Shamrai Backup And Restore Drill

This runbook covers the canonical Shamrai VDS deployment only:

- Server: `root@82.147.67.245`
- App path: `/opt/shamrai-mini-app`
- Docker Compose project: `shamrai`
- Preview health: `http://127.0.0.1:8082/api/health`

Keep one canonical preview deployment. Do not create a second app path, Compose project, or preview port to solve conflicts.

## What Is Backed Up

Postgres is the recovery source of truth. The nightly job creates a custom-format `pg_dump`, encrypts it with `age`, stores a non-secret manifest with table counts, and removes the unencrypted temp dump.

Redis is not restored from backup. The restore drill starts Redis clean and verifies `redis-cli ping`; critical outbox, payments, subscriptions, users, delivery permissions, and payment states live in Postgres.

Retention:

- `14` daily encrypted backups in `/opt/shamrai-mini-app/db-backups/daily`
- `8` weekly encrypted backups in `/opt/shamrai-mini-app/db-backups/weekly`
- `latest.dump.age` and `latest.manifest.json` symlinks point to the newest daily backup

## Secret Placement

VDS backup config:

- Path: `/opt/shamrai-mini-app/ops/backup.env`
- Create if missing: yes, via `scripts/configure-shamrai-backups.ps1`
- Git ignored: it is outside the repository and must never be copied into git
- Mode: `0600`
- Safe placeholder:

```bash
SHAMRAI_BACKUP_AGE_RECIPIENT=age1replacewithpublicrecipient
```

The value is an `age` public recipient, not a private key. The script refuses to run real backups with the placeholder.

Operator private age identity:

- Path: `C:\Users\Sa1z1ngr0z\.config\age\shamrai-backup-identity.txt`
- Create if missing: yes, manually on the operator machine only
- Git ignored: never store in this repository
- Safe placeholder:

```text
AGE-SECRET-KEY-1...
```

GitHub Actions secrets:

- `SHAMRAI_SSH_PRIVATE_KEY=...`
- `SHAMRAI_BACKUP_AGE_IDENTITY=...`

Store these only in GitHub repository Actions secrets. Do not put them in `.env`, docs, commits, shell history, or chat.

## Configure Nightly Backups

Run from the repository root:

```powershell
$env:SHAMRAI_BACKUP_AGE_RECIPIENT = 'age1replacewithrealpublicrecipient'
powershell -ExecutionPolicy Bypass -File .\scripts\configure-shamrai-backups.ps1
Remove-Item Env:\SHAMRAI_BACKUP_AGE_RECIPIENT
```

To install the cron job and immediately create a first backup:

```powershell
$env:SHAMRAI_BACKUP_AGE_RECIPIENT = 'age1replacewithrealpublicrecipient'
powershell -ExecutionPolicy Bypass -File .\scripts\configure-shamrai-backups.ps1 -RunBackupNow
Remove-Item Env:\SHAMRAI_BACKUP_AGE_RECIPIENT
```

The script inventories Docker containers, Compose labels, ports, disk space, and the canonical path before writing:

- `/opt/shamrai-mini-app/ops/backup-db.sh`
- `/opt/shamrai-mini-app/ops/backup.env`
- `/etc/cron.d/shamrai-db-backup`

The cron schedule is `02:30 Europe/Moscow` nightly.

## Weekly Restore Drill

Manual run from the operator machine:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-shamrai-restore-drill.ps1
```

The script:

1. Finds the newest encrypted backup and manifest.
2. Downloads both to `.deploy/restore-drill-*`.
3. Decrypts locally with `C:\Users\Sa1z1ngr0z\.config\age\shamrai-backup-identity.txt`.
4. Verifies the decrypted dump SHA-256 against the manifest.
5. Uploads the decrypted dump to a temporary `0700` stage under `/tmp`.
6. Starts a temporary Compose project named `shamrai-restore-drill-*` on localhost-only ports `18000` and `18082`.
7. Restores Postgres, compares manifest table counts, verifies Redis, backend health, frontend HTML, and canonical production health.
8. Deletes the temporary Compose project, volumes, and stage unless `-KeepDrillProject` is passed.

Use an explicit backup when needed:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-shamrai-restore-drill.ps1 -BackupFile /opt/shamrai-mini-app/db-backups/daily/shamrai-db.YYYYMMDDTHHMMSSZ.dump.age
```

GitHub Actions runs the same drill weekly via `.github/workflows/restore-drill.yml` and can also be started manually with `workflow_dispatch`.

## Manual Production Restore

Only run this during an explicit maintenance window. This restores the canonical production database and must not touch system nginx or unrelated containers.

1. Create a fresh pre-restore backup:

```bash
cd /opt/shamrai-mini-app
install -d -m 0700 /opt/shamrai-mini-app/db-backups/manual
docker compose -p shamrai exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "/opt/shamrai-mini-app/db-backups/manual/pre-restore-$(date -u +%Y%m%dT%H%M%SZ).dump"
chmod 0600 /opt/shamrai-mini-app/db-backups/manual/pre-restore-*.dump
```

2. Decrypt the chosen encrypted backup on the operator machine:

```powershell
age -d -i C:\Users\Sa1z1ngr0z\.config\age\shamrai-backup-identity.txt -o .\.deploy\manual-restore.dump .\.deploy\backup.dump.age
```

3. Upload it to the VDS as a temporary file:

```powershell
scp -i C:\Users\Sa1z1ngr0z\.ssh\codex_deploy_ed25519 .\.deploy\manual-restore.dump root@82.147.67.245:/tmp/shamrai-manual-restore.dump
```

4. Restore and restart only Shamrai app services:

```bash
cd /opt/shamrai-mini-app
chmod 0600 /tmp/shamrai-manual-restore.dump
docker compose -p shamrai stop backend frontend
docker compose -p shamrai exec -T postgres sh -c 'pg_restore --clean --if-exists --no-owner --role="$POSTGRES_USER" --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < /tmp/shamrai-manual-restore.dump
rm -f /tmp/shamrai-manual-restore.dump
docker compose -p shamrai up -d backend frontend
docker compose -p shamrai ps
curl -fsS http://127.0.0.1:8082/api/health
```

## Failure Triage

- Backup fails before encryption: check `docker compose -p shamrai ps`, Postgres health, disk space, and `/opt/shamrai-mini-app/ops/backup.log`.
- Backup fails at encryption: check `age` is installed and `/opt/shamrai-mini-app/ops/backup.env` has a real `SHAMRAI_BACKUP_AGE_RECIPIENT`.
- Restore drill hash mismatch: treat the backup artifact as corrupt; keep the encrypted file and manifest for investigation and use an older backup.
- Restore drill count mismatch: the dump restored but data does not match the manifest; do not trust that artifact.
- Backend does not start in drill: inspect temporary backend logs before rerunning with cleanup, then compare app env requirements against the restore-drill Compose environment.
- Production health fails after drill: the drill should not mutate production. Check `docker compose -p shamrai ps`, canonical port `8082`, and recent production logs before touching nginx or unrelated services.
