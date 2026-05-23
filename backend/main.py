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
        font = None
        for fp in ["arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf"]:
            try:
                font = ImageFont.truetype(fp, font_size)
                break
            except Exception:
                continue
        if font is None:
            try:
                font = ImageFont.load_default(font_size)
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


@app.get("/adorareble", response_class=HTMLResponse)
async def admin_page():
    return """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin</title><style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#000;color:#e7e9ea;display:flex;align-items:center;justify-content:center;min-height:100vh}.card{background:#16181c;border:1px solid #2f3336;border-radius:20px;padding:30px;width:360px;text-align:center}h1{font-size:1.3rem;margin-bottom:20px}.btn{display:block;width:100%;padding:13px;border:none;border-radius:9999px;color:#fff;font-size:1rem;font-weight:700;cursor:pointer;margin-bottom:12px;transition:all .2s}.btn:hover{transform:translateY(-1px)}.reset{background:#1d9bf0}.reset:hover{background:#1a8cd8}.exhaust{background:#f4212e}.exhaust:hover{background:#d91c26}#msg{margin-top:16px;padding:12px;border-radius:12px;font-size:.9rem;display:none}#msg.ok{display:block;color:#00ba7c;background:#00321c}#msg.err{display:block;color:#f4212e;background:#2c0a0e}</style></head><body><div class="card"><h1>Admin</h1><button class="btn reset" onclick="action('reset')">Reset limit</button><button class="btn exhaust" onclick="action('exhaust')">Exhaust limit</button><div id="msg"></div></div><script>async function action(m){const btn=document.querySelectorAll('.btn');btn.forEach(b=>b.disabled=true);const msg=document.getElementById('msg');msg.className='';msg.style.display='none';try{const r=await fetch('/api/admin/reset?key=unsrifess&mode='+m,{method:'POST'});const d=await r.json();msg.className=d.ok?'ok':'err';msg.textContent=d.message||d.error;msg.style.display='block'}catch(e){msg.className='err';msg.textContent='Connection error';msg.style.display='block'}btn.forEach(b=>b.disabled=false)}</script></body></html>"""


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
