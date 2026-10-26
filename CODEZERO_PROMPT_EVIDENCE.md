# CodeZero Prompt and Iteration Evidence

**Project:** Edvexa Data Quality & ETL Monitoring Platform  
**Evidence date:** October 26, 2026  
**Purpose:** Separate evidence for the CodeZero workflow described in the assignment.

## Important note on authenticity

The repository did not contain an export of the original verbatim CodeZero prompt/chat transcript. The examples below are **reconstructed prompt examples based on the development activities recorded in the project notes and review report**. They are not presented as verbatim historical prompts, screenshots, or a transcript. The activity and outcomes below are tied to recorded repository references and test results; prompt wording is illustrative.

If the original CodeZero session is still available in the CodeZero interface, its actual transcript or screenshots should replace or supplement these reconstructed examples.

## Iteration 1 — Explore the starter exercise and frame the requirements

**Recorded activity:** CodeZero was used during initial exploration of the standalone `csv_quality_checker.py` exercise to examine baseline validation requirements and plan the initial architecture/README scope.

**Representative prompt (reconstructed; not verbatim):**

> Inspect the supplied `csv_quality_checker.py` starter exercise. Identify the validation rules and edge cases that are explicitly supported by the code. Separate confirmed behavior from assumptions, then produce a concise requirements checklist and a suggested architecture/README outline. Do not invent requirements that are not supported by the starter material.

**How this prompt supports quality:** It scopes the task to supplied evidence, asks for a distinction between facts and assumptions, and requests outputs that can be checked rather than accepting a vague implementation suggestion.

**Recorded references:** The technical notes record Git commit `7983368c3879efe141b90b7a269c1cb0882505b1` and Plan ID `6adcf6109cb64fb3236a5352` for the initial exploration/plan.

**Outcome recorded in the project:** Initial validation requirements and architecture/README planning were informed by this exploration. The starter CLI exercise is not part of the final web-app repository.

## Iteration 2 — Conduct an independent, read-only assignment review

**Recorded activity:** CodeZero reviewed the FastAPI app, CSV import and validation, transformations and ETL services, database models, dashboard endpoints/JavaScript, API and test modules, README, technical notes, schema DDL, and sample CSVs against the assignment requirements.

**Representative prompt (reconstructed; not verbatim):**

> Perform a read-only review of this repository against the assignment requirements. Inspect the upload, parsing, validation, transformation, ETL, persistence, dashboard, API, and test paths. For each issue, provide the file/function and concrete evidence, explain the impact, and recommend a minimal reproducible test. Do not edit application files. Clearly state what was and was not tested, including database and deployment limitations.

**How this prompt supports quality:** It explicitly prevents unrequested code changes, requires evidence and reproducible tests, and asks the review to state its verification limits.

**Recorded outcome:** The baseline run reported **99 passed tests and 17 deprecation warnings** using an in-memory SQLite test fixture. This did not verify live PostgreSQL or the deployed Render service. CodeZero did not modify application files during that review session.

## Iteration 3 — Probe the reprocessing/audit-record defect

**Recorded activity:** A targeted runtime probe investigated job reprocessing and its job-scoped clean-record export result.

**Representative prompt (reconstructed; not verbatim):**

> Trace the reprocessing path for a job that previously accepted at least one clean row. In an isolated test setup, record the job's accepted-row count and `JobCleanRecord` count before and after reprocessing. Check whether the business-table duplicate check incorrectly classifies the job's own previously accepted record as a new duplicate. Report the exact observed values and the relevant functions; do not change code during the probe.

**Recorded outcome:** The review notes report one clean audit row before reprocessing and zero afterward; the reprocessed job showed zero accepted and one duplicate. This pointed to a defect affecting the job-specific clean-record export.

## Iteration 4 — Apply and verify the follow-up separately

**Attribution boundary:** The CodeZero review itself did not change application code. A separate follow-up engineering pass applied fixes and added regression tests for reprocessing/export preservation, the trend-window query, bounded upload reads, malformed CSV row widths/multiline row positions, and invalid date filters.

**Recorded verification:** After pulling GitHub commit `1d45be9`, the project owner ran:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Result: **109 passed, 0 failed, 23 deprecation warnings in 19.91 seconds**. The test suite uses in-memory SQLite. This is a separate post-fix run; it must not be described as the original CodeZero test result or as live PostgreSQL verification.

## Summary of the workflow

| Iteration | Activity | Verification/result | Attribution |
|---|---|---|---|
| 1 | Inspect starter exercise and establish baseline validation/architecture scope | Recorded initial exploration commit and plan reference | CodeZero exploration |
| 2 | Read-only review against assignment and inspect existing tests | 99 passed / 17 warnings on the pre-fix in-memory SQLite baseline; review findings recorded | CodeZero review |
| 3 | Probe reprocessing and audit-row behavior | Runtime probe reported clean audit row disappearing after reprocessing | CodeZero review probe |
| 4 | Apply focused changes and rerun regression suite | 109 passed / 0 failed / 23 warnings locally | Separate follow-up engineering pass, not CodeZero |

## Credential and secret-handling check

A current-default-branch review was made while preparing this evidence:

- No `.env` or `.env.example` file is present in the current default-branch tree. Local `.env` files and `.env.*` variants are ignored by Git.
- Configuration values must be set locally in a private, untracked `.env` file or through the deployment provider's environment settings. No live database URLs, passwords, reset tokens, or object-storage keys belong in tracked documentation or source files.
- Test-only credential strings use explicit non-real `TEST_ONLY_..._NOT_REAL` markers.
- A targeted review of the current files inspected found no known provider-token value in those files. This is not a dedicated whole-repository secret scan. A full historical Git-object scan has **not** been completed.

**Important:** Never commit real database URLs, passwords, API keys, object-storage keys, reset tokens, private keys, or production `.env` files. If a genuine credential was ever pushed, rotate/revoke it even after removing it from the latest files; deleting a value from the current branch does not remove it from Git history.

## Related records

- [CodeZero Review](CODEZERO_REVIEW.md)
- [Technical Notes](TECHNICAL_NOTES.md)
- [README](README.md)
- [Demo Checklist](DEMO_CHECKLIST.md)
