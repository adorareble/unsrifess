import os
import sys
import gzip
import uuid
import base64
import asyncio
import math
import json
import time

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Form, UploadFile, File
from fastapi.responses import HTMLResponse
from twitter_client import TwitterClient
from PIL import Image, ImageDraw, ImageFont

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
        draw.text((tx, ty), WATERMARK_TEXT, font=font, fill=(255, 255, 255, 102))

        img = Image.alpha_composite(img, overlay).convert("RGB")
        img.save(image_path, quality=92)
    except Exception as e:
        print(f"Watermark failed: {e}", flush=True)


app = FastAPI(title="TwitterTools")
client = TwitterClient()

TEMP_DIR = os.environ.get("TEMP_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "temp_images"))
os.makedirs(TEMP_DIR, exist_ok=True)

RATE_LIMIT = 5
RATE_WINDOW = 3600
RATE_FILE = os.path.join(TEMP_DIR, "tweet_log.json")


def check_rate_limit():
    now = time.time()
    timestamps = []
    try:
        with open(RATE_FILE) as f:
            timestamps = json.load(f)
    except Exception:
        pass
    timestamps = [t for t in timestamps if now - t < RATE_WINDOW]
    remaining = max(0, RATE_LIMIT - len(timestamps))
    if len(timestamps) >= RATE_LIMIT:
        next_at = timestamps[0] + RATE_WINDOW
        return {"ok": False, "remaining": remaining, "reset_at": next_at, "message": f"Rate limit reached. Try again after {time.strftime('%H:%M', time.localtime(next_at))}."}
    return {"ok": True, "remaining": remaining, "timestamps": timestamps}


def log_tweet(timestamps):
    timestamps.append(time.time())
    with open(RATE_FILE, "w") as f:
        json.dump(timestamps, f)


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
    rate = check_rate_limit()
    resp = {"logged_in": logged_in, "remaining": rate["remaining"]}
    if not rate["ok"]:
        resp["rate_limited"] = True
        resp["reset_at"] = rate["reset_at"]
        resp["rate_message"] = rate["message"]
    return resp


@app.post("/api/tweet-sync")
async def tweet_sync(
    text: str = Form(...),
    images: list[UploadFile] = File(default=None),
):
    rate = check_rate_limit()
    if not rate["ok"]:
        return {"success": False, "error": rate["message"], "rate_limited": True, "reset_at": rate["reset_at"]}
    log_tweet(rate["timestamps"])

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


@app.post("/api/admin/reset")
async def admin_reset(key: str = "", mode: str = ""):
    if key != "unsrifess":
        return {"error": "invalid key"}
    try:
        if mode == "exhaust":
            now = time.time()
            timestamps = [now for _ in range(RATE_LIMIT)]
            with open(RATE_FILE, "w") as f:
                json.dump(timestamps, f)
            return {"ok": True, "message": "All slots exhausted"}
        if os.path.exists(RATE_FILE):
            os.remove(RATE_FILE)
        return {"ok": True, "message": "Rate limit reset"}
    except Exception as e:
        return {"error": str(e)}
