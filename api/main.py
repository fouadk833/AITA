import logging
import logging.config
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.db.database import create_tables
from api.routers import runs, coverage, flakiness, agents, branches, pulls, ws, webhooks

load_dotenv()

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            "datefmt": "%H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        }
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    # silence noisy third-party loggers
    "loggers": {
        "httpx": {"level": "WARNING"},
        "httpcore": {"level": "WARNING"},
        "urllib3": {"level": "WARNING"},
        "anthropic": {"level": "WARNING"},
        "chromadb": {"level": "WARNING"},
        "chromadb.telemetry": {"level": "ERROR"},
        "chromadb.telemetry.product.posthog": {"level": "CRITICAL"},
    },
})

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting AITA API — creating DB tables")
    await create_tables()
    from api.db.database import SessionLocal
    from api.services.run_service import mark_stale_runs
    async with SessionLocal() as db:
        stale = await mark_stale_runs(db)
        if stale:
            logger.warning("Marked %d stale run(s) as error (interrupted by previous restart)", stale)
    logger.info("DB ready")
    yield
    logger.info("AITA API shutting down")


app = FastAPI(title="AITA API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router, prefix="/api")
app.include_router(coverage.router, prefix="/api")
app.include_router(flakiness.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(branches.router, prefix="/api")
app.include_router(pulls.router, prefix="/api")
app.include_router(ws.router)        # WebSocket — no /api prefix (uses /ws/...)
app.include_router(webhooks.router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}
