import logging
from collections.abc import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.config import settings, sanitize_database_url

logger = logging.getLogger("edvexa.database")

# Configure engine options according to database dialect
engine_kwargs = {
    "pool_pre_ping": True,
}

connect_args = {}

if settings.DATABASE_URL.startswith("sqlite"):
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
        Base.metadata.create_all(bind=engine)
        logger.info("Database schema initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing database schema: {e}")
        raise
