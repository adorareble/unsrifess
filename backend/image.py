import os
import math
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp_images")
os.makedirs(TEMP_DIR, exist_ok=True)

WATERMARK_TEXT = "@unsrifess"
MAX_IMAGE_DIMENSION = 1920
JPEG_QUALITY = 80


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


def compress_image(image_path):
    try:
        img = Image.open(image_path)
        if img.mode == "RGBA":
            img = img.convert("RGB")

        w, h = img.size
        if max(w, h) > MAX_IMAGE_DIMENSION:
            ratio = MAX_IMAGE_DIMENSION / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        img.save(image_path, "JPEG", quality=JPEG_QUALITY, optimize=True)
    except Exception as e:
        print(f"Compress failed: {e}", flush=True)
