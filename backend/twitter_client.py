import os
import json
import asyncio
import logging
from twikit import Client

STATE_FILE = os.path.join(
    os.environ.get("STATE_DIR", os.path.dirname(os.path.dirname(__file__))),
    "twitter_state.json"
)
MAX_CHARS = 280


def split_into_chunks(text, max_length=MAX_CHARS):
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text.strip()

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        split_at = -1
        for sep in [". ", ".\n", "!\n", "?\n", "\n\n"]:
            idx = remaining.rfind(sep, 0, max_length + 1)
            if idx > split_at:
                split_at = idx + len(sep)

        if split_at <= 0 or split_at > max_length:
            split_at = remaining.rfind(" ", 0, max_length + 1)

        if split_at <= 0:
            split_at = max_length

        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()

        if not chunk:
            chunks.append(remaining[:max_length])
            remaining = remaining[max_length:]

    return chunks


class TwitterClient:
    def __init__(self, state_file=STATE_FILE):
        self.state_file = state_file
        self._client = Client(
            "en-US",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
        self._ensure_state_format()

    def _ensure_state_format(self):
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file) as f:
                data = json.load(f)
            if "cookies" in data:
                cookies = {c["name"]: c["value"] for c in data["cookies"]}
            else:
                cookies = data
            cookies.pop("__cf_bm", None)
            with open(self.state_file, "w") as f:
                json.dump(cookies, f, indent=2)
            if "cookies" in data:
                logging.info("Converted Playwright state to twikit format")
        except Exception as e:
            logging.error(f"State conversion failed: {e}")

    def is_logged_in(self):
        if not os.path.exists(self.state_file):
            return False
        try:
            with open(self.state_file) as f:
                cookies = json.load(f)
            return bool(cookies.get("auth_token"))
        except Exception:
            return False

    async def post_tweet(self, text, image_paths=None, progress_callback=None):
        if not text or not text.strip():
            return {"success": False, "error": "Text is empty"}

        if not os.path.exists(self.state_file):
            return {
                "success": False,
                "error": "Not logged in. Run setup_login.py first.",
            }

        with open(self.state_file) as f:
            cookies = json.load(f)
        cookies.pop("__cf_bm", None)
        self._client.set_cookies(cookies, clear_cookies=True)

        if image_paths is None:
            image_paths = []

        chunks = split_into_chunks(text.strip())
        tweet_urls = []

        try:
            media_ids = []
            if image_paths:
                for idx, fp in enumerate(image_paths):
                    if progress_callback:
                        progress_callback(
                            idx + 1,
                            len(image_paths),
                            f"Uploading image {idx + 1} of {len(image_paths)}...",
                        )
                    media_id = await self._client.upload_media(fp)
                    media_ids.append(media_id)

            reply_to_id = None
            for i, chunk in enumerate(chunks):
                if progress_callback:
                    progress_callback(
                        i + 1,
                        len(chunks),
                        f"Posting tweet {i + 1} of {len(chunks)}...",
                    )

                kwargs = {"text": chunk}
                if media_ids and i == 0:
                    kwargs["media_ids"] = media_ids
                if reply_to_id:
                    kwargs["reply_to"] = reply_to_id

                tweet = await self._client.create_tweet(**kwargs)
                tweet_urls.append(f"https://x.com/{tweet.user.screen_name}/status/{tweet.id}")
                reply_to_id = tweet.id

                if i < len(chunks) - 1:
                    await asyncio.sleep(2)

            if progress_callback:
                progress_callback(len(chunks), len(chunks), "Done")

            return {"success": True, "urls": tweet_urls}

        except Exception as e:
            err_msg = str(e)
            if "403" in err_msg or "Forbidden" in err_msg or "Cloudflare" in err_msg or "blocked" in err_msg:
                err_msg = "Session blocked by Cloudflare. Run setup_login.py again to refresh session."
            logging.exception(f"post_tweet failed: {e}")
            if progress_callback:
                progress_callback(0, 0, f"Error: {err_msg}")
            return {"success": False, "error": err_msg}
