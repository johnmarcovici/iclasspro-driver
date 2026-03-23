import asyncio
import json
import os
import sys
from typing import List, Dict

# Add current directory to sys.path to help with module resolution
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from dotenv import load_dotenv, set_key, find_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

from scraper import scrape_available_classes


class ScheduleSaveRequest(BaseModel):
    filename: str
    schedule: List[Dict[str, str]]


class UpdateEnvRequest(BaseModel):
    filename: str


# Load environment variables
load_dotenv(override=True)

app = FastAPI(title="iClassPro Enrollment Dashboard")

# Ensure templates and schedules directories exist
os.makedirs("templates", exist_ok=True)
os.makedirs("schedules/tmp", exist_ok=True)

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def get(request: Request):
    context = {
        "request": request,
        "email": os.getenv("ICLASS_EMAIL", ""),
        "password": os.getenv("ICLASS_PASSWORD", ""),
        "student_id": os.getenv("ICLASS_STUDENT_ID", ""),
        "promo_code": os.getenv("ICLASS_PROMO_CODE", ""),
        "initial_schedule": [],
        "default_schedule_filename": None,
        # Set base_url for the template to use, especially for WebSocket connections
        "base_url": os.getenv("ICLASS_BASE_URL", "http://localhost:8000"),
    }

    # Try to load the default schedule from .env to pre-populate the grid
    default_schedule_env = os.getenv("ICLASS_SCHEDULE")
    if default_schedule_env:
        default_schedule_path = os.path.normpath(default_schedule_env)
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
    schedules_dir = "schedules"
    if os.path.exists(schedules_dir):
        for f in os.listdir(schedules_dir):
            if f.endswith(".json") and os.path.isfile(os.path.join(schedules_dir, f)):
                schedules_list.append(f)
    context["schedules_list"] = schedules_list

    return templates.TemplateResponse(request, "index.html", context)


@app.post("/api/update-default-schedule")
async def update_default_schedule(request: UpdateEnvRequest):
    dotenv_path = find_dotenv()
    if not dotenv_path:
        with open(".env", "w") as f:
            pass
        dotenv_path = find_dotenv()

    schedule_path = os.path.join("schedules", request.filename)
    if not os.path.exists(schedule_path):
        raise HTTPException(status_code=404, detail="Schedule file not found")

    set_key(dotenv_path, "ICLASS_SCHEDULE", schedule_path)
    return {"message": f"Default schedule updated to {request.filename}"}


@app.get("/api/schedules")
async def list_schedules():
    schedules = []
    schedules_dir = "schedules"
    if os.path.exists(schedules_dir):
        for f in os.listdir(schedules_dir):
            if f.endswith(".json") and os.path.isfile(os.path.join(schedules_dir, f)):
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
        config_data = await websocket.receive_text()
        config = json.loads(config_data)

        email = config.get("email", "")
        password = config.get("password", "")
        student_id = config.get("student_id", "")
        promo_code = config.get("promo_code", "")
        schedule = config.get("schedule", [])
        from_scraper = config.get("from_scraper", False)

        # Get base_url from env or default to localhost:8000
        base_url = os.getenv("ICLASS_BASE_URL", "http://localhost:8000")

        if not all([email, password, student_id]):
            await websocket.send_text(
                "Error: Email, password, and student ID are required."
            )
            return

        if not schedule:
            await websocket.send_text("Error: Schedule is empty.")
            return

        tmp_schedule_path = "schedules/tmp/web_schedule.json"
        with open(tmp_schedule_path, "w") as f:
            json.dump(schedule, f)

        await websocket.send_text("Starting iClassPro automation...")

        cmd_args = [
            sys.executable,  # Use sys.executable for the correct python interpreter
            "iclasspro.py",
            "--email",
            email,
            "--password",
            password,
            "--student-id",
            str(student_id),
            "--base-url",  # Pass base_url as a command-line argument
            base_url,
        ]
        if promo_code:
            cmd_args.extend(["--promo-code", promo_code])

        if from_scraper:
            cmd_args.extend(["--scraped-data", tmp_schedule_path])
        else:
            cmd_args.extend(["--schedule", tmp_schedule_path])

        process = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        while True:
            line = await process.stdout.readline()
            if not line:
                break
            await websocket.send_text(line.decode("utf-8").rstrip())

        await process.wait()
        if process.returncode == 0:
            await websocket.send_text("Automation completed successfully.")
        else:
            if process.returncode not in [-15, -9]:
                await websocket.send_text(
                    f"Automation failed with exit code {process.returncode}."
                )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_text(f"Error: {str(e)}")
    finally:
        if process and process.returncode is None:
            process.terminate()
        await websocket.close()


@app.websocket("/ws/scrape")
async def websocket_scrape_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        config_data = await websocket.receive_text()
        config = json.loads(config_data)
        email = config.get("email", "")
        password = config.get("password", "")
        student_id = config.get("student_id", "")
        if not all([email, password, student_id]):
            await websocket.send_text(
                json.dumps({"error": "Email, password, and student ID are required."})
            )
            return
        await websocket.send_text(json.dumps({"log": "Starting scraper..."}))
        # --- FIX START ---
        # Instantiate IClassPro with the base_url
        base_url = os.getenv("ICLASS_BASE_URL", "http://localhost:8000")
        # Pass base_url to scrape_available_classes
        async for class_info in scrape_available_classes(
            email, password, student_id, base_url
        ):
            await websocket.send_text(json.dumps(class_info))
        await websocket.send_text(json.dumps({"log": "Scraping complete."}))
        # --- FIX END ---
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_text(json.dumps({"error": f"An error occurred: {str(e)}"}))
    finally:
        await websocket.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
