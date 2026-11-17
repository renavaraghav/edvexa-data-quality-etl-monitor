# Edvexa release validation

The GitHub Actions job **Survey platform regressions** runs isolated tests with synthetic questionnaires and respondent data. It covers extraction, mapping, QC, disposition accounting and workbook exports across different input sizes, plus survey API, storage, security and SQLite regressions.

For a protected `main` branch, require **Survey platform regressions** on the candidate revision and use that result when authorizing a deployment of the same commit. The workflow runs for pull requests to `main`, push events on `main`, and merge queues. GitHub branch protection and Render deployment policies are managed separately.

The [BHTT inventory notes](../BHTT_INVENTORY_STATUS.md) document evidence for one sample questionnaire. Its local characterization and browser tests require the original DOCX. General CI uses repository tests and synthetic inputs.

## Disposable PostgreSQL verification

Use a loopback-only PostgreSQL server and a dedicated database named `edvexa_test_*`. The integration tests create and remove random schemas in that database. Use synthetic records and local temporary file storage. Never point this suite at a shared or production database.

```powershell
$env:EDVEXA_TEST_POSTGRES_URL = 'postgresql+psycopg://test_user@127.0.0.1:55432/edvexa_test_release'
python -m pytest -q tests/test_postgres_survey_integration.py tests/test_survey_sql_bootstrap.py
```

PostgreSQL uses SQLAlchemy startup initialization. `schema.sql` targets SQLite only. The PostgreSQL checks cover clean startup, repeat and concurrent upgrade, transaction rollback, concurrent review/reset, stable export snapshots, pagination, and an indexed query plan. A hosted replica's TLS, credentials, pool settings, and object storage need separate pre-release verification in an approved nonproduction environment.
