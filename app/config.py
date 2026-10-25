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
    VERSION: str = "2.7.0"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development").lower()
    
    # Database
    DATABASE_URL: str = normalize_database_url(os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'edvexa_data.db'}"))

    @property
    def database_type(self) -> str:
        """Derive database dialect: 'postgresql' or 'sqlite'."""
        if "postgres" in self.DATABASE_URL.lower():
            return "postgresql"
        return "sqlite"
    
    # Uploads & Storage Abstraction
    UPLOAD_MAX_SIZE_BYTES: int = int(os.getenv("UPLOAD_MAX_SIZE_BYTES", str(10 * 1024 * 1024)))  # 10 MB
    ALLOWED_EXTENSIONS: set[str] = {"csv"}
    UPLOADS_DIR: Path = BASE_DIR / "uploads_storage"
    
    # Storage Backend ('local', 'neon', 's3', 'r2')
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "local").lower()
    
    # Neon Object Storage / S3-Compatible Credentials
    S3_ENDPOINT_URL: str | None = (
        os.getenv("NEON_STORAGE_ENDPOINT")
        or os.getenv("AWS_ENDPOINT_URL_S3")
        or os.getenv("S3_ENDPOINT_URL")
        or os.getenv("R2_ENDPOINT_URL")
    )
    S3_BUCKET_NAME: str = (
        os.getenv("NEON_STORAGE_BUCKET_NAME")
        or os.getenv("S3_BUCKET_NAME")
        or os.getenv("R2_BUCKET_NAME")
        or "edvexa-uploads"
    )
    S3_ACCESS_KEY_ID: str | None = (
        os.getenv("NEON_STORAGE_ACCESS_KEY_ID")
        or os.getenv("AWS_ACCESS_KEY_ID")
        or os.getenv("S3_ACCESS_KEY_ID")
        or os.getenv("R2_ACCESS_KEY_ID")
    )
    S3_SECRET_ACCESS_KEY: str | None = (
        os.getenv("NEON_STORAGE_SECRET_ACCESS_KEY")
        or os.getenv("AWS_SECRET_ACCESS_KEY")
        or os.getenv("S3_SECRET_ACCESS_KEY")
        or os.getenv("R2_SECRET_ACCESS_KEY")
    )
    S3_REGION_NAME: str = (
        os.getenv("NEON_STORAGE_REGION")
        or os.getenv("AWS_REGION")
        or "ap-southeast-1"
    )

    # Legacy Cloudflare R2 properties for backwards compatibility
    R2_ACCOUNT_ID: str | None = os.getenv("R2_ACCOUNT_ID")
    R2_BUCKET_NAME: str | None = os.getenv("R2_BUCKET_NAME", S3_BUCKET_NAME)
    R2_ACCESS_KEY_ID: str | None = os.getenv("R2_ACCESS_KEY_ID", S3_ACCESS_KEY_ID)
    R2_SECRET_ACCESS_KEY: str | None = os.getenv("R2_SECRET_ACCESS_KEY", S3_SECRET_ACCESS_KEY)
    R2_ENDPOINT_URL: str | None = os.getenv("R2_ENDPOINT_URL", S3_ENDPOINT_URL)

    @property
    def effective_s3_endpoint(self) -> str | None:
        """Derive the S3 endpoint URL (Neon Object Storage or generic S3)."""
        if self.S3_ENDPOINT_URL:
            return self.S3_ENDPOINT_URL.strip()
        if self.R2_ACCOUNT_ID:
            return f"https://{self.R2_ACCOUNT_ID.strip()}.r2.cloudflarestorage.com"
        return None

    @property
    def is_s3_configured(self) -> bool:
        """Check if S3 / Neon Object Storage credentials and bucket details are present."""
        return bool(
            self.S3_BUCKET_NAME
            and self.S3_ACCESS_KEY_ID
            and self.S3_SECRET_ACCESS_KEY
            and self.effective_s3_endpoint
        )

    @property
    def is_r2_configured(self) -> bool:
        """Backwards compatibility alias for is_s3_configured."""
        return self.is_s3_configured

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
