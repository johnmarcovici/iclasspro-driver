import asyncio
import base64
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Optional
from uuid import uuid4

import yaml
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    BackgroundTasks,
)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

from iclasspro import IClassPro


class JobType(str, Enum):
    DISCOVER = "discover"
    ENROLL_SCHEDULE = "enroll_schedule"
    ENROLL_SELECTED = "enroll_selected"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScheduleSaveRequest(BaseModel):
    filename: str
    schedule: List[Dict[str, str]]


class UpdateEnvRequest(BaseModel):
    filename: str


class SaveConfigRequest(BaseModel):
    email: str
    password: str
    student_id: str
    promo_code: str
    complete_transaction: bool


class LoginRequest(BaseModel):
    email: str
    password: str
    student_id: str


class JobConfigRequest(BaseModel):
    job_type: JobType
    scrape_days: str = ""
    scrape_locations: str = ""
    deep_scrape: bool = False
    promo_code: str = ""
    complete_transaction: Optional[bool] = None
    schedule: List[Dict[str, str]] = []
    selected_classes: List[Dict[str, str]] = []


# Load environment variables
load_dotenv(override=True)


# Encryption setup
def _get_cipher():
    """Get or create encryption cipher for storing passwords."""
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        # Generate a new key if one doesn't exist (for development)
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        print(f"Generated new ENCRYPTION_KEY. Set it in .env: ENCRYPTION_KEY={key}")
    return Fernet(key.encode() if isinstance(key, str) else key)


def _encrypt_password(password: str) -> str:
    """Encrypt a password for storage."""
    cipher = _get_cipher()
    return cipher.encrypt(password.encode()).decode()


def _decrypt_password(encrypted_password: str) -> str:
    """Decrypt a stored password."""
    cipher = _get_cipher()
    return cipher.decrypt(encrypted_password.encode()).decode()


app = FastAPI(title="iClassPro Enrollment Dashboard")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("DASHBOARD_SESSION_SECRET", "dev-session-secret-change-me"),
    same_site="lax",
    https_only=os.getenv("COOKIE_SECURE", "0").lower() in ("1", "true", "yes"),
)

# Ensure templates directory exists
os.makedirs("templates", exist_ok=True)
os.makedirs("schedules/tmp", exist_ok=True)
os.makedirs("schedules/users", exist_ok=True)

templates = Jinja2Templates(directory="templates")
DB_PATH = os.path.join(os.path.dirname(__file__), "app.db")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def _using_postgres() -> bool:
    normalized = _normalize_database_url(DATABASE_URL)
    return normalized.startswith("postgresql://")


class _DbConnectionWrapper:
    def __init__(self, conn, use_postgres: bool):
        self._conn = conn
        self._use_postgres = use_postgres

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)

    def execute(self, query: str, params=()):
        if params is None:
            params = ()
        if self._use_postgres:
            query = query.replace("?", "%s")
        return self._conn.execute(query, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


# Job queue management
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "3"))
MAX_JOBS_PER_USER = int(os.getenv("MAX_JOBS_PER_USER", "2"))
_job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
_job_processes: Dict[str, asyncio.subprocess.Process] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db_conn():
    if _using_postgres():
        if psycopg is None:
            raise RuntimeError(
                "DATABASE_URL is set for PostgreSQL, but psycopg is not installed. "
                "Install dependencies again to enable cloud or multi-user Postgres mode."
            )
        conn = psycopg.connect(
            _normalize_database_url(DATABASE_URL),
            row_factory=dict_row,
        )
        return _DbConnectionWrapper(conn, use_postgres=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return _DbConnectionWrapper(conn, use_postgres=False)


def _init_db() -> None:
    with _get_db_conn() as conn:
        if _using_postgres():
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    iclass_email TEXT NOT NULL,
                    iclass_password TEXT NOT NULL,
                    student_id TEXT NOT NULL,
                    promo_code TEXT NOT NULL DEFAULT '',
                    complete_transaction INTEGER NOT NULL DEFAULT 1,
                    default_schedule_filename TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(iclass_email, student_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    config TEXT NOT NULL,
                    result TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    iclass_email TEXT NOT NULL,
                    iclass_password TEXT NOT NULL,
                    student_id TEXT NOT NULL,
                    promo_code TEXT NOT NULL DEFAULT '',
                    complete_transaction INTEGER NOT NULL DEFAULT 1,
                    default_schedule_filename TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(iclass_email, student_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    config TEXT NOT NULL,
                    result TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)
            """
        )


def _verify_iclass_credentials(email: str, password: str) -> bool:
    driver = None
    try:
        driver = IClassPro(save_screenshots=False)
        driver.webdriver()
        driver.login(email=email, password=password)
        return True
    except Exception:
        return False
    finally:
        if driver:
            driver.close()


def _upsert_user(email: str, password: str, student_id: str) -> int:
    now = _utc_now()
    encrypted_pwd = _encrypt_password(password)
    with _get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (iclass_email, iclass_password, student_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(iclass_email, student_id)
            DO UPDATE SET iclass_password=excluded.iclass_password, updated_at=excluded.updated_at
            """,
            (email.strip(), encrypted_pwd, str(student_id).strip(), now, now),
        )
        row = conn.execute(
            "SELECT id FROM users WHERE iclass_email = ? AND student_id = ?",
            (email.strip(), str(student_id).strip()),
        ).fetchone()
    return int(row["id"])


def _get_user_by_id(user_id: int):
    with _get_db_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user:
            # Decrypt password for internal use
            try:
                decrypted_pwd = _decrypt_password(user["iclass_password"])
                # Return a dict-like object with decrypted password
                user_dict = dict(user)
                user_dict["iclass_password"] = decrypted_pwd
                return user_dict
            except Exception:
                user_dict = dict(user)
                # Never expose encrypted blobs to password fields.
                user_dict["iclass_password"] = ""

                # In local/dev mode, fall back to .env creds for this user.
                if _is_dev_mode():
                    env_creds = _get_local_env_credentials()
                    if (
                        user_dict.get("iclass_email", "").strip() == env_creds["email"]
                        and str(user_dict.get("student_id", "")).strip()
                        == env_creds["student_id"]
                        and env_creds["password"]
                    ):
                        user_dict["iclass_password"] = env_creds["password"]

                return user_dict
        return None


def _is_dev_mode() -> bool:
    return os.getenv("ENVIRONMENT", "development").lower() in (
        "development",
        "dev",
        "local",
    )


def _get_local_env_credentials() -> Dict[str, str]:
    return {
        "email": os.getenv("ICLASS_EMAIL", "").strip(),
        "password": os.getenv("ICLASS_PASSWORD", ""),
        "student_id": os.getenv("ICLASS_STUDENT_ID", "").strip(),
    }


def _get_local_env_defaults() -> Dict[str, object]:
    return {
        "promo_code": os.getenv("ICLASS_PROMO_CODE", "").strip(),
        "complete_transaction": os.getenv("ICLASS_COMPLETE_TRANSACTION", "0").lower()
        in ("1", "true", "yes"),
    }


def _create_job(user_id: int, job_type: JobType, config: Dict):
    """Create a new job record."""
    job_id = str(uuid4())
    now = _utc_now()
    with _get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, user_id, job_type, status, config, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                user_id,
                job_type.value,
                JobStatus.QUEUED.value,
                json.dumps(config),
                now,
            ),
        )
    return job_id


def _get_job(job_id: str):
    """Fetch a job by ID."""
    with _get_db_conn() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def _update_job_status(
    job_id: str, status: JobStatus, error_msg: str = None, result: str = None
):
    """Update job status."""
    now = _utc_now()
    with _get_db_conn() as conn:
        updates = {"status": status.value}
        if status == JobStatus.RUNNING and not _get_job(job_id)["started_at"]:
            updates["started_at"] = now
        if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            updates["finished_at"] = now
        if error_msg:
            updates["error_message"] = error_msg
        if result:
            updates["result"] = result

        update_str = ", ".join(f"{k} = ?" for k in updates.keys())
        conn.execute(
            f"UPDATE jobs SET {update_str} WHERE id = ?",
            list(updates.values()) + [job_id],
        )


def _list_user_jobs(user_id: int, limit: int = 50):
    """List a user's jobs, most recent first."""
    with _get_db_conn() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()


def _count_user_active_jobs(user_id: int) -> int:
    """Count how many jobs a user has in QUEUED or RUNNING state."""
    with _get_db_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE user_id = ? AND status IN (?, ?)",
            (user_id, JobStatus.QUEUED.value, JobStatus.RUNNING.value),
        ).fetchone()
    return row["cnt"] if row else 0


def _get_authenticated_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return _get_user_by_id(int(user_id))


def _require_authenticated_user(request: Request):
    user = _get_authenticated_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _apply_dev_env_defaults_to_user(user_id: int):
    """Seed missing profile defaults from .env in local/dev mode only."""
    if not _is_dev_mode():
        return

    defaults = _get_local_env_defaults()
    now = _utc_now()

    with _get_db_conn() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return

        # Only fill promo code if it's currently empty.
        if not (user["promo_code"] or "").strip() and defaults["promo_code"]:
            conn.execute(
                "UPDATE users SET promo_code = ?, updated_at = ? WHERE id = ?",
                (defaults["promo_code"], now, user_id),
            )

        # Align initial complete_transaction with env on first login record only.
        if user["created_at"] == user["updated_at"]:
            conn.execute(
                "UPDATE users SET complete_transaction = ?, updated_at = ? WHERE id = ?",
                (1 if defaults["complete_transaction"] else 0, now, user_id),
            )


def _get_authenticated_ws_user(websocket: WebSocket):
    session = websocket.scope.get("session") or {}
    user_id = session.get("user_id")
    if not user_id:
        return None
    return _get_user_by_id(int(user_id))


def _user_schedule_dir(user_id: int) -> str:
    user_dir = os.path.join("schedules", "users", str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    return user_dir


async def _run_job_process(job_id: str, cmd_args: List[str], user_id: int):
    """Execute a background job process and track its status."""
    try:
        _update_job_status(job_id, JobStatus.RUNNING)

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        _job_processes[job_id] = process

        output_lines = []
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text_line = line.decode("utf-8").rstrip()
            output_lines.append(text_line)

        await process.wait()

        if process.returncode == 0:
            _update_job_status(
                job_id, JobStatus.COMPLETED, result="\n".join(output_lines)
            )
        else:
            error_msg = f"Process failed with exit code {process.returncode}"
            _update_job_status(job_id, JobStatus.FAILED, error_msg=error_msg)

    except asyncio.CancelledError:
        _update_job_status(job_id, JobStatus.CANCELLED)
        if job_id in _job_processes:
            try:
                _job_processes[job_id].terminate()
            except OSError:
                pass
        raise
    except Exception as e:
        _update_job_status(job_id, JobStatus.FAILED, error_msg=str(e))
    finally:
        if job_id in _job_processes:
            del _job_processes[job_id]


async def _enqueue_and_run_job(
    user_id: int, job_type: JobType, config: Dict, cmd_args: List[str]
):
    """Enqueue a job and run it as a background task."""
    job_id = _create_job(user_id, job_type, config)

    # Acquire semaphore and run job
    async def run():
        async with _job_semaphore:
            await _run_job_process(job_id, cmd_args, user_id)

    asyncio.create_task(run())
    return job_id


_init_db()


def _load_locations() -> list:
    """Load the known locations list from config/locations.yaml."""
    config_path = os.path.join(os.path.dirname(__file__), "config", "locations.yaml")
    with open(config_path, "r") as f:
        return yaml.safe_load(f).get("locations", [])


@app.get("/", response_class=HTMLResponse)
async def get(request: Request):
    user = _get_authenticated_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    context = {
        "request": request,
        "email": user["iclass_email"],
        "password": user["iclass_password"],
        "student_id": user["student_id"],
        "promo_code": user["promo_code"],
        "complete_transaction": bool(user["complete_transaction"]),
        "initial_schedule": [],
        "default_schedule_filename": None,
        "locations": _load_locations(),
    }

    default_schedule = user["default_schedule_filename"]
    if default_schedule:
        default_schedule_path = os.path.join(
            _user_schedule_dir(int(user["id"])),
            os.path.basename(default_schedule),
        )
        if os.path.exists(default_schedule_path):
            try:
                with open(default_schedule_path, "r") as f:
                    context["initial_schedule"] = json.load(f)
                context["default_schedule_filename"] = os.path.basename(
                    default_schedule_path
                )
            except Exception:
                context["initial_schedule"] = []
                context["default_schedule_filename"] = None

    schedules_list = []
    schedules_dir = _user_schedule_dir(int(user["id"]))
    if os.path.exists(schedules_dir):
        for f in os.listdir(schedules_dir):
            if f.endswith(".json") and os.path.isfile(os.path.join(schedules_dir, f)):
                schedules_list.append(f)
    context["schedules_list"] = schedules_list

    return templates.TemplateResponse("index.html", context)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _get_authenticated_user(request):
        return RedirectResponse(url="/", status_code=303)

    # In local/dev mode, pre-fill from .env so single-user workflows stay smooth.
    defaults = {"email": "", "password": "", "student_id": ""}
    if _is_dev_mode():
        defaults = _get_local_env_credentials()

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "prefill_email": defaults["email"],
            "prefill_password": defaults["password"],
            "prefill_student_id": defaults["student_id"],
        },
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    user = _get_authenticated_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    context = {
        "request": request,
        "email": user["iclass_email"],
        "student_id": user["student_id"],
        "promo_code": user["promo_code"],
        "complete_transaction": bool(user["complete_transaction"]),
    }
    return templates.TemplateResponse("settings.html", context)


@app.post("/api/auth/login")
async def login(request: Request, payload: LoginRequest):
    email = payload.email.strip()
    password = payload.password
    student_id = str(payload.student_id).strip()

    if not email or not password or not student_id:
        raise HTTPException(
            status_code=400, detail="Email, password and student ID are required"
        )

    is_valid = await asyncio.to_thread(_verify_iclass_credentials, email, password)
    if not is_valid:
        # Optional local fallback for development if payload exactly matches .env.
        allow_fallback = os.getenv("ALLOW_LOCAL_ENV_LOGIN_FALLBACK", "1").lower() in (
            "1",
            "true",
            "yes",
        )
        env_creds = _get_local_env_credentials()
        env_match = (
            email == env_creds["email"]
            and password == env_creds["password"]
            and student_id == env_creds["student_id"]
        )

        if not (_is_dev_mode() and allow_fallback and env_match):
            raise HTTPException(
                status_code=401,
                detail=(
                    "Could not validate iClassPro credentials. "
                    "If using local .env credentials, verify ICLASS_EMAIL, "
                    "ICLASS_PASSWORD, and ICLASS_STUDENT_ID are current."
                ),
            )

    user_id = _upsert_user(email=email, password=password, student_id=student_id)
    _apply_dev_env_defaults_to_user(user_id)
    request.session["user_id"] = user_id
    return {"message": "Logged in"}


@app.post("/api/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return {"message": "Logged out"}


@app.get("/api/jobs")
async def list_jobs(request: Request):
    """List current user's jobs."""
    user = _require_authenticated_user(request)
    jobs = _list_user_jobs(int(user["id"]))
    return [
        {
            "id": dict(job)["id"],
            "type": dict(job)["job_type"],
            "status": dict(job)["status"],
            "created_at": dict(job)["created_at"],
            "started_at": dict(job)["started_at"],
            "finished_at": dict(job)["finished_at"],
            "error": dict(job)["error_message"],
        }
        for job in jobs
    ]


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str, request: Request):
    """Get status of a specific job."""
    user = _require_authenticated_user(request)
    job = _get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_dict = dict(job)
    if job_dict["user_id"] != int(user["id"]):
        raise HTTPException(status_code=403, detail="Unauthorized")

    return {
        "id": job_dict["id"],
        "type": job_dict["job_type"],
        "status": job_dict["status"],
        "created_at": job_dict["created_at"],
        "started_at": job_dict["started_at"],
        "finished_at": job_dict["finished_at"],
        "error": job_dict["error_message"],
        "result": job_dict["result"],
    }


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    """Cancel a running job."""
    user = _require_authenticated_user(request)
    job = _get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_dict = dict(job)
    if job_dict["user_id"] != int(user["id"]):
        raise HTTPException(status_code=403, detail="Unauthorized")

    if job_dict["status"] not in (JobStatus.QUEUED.value, JobStatus.RUNNING.value):
        raise HTTPException(status_code=400, detail="Cannot cancel completed job")

    if job_id in _job_processes:
        try:
            _job_processes[job_id].terminate()
        except OSError:
            pass

    _update_job_status(job_id, JobStatus.CANCELLED)
    return {"message": "Job cancelled"}

    user = _require_authenticated_user(request)
    safe_filename = os.path.basename(payload.filename)
    schedule_path = os.path.join(_user_schedule_dir(int(user["id"])), safe_filename)
    if not os.path.exists(schedule_path):
        raise HTTPException(status_code=404, detail="Schedule file not found")

    with _get_db_conn() as conn:
        conn.execute(
            "UPDATE users SET default_schedule_filename = ?, updated_at = ? WHERE id = ?",
            (safe_filename, _utc_now(), int(user["id"])),
        )
    return {"message": f"Default schedule updated to {safe_filename}"}


@app.post("/api/save-config")
async def save_config(request: Request, payload: SaveConfigRequest):
    user = _require_authenticated_user(request)
    with _get_db_conn() as conn:
        conn.execute(
            """
            UPDATE users
            SET iclass_email = ?,
                iclass_password = ?,
                student_id = ?,
                promo_code = ?,
                complete_transaction = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                payload.email.strip(),
                _encrypt_password(payload.password),
                str(payload.student_id).strip(),
                payload.promo_code,
                1 if payload.complete_transaction else 0,
                _utc_now(),
                int(user["id"]),
            ),
        )
    return {"message": "Configuration saved"}


@app.get("/api/schedules")
async def list_schedules(request: Request):
    user = _require_authenticated_user(request)
    schedules = []
    schedules_dir = _user_schedule_dir(int(user["id"]))
    if os.path.exists(schedules_dir):
        for f in os.listdir(schedules_dir):
            if f.endswith(".json") and os.path.isfile(os.path.join(schedules_dir, f)):
                schedules.append(f)
    return schedules


@app.get("/api/schedules/{filename}")
async def get_schedule(filename: str, request: Request):
    user = _require_authenticated_user(request)
    safe_filename = os.path.basename(filename)
    if not safe_filename.endswith(".json"):
        safe_filename += ".json"
    filepath = os.path.join(_user_schedule_dir(int(user["id"])), safe_filename)
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


@app.post("/api/schedules")
async def save_schedule(request: Request, payload: ScheduleSaveRequest):
    user = _require_authenticated_user(request)
    safe_filename = os.path.basename(payload.filename)
    if not safe_filename.endswith(".json"):
        safe_filename += ".json"
    filepath = os.path.join(_user_schedule_dir(int(user["id"])), safe_filename)
    with open(filepath, "w") as f:
        json.dump(payload.schedule, f, indent=4)
    return {"message": f"Successfully saved {safe_filename}"}


@app.websocket("/ws/scrape")
async def websocket_scrape(websocket: WebSocket):
    await websocket.accept()
    process = None
    job_id = None

    try:
        user = _get_authenticated_ws_user(websocket)
        if not user:
            await websocket.send_text("Error: Authentication required.")
            await websocket.close()
            return

        # Check if user is at active job limit
        active = _count_user_active_jobs(int(user["id"]))
        if active >= MAX_JOBS_PER_USER:
            await websocket.send_text(
                f"Error: Maximum {MAX_JOBS_PER_USER} concurrent jobs allowed per user."
            )
            await websocket.close()
            return

        config_data = await websocket.receive_text()
        config = json.loads(config_data)

        email = user["iclass_email"]
        password = user["iclass_password"]
        student_id = user["student_id"]
        scrape_days = config.get("scrape_days", "")
        scrape_locations = config.get("scrape_locations", "")
        deep_scrape = config.get("deep_scrape", False)

        if not all([email, password, student_id]):
            await websocket.send_text(
                "Error: Email, password, and student ID are required."
            )
            await websocket.close()
            return

        # Create job record
        job_id = _create_job(int(user["id"]), JobType.DISCOVER, config)
        _update_job_status(job_id, JobStatus.RUNNING)

        await websocket.send_text(f"Job {job_id} started.")
        await websocket.send_text("Starting class discovery scrape...")

        cmd_args = [
            "iclasspro.py",
            "--email",
            email,
            "--password",
            password,
            "--student-id",
            str(student_id),
            "--scrape",
        ]
        if scrape_days:
            cmd_args.extend(["--scrape-days", scrape_days])
        if scrape_locations:
            cmd_args.extend(["--scrape-locations", scrape_locations])
        if deep_scrape:
            cmd_args.append("--deep-scrape")

        async with _job_semaphore:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            _job_processes[job_id] = process

            output_lines = []
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                text_line = line.decode("utf-8").rstrip()
                output_lines.append(text_line)
                await websocket.send_text(text_line)

            await process.wait()

            if process.returncode == 0:
                _update_job_status(
                    job_id, JobStatus.COMPLETED, result="\n".join(output_lines)
                )
                await websocket.send_text("Discovery completed successfully.")
            else:
                error_msg = f"Discovery failed with exit code {process.returncode}"
                _update_job_status(job_id, JobStatus.FAILED, error_msg=error_msg)
                await websocket.send_text(error_msg)

    except WebSocketDisconnect:
        if job_id:
            _update_job_status(job_id, JobStatus.CANCELLED)
    except Exception as e:
        if job_id:
            _update_job_status(job_id, JobStatus.FAILED, error_msg=str(e))
        try:
            await websocket.send_text(f"Error: {str(e)}")
        except Exception:
            pass
    finally:
        if process and process.returncode is None:
            try:
                process.terminate()
            except OSError:
                pass
        if job_id and job_id in _job_processes:
            del _job_processes[job_id]
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/ws/enroll-selected")
async def websocket_enroll_selected(websocket: WebSocket):
    await websocket.accept()
    process = None
    tmp_path = None
    job_id = None

    try:
        user = _get_authenticated_ws_user(websocket)
        if not user:
            await websocket.send_text("Error: Authentication required.")
            await websocket.close()
            return

        # Check if user is at active job limit
        active = _count_user_active_jobs(int(user["id"]))
        if active >= MAX_JOBS_PER_USER:
            await websocket.send_text(
                f"Error: Maximum {MAX_JOBS_PER_USER} concurrent jobs allowed per user."
            )
            await websocket.close()
            return

        config_data = await websocket.receive_text()
        config = json.loads(config_data)

        email = user["iclass_email"]
        password = user["iclass_password"]
        student_id = user["student_id"]
        promo_code = config.get("promo_code", user["promo_code"])
        complete_transaction = config.get(
            "complete_transaction", bool(user["complete_transaction"])
        )
        selected_classes = config.get("selected_classes", [])

        if not all([email, password, student_id]):
            await websocket.send_text(
                "Error: Email, password, and student ID are required."
            )
            await websocket.close()
            return

        if not selected_classes:
            await websocket.send_text("Error: No classes selected for enrollment.")
            await websocket.close()
            return

        # Create job record
        job_id = _create_job(int(user["id"]), JobType.ENROLL_SELECTED, config)
        _update_job_status(job_id, JobStatus.RUNNING)

        # Save selected classes to a unique temporary file per run
        tmp_path = os.path.join("schedules", "tmp", f"schedule_{uuid4().hex}.json")
        with open(tmp_path, "w") as f:
            json.dump(selected_classes, f, indent=4)

        await websocket.send_text(f"Job {job_id} started.")
        await websocket.send_text("Starting enrollment of selected classes...")

        cmd_args = [
            "iclasspro.py",
            "--email",
            email,
            "--password",
            password,
            "--student-id",
            str(student_id),
            "--schedule",
            tmp_path,
        ]

        if promo_code:
            cmd_args.extend(["--promo-code", promo_code])

        if complete_transaction:
            cmd_args.append("--complete-transaction")

        async with _job_semaphore:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            _job_processes[job_id] = process

            output_lines = []
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                text_line = line.decode("utf-8").rstrip()
                output_lines.append(text_line)
                await websocket.send_text(text_line)

            await process.wait()

            if process.returncode == 0:
                _update_job_status(
                    job_id, JobStatus.COMPLETED, result="\n".join(output_lines)
                )
                await websocket.send_text("Enrollment completed successfully.")
            else:
                error_msg = f"Enrollment failed with exit code {process.returncode}"
                _update_job_status(job_id, JobStatus.FAILED, error_msg=error_msg)
                await websocket.send_text(error_msg)

    except WebSocketDisconnect:
        if job_id:
            _update_job_status(job_id, JobStatus.CANCELLED)
    except Exception as e:
        if job_id:
            _update_job_status(job_id, JobStatus.FAILED, error_msg=str(e))
        try:
            await websocket.send_text(f"Error: {str(e)}")
        except Exception:
            pass
    finally:
        if process and process.returncode is None:
            try:
                process.terminate()
            except OSError:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        if job_id and job_id in _job_processes:
            del _job_processes[job_id]
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/ws/run")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    process = None
    tmp_schedule_path = None
    job_id = None

    try:
        user = _get_authenticated_ws_user(websocket)
        if not user:
            await websocket.send_text("Error: Authentication required.")
            await websocket.close()
            return

        # Check if user is at active job limit
        active = _count_user_active_jobs(int(user["id"]))
        if active >= MAX_JOBS_PER_USER:
            await websocket.send_text(
                f"Error: Maximum {MAX_JOBS_PER_USER} concurrent jobs allowed per user."
            )
            await websocket.close()
            return

        # First message should be the configuration JSON
        config_data = await websocket.receive_text()
        config = json.loads(config_data)

        email = user["iclass_email"]
        password = user["iclass_password"]
        student_id = user["student_id"]
        promo_code = config.get("promo_code", user["promo_code"])
        complete_transaction = config.get(
            "complete_transaction", bool(user["complete_transaction"])
        )
        schedule = config.get("schedule", [])

        if not all([email, password, student_id]):
            await websocket.send_text(
                "Error: Email, password, and student ID are required."
            )
            await websocket.close()
            return

        if not schedule:
            await websocket.send_text(
                "Error: Schedule is empty. Please add at least one class."
            )
            await websocket.close()
            return

        # Create job record
        job_id = _create_job(int(user["id"]), JobType.ENROLL_SCHEDULE, config)
        _update_job_status(job_id, JobStatus.RUNNING)

        # Save the schedule to a temporary file
        tmp_schedule_path = os.path.join(
            "schedules", "tmp", f"schedule_{uuid4().hex}.json"
        )
        with open(tmp_schedule_path, "w") as f:
            json.dump(schedule, f, indent=4)

        # Send a starting message
        await websocket.send_text(f"Job {job_id} started.")
        await websocket.send_text("Starting iClassPro automation...")

        # Build the command arguments
        cmd_args = [
            "iclasspro.py",
            "--email",
            email,
            "--password",
            password,
            "--student-id",
            str(student_id),
            "--schedule",
            tmp_schedule_path,
        ]

        if promo_code:
            cmd_args.extend(["--promo-code", promo_code])

        if complete_transaction:
            cmd_args.append("--complete-transaction")

        async with _job_semaphore:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # Merge stderr into stdout
            )
            _job_processes[job_id] = process

            output_lines = []
            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                # Decode the line and send it to the websocket
                text_line = line.decode("utf-8").rstrip()
                output_lines.append(text_line)
                await websocket.send_text(text_line)

            await process.wait()

            if process.returncode == 0:
                _update_job_status(
                    job_id, JobStatus.COMPLETED, result="\n".join(output_lines)
                )
                await websocket.send_text("Automation completed successfully.")
            else:
                # Only send failed message if we didn't deliberately kill it
                error_msg = f"Automation failed with exit code {process.returncode}"
                _update_job_status(job_id, JobStatus.FAILED, error_msg=error_msg)
                await websocket.send_text(error_msg)

    except WebSocketDisconnect:
        if job_id:
            _update_job_status(job_id, JobStatus.CANCELLED)
    except Exception as e:
        if job_id:
            _update_job_status(job_id, JobStatus.FAILED, error_msg=str(e))
        try:
            await websocket.send_text(f"Error: {str(e)}")
        except Exception:
            pass  # Socket might already be closed
    finally:
        # CRITICAL: Kill the subprocess if it's still running when the connection drops
        if process and process.returncode is None:
            try:
                process.terminate()
            except OSError:
                pass
        if tmp_schedule_path and os.path.exists(tmp_schedule_path):
            try:
                os.remove(tmp_schedule_path)
            except OSError:
                pass
        if job_id and job_id in _job_processes:
            del _job_processes[job_id]
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
