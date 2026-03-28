import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import List, Dict
from uuid import uuid4

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

from iclasspro import IClassPro


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


# Load environment variables
load_dotenv(override=True)

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _get_db_conn() as conn:
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
    with _get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (iclass_email, iclass_password, student_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(iclass_email, student_id)
            DO UPDATE SET iclass_password=excluded.iclass_password, updated_at=excluded.updated_at
            """,
            (email.strip(), password, str(student_id).strip(), now, now),
        )
        row = conn.execute(
            "SELECT id FROM users WHERE iclass_email = ? AND student_id = ?",
            (email.strip(), str(student_id).strip()),
        ).fetchone()
    return int(row["id"])


def _get_user_by_id(user_id: int):
    with _get_db_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


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
    return templates.TemplateResponse("login.html", {"request": request})


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
        raise HTTPException(
            status_code=401, detail="Could not validate iClassPro credentials"
        )

    user_id = _upsert_user(email=email, password=password, student_id=student_id)
    request.session["user_id"] = user_id
    return {"message": "Logged in"}


@app.post("/api/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return {"message": "Logged out"}


@app.post("/api/update-default-schedule")
async def update_default_schedule(request: Request, payload: UpdateEnvRequest):
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
                payload.password,
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

    try:
        user = _get_authenticated_ws_user(websocket)
        if not user:
            await websocket.send_text("Error: Authentication required.")
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

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text_line = line.decode("utf-8").rstrip()
            await websocket.send_text(text_line)

        await process.wait()

        if process.returncode != 0 and process.returncode not in (-15, -9):
            await websocket.send_text(
                f"Discovery failed with exit code {process.returncode}."
            )

    except WebSocketDisconnect:
        pass
    except Exception as e:
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
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/ws/enroll-selected")
async def websocket_enroll_selected(websocket: WebSocket):
    await websocket.accept()
    process = None
    tmp_path = None

    try:
        user = _get_authenticated_ws_user(websocket)
        if not user:
            await websocket.send_text("Error: Authentication required.")
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

        # Save selected classes to a unique temporary file per run
        tmp_path = os.path.join("schedules", "tmp", f"schedule_{uuid4().hex}.json")
        with open(tmp_path, "w") as f:
            json.dump(selected_classes, f, indent=4)

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

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text_line = line.decode("utf-8").rstrip()
            await websocket.send_text(text_line)

        await process.wait()

        if process.returncode == 0:
            await websocket.send_text("Enrollment completed successfully.")
        elif process.returncode not in (-15, -9):
            await websocket.send_text(
                f"Enrollment failed with exit code {process.returncode}."
            )

    except WebSocketDisconnect:
        pass
    except Exception as e:
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
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/ws/run")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    process = None
    tmp_schedule_path = None

    try:
        user = _get_authenticated_ws_user(websocket)
        if not user:
            await websocket.send_text("Error: Authentication required.")
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

        # Save the schedule to a temporary file
        tmp_schedule_path = os.path.join(
            "schedules", "tmp", f"schedule_{uuid4().hex}.json"
        )
        with open(tmp_schedule_path, "w") as f:
            json.dump(schedule, f, indent=4)

        # Send a starting message
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

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # Merge stderr into stdout
        )

        while True:
            line = await process.stdout.readline()
            if not line:
                break

            # Decode the line and send it to the websocket
            text_line = line.decode("utf-8").rstrip()
            await websocket.send_text(text_line)

        await process.wait()

        if process.returncode == 0:
            await websocket.send_text("Automation completed successfully.")
        else:
            # Only send failed message if we didn't deliberately kill it
            if process.returncode != -15 and process.returncode != -9:
                await websocket.send_text(
                    f"Automation failed with exit code {process.returncode}."
                )

    except WebSocketDisconnect:
        # Expected behavior when user closes the tab or clicks Stop
        pass
    except Exception as e:
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
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
