import os
import sys
import uuid
import base64
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from twitter_client import TwitterClient

STATE_DIR = os.environ.get("STATE_DIR", os.path.dirname(os.path.dirname(__file__)))
STATE_FILE = os.path.join(STATE_DIR, "twitter_state.json")

state_b64 = os.environ.get("TWITTER_STATE_B64")
if state_b64:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        decoded = base64.b64decode(state_b64).decode("utf-8")
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(decoded)
    except Exception:
        pass

app = FastAPI(title="TwitterTools")
client = TwitterClient()
executor = ThreadPoolExecutor(max_workers=2)

TEMP_DIR = os.environ.get("TEMP_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp_images"))
os.makedirs(TEMP_DIR, exist_ok=True)

tasks = {}
tasks_lock = threading.Lock()


def get_task(task_id):
    with tasks_lock:
        return tasks.get(task_id)


def set_task(task_id, data):
    with tasks_lock:
        tasks[task_id] = data


def run_tweet_task(task_id, text, image_path):
    try:
        def progress(current, total, msg):
            set_task(task_id, {
                "status": "running",
                "progress": msg,
                "current": current,
                "total": total,
            })

        set_task(task_id, {"status": "running", "progress": "Starting..."})
        result = client.post_tweet(text, image_path, progress_callback=progress)

        if result["success"]:
            set_task(task_id, {
                "status": "done",
                "progress": "Complete",
                "urls": result.get("urls", []),
            })
        else:
            set_task(task_id, {
                "status": "error",
                "progress": result.get("error", "Unknown error"),
            })
    except Exception as e:
        set_task(task_id, {
            "status": "error",
            "progress": str(e),
        })
    finally:
        if image_path:
            try:
                os.remove(image_path)
            except Exception:
                pass


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "frontend", "index.html"
    )
    with open(html_path, encoding="utf-8") as f:
        return f.read()


@app.get("/api/status")
async def status():
    logged_in = await asyncio.to_thread(client.is_logged_in)
    return {"logged_in": logged_in}


@app.post("/api/tweet")
async def tweet(
    text: str = Form(...),
    image: UploadFile = File(default=None),
):
    saved_path = None
    if image and image.filename:
        ext = os.path.splitext(image.filename)[1] or ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        saved_path = os.path.join(TEMP_DIR, filename)
        content = await image.read()
        with open(saved_path, "wb") as f:
            f.write(content)

    task_id = uuid.uuid4().hex
    set_task(task_id, {"status": "pending", "progress": "Queued..."})

    executor.submit(run_tweet_task, task_id, text, saved_path)

    return {"task_id": task_id}


@app.get("/api/task/{task_id}")
async def task_status(task_id: str):
    t = get_task(task_id)
    if t is None:
        return {"status": "not_found"}
    return t
