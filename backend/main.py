import os
import sys
import gzip
import uuid
import base64
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from twitter_client import TwitterClient
from PIL import Image, ImageDraw, ImageFont
import math

STATE_DIR = os.environ.get("STATE_DIR", os.path.dirname(os.path.dirname(__file__)))
STATE_FILE = os.path.join(STATE_DIR, "twitter_state.json")

state_gz_b64 = os.environ.get("TWITTER_STATE_GZ")
if state_gz_b64:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        compressed = base64.b64decode(state_gz_b64)
        decoded = gzip.decompress(compressed).decode("utf-8")
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(decoded)
        print(f"STATE_FILE written to {STATE_FILE} ({len(decoded)} bytes)", flush=True)
    except Exception as e:
        print(f"STATE_FILE write failed: {e}", flush=True)

WATERMARK_TEXT = "@unsrifess"


def add_watermark(image_path):
    try:
        img = Image.open(image_path).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        font_size = max(24, int(math.sqrt(img.width * img.height) * 0.045))
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        tx = (img.width - tw) // 2
        ty = (img.height - th) // 2
        draw.text((tx, ty), WATERMARK_TEXT, font=font, fill=(255, 255, 255, 40))

        img = Image.alpha_composite(img, overlay).convert("RGB")
        img.save(image_path, quality=92)
    except Exception as e:
        print(f"Watermark failed: {e}", flush=True)


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


def run_tweet_task(task_id, text, saved_paths):
    try:
        def progress(current, total, msg):
            set_task(task_id, {
                "status": "running",
                "progress": msg,
                "current": current,
                "total": total,
            })

        set_task(task_id, {"status": "running", "progress": "Starting..."})
        result = client.post_tweet(text, saved_paths, progress_callback=progress)

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
        if saved_paths:
            for p in saved_paths:
                try:
                    os.remove(p)
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
    images: list[UploadFile] = File(default=None),
):
    saved_paths = []
    if images:
        for img in images:
            if img and img.filename:
                ext = os.path.splitext(img.filename)[1] or ".jpg"
                filename = f"{uuid.uuid4().hex}{ext}"
                saved_path = os.path.join(TEMP_DIR, filename)
                content = await img.read()
                with open(saved_path, "wb") as f:
                    f.write(content)
                add_watermark(saved_path)
                saved_paths.append(saved_path)

    task_id = uuid.uuid4().hex
    set_task(task_id, {"status": "pending", "progress": "Queued..."})

    executor.submit(run_tweet_task, task_id, text, saved_paths)

    return {"task_id": task_id}


@app.get("/api/task/{task_id}")
async def task_status(task_id: str):
    t = get_task(task_id)
    if t is None:
        return {"status": "not_found"}
    return t


@app.post("/api/tweet-sync")
async def tweet_sync(
    text: str = Form(...),
    images: list[UploadFile] = File(default=None),
):
    saved_paths = []
    try:
        if images:
            for img in images:
                if img and img.filename:
                    ext = os.path.splitext(img.filename)[1] or ".jpg"
                    filename = f"{uuid.uuid4().hex}{ext}"
                    saved_path = os.path.join(TEMP_DIR, filename)
                    content = await img.read()
                    with open(saved_path, "wb") as f:
                        f.write(content)
                    add_watermark(saved_path)
                    saved_paths.append(saved_path)

        result = await asyncio.to_thread(client.post_tweet, text, saved_paths)
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        for p in saved_paths:
            try:
                os.remove(p)
            except Exception:
                pass
