import asyncio
import os
import sys

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

app = FastAPI(title="iClassPro Enrollment Dashboard")

# Ensure templates directory exists
os.makedirs("templates", exist_ok=True)

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def get(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.websocket("/ws/run")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    # Send a starting message
    await websocket.send_text("Starting iClassPro automation...")

    # We will run the python script as a subprocess so we can capture its stdout/stderr
    # without having to heavily modify the logging setup in iclasspro.py right away.
    # We'll use the short schedule for testing the UI.
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "iclasspro.py",
            "--schedule",
            "schedules/short_schedule.json",
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
            await websocket.send_text(
                f"Automation failed with exit code {process.returncode}."
            )

    except Exception as e:
        await websocket.send_text(f"Error starting automation: {str(e)}")
    finally:
        await websocket.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
