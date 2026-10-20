"""FastAPI application — entrypoint, lifespan, and static file serving."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import DEFAULT_SEARCHES
from .database import Base, SessionLocal, engine, ensure_db_schema
from .models import TrackedSearch
from .routers import analytics, antiban, database_ops, scraper_ops, searches, sniper
from .scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# ── Seed defaults on first run ────────────────────────────────────────────

def _seed_defaults() -> None:
    db = SessionLocal()
    try:
        if db.query(TrackedSearch).count() == 0:
            for text in DEFAULT_SEARCHES:
                db.add(TrackedSearch(search_text=text))
            db.commit()
            logger.info("Seeded %d default searches.", len(DEFAULT_SEARCHES))
    finally:
        db.close()


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    Base.metadata.create_all(bind=engine)
    ensure_db_schema(engine)
    _seed_defaults()
    start_scheduler()
    logger.info("🚀 Vinted Sales Tracker is running!")
    yield
    # Shutdown
    stop_scheduler()
    logger.info("Vinted Sales Tracker stopped.")


# ── App ───────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Vinted Sales Tracker",
    description="Track sales and pricing trends for popular items on Vinted UK",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(searches.router)
app.include_router(analytics.router)
app.include_router(sniper.router)
app.include_router(scraper_ops.router)
app.include_router(database_ops.router)
app.include_router(antiban.router)

# Static files (CSS, JS)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")



@app.get("/")
async def serve_index():
    """Serve the single-page frontend."""
    return FileResponse(str(FRONTEND_DIR / "index.html"))
