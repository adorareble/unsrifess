import os
import sys
import uuid
import math

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, Form, UploadFile, File
from fastapi.responses import HTMLResponse
from twitter_client import TwitterClient
from PIL import Image, ImageDraw, ImageFont

TEMP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "temp_images"
)
os.makedirs(TEMP_DIR, exist_ok=True)

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
        outline_range = max(1, font_size // 20)
        for ox in range(-outline_range, outline_range + 1):
            for oy in range(-outline_range, outline_range + 1):
                if ox == 0 and oy == 0:
                    continue
                draw.text((tx + ox, ty + oy), WATERMARK_TEXT, font=font, fill=(0, 0, 0, 153))
        draw.text((tx, ty), WATERMARK_TEXT, font=font, fill=(255, 255, 255, 102))

        img = Image.alpha_composite(img, overlay).convert("RGB")
        img.save(image_path, quality=92)
    except Exception as e:
        print(f"Watermark failed: {e}", flush=True)


app = FastAPI(title="TwitterTools")
client = TwitterClient()


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "frontend", "index.html"
    )
    with open(html_path, encoding="utf-8") as f:
        return f.read()


@app.get("/api/status")
async def status():
    return {
        "logged_in": client.is_logged_in(),
        "daily_remaining": client.daily_remaining(),
        "daily_limit": client.daily_limit(),
        "online": client.is_online(),
        "resets_at": client.next_reset(),
    }


@app.post("/api/reset-limit")
async def reset_limit():
    client.reset_daily_counter()
    return {"success": True, "daily_remaining": client.daily_remaining()}


@app.post("/api/set-limit")
async def set_limit(value: int = Form(...)):
    client.set_daily_limit(value)
    return {
        "success": True,
        "daily_limit": client.daily_limit(),
        "daily_remaining": client.daily_remaining(),
    }


@app.post("/api/set-online")
async def set_online(value: bool = Form(...)):
    client.set_online(value)
    return {"success": True, "online": client.is_online()}


@app.get("/adorareble", response_class=HTMLResponse)
async def admin_page():
    html_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "frontend", "admin.html"
    )
    with open(html_path, encoding="utf-8") as f:
        return f.read()


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

        result = await client.post_tweet(text, saved_paths)
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        for p in saved_paths:
            try:
                os.remove(p)
            except Exception:
                pass
