# Edvexa release validation

**BHTT release acceptance is blocked.** The known DOCX supports 59 extracted candidates. The three unresolved positions require authoritative questionnaire evidence. The pytest characterization and the synthetic browser tests are separate from BHTT acceptance.

The gate uses `bhtt_approval.json` as a repository-owned approval record. After independent review of all 62 substantive entries, the record must contain the SHA-256 of the exact questionnaire DOCX and the SHA-256 of the approved inventory JSON, plus the reviewer's identity, ISO approval date, and a link to the review record. Each ordered inventory entry contains `id`, `classification` (`substantive`), `source_line`, `source_text_sha256` (UTF-8 extracted source block), `question_type`, and `evidence_reference`. The inventory's top-level `source_sha256` binds it to the DOCX. External specifications and decisions about distinct follow-up prompts need evidence references. Supplying a different inventory at the command line cannot change the approval record.

1. On GitHub, configure `main` branch protection or an active ruleset. Require pull requests, the exact **BHTT 62 substantive questions** status check from GitHub Actions, current approvals from independent owners for release policy and workflow changes, and up-to-date checks on the merged revision. For merge queues, require the check on `merge_group` as well. Prevent direct pushes and bypasses, including administrator bypasses. Protect the workflow and approval files with code ownership and independent approval.
2. After independent approval, an administrator sets the protected repository variable `BHTT_RELEASE_APPROVAL_SHA256` to the digest of the reviewed `release/bhtt_approval.json` bytes. Keep this outside contributor control. The workflow expects the DOCX and JSON at `release/evidence/bhtt.docx` and `release/evidence/bhtt_inventory.json`. For confidential evidence, arrange delivery from an authorized private source in the reviewed workflow before the check. Only publish evidence that has been approved for publication. A missing pin, missing artifact or failed comparison must return failure. Keep the job unconditional and preserve its exit status.
3. On Render, require successful checks for the exact commit, or turn off auto-deploy until release authorization. Also require `python -m scripts.verify_bhtt_inventory release/evidence/bhtt.docx --inventory release/evidence/bhtt_inventory.json --require-ci-pin` to succeed in the build/release path before serving traffic. Provide the same trusted pin and evidence there, using protected configuration for any confidential material. This build check also covers manually triggered deploys and hooks. Restrict deployment permissions and verify a negative check fails before considering the pipeline enforced.

These are setup instructions. The local changes do not install GitHub branch protections or modify the running Render service.

## Disposable PostgreSQL verification

Use a loopback-only PostgreSQL server and a dedicated database named `edvexa_test_*`. The integration tests create and remove random schemas in that database. Use synthetic records and local temporary file storage. Never point this suite at a shared or production database.

```powershell
$env:EDVEXA_TEST_POSTGRES_URL = 'postgresql+psycopg://test_user@127.0.0.1:55432/edvexa_test_release'
python -m pytest -q tests/test_postgres_survey_integration.py tests/test_survey_sql_bootstrap.py
```

PostgreSQL uses SQLAlchemy startup initialization. `schema.sql` targets SQLite only. The PostgreSQL checks cover clean startup, repeat and concurrent upgrade, transaction rollback, concurrent review/reset, stable export snapshots, pagination, and an indexed query plan. A hosted replica's TLS, credentials, pool settings, and object storage need separate pre-release verification in an approved nonproduction environment.
