import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.exc import DatabaseError


def _resource_base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def _load_environment() -> None:
    """Load bundled defaults first, then local .env, then explicit override."""

    base = _resource_base()
    if getattr(sys, "frozen", False):
        load_dotenv(base / ".env.defaults", override=False)
        load_dotenv(base / ".env", override=False)
        load_dotenv(Path(sys.executable).resolve().parent / ".env", override=False)

    load_dotenv(base / ".env", override=False)

    custom_env = os.getenv("CHIPVERIFY_ENV_FILE", "").strip()
    if custom_env:
        load_dotenv(custom_env, override=True)


_load_environment()

logger = logging.getLogger(__name__)

from observability.sentry_config import init_sentry

init_sentry()


def _ensure_openai_dependency() -> None:
    """Bedrock/NIM/OpenAI providers need the openai SDK in the active Python env."""
    provider = (
        os.getenv("CHIPVERIFY_LLM_PROVIDER")
        or os.getenv("MODEL_PROVIDER")
        or (
            "bedrock"
            if os.getenv("BEDROCK_API_KEY") or os.getenv("AWS_BEARER_TOKEN_BEDROCK")
            else "gemini"
        )
    ).strip().lower()
    if provider in {"bedrock", "nim", "openai", "azure_openai"}:
        try:
            import openai  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Missing Python package 'openai'. Activate the project venv and run: "
                "python -m pip install -r backend/requirements.txt"
            ) from exc


_ensure_openai_dependency()

# Ensure backend root and original_core/ are importable regardless of cwd.
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_ORIGINAL_CORE_DIR = os.path.join(_BACKEND_DIR, "original_core")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
if _ORIGINAL_CORE_DIR not in sys.path:
    sys.path.insert(0, _ORIGINAL_CORE_DIR)

import database.database as db
from database.models import Run, RunEvent
import database.verification_models  # noqa: F401 — registers new tables with Base

from routes import api
from routes import design as design_routes
from routes import diagnostics as diagnostics_routes
from routes import agent_ws as agent_ws_routes
from routes import tools as tools_routes
from routes import chat_stream as chat_stream_routes
from routes import mental_model as mental_model_routes
from routes import patches as patches_routes
from routes import staged_verification as staged_verification_routes
from routes import uvm_debug as uvm_debug_routes
from routes import formal_debug as formal_debug_routes
from routes import project_tasks as project_tasks_routes
from routes import completions as completions_routes
from routes import codebase_graph as codebase_graph_routes
from routes import sv_lint as sv_lint_routes
from routes import simulator as simulator_routes


def _is_sqlite_corruption_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "database disk image is malformed",
            "file is not a database",
            "database image is malformed",
            "malformed",
        )
    )


def _initialize_database() -> None:
    try:
        db.Base.metadata.create_all(bind=db.engine)
        _apply_lightweight_schema_migrations()
    except DatabaseError as exc:
        if not _is_sqlite_corruption_error(exc):
            raise

        original_database_url = db.DATABASE_URL
        db.engine.dispose()

        quarantined_path = None
        try:
            quarantined_path = db.quarantine_sqlite_database("startup-corruption")
        except OSError:
            # If Windows has a lock on the corrupt DB file, recover by switching
            # to a fresh SQLite file rather than failing startup.
            pass

        if quarantined_path is None:
            fallback_db_path = db.get_sqlite_database_path()
            if fallback_db_path is not None:
                fallback_path = fallback_db_path.with_name(
                    f"{fallback_db_path.stem}.recovered-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}{fallback_db_path.suffix}"
                )
                db.rebuild_sqlite_engine(f"sqlite:///{fallback_path.as_posix()}")
            else:
                db.rebuild_sqlite_engine(original_database_url)

        try:
            db.Base.metadata.create_all(bind=db.engine)
            _apply_lightweight_schema_migrations()
        except DatabaseError as retry_exc:
            raise RuntimeError(
                "Failed to recover the SQLite database after quarantining the corrupted file. "
                "Delete the local database manually or set DATABASE_URL to a fresh location."
            ) from retry_exc

        if quarantined_path is not None:
            logger.warning(
                "Recovered corrupted SQLite database; quarantined to %s",
                quarantined_path,
            )


def _apply_lightweight_schema_migrations() -> None:
    """Apply additive SQLite migrations for local desktop databases.

    The app currently uses SQLAlchemy `create_all`, which creates new tables but
    does not add columns to existing SQLite databases. Keep this small and
    additive until the project adopts a real migration framework.
    """

    if not db.IS_SQLITE:
        return

    with db.engine.begin() as connection:
        run_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(runs)").fetchall()
        }
        if "mental_model_revision_id" not in run_columns:
            connection.exec_driver_sql(
                "ALTER TABLE runs ADD COLUMN mental_model_revision_id VARCHAR"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_runs_mental_model_revision_id "
                "ON runs (mental_model_revision_id)"
            )

        chat_thread_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(chat_threads)"
            ).fetchall()
        }
        if "thread_kind" not in chat_thread_columns:
            connection.exec_driver_sql(
                "ALTER TABLE chat_threads ADD COLUMN thread_kind VARCHAR DEFAULT 'main' NOT NULL"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_chat_threads_thread_kind "
                "ON chat_threads (thread_kind)"
            )
        if "parent_thread_id" not in chat_thread_columns:
            connection.exec_driver_sql(
                "ALTER TABLE chat_threads ADD COLUMN parent_thread_id VARCHAR"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_chat_threads_parent_thread_id "
                "ON chat_threads (parent_thread_id)"
            )
        if "agent_name" not in chat_thread_columns:
            connection.exec_driver_sql(
                "ALTER TABLE chat_threads ADD COLUMN agent_name VARCHAR"
            )
        if "active_task_id" not in chat_thread_columns:
            connection.exec_driver_sql(
                "ALTER TABLE chat_threads ADD COLUMN active_task_id VARCHAR"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_chat_threads_active_task_id "
                "ON chat_threads (active_task_id)"
            )

        project_task_table = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='project_tasks'"
        ).fetchone()
        if project_task_table:
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_project_tasks_project_status "
                "ON project_tasks (project_id, status)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_project_tasks_thread_id "
                "ON project_tasks (thread_id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_project_tasks_run_id "
                "ON project_tasks (run_id)"
            )

        run_event_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(run_events)"
            ).fetchall()
        }
        if "event_kind" not in run_event_columns:
            connection.exec_driver_sql(
                "ALTER TABLE run_events ADD COLUMN event_kind VARCHAR(32) "
                "DEFAULT 'log' NOT NULL"
            )

        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_chat_token_usage_project_id "
            "ON chat_token_usage (project_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_chat_token_usage_thread_id "
            "ON chat_token_usage (thread_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_chat_token_usage_assistant_message_id "
            "ON chat_token_usage (assistant_message_id)"
        )


_initialize_database()


def _recover_orphaned_runs() -> None:
    """Mark stale in-flight runs after restart so UI state remains consistent."""
    db_session = db.SessionLocal()
    try:
        orphaned_runs = db_session.query(Run).filter(Run.status == "running").all()
        for run in orphaned_runs:
            run.status = "interrupted"
            run.completed_at = datetime.now(timezone.utc)
            run.verification_summary = (
                "Run marked interrupted during backend startup recovery"
            )

            max_seq = (
                db_session.query(func.max(RunEvent.seq_no))
                .filter(RunEvent.run_id == run.id)
                .scalar()
            )
            event = RunEvent(
                id=str(uuid.uuid4()),
                run_id=run.id,
                seq_no=int(max_seq or 0) + 1,
                phase="recovery",
                level="warning",
                message="Backend restarted while run was active; run marked interrupted.",
            )
            db_session.add(event)

        if orphaned_runs:
            db_session.commit()
    finally:
        db_session.close()


_recover_orphaned_runs()

app = FastAPI(
    title="ChipVerify AI Studio Backend",
    description="Local-first on-prem orchestration API for AI-native chip verification workflows.",
    version="0.2.0-studio",
)

allowed_origins_raw = os.environ.get(
    "CHIPVERIFY_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,null",
)

allowed_origins = [
    origin.strip() for origin in allowed_origins_raw.split(",") if origin.strip()
]

allow_insecure_cors = os.environ.get(
    "CHIPVERIFY_ALLOW_INSECURE_CORS", "false"
).strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

if "*" in allowed_origins and not allow_insecure_cors:
    raise RuntimeError(
        "Refusing insecure CORS config: CHIPVERIFY_ALLOWED_ORIGINS contains '*'. "
        "Set explicit origins (recommended) or set CHIPVERIFY_ALLOW_INSECURE_CORS=true to override."
    )

# Restrict CORS to local desktop/web origins for on-prem deployments.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=600,
)


@app.on_event("startup")
async def _register_codebase_graph_event_loop() -> None:
    import asyncio

    from services.codebase_graph.events import set_event_loop

    set_event_loop(asyncio.get_running_loop())


# Mount routes
app.include_router(api.router)
app.include_router(design_routes.router)
app.include_router(tools_routes.router)
app.include_router(diagnostics_routes.router)
app.include_router(agent_ws_routes.router)
app.include_router(chat_stream_routes.router)
app.include_router(mental_model_routes.router)
app.include_router(patches_routes.router)
app.include_router(staged_verification_routes.router)
app.include_router(uvm_debug_routes.router)
app.include_router(formal_debug_routes.router)
app.include_router(project_tasks_routes.router)
app.include_router(completions_routes.router)
app.include_router(sv_lint_routes.router)
app.include_router(codebase_graph_routes.router, prefix="/api/v1")
app.include_router(simulator_routes.router)


@app.get("/")
def read_root():
    return {
        "message": "ChipVerify AI Studio backend is running in local-first on-prem mode",
        "product": "chipverify-ai-studio",
        "mode": "local-first-on-prem",
        "pipeline": "ai-native-verification",
    }


def _mount_optional_frontend() -> None:
    """Serve the built React app for no-certificate browser demo bundles."""

    frontend_dist_raw = os.environ.get("CHIPVERIFY_FRONTEND_DIST", "").strip()
    if not frontend_dist_raw:
        return

    frontend_dist = Path(frontend_dist_raw).resolve()
    index_file = frontend_dist / "index.html"
    if not index_file.is_file():
        logger.warning(
            "CHIPVERIFY_FRONTEND_DIST ignored; index.html not found: %s",
            frontend_dist,
        )
        return

    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/app/assets",
            StaticFiles(directory=str(assets_dir)),
            name="chipverify_frontend_assets",
        )

    @app.get("/app", include_in_schema=False)
    async def chipverify_frontend_redirect():
        return RedirectResponse(url="/app/")

    @app.get("/app/{path:path}", include_in_schema=False)
    async def chipverify_frontend(path: str = ""):
        candidate = (frontend_dist / path).resolve()
        try:
            candidate.relative_to(frontend_dist)
        except ValueError:
            return FileResponse(index_file)

        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)


_mount_optional_frontend()
