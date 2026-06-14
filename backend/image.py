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

CARD_WIDTH = 800
CARD_PADDING = 72
CARD_FONT_SIZE = 32
ACCENT_COLOR = (29, 155, 240)
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


def _wrap_text_justified(text, font, max_width, draw):
    lines = []
    space_w = draw.textbbox((0, 0), ' ', font=font)
    sw = space_w[2] - space_w[0]
    for paragraph in text.split('\n'):
        words = paragraph.split(' ')
        current = []
        current_w = 0
        for word in words:
            if not word:
                continue
            wb = draw.textbbox((0, 0), word, font=font)
            ww = wb[2] - wb[0]
            if current:
                test_w = current_w + sw + ww
            else:
                test_w = ww
            if test_w <= max_width:
                current.append(word)
                current_w = current_w + (sw if current_w else 0) + ww
            else:
                if current:
                    lines.append((list(current), current_w))
                else:
                    lines.append(([word], ww))
                current = [word]
                current_w = ww
        if current:
            lines.append((list(current), current_w))
    return lines, sw


def _draw_x_logo(draw, x, y, size, color):
    t = max(1, size // 6)
    m = size * 0.24
    draw.line([(x + m, y + m), (x + size - m, y + size - m)], fill=color, width=t)
    draw.line([(x + size - m, y + m), (x + m, y + size - m)], fill=color, width=t)


def generate_card_image(text):
    BG = (21, 32, 43)
    TEXT_COLOR = (255, 255, 255)
    FOOTER_COLOR = (255, 255, 255, 179)
    SEP_COLOR = (255, 255, 255, 40)
    ACCENT = ACCENT_COLOR
    FOOTER_FONT_SIZE = 18
    LINE_SPACING = 1.5

    font = _load_card_font(CARD_FONT_SIZE)
    footer_font = _load_card_font(FOOTER_FONT_SIZE)

    dummy = Image.new("RGB", (CARD_WIDTH, CARD_WIDTH), BG)
    draw_d = ImageDraw.Draw(dummy)

    max_text_width = CARD_WIDTH - 2 * CARD_PADDING - 16
    wrapped_lines, space_w = _wrap_text_justified(text, font, max_text_width, draw_d)
    line_height = int(CARD_FONT_SIZE * LINE_SPACING)
    text_block_height = len(wrapped_lines) * line_height

    logo_size = 14
    footer_prefix = f"{CARD_FOOTER_LINK} \u00b7 generated by @unsrifess on "
    fb_prefix = draw_d.textbbox((0, 0), footer_prefix, font=footer_font)
    fb_w_prefix = fb_prefix[2] - fb_prefix[0]
    fb_h = fb_prefix[3] - fb_prefix[1]
    gap_after_text = 24
    gap_after_sep = 14

    content_top_extra = 4
    content_block_h = content_top_extra + text_block_height + gap_after_text + 1 + gap_after_sep + fb_h
    min_height = int(CARD_WIDTH * 0.55)
    card_height = max(min_height, content_block_h + 2 * CARD_PADDING)

    content_origin = (card_height - content_block_h) // 2

    img = Image.new("RGB", (CARD_WIDTH, card_height), BG)
    draw = ImageDraw.Draw(img)

    text_x = CARD_PADDING + 16
    text_y = content_origin + content_top_extra
    indent_left = CARD_PADDING + 4
    bar_x = indent_left
    bar_top = text_y - 4
    bar_bottom = text_y + text_block_height + 4
    bar_w = 4
    draw.rectangle([bar_x, bar_top, bar_x + bar_w, bar_bottom], fill=ACCENT)

    for i, (words, line_w) in enumerate(wrapped_lines):
        if i < len(wrapped_lines) - 1 and len(words) > 1 and line_w < max_text_width:
            extra = max_text_width - line_w
            gaps = len(words) - 1
            extra_per_gap = extra / gaps
            x = text_x
            for word in words:
                draw.text((x, text_y), word, font=font, fill=TEXT_COLOR)
                wb = draw.textbbox((0, 0), word, font=font)
                x += (wb[2] - wb[0]) + space_w + extra_per_gap
        else:
            draw.text((text_x, text_y), ' '.join(words), font=font, fill=TEXT_COLOR)
        text_y += line_height

    sep_y = content_origin + content_top_extra + text_block_height + gap_after_text
    draw.line(
        [(CARD_PADDING, sep_y), (CARD_WIDTH - CARD_PADDING, sep_y)],
        fill=SEP_COLOR, width=1,
    )

    fb_full_w = fb_w_prefix + logo_size + 4
    footer_x = (CARD_WIDTH - fb_full_w) // 2
    footer_y = sep_y + gap_after_sep
    draw.text((footer_x, footer_y), footer_prefix, font=footer_font, fill=FOOTER_COLOR)
    logo_x = footer_x + fb_w_prefix + 2
    logo_y = footer_y + (fb_h - logo_size) // 2 + 1
    _draw_x_logo(draw, logo_x, logo_y, logo_size, FOOTER_COLOR)

    filename = f"card_{uuid.uuid4().hex}.jpg"
    save_path = os.path.join(TEMP_DIR, filename)
    img.save(save_path, "JPEG", quality=92, optimize=True)
    return save_path


async def process_image_async(image_path):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda p: (compress_image(p), add_watermark(p)), image_path)
