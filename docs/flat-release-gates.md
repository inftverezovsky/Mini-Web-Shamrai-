# Flat Subscription Release Gates

Flat sales remain disabled until the exact release commit has both a green CI attestation and a completed hidden-plan purchase attestation. These files are generated evidence, live under the git-ignored `.deploy/` directory, and must never be committed.

## Exact-SHA CI Attestation

The final `release-attestation` job in `.github/workflows/ci.yml` runs only after these mandatory jobs succeed:

- backend tests and Python dependency audit;
- separate line-coverage gates of at least `80%` for `flat_subscriptions` and immutable `subscription_pricing` (each module is measured independently);
- frontend lint, unit tests, dependency audit, build, Chromium, and WebKit E2E;
- redacted Gitleaks history scan;
- backend/frontend Docker builds;
- PostgreSQL migration qualification from both an empty database and a seeded
  `20260802_0041` database, including migration-head, model-drift, immutable
  payment snapshot, reconciliation, unique-index, application-import, and
  database-readiness checks;
- real PostgreSQL concurrency tests;
- Docker Compose validation;
- PowerShell source and operations tests.

It uploads `ci-release-attestation-<full-commit-sha>` containing `.deploy/ci-release-attestation.json`. The JSON records the exact `github.sha`, UTC timestamp, repository/event/ref protection context, workflow run identity, and the success result of every mandatory job. Download the artifact from the matching GitHub Actions run; do not copy an attestation from another commit or edit it locally. Production release accepts only a successful `push` run from a protected branch.

An uploaded artifact means CI passed for that SHA. It does not prove that the backup/restore or hidden purchase gates passed.

The release migration chain is PostgreSQL-only. SQLite is supported only for
isolated ORM/unit tests; it is not a substitute for the PostgreSQL migration,
partial-index, locking, or readiness qualification above.

## Hidden Purchase E2E Attestation

Use a dedicated test client that is allowlisted for exactly one active hidden flat tariff. Keep public flat sales disabled. Complete the real provider scenario for that client:

1. create checkout with a fresh `Idempotency-Key`;
2. pay and wait for `PaymentAttempt.payment_status=succeeded`;
3. configure the initial flat amount;
4. take at least one prediction with a real stake amount;
5. settle the client stake and reach `completed` with no open stakes.

The test should use a fresh flat subscription. The verifier intentionally requires one `subscription_purchase` credit whose target equals the immutable payment-attempt snapshot.

Obtain a short-lived JWT for that test client through the approved runtime secret/session process. Put it only in the current PowerShell process:

```powershell
$env:SHAMRAI_HIDDEN_GATE_JWT = '<runtime-test-client-jwt>'
$releaseSha = (git rev-parse HEAD).Trim().ToLowerInvariant()
$ciEvidence = Get-Content -Raw .\.deploy\ci-release-attestation.json | ConvertFrom-Json
$minimumEvidenceTime = [datetimeoffset]::Parse([string]$ciEvidence.verified_at)

powershell -ExecutionPolicy Bypass -File .\scripts\verify-shamrai-hidden-flat-e2e.ps1 `
  -PaymentAttemptId '<paid-payment-attempt-uuid>' `
  -HiddenPlanId 123 `
  -ExpectedGitSha $releaseSha `
  -MinimumEvidenceTimeUtc $minimumEvidenceTime

Remove-Item Env:\SHAMRAI_HIDDEN_GATE_JWT
```

`SHAMRAI_HIDDEN_GATE_JWT` is runtime-only. Do not create a file for it, do not add it to `backend/.env`, and do not put it in GitHub variables, logs, tickets, docs, or chat. If an automation later runs this verification, store the value as a GitHub Actions **Secret**, not a variable.

The verifier:

- accepts HTTPS only, except loopback HTTP for local tests;
- disables redirects so the authorization header cannot be forwarded to another origin;
- checks `/api/version` against the required full commit SHA and build timestamp;
- confirms the authenticated client can see the expected plan and that it is active, flat, and hidden;
- confirms the exact positive-RUB YooKassa/Tegro attempt is `succeeded`, checkout state is `completed`, setup is complete, and the immutable target snapshot matches its purchase credit;
- confirms the current subscription is `completed`, profit reached the target, no stake remains open, and every client stake is settled;
- removes any stale attestation before checking and after every failure;
- writes only non-secret evidence to `.deploy/hidden-flat-e2e-attestation.json` using an atomic replacement.

The attestation contains the release SHA/build time, attempt/provider identifiers, hidden plan and flat subscription identifiers, revision, target/profit, and settled-stake count. It never contains the JWT, user ID, checkout URL, provider payload, or credentials.

## Release Rule

Do not activate a public flat tariff merely because either file exists. A local JSON file is only a bootstrap pointer. The deploy script authenticates to GitHub, downloads the immutable artifact from the referenced workflow run, and verifies its repository, workflow path, trigger, run attempt, exact SHA, and contents. The hidden purchase file is also not trusted by itself: immediately before a public release, the deploy script reruns the verifier against the live API with the runtime test-client JWT and atomically replaces the file.

Set the GitHub token only in the current deployment process. It needs read access to Actions artifacts for this repository:

```powershell
$env:GITHUB_PERSONAL_ACCESS_TOKEN = '<runtime-actions-read-token>'
# Run the gated preview/public commands below.
Remove-Item Env:\GITHUB_PERSONAL_ACCESS_TOKEN
```

Never put that token in the repository, `.env`, PowerShell profile, command arguments, docs, tickets, or chat.

The safe two-phase sequence for one exact commit is:

1. Merge/push the clean commit to the protected default branch and download its successful CI attestation.
2. Run the protected `Restore drill` workflow for that same SHA and download both S3 retention and restore attestations into `.deploy/`.
3. Deploy the exact SHA with `-PreviewOnly`. The command stops only the backend, creates a new signed encrypted S3 backup, and restore-drills that exact artifact with the local `age` identity before it may run Alembic. A backup or restore failure restarts the previous backend without running migrations. The successful command updates the canonical application/API and migrations on port `8082`, verifies preview health, plans, statistics, and the migration head, but does not publish new frontend files to the public web root. Public flat purchases stay disabled.
4. Complete the real hidden-plan purchase and run the hidden verifier for that exact SHA.
5. Run the same deploy command without `-PreviewOnly`; it performs the live hidden recheck, repeats the quiesced backup/restore gate, verifies the preview APIs, and only then publishes the frontend.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy-public-shamrai-web.ps1 -PreviewOnly -RepairShamraiConflicts
powershell -ExecutionPolicy Bypass -File .\scripts\deploy-public-shamrai-web.ps1 -RepairShamraiConflicts
```

Before production release, the deploy gate must independently verify that:

- CI, restore, and hidden E2E evidence have their required current schema; S3 capability evidence has `schema_version=3`; all have `status=passed`;
- CI and hidden E2E attestations contain the exact clean commit SHA being released;
- timestamps satisfy the release runbook's freshness limits;
- the S3 retention and successful isolated restore attestations also pass;
- the hidden tariff remains hidden and public flat sales remain feature-flagged off until final monitoring begins.

Attestations are immutable evidence for one release attempt. A code change, failed retry, changed plan, corrected stake, or repeated purchase requires a fresh CI run and, where it affects the scenario, a fresh hidden purchase verification.
