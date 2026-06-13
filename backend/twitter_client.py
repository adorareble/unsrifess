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
        self._cookies_cache = None
        self._cookies_mtime = 0
        self._ensure_state_format()

    def _load_cookies(self):
        try:
            mtime = os.path.getmtime(self.state_file)
        except OSError:
            return None
        if mtime == self._cookies_mtime and self._cookies_cache is not None:
            return self._cookies_cache
        try:
            with open(self.state_file) as f:
                data = json.load(f)
            if "cookies" in data:
                cookies = {c["name"]: c["value"] for c in data["cookies"]}
            else:
                cookies = data
            cookies.pop("__cf_bm", None)
            self._cookies_cache = cookies
            self._cookies_mtime = mtime
            return cookies
        except Exception as e:
            logging.error(f"Failed to load state: {e}")
            return None

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
        cookies = self._load_cookies()
        return bool(cookies and cookies.get("auth_token"))

    async def post_tweet(self, text, image_paths=None, progress_callback=None):
        if not text or not text.strip():
            return {"success": False, "error": "Text is empty"}

        cookies = self._load_cookies()
        if cookies is None:
            return {
                "success": False,
                "error": "Not logged in. Run setup_login.py first.",
            }
        self._client.set_cookies(cookies, clear_cookies=True)

        if image_paths is None:
            image_paths = []

        stripped = text.strip()
        logging.info(
            f"=== post_tweet called === text.len={len(stripped)} cp "
            f"text.repr={repr(stripped[:200])}...{repr(stripped[-200:])}"
        )

        chunks = split_into_chunks(stripped)
        logging.info(f"--- post_tweet: {len(chunks)} chunks from {len(text)} cp text ---")
        for ci, c in enumerate(chunks):
            logging.info(
                f"  chunk[{ci}] len={len(c)} cp | "
                f"start={repr(c[:80])} | "
                f"end={repr(c[-80:])}"
            )
        tweet_urls = []
        reply_to_id = None
        posted = 0

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

                logging.info(
                    f"  -> sending chunk[{i}] len={len(chunk)} cp "
                    f"reply_to={reply_to_id} "
                    f"text={repr(chunk)}"
                )
                tweet = await self._client.create_tweet(**kwargs)
                if tweet is not None:
                    tweet_text = getattr(tweet, 'text', None) or getattr(tweet, 'full_text', '') or ''
                    logging.info(
                        f"  <- response chunk[{i}] id={tweet.id} "
                        f"text={repr(tweet_text)[:300]}"
                    )

                if tweet is None:
                    err_msg = f"Failed to post tweet {i + 1}: create_tweet returned None"
                    logging.error(err_msg)
                    if posted == 0:
                        raise Exception(err_msg)
                    break

                tweet_urls.append(
                    f"https://x.com/{tweet.user.screen_name}/status/{tweet.id}"
                )
                reply_to_id = tweet.id
                posted += 1

                if i < len(chunks) - 1:
                    await asyncio.sleep(2)

            if progress_callback:
                progress_callback(len(chunks), len(chunks), "Done")

            if posted == len(chunks):
                return {"success": True, "urls": tweet_urls}

            return {
                "success": True,
                "urls": tweet_urls,
                "partial": True,
                "warning": f"Only {posted} of {len(chunks)} tweets posted.",
            }

        except Exception as e:
            err_msg = str(e)
            if "403" in err_msg or "Forbidden" in err_msg or "Cloudflare" in err_msg or "blocked" in err_msg:
                err_msg = "Session blocked by Cloudflare. Run setup_login.py again to refresh session."
            logging.exception(f"post_tweet failed: {e}")

            if tweet_urls:
                return {
                    "success": True,
                    "urls": tweet_urls,
                    "partial": True,
                    "warning": f"Posted {len(tweet_urls)} of {len(chunks)} tweets before error: {err_msg}",
                }

            if progress_callback:
                progress_callback(0, 0, f"Error: {err_msg}")
            return {"success": False, "error": err_msg}


    async def check_mutual(self, target_screen_name: str) -> dict:
        cookies = self._load_cookies()
        if cookies is None:
            return {"error": "Not logged in. Run setup_login.py first."}
        self._client.set_cookies(cookies, clear_cookies=True)

        try:
            unsrifess = await self._client.get_user_by_screen_name("unsrifess")
            unsrifess_id = unsrifess.id

            raw_resp, _ = await self._client.gql.user_by_screen_name(target_screen_name)
            target_raw = raw_resp["data"]["user"]["result"]
            target_id = target_raw.get("rest_id")
            legacy = target_raw.get("legacy", {})

            we_follow = legacy.get("followed_by", False)
            follows_us = legacy.get("following", False)

            logging.info(
                f"check_mutual({target_screen_name}): "
                f"target_id={target_id}, "
                f"we_follow={we_follow}, follows_us={follows_us}"
            )

            return {
                "is_mutual": we_follow and follows_us,
                "follows_us": follows_us,
                "we_follow": we_follow,
                "target_id": str(target_id) if target_id else "",
                "unsrifess_id": str(unsrifess_id),
                "screen_name": target_screen_name,
            }
        except Exception as e:
            logging.exception(f"check_mutual failed: {e}")
            return {"error": str(e)}

    async def follow_user(self, target_id: str) -> dict:
        cookies = self._load_cookies()
        if cookies is None:
            return {"success": False, "error": "Not logged in"}
        self._client.set_cookies(cookies, clear_cookies=True)

        try:
            result = await self._client.follow_user(target_id)
            return {"success": True}
        except Exception as e:
            logging.exception(f"follow_user failed: {e}")
            return {"success": False, "error": str(e)}

    async def unfollow_user(self, target_id: str) -> dict:
        cookies = self._load_cookies()
        if cookies is None:
            return {"success": False, "error": "Not logged in"}
        self._client.set_cookies(cookies, clear_cookies=True)

        try:
            result = await self._client.unfollow_user(target_id)
            return {"success": True}
        except Exception as e:
            logging.exception(f"unfollow_user failed: {e}")
            return {"success": False, "error": str(e)}

    async def delete_tweet(self, tweet_id_str: str) -> dict:
        cookies = self._load_cookies()
        if cookies is None:
            return {"success": False, "error": "Not logged in"}
        self._client.set_cookies(cookies, clear_cookies=True)

        try:
            await self._client.delete_tweet(tweet_id_str)
            return {"success": True}
        except Exception as e:
            logging.exception(f"delete_tweet failed: {e}")
            return {"success": False, "error": str(e)}


client = TwitterClient()
