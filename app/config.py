import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env if present
load_dotenv(BASE_DIR / ".env")

class Settings:
    PROJECT_NAME: str = "Edvexa Data Quality & ETL Monitor"
    PROJECT_SUBTITLE: str = "Reliable Data. Clean Pipelines. Trusted Decisions."
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'edvexa_data.db'}")
    
    # Uploads
    UPLOAD_MAX_SIZE_BYTES: int = int(os.getenv("UPLOAD_MAX_SIZE_BYTES", str(10 * 1024 * 1024)))  # 10 MB
    ALLOWED_EXTENSIONS: set[str] = {"csv"}
    UPLOADS_DIR: Path = BASE_DIR / "uploads_storage"
    
    # Supported Datasets & Business Rules
    SUPPORTED_DATASETS: list[str] = ["customers", "sales", "inventory"]
    SUPPORTED_CURRENCIES: set[str] = {"USD", "EUR", "GBP", "CAD", "AUD", "JPY", "INR"}
    
    # Pagination
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

settings = Settings()

# Ensure uploads directory exists
settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
