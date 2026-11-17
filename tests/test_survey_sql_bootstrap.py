import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app import database
from app.models import SurveyQuestionMapping, SurveyReviewEvent


SCHEMA = Path(__file__).resolve().parents[1] / "schema.sql"


def _insert_run_and_review(connection):
    audit = connection.execute("INSERT INTO survey_audit_runs (raw_filename, raw_file_hash) VALUES (?, ?)",
                               ("synthetic.csv", "a" * 64)).lastrowid
    respondent = connection.execute("""
        INSERT INTO survey_respondents (audit_id, respondent_key, row_number, disposition, raw_data_json)
        VALUES (?, 'R1', 2, 'REVIEW_REQUIRED', '{}')
    """, (audit,)).lastrowid
    connection.execute("""
        INSERT INTO survey_review_events (audit_id, respondent_id, previous_disposition, new_disposition, reviewer, reason)
        VALUES (?, ?, 'REVIEW_REQUIRED', 'ACCEPTED', 'QA', 'Verified source')
    """, (audit, respondent))
    return audit


def test_actual_sql_bootstrap_is_repeatable_and_compatible(tmp_path):
    target = tmp_path / "bootstrap.sqlite3"
    with sqlite3.connect(target) as connection:
        for _ in range(2):
            connection.executescript(SCHEMA.read_text(encoding="utf-8"))
        audit = _insert_run_and_review(connection)
        assert connection.execute("SELECT id FROM survey_review_events").fetchall() == [(1,)]
        assert {row[2] for row in connection.execute("PRAGMA foreign_key_list(survey_review_events)")} == {
            "survey_audit_runs", "survey_respondents"}
        connection.execute("DELETE FROM survey_audit_runs WHERE id=?", (audit,))
        assert connection.execute("SELECT count(*) FROM survey_review_events").fetchone()[0] == 0
    engine = create_engine(f"sqlite:///{target}")
    inspector = inspect(engine)
    for table in database.Base.metadata.sorted_tables:
        assert {column["name"] for column in inspector.get_columns(table.name)} == set(table.columns.keys())
        checks = {(fk["constrained_columns"][0], fk["referred_table"]) for fk in inspector.get_foreign_keys(table.name)}
        assert checks == {(fk.parent.name, fk.column.table.name) for fk in table.foreign_keys}
    engine.dispose()


def test_previous_sql_schema_upgrade_preserves_mapping_and_review_records(tmp_path, monkeypatch):
    path = tmp_path / "legacy-bootstrap.sqlite3"
    legacy = SCHEMA.read_text(encoding="utf-8").replace(
        "    mapping_status VARCHAR(30) NOT NULL DEFAULT 'SUGGESTED',\n", ""
    ).replace("    qc_instructions TEXT,", "    is_verified INTEGER NOT NULL DEFAULT 0,")
    legacy = legacy.replace("CREATE TABLE IF NOT EXISTS survey_review_events (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,",
                            "CREATE TABLE IF NOT EXISTS survey_review_events (\n    id SERIAL PRIMARY KEY,")
    with sqlite3.connect(path) as connection:
        connection.executescript(legacy)
        audit = _insert_run_and_review(connection)
        connection.execute("""
            INSERT INTO survey_question_mappings (audit_id, question_number, question_text, question_type, mapped_columns_json)
            VALUES (?, 'Q1', 'Age?', 'numeric', '["Age"]')
        """, (audit,))
        assert connection.execute("SELECT id FROM survey_review_events").fetchone()[0] is None
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(database, "engine", engine)
    database.init_db()
    database.init_db()
    with Session(engine) as db:
        event = db.scalar(select(SurveyReviewEvent))
        mapping = db.scalar(select(SurveyQuestionMapping))
        assert (event.id, event.reason) == (1, "Verified source")
        assert (mapping.question_number, mapping.mapping_status) == ("Q1", "SUGGESTED")
    engine.dispose()
