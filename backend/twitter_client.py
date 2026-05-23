import os
import time
import json
import logging
from playwright.sync_api import sync_playwright

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

    def is_logged_in(self):
        if not os.path.exists(self.state_file):
            return False
        try:
            with open(self.state_file, encoding="utf-8") as f:
                data = json.load(f)
            cookies = data.get("cookies", [])
            now = time.time()
            for c in cookies:
                if c.get("name") == "auth_token" and c.get("expires", 0) > now:
                    return True
            return False
        except Exception as e:
            logging.error(f"is_logged_in error: {e}")
            return False

    def login(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            context = browser.new_context()
            page = context.new_page()
            page.goto("https://x.com/login", wait_until="domcontentloaded", timeout=120000)

            print("\n=== Browser opened for login ===")
            print("Log in to X/Twitter manually in the browser window.")
            print("Waiting up to 5 minutes...\n")

            try:
                page.wait_for_selector(
                    'a[data-testid="AppTabBar_Profile_Link"]',
                    timeout=300000,
                )
                time.sleep(3)
                state_dir = os.path.dirname(self.state_file) or "."
                os.makedirs(state_dir, exist_ok=True)
                context.storage_state(path=self.state_file)
                print(f"Session saved to {self.state_file}")
            except Exception as e:
                logging.exception(f"Login failed: {e}")
                raise
            finally:
                browser.close()

    def post_tweet(self, text, image_paths=None, progress_callback=None):
        if not text or not text.strip():
            return {"success": False, "error": "Text is empty"}

        if not os.path.exists(self.state_file):
            return {
                "success": False,
                "error": "Not logged in. Run setup_login.py first.",
            }

        if image_paths is None:
            image_paths = []

        with open(self.state_file, encoding="utf-8") as f:
            state_data = json.load(f)
        saved_cookies = state_data.get("cookies", [])

        chunks = split_into_chunks(text.strip())

        max_retries = 2
        for attempt in range(max_retries):
            try:
                return self._post_tweet_attempt(
                    chunks, saved_cookies,
                    image_paths, progress_callback
                )
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(3)
                    continue
                raise

    def _post_tweet_attempt(self, chunks, saved_cookies, image_paths, progress_callback):
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--single-process",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--disable-translate",
                    "--no-first-run",
                    "--disable-default-apps",
                    "--disable-component-update",
                    "--no-crash-upload",
                    "--no-crash-diagnostics",
                    "--js-flags=--max-old-space-size=256",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 600},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            )
            context.add_cookies(saved_cookies)
            page = context.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
            """)
            tweet_urls = []
            prev_tweet_url = None

            try:
                for i, chunk in enumerate(chunks):
                    if progress_callback:
                        progress_callback(
                            i + 1, len(chunks),
                            f"Posting tweet {i + 1} of {len(chunks)}..."
                        )

                    imgs = image_paths if i == 0 and image_paths else None
                    url = self._post_one(page, chunk, imgs, prev_tweet_url)
                    if url:
                        tweet_urls.append(url)
                        prev_tweet_url = url

                    if i < len(chunks) - 1:
                        time.sleep(2)

                if progress_callback:
                    progress_callback(len(chunks), len(chunks), "Done")

                return {"success": True, "urls": tweet_urls}
            except Exception as e:
                logging.exception(f"post_tweet failed: {e}")
                if progress_callback:
                    progress_callback(0, 0, f"Error: {e}")
                return {"success": False, "error": str(e)}
            finally:
                browser.close()

    def _post_one(self, page, text, image_paths=None, reply_to_url=None):
        tweet_url = [None]
        tweet_error = [None]

        def capture_id(response):
            if "CreateTweet" not in response.url:
                return
            try:
                data = response.json()
                errors = data.get("errors")
                if errors:
                    tweet_error[0] = errors[0].get("message", "X blocked the tweet")
                    return
                result = (
                    data.get("data", {})
                    .get("create_tweet", {})
                    .get("tweet_results", {})
                    .get("result", {})
                )
                rid = result.get("rest_id")
                if rid:
                    tweet_url[0] = rid
            except Exception:
                pass

        page.on("response", capture_id)

        try:
            if reply_to_url:
                page.goto(reply_to_url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)
                page.keyboard.press("r")
                time.sleep(2)
            else:
                page.goto(
                    "https://x.com/home", wait_until="domcontentloaded", timeout=90000
                )
                time.sleep(8)

            selectors = [
                '[data-testid="tweetTextarea_0"]',
                'div[role="textbox"]',
                '[data-testid="tweetTextarea_1"]',
            ]
            textbox = None
            for sel in selectors:
                try:
                    tb = page.locator(sel).first
                    tb.wait_for(state="visible", timeout=15000)
                    textbox = tb
                    break
                except Exception:
                    continue
            if not textbox:
                raise Exception("Could not find tweet textbox")

            textbox.click()
            time.sleep(1)
            page.keyboard.type(text, delay=10)

            if image_paths:
                for fp in image_paths:
                    self._upload_image(page, fp)

            tweet_url[0] = None

            submit_btn = self._find_submit_button(page)
            submit_btn.hover()
            time.sleep(0.5 + (time.time() % 1))
            submit_btn.click()

            try:
                page.wait_for_selector(
                    '[data-testid="tweetTextarea_0"]',
                    state="detached",
                    timeout=15000,
                )
            except Exception:
                pass

            for _ in range(15):
                if tweet_url[0] is not None:
                    break
                if tweet_error[0] is not None:
                    raise Exception(tweet_error[0])
                time.sleep(1)

            tid = tweet_url[0]
            if tid:
                username = self._get_username(page)
                if username:
                    return f"https://x.com/{username}/status/{tid}"

            if tweet_error[0]:
                raise Exception(tweet_error[0])

            return None
        finally:
            page.remove_listener("response", capture_id)

    def _upload_image(self, page, file_path):
        if not file_path or not os.path.exists(file_path):
            return

        try:
            input_el = page.locator('input[type="file"]').first
            if input_el.count() > 0:
                input_el.set_input_files(file_path)
                time.sleep(3)
                return
        except Exception:
            pass

        try:
            media_btn = page.locator(
                'div[data-testid="attachmentsButton"]'
            ).first
            if media_btn.is_visible(timeout=3000):
                with page.expect_file_chooser() as fc_info:
                    media_btn.click()
                fc = fc_info.value
                fc.set_files(file_path)
                time.sleep(3)
        except Exception:
            pass

    def _find_submit_button(self, page):
        for selector in [
            'div[data-testid="tweetButton"]',
            'button[data-testid="tweetButton"]',
            'div[data-testid="tweetButtonInline"]',
            'button[data-testid="tweetButtonInline"]',
        ]:
            btn = page.locator(selector).first
            try:
                btn.wait_for(state="visible", timeout=5000)
                return btn
            except Exception:
                continue
        raise Exception("Could not find tweet submit button")

    def _get_username(self, page):
        try:
            href = page.evaluate(
                """
                () => document.querySelector(
                    'a[data-testid="AppTabBar_Profile_Link"]'
                )?.getAttribute('href')
            """
            )
            if href:
                return href.strip("/")
        except Exception:
            pass
        return None
