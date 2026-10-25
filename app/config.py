import os
import re
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env if present
load_dotenv(BASE_DIR / ".env")

def normalize_database_url(url: str) -> str:
    """
    Normalize database connection URLs for SQLAlchemy compatibility:
    - Replaces deprecated 'postgres://' schema with 'postgresql://' (required by SQLAlchemy 1.4/2.0+).
    - Ensures PostgreSQL connections specify sslmode=require for cloud providers like Neon if not present.
    """
    if not url:
        return f"sqlite:///{BASE_DIR / 'edvexa_data.db'}"

    trimmed = url.strip()
    if trimmed.startswith("postgres://"):
        trimmed = "postgresql://" + trimmed[len("postgres://"):]

    # For PostgreSQL URLs without sslmode, append sslmode=require unless running locally
    if trimmed.startswith("postgresql://") and "sslmode" not in trimmed and "localhost" not in trimmed and "127.0.0.1" not in trimmed:
        separator = "&" if "?" in trimmed else "?"
        trimmed = f"{trimmed}{separator}sslmode=require"

    return trimmed

def sanitize_database_url(url: str) -> str:
    """Redact passwords from database URLs for safe logging."""
    if not url:
        return ""
    # Matches user:password@host
    return re.sub(r":([^:@]+)@", ":****@", url)

class Settings:
    PROJECT_NAME: str = "Edvexa Data Quality & ETL Monitor"
    PROJECT_SUBTITLE: str = "Reliable Data. Clean Pipelines. Trusted Decisions."
    VERSION: str = "2.6.0"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development").lower()
    
    # Database
    DATABASE_URL: str = normalize_database_url(os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'edvexa_data.db'}"))
    
    # Uploads & Storage Abstraction
    UPLOAD_MAX_SIZE_BYTES: int = int(os.getenv("UPLOAD_MAX_SIZE_BYTES", str(10 * 1024 * 1024)))  # 10 MB
    ALLOWED_EXTENSIONS: set[str] = {"csv"}
    UPLOADS_DIR: Path = BASE_DIR / "uploads_storage"
    
    # Storage Backend ('local' or 'r2')
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "local").lower()
    R2_ACCOUNT_ID: str | None = os.getenv("R2_ACCOUNT_ID")
    R2_BUCKET_NAME: str | None = os.getenv("R2_BUCKET_NAME")
    R2_ACCESS_KEY_ID: str | None = os.getenv("R2_ACCESS_KEY_ID")
    R2_SECRET_ACCESS_KEY: str | None = os.getenv("R2_SECRET_ACCESS_KEY")
    R2_ENDPOINT_URL: str | None = os.getenv("R2_ENDPOINT_URL")

    @property
    def effective_r2_endpoint(self) -> str | None:
        """Derive the official S3-compatible Cloudflare R2 endpoint URL."""
        if self.R2_ENDPOINT_URL:
            return self.R2_ENDPOINT_URL.strip()
        if self.R2_ACCOUNT_ID:
            return f"https://{self.R2_ACCOUNT_ID.strip()}.r2.cloudflarestorage.com"
        return None

    @property
    def is_r2_configured(self) -> bool:
        """Check if all necessary Cloudflare R2 credentials and bucket details are present."""
        return bool(
            self.R2_BUCKET_NAME
            and self.R2_ACCESS_KEY_ID
            and self.R2_SECRET_ACCESS_KEY
            and (self.R2_ACCOUNT_ID or self.R2_ENDPOINT_URL)
        )

    # Security & Destructive Action Safeguards
    ADMIN_RESET_TOKEN: str | None = os.getenv("ADMIN_RESET_TOKEN")
    ALLOW_DESTRUCTIVE_RESET: bool = (
        os.getenv(
            "ALLOW_DESTRUCTIVE_RESET",
            "false" if os.getenv("ENVIRONMENT", "development").lower() == "production" else "true"
        ).lower() in ("true", "1", "yes")
    )

    # CORS Configuration
    CORS_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "*").split(",")
        if origin.strip()
    ]

    # Supported Datasets & Business Rules
    SUPPORTED_DATASETS: list[str] = ["customers", "sales", "inventory"]
    SUPPORTED_CURRENCIES: set[str] = {"USD", "EUR", "GBP", "CAD", "AUD", "JPY", "INR"}
    
    # Pagination
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

settings = Settings()

# Ensure uploads directory exists for local backend or local cache
settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
