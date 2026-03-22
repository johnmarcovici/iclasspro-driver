import asyncio
import json
import os
import sys
from typing import List, Dict

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request


class ScheduleSaveRequest(BaseModel):
    filename: str
    schedule: List[Dict[str, str]]


# Load environment variables
load_dotenv(override=True)

app = FastAPI(title="iClassPro Enrollment Dashboard")

# Ensure templates directory exists
os.makedirs("templates", exist_ok=True)
os.makedirs("schedules/tmp", exist_ok=True)

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def get(request: Request):
    # Pre-populate with values from .env
    context = {
        "request": request,
        "email": os.getenv("ICLASS_EMAIL", ""),
        "password": os.getenv("ICLASS_PASSWORD", ""),
        "student_id": os.getenv("ICLASS_STUDENT_ID", ""),
        "promo_code": os.getenv("ICLASS_PROMO_CODE", ""),
    }

    # Try to load the default schedule to pre-populate the grid
    default_schedule_path = os.getenv(
        "ICLASS_SCHEDULE", "schedules/short_schedule.json"
    )
    try:
        with open(default_schedule_path, "r") as f:
            context["initial_schedule"] = json.load(f)
    except Exception:
        context["initial_schedule"] = []

    return templates.TemplateResponse("index.html", context)


@app.get("/api/schedules")
async def list_schedules():
    schedules = []
    if os.path.exists("schedules"):
        for f in os.listdir("schedules"):
            if f.endswith(".json") and os.path.isfile(os.path.join("schedules", f)):
                schedules.append(f)
    return schedules


@app.get("/api/schedules/{filename}")
async def get_schedule(filename: str):
    safe_filename = os.path.basename(filename)
    if not safe_filename.endswith(".json"):
        safe_filename += ".json"
    filepath = os.path.join("schedules", safe_filename)
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


@app.post("/api/schedules")
async def save_schedule(request: ScheduleSaveRequest):
    safe_filename = os.path.basename(request.filename)
    if not safe_filename.endswith(".json"):
        safe_filename += ".json"
    filepath = os.path.join("schedules", safe_filename)
    with open(filepath, "w") as f:
        json.dump(request.schedule, f, indent=4)
    return {"message": f"Successfully saved {safe_filename}"}


@app.websocket("/ws/run")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    process = None

    try:
        # First message should be the configuration JSON
        config_data = await websocket.receive_text()
        config = json.loads(config_data)

        email = config.get("email", "")
        password = config.get("password", "")
        student_id = config.get("student_id", "")
        promo_code = config.get("promo_code", "")
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
        tmp_schedule_path = "schedules/tmp/web_schedule.json"
        with open(tmp_schedule_path, "w") as f:
            json.dump(schedule, f)

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
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
