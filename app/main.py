import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.config import settings
from app.database import get_db, init_db
from app.models import utc_now
from app.schemas import HealthResponse
from app.routers import uploads, jobs, errors, records, dashboard

# Setup structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("edvexa.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize database schema
    logger.info("Initializing database schema...")
    init_db()
    logger.info("Edvexa Data Quality & ETL Monitor ready.")
    yield
    # Shutdown
    logger.info("Application shutting down.")

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Real-time CSV validation, automated ETL pipelines, and data quality governance monitoring.",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static assets
static_path = Path(__file__).parent / "static"
static_path.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Mount API Routers
app.include_router(uploads.router)
app.include_router(jobs.router)
app.include_router(errors.router)
app.include_router(records.router)
app.include_router(dashboard.router)

# Health endpoint
@app.get("/api/health", response_model=HealthResponse, tags=["System Health"])
def health_check(db: Session = Depends(get_db)):
    """Health check endpoint verifying application status and database connectivity."""
    db_connected = False
    try:
        db.execute(text("SELECT 1"))
        db_connected = True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")

    return HealthResponse(
        status="healthy" if db_connected else "degraded",
        version=settings.VERSION,
        database_connected=db_connected,
        timestamp=utc_now()
    )

# Favicon endpoint
@app.get("/favicon.ico", include_in_schema=False)
async def get_favicon():
    """Serve favicon.ico for browser requests."""
    favicon_file = static_path / "favicon.ico"
    if favicon_file.exists():
        return FileResponse(favicon_file, media_type="image/x-icon")
    return JSONResponse(status_code=404, content={"detail": "Favicon not found"})

# Frontend SPA route
templates_path = Path(__file__).parent / "templates"
templates_path.mkdir(parents=True, exist_ok=True)

@app.get("/", response_class=HTMLResponse, tags=["Frontend"])
async def serve_dashboard():
    """Serve the primary single-page dashboard application."""
    index_file = templates_path / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h2>Dashboard interface template is loading...</h2>", status_code=200)
    with open(index_file, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), status_code=200)

# Safe Global Exception Handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled server error on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An internal server error occurred. Please contact the platform administrator."
        }
    )
