# Shamrai Backup And Restore Drill

This runbook covers the canonical Shamrai VDS deployment only:

- Server: `root@82.147.67.245`
- App path: `/opt/shamrai-mini-app`
- Docker Compose project: `shamrai`
- Preview health: `http://127.0.0.1:8082/api/health`

Keep one canonical preview deployment. Do not create a second app path, Compose project, or preview port to solve conflicts.

## What Is Backed Up

Postgres is the recovery source of truth. The nightly job creates a custom-format `pg_dump`, encrypts it with `age`, stores a non-secret manifest with table counts and hashes/sizes for both plaintext and encrypted artifacts, and removes the unencrypted temp dump.

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
- `SHAMRAI_BACKUP_S3_ACCESS_KEY_ID=<runtime-only-access-key-id>`
- `SHAMRAI_BACKUP_S3_SECRET_ACCESS_KEY=<runtime-only-secret-access-key>`
- `SHAMRAI_BACKUP_S3_SESSION_TOKEN=<optional-runtime-only-session-token>`

Store these only at **GitHub repository → Settings → Secrets and variables → Actions → Repository secrets**. Do not create a project file for them; git-ignore status is therefore not applicable. Do not put them in `.env`, docs, commits, shell history, or chat.

Off-host endpoint configuration is non-secret and belongs at **GitHub repository → Settings → Secrets and variables → Actions → Variables**:

- `SHAMRAI_BACKUP_S3_BUCKET=<non-secret-bucket-name>`
- `SHAMRAI_BACKUP_S3_PREFIX=shamrai`
- `SHAMRAI_BACKUP_S3_ENDPOINT_URL=https://s3.example.invalid`
- `SHAMRAI_BACKUP_S3_REGION=us-east-1`

For the VDS backup process, static S3 credentials have no file path: do not create a credential file and do not add them to `/opt/shamrai-mini-app/ops/backup.env`. An approved secret manager or scheduler wrapper must inject `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and optional `AWS_SESSION_TOKEN` into the `backup-db.sh` process environment at execution time. The generated `backup.env` remains mode `0600`, outside git, and contains only the age public recipient plus non-secret backup/endpoint configuration.

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

## Optional Off-Host S3-Compatible Copy

Off-host upload is opt-in. With no `OffHostS3Bucket`/`SHAMRAI_BACKUP_S3_BUCKET`, backup behavior remains local-only and cron invokes `backup-db.sh` directly. A bucket enables remote upload as a required second stage: the local encrypted dump and manifest are committed first, then the encrypted object is uploaded and verified, and the manifest is uploaded last as the remote commit marker. Missing runtime credentials or any remote error still runs normal local retention, preserves the newest completed local pair, and then returns the saved non-zero status.

Safe command-generation check (no SSH, VDS, Docker, or S3 request):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\configure-shamrai-backups.ps1 `
  -DryRun `
  -BackupAgeRecipient age1replacewithpublicrecipient `
  -OffHostS3Bucket example-backups `
  -OffHostS3Prefix shamrai `
  -OffHostS3EndpointUrl https://s3.example.invalid `
  -OffHostS3Region us-east-1 `
  -OffHostCredentialWrapper /usr/local/sbin/shamrai-backup-with-runtime-credentials
```

To install only the non-secret remote configuration, pass the same parameters without `-DryRun` and use the real public age recipient. The configure script never accepts or forwards S3 credentials. A non-dry-run off-host configuration fails before SSH unless `-OffHostCredentialWrapper` is explicit. Dry-run without it is allowed only to preview object names and reports `off_host_schedule=blocked_missing_credential_wrapper`.

The credential wrapper is a non-secret absolute executable path, for example `/usr/local/sbin/shamrai-backup-with-runtime-credentials`. It must already exist on the VDS, be a regular non-symlink file owned by root, and neither it nor any parent directory may be group/world writable. Cron invokes it as:

```text
/usr/local/sbin/shamrai-backup-with-runtime-credentials -- /opt/shamrai-mini-app/ops/backup-db.sh
```

The wrapper contract is to obtain short-lived, prefix-limited credentials from the approved runtime secret provider, export the variables below only for the child process, and then `exec` the command after `--`. The wrapper must not contain credential values. The configure script verifies the path and ownership/mode chain before replacing cron, and `-RunBackupNow` uses the same wrapper in off-host mode.

Runtime credential contract for the generated backup process:

```text
AWS_ACCESS_KEY_ID=<runtime-only-access-key-id>
AWS_SECRET_ACCESS_KEY=<runtime-only-secret-access-key>
AWS_SESSION_TOKEN=<optional-runtime-only-session-token>
```

Use a dedicated bucket principal limited to `s3:PutObject`, `s3:GetObject` (also used by `HeadObject`), and prefix-scoped `s3:ListBucket`. Do not grant `s3:DeleteObject`, lifecycle administration, bucket policy administration, or Object Lock bypass to the backup process. Never enable shell tracing (`set -x`) around the credential injection wrapper.

Integrity sequence:

1. Create the plaintext custom-format dump in a mode-`0700` temporary directory.
2. Encrypt it with `age` into a temporary `.dump.age`.
3. Compute the encrypted SHA-256 and byte length, then create manifest schema v2.
4. Validate the manifest JSON, install the local ciphertext, then install the local manifest before attempting off-host upload.
5. Upload ciphertext with SHA-256 object metadata and verify `HeadObject` size/hash.
6. Upload and verify the manifest last. Restore selection considers a manifest the commit marker.

`backup-db.sh` takes a non-blocking exclusive `flock` before timestamp generation. A concurrent cron/manual writer is rejected instead of sharing object names or overwriting a manifest/ciphertext pair.

These hashes detect corruption and mismatched object pairs; they are not an independent proof of backup-writer identity because an `age` recipient is public. Provenance therefore also depends on least-privilege write credentials, provider audit logs, versioning, and immutable retention. For a higher-assurance tier, add a separately protected manifest-signing key through the secret manager before treating signatures as authoritative.

Off-host retention is provider-side; the backup script deliberately has no remote delete permission. Configure and periodically audit:

- bucket versioning before the first upload;
- Object Lock/WORM or an equivalent immutable retention policy where the provider supports it;
- lifecycle retention that meets the recovery policy (for example, current daily objects for at least 35 days and non-current versions for at least 90 days);
- lifecycle rules that keep each `.dump.age` and matching `.manifest.json` for the same duration;
- a separate administrative identity for retention changes and restores.

Failure alerts are mandatory when off-host mode is enabled. Alert on a non-zero backup exit, the `shamrai-backup` syslog tag, or the absence of a newly committed remote manifest within 26 hours. Keep `/opt/shamrai-mini-app/ops/backup.log` under log rotation and route cron/monitoring failures to the on-call channel. A local success without a recent remote manifest is a degraded backup, not a successful off-host backup.

## Weekly Restore Drill

Manual run from the operator machine:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-shamrai-restore-drill.ps1
```

The script:

1. Selects the newest encrypted backup from the VDS by default, or the newest committed off-host daily manifest when `-ArtifactSource S3` is explicit.
2. Downloads both objects to `.deploy/restore-drill-*` from the selected source.
3. For off-host objects, verifies S3 size/hash metadata and manifest schema v2 ciphertext hash/size before decryption.
4. Decrypts locally with `C:\Users\Sa1z1ngr0z\.config\age\shamrai-backup-identity.txt` and verifies the decrypted dump SHA-256 against the manifest.
5. Uploads the decrypted dump to a temporary `0700` stage under `/tmp`.
6. Starts a temporary Compose project named `shamrai-restore-drill-*` on localhost-only ports `18000` and `18082`.
7. Restores Postgres, compares manifest table counts, verifies Redis, backend health, frontend HTML, and canonical production health.
8. Deletes the temporary Compose project, volumes, and stage unless `-KeepDrillProject` is passed.

Use an explicit backup when needed:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-shamrai-restore-drill.ps1 -BackupFile /opt/shamrai-mini-app/db-backups/daily/shamrai-db.YYYYMMDDTHHMMSSZ.dump.age
```

Preview selection of the newest off-host artifact without network access:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-shamrai-restore-drill.ps1 `
  -DryRun `
  -ArtifactSource S3 `
  -OffHostS3Bucket example-backups `
  -OffHostS3Prefix shamrai `
  -OffHostS3EndpointUrl https://s3.example.invalid `
  -OffHostS3Region us-east-1
```

For a real off-host drill, inject `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and optional `AWS_SESSION_TOKEN` into the current process from the approved secret store, omit `-DryRun`, and keep `-ArtifactSource S3`. To select one immutable object explicitly, add:

```powershell
-OffHostArtifactKey shamrai/daily/shamrai-db.YYYYMMDDTHHMMSSZ.dump.age
```

The key must match the configured prefix and generated timestamped daily key format; traversal-like or unrelated keys are rejected. Off-host selection lists committed manifests, not orphan ciphertext uploads. The restore remains an isolated drill and still requires explicit SSH access to the canonical target; it never changes production by default.

GitHub Actions runs the VDS-source drill weekly via `.github/workflows/restore-drill.yml`. A manual `workflow_dispatch` can select `artifact_source=s3` and optionally provide `off_host_artifact_key`; only that S3 job step receives the off-host credential secrets.

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
- Off-host upload fails after local commit: preserve the local pair, check the non-secret endpoint/bucket/prefix configuration, confirm the scheduler injected the required runtime credential variables, and inspect the provider audit log. Never print or copy the credential values into the ticket.
- No recent off-host manifest: treat ciphertext without its matching manifest as uncommitted; investigate alerting, bucket lifecycle, versioning/Object Lock status, and the backup process exit code.
- Off-host metadata/hash mismatch: quarantine the selected object version, retain provider audit evidence, and drill an older committed version. Do not rewrite or delete the suspect version with the backup identity.
- Restore drill hash mismatch: treat the backup artifact as corrupt; keep the encrypted file and manifest for investigation and use an older backup.
- Restore drill count mismatch: the dump restored but data does not match the manifest; do not trust that artifact.
- Backend does not start in drill: inspect temporary backend logs before rerunning with cleanup, then compare app env requirements against the restore-drill Compose environment.
- Production health fails after drill: the drill should not mutate production. Check `docker compose -p shamrai ps`, canonical port `8082`, and recent production logs before touching nginx or unrelated services.
