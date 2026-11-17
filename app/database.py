import logging
from collections.abc import Generator
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.config import settings, sanitize_database_url

logger = logging.getLogger("edvexa.database")

# Configure engine options according to database dialect
engine_kwargs = {
    "pool_pre_ping": True,
}

connect_args = {}

if settings.DATABASE_URL.startswith("sqlite"):
    import sqlite3
    import datetime
    sqlite3.register_adapter(datetime.date, lambda val: val.isoformat())
    sqlite3.register_adapter(datetime.datetime, lambda val: val.isoformat())
    connect_args["check_same_thread"] = False
    engine_kwargs["connect_args"] = connect_args
elif settings.DATABASE_URL.startswith("postgresql"):
    # PostgreSQL / Neon optimizations
    engine_kwargs["pool_size"] = 5
    engine_kwargs["max_overflow"] = 10
    engine_kwargs["pool_recycle"] = 300
    engine_kwargs["connect_args"] = connect_args

logger.info(f"Configuring database engine for: {sanitize_database_url(settings.DATABASE_URL)}")

engine = create_engine(
    settings.DATABASE_URL,
    **engine_kwargs
)

if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys = ON")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db() -> Generator[Session, None, None]:
    """Provide a transactional database session scope."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db() -> None:
    """Create all registered database tables if they do not exist."""
    import app.models  # Ensure all models are registered
    try:
        with engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(286456239001)"))
            elif connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                _upgrade_legacy_sqlite(connection)
            Base.metadata.create_all(bind=connection)
        logger.info("Database schema initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing database schema: {e}")
        raise


def _upgrade_legacy_sqlite(connection):
    """Repair the previous standalone SQL bootstrap without discarding its records."""
    from app.models import SurveyQuestionMapping, SurveyReviewEvent

    inspector = inspect(connection)
    if inspector.has_table("survey_question_mappings"):
        columns = {col["name"] for col in inspector.get_columns("survey_question_mappings")}
        if "mapping_status" not in columns:
            connection.exec_driver_sql("ALTER TABLE survey_question_mappings ADD COLUMN mapping_status VARCHAR(30) NOT NULL DEFAULT 'SUGGESTED'")
        if "qc_instructions" not in columns:
            connection.exec_driver_sql("ALTER TABLE survey_question_mappings ADD COLUMN qc_instructions TEXT")
        # Older SQL used a different name for the single-column index.
        if "ix_sqm_audit_qnum" not in {index["name"] for index in inspector.get_indexes("survey_question_mappings")}:
            next(index for index in SurveyQuestionMapping.__table__.indexes if index.name == "ix_sqm_audit_qnum").create(connection)
    if inspector.has_table("survey_review_events"):
        key_type = connection.exec_driver_sql("PRAGMA table_info(survey_review_events)").fetchone()[2].upper()
        if key_type == "SERIAL":
            connection.exec_driver_sql("ALTER TABLE survey_review_events RENAME TO survey_review_events_legacy")
            connection.exec_driver_sql("DROP INDEX IF EXISTS ix_survey_review_events_audit_id")
            connection.exec_driver_sql("DROP INDEX IF EXISTS ix_survey_review_events_respondent_id")
            SurveyReviewEvent.__table__.create(connection)
            connection.exec_driver_sql("""
                INSERT INTO survey_review_events
                    (id, audit_id, respondent_id, previous_disposition, new_disposition, reviewer, reason, created_at)
                SELECT COALESCE(id, (SELECT COALESCE(MAX(id), 0) FROM survey_review_events_legacy)
                    + ROW_NUMBER() OVER (ORDER BY rowid)),
                    audit_id, respondent_id, previous_disposition, new_disposition, reviewer, reason, created_at
                FROM survey_review_events_legacy
            """)
            connection.exec_driver_sql("DROP TABLE survey_review_events_legacy")
