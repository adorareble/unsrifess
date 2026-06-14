import os
import math
import uuid
import asyncio
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp_images")
FONTS_DIR = os.path.join(BASE_DIR, "assets", "fonts")
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(FONTS_DIR, exist_ok=True)

WATERMARK_TEXT = "@unsrifess"
MAX_IMAGE_DIMENSION = 1920
JPEG_QUALITY = 80

CARD_WIDTH = 400
CARD_PADDING_X = 40
CARD_PADDING_Y = 32
CARD_FONT_SIZE = 20
CARD_FOOTER_LINK = "https://x.unsrifess.my.id"


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
        img.save(image_path, quality=JPEG_QUALITY, optimize=True)
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


def _load_card_font(size):
    font_paths = [
        os.path.join(FONTS_DIR, "PlusJakartaSans-VariableFont_wght.ttf"),
        os.path.join(FONTS_DIR, "PlusJakartaSans-SemiBold.ttf"),
        os.path.join(FONTS_DIR, "PlusJakartaSans-Bold.ttf"),
        "arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]
    for fp in font_paths:
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def _wrap_text(text, font, max_width, draw):
    lines = []
    for paragraph in text.split('\n'):
        words = paragraph.split(' ')
        current_line = ''
        for word in words:
            if not word:
                continue
            test_line = current_line + (' ' if current_line else '') + word
            bbox = draw.textbbox((0, 0), test_line, font=font)
            w = bbox[2] - bbox[0]
            if w <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                bbox_w = draw.textbbox((0, 0), word, font=font)
                ww = bbox_w[2] - bbox_w[0]
                if ww > max_width:
                    current_line = ''
                    for c in word:
                        test_c = current_line + c
                        bbox_c = draw.textbbox((0, 0), test_c, font=font)
                        cw = bbox_c[2] - bbox_c[0]
                        if cw > max_width and current_line:
                            lines.append(current_line)
                            current_line = c
                        else:
                            current_line = test_c
                else:
                    current_line = word
        if current_line:
            lines.append(current_line)
    return lines


def generate_card_image(text):
    CARD_BG = (21, 32, 43)
    CARD_TEXT_COLOR = (255, 255, 255)
    CARD_FOOTER_COLOR = (255, 255, 255, 179)
    CARD_SEP_COLOR = (255, 255, 255, 51)
    CARD_QUOTE_COLOR = (255, 255, 255, 26)
    FOOTER_FONT_SIZE = 11
    QUOTE_FONT_SIZE = 64
    LINE_SPACING = 1.55

    font = _load_card_font(CARD_FONT_SIZE)
    quote_font = _load_card_font(QUOTE_FONT_SIZE)
    footer_font = _load_card_font(FOOTER_FONT_SIZE)

    img_dummy = Image.new("RGB", (CARD_WIDTH, CARD_WIDTH), CARD_BG)
    draw_dummy = ImageDraw.Draw(img_dummy)

    max_text_width = CARD_WIDTH - 2 * CARD_PADDING_X
    wrapped_lines = _wrap_text(text, font, max_text_width, draw_dummy)
    line_height = int(CARD_FONT_SIZE * LINE_SPACING)
    text_block_height = len(wrapped_lines) * line_height

    quote_offset = 30
    text_vertical = CARD_PADDING_Y + quote_offset + text_block_height
    footer_zone = 40
    MIN_CARD_HEIGHT = 400
    card_height = max(MIN_CARD_HEIGHT, text_vertical + 20 + footer_zone + CARD_PADDING_Y)

    img = Image.new("RGB", (CARD_WIDTH, card_height), CARD_BG)
    draw = ImageDraw.Draw(img)

    draw.text((CARD_PADDING_X, CARD_PADDING_Y - 10), '"', font=quote_font, fill=CARD_QUOTE_COLOR)

    y = CARD_PADDING_Y + quote_offset - 10
    for line in wrapped_lines:
        draw.text((CARD_PADDING_X, y), line, font=font, fill=CARD_TEXT_COLOR)
        y += line_height

    sep_y = card_height - CARD_PADDING_Y - footer_zone
    draw.line(
        [(CARD_PADDING_X, sep_y), (CARD_WIDTH - CARD_PADDING_X, sep_y)],
        fill=CARD_SEP_COLOR, width=1,
    )

    footer_full = f"{CARD_FOOTER_LINK} \u00b7 generated by @unsrifess on X"
    bbox = draw.textbbox((0, 0), footer_full, font=footer_font)
    fw = bbox[2] - bbox[0]
    footer_x = CARD_PADDING_X
    draw.text((footer_x, sep_y + 14), footer_full, font=footer_font, fill=CARD_FOOTER_COLOR)

    filename = f"card_{uuid.uuid4().hex}.jpg"
    save_path = os.path.join(TEMP_DIR, filename)
    img.save(save_path, "JPEG", quality=85, optimize=True)
    return save_path


async def process_image_async(image_path):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda p: (compress_image(p), add_watermark(p)), image_path)
