# BHTT Wave 3B questionnaire evidence

**Release gate: blocked.** The mandatory acceptance criterion remains 62 substantive questions. The current Word evidence supports 59 extracted candidates and seven excluded blocks. Three inventory positions are unresolved: no source establishes their identities, wording, response structures, or inclusion as standalone questions. This report does not assign question IDs to those positions.

## Source questionnaire and inventory

The inspected source is `C:/Users/rjha1/Downloads/2026.10.29_INV BHTT_W3B(1).docx`, SHA-256 `73d513c37ceee7bd9be432f56994ba387b00b6fe14d3e352f4cddef4ca0ae60e`.

- `C:/Users/rjha1/Downloads/2026.10.29_INV%20BHTT_W3B%281%29.docx` and the Edvexa cache `surveys_storage/qq_73d513c37cee_2026.10.29_INV BHTT_W3B(1).docx` are copies of that source.
- `C:/Users/rjha1/Downloads/2026.10.29_INV BHTT_W3B.docx` has SHA-256 `e38afd7321b54871d9cc9115495174fbd6dea9a6cb6f41ceed4d4a0950148eb6`. It is also present in the MANAGER root and reusable project assets. It produced the same original extracted identifiers; it is not an independent specification.
- The local, Git-ignored `surveys_storage/bhtt_w3b_candidate_inventory.json` contains text, normalized source-line locations, classifications, matrix/choice structures, dependent fields and exclusions. It can be regenerated without accessing respondent data.

The former 62-block count included embedded/derived fields and instructions. The seven exclusions are `Q.WAVE_MonthYear`, the `PS2` wrapper, `Q.StartText`, `Q.AwarenessBlockText`, `Q.Past_Current_Usage`, `Q.Past_Current_Usage_Net`, and `Q.ProfilingIntro`. The parser retains the three actual prompts in the PS2 example under source-linked child IDs and recovers `Q_Postal Code` as its own question. Vendor wording and wrapper instructions are excluded.

## Other material located

| Location | Files and use |
|---|---|
| Downloads | `BHTT_W3B_Independent_Questionnaire_Coverage_Review.md`, `BHTT_W3B_Copilot_Audit.md`: questionnaire-to-tabulation gap analyses, not complete question inventories. |
| Downloads | `BHTT_W3B_README.md`, `BHTT_W3B_STAGE1_BASELINE_REPORT.md`, `BHTT_W3B_QA_NOT_FINAL.md`, `2026.11.06_BHTT_W3B_QA_Audit.md`, `2026.11.06_BHTT_W3B_QA_Audit_FINAL.md`, `2026.11.06_BHTT_US_W3B_SOA_QA_REPORT.md`, `2026.11.06_BHTT_US_W3B_FINAL_CLEANUP_QA.md`: reproduction, reconciliation and result QA. |
| Downloads / MANAGER output folders | `2026.06_29 BHTT_US_ALL.xlsx`, `2026.06.30_BHTT_US_Tables(1).xlsx`, `2026.11.06_BHTT_US_Tables_W3B.xlsx` and its FINAL, FINAL(3), FINAL(3) (1), FINAL_FORMATTED and SOA_CORRECTED variants, and `BHTT_Manager_Format_W1_W2_W3_W3B_704_FINAL.xlsx`: historical/template/output workbooks. The independent review warns that output coverage is incomplete. |
| Downloads / Edvexa cache | `2026.11.06_BHTT Data.csv`, `2026.11.06_BHTT Data(2).csv`, `BHTT_W3B_RawData_QQ_Recode_Copy.csv` and respondent-reconciliation CSV/ZIP files: response data or derived records, not independent questionnaire specifications. Real respondent values were not used in the test suite. |
| `Downloads/MANAGER/outputs/bhtt_reusable_20261111/` | `BHTT_W3B_Reusable_Project.zip`: its `MAPPING.md`, `assets/config.json`, `assets/table_rules.json`, and `bhtt_w3b/spec.py` bind fields to particular output tables. The asset manifest lists the known questionnaire, historical workbook, output template, master output and QA files, not the missing Teen Global or Moms specification. |
| `Downloads/MANAGER/bhtt_work/` | `soa_20261110/questionnaire_paragraphs.json` and `complete_20261111/questionnaire.txt`: transcriptions, not independent evidence. `complete_20261111/verified_project/` contains the same table mapping and assets identified above. |
| GitHub | Branches `main` (`0e44f31`), `feature/survey-data-scrubbing-module` (`7330264`), and `fix/survey-qq-parsing-and-qc-rules` (`50e4f04`). [PR #1](https://github.com/renavaraghav/edvexa-data-quality-etl-monitor/pull/1) merged; [PR #2](https://github.com/renavaraghav/edvexa-data-quality-etl-monitor/pull/2) open. Tracked history and the PR heads contain parser/tests and the synthetic CSV; no original questionnaire, Excel questionnaire specification, or approved inventory was found. |

The workspace and the accessible Downloads, Documents, Desktop and OneDrive locations were searched by relevant filenames. The BHTT archives were checked by entry names and the reusable project's asset manifest was checked by purpose.

Two records use the number 62, but neither establishes the substantive baseline:

- `Downloads/MANAGER/bhtt_work/complete_20261111/verified_project/assets/config.json` contains a 62-entry `questionnaire_index`. Each entry is a paragraph number and text from the same DOCX. The index includes `Q.WAVE_MonthYear`, `Q.IntroThanks`, display blocks, the derived usage net, and a `Q.WhenPastTreatment QC CHECK` instruction. It omits `Q_Postal Code` and the nested parent prompts. It is a derived paragraph index, not an approved question list.
- `C:/Users/rjha1/.gemini/antigravity/brain/e10dc557-49d6-48d4-a0cb-fe1231e7a287/survey_data_scrubbing_local_verification_report.md` reported a 62-substantive pass, with companion browser screenshots and scratch code in that directory. Its cited parser test asserted a minimum of 50 and the browser counted unfiltered rows. This explains the historical claim without verifying it.

OneDrive had another `BHTT_Manager_Format_W1_W2_W3_W3B_704_FINAL.xlsx` under `OneDrive/Desktop/OneDrive/`, an output workbook. No matching reference was located in Documents.

## Information needed to close the gate

1. **Unresolved inventory positions 60, 61, 62:** supply source IDs, exact prompts, response structure, routing, and evidence they are three distinct substantive questions. There are no validated identities for these slots.
2. **External references:** establish the correct Teen Global and Moms workbook versions. They are cited for countries, gender, market age eligibility, and profiling/geographic items. The currently counted draft entries that reference those files are not proof of the missing three.
3. **Conditional fields:** the sports-team, musical-instrument, and Other follow-ups under `Q.Activities` are preserved as dependent fields. Equivalent follow-ups also exist elsewhere. A programming codebook or approved inventory must establish which, if any, count independently before promoting them.

To reproduce the evidence, set `BHTT_QQ_PATH` to the local Word file for pytest, or run:

```powershell
python -m scripts.verify_bhtt_inventory "<local BHTT DOCX>" --show-extraction
```

The verifier checks the exact source hash against the repository approval record, which currently says `blocked`. Passing the 59-candidate regression does not pass release acceptance. See [the release procedure](release/README.md) for approval provenance, CI enforcement and the deployment check.
