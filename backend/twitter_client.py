import os
import time
from playwright.sync_api import sync_playwright

STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "twitter_state.json"
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
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-gpu",
                    ],
                )
                context = browser.new_context(storage_state=self.state_file)
                page = context.new_page()
                page.goto("https://x.com", wait_until="load", timeout=30000)
                logged = False
                try:
                    page.wait_for_selector(
                        'a[data-testid="AppTabBar_Profile_Link"]',
                        timeout=15000,
                    )
                    logged = True
                except Exception:
                    pass
                browser.close()
                return logged
        except Exception as e:
            import logging
            logging.error(f"is_logged_in failed: {e}")
            return False

    def login(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context()
            page = context.new_page()
            page.goto("https://x.com/login", wait_until="load")

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
                import logging
                logging.exception(f"Login failed: {e}")
                raise
            finally:
                browser.close()

    def post_tweet(self, text, image_path=None, progress_callback=None):
        if not text or not text.strip():
            return {"success": False, "error": "Text is empty"}

        if not os.path.exists(self.state_file):
            return {
                "success": False,
                "error": "Not logged in. Run setup_login.py first.",
            }

        chunks = split_into_chunks(text.strip())
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-gpu",
                ],
            )
            context = browser.new_context(
                storage_state=self.state_file,
                viewport={"width": 1280, "height": 720},
            )
            page = context.new_page()
            tweet_urls = []
            prev_tweet_url = None

            try:
                for i, chunk in enumerate(chunks):
                    if progress_callback:
                        progress_callback(
                            i + 1, len(chunks),
                            f"Posting tweet {i + 1} of {len(chunks)}..."
                        )

                    img = image_path if i == 0 and image_path else None
                    url = self._post_one(page, chunk, img, prev_tweet_url)
                    if url:
                        tweet_urls.append(url)
                        prev_tweet_url = url

                    if i < len(chunks) - 1:
                        time.sleep(2)

                if progress_callback:
                    progress_callback(len(chunks), len(chunks), "Done")

                return {"success": True, "urls": tweet_urls}
            except Exception as e:
                import logging
                logging.exception(f"post_tweet failed: {e}")
                if progress_callback:
                    progress_callback(0, 0, f"Error: {e}")
                return {"success": False, "error": str(e)}
            finally:
                browser.close()

    def _post_one(self, page, text, image_path=None, reply_to_url=None):
        tweet_id = [None]

        def capture_id(response):
            if "CreateTweet" not in response.url:
                return
            try:
                data = response.json()
                result = (
                    data.get("data", {})
                    .get("create_tweet", {})
                    .get("tweet_results", {})
                    .get("result", {})
                )
                rid = result.get("rest_id")
                if rid:
                    tweet_id[0] = rid
            except Exception:
                pass

        page.on("response", capture_id)

        try:
            if reply_to_url:
                page.goto(reply_to_url, wait_until="load", timeout=30000)
                time.sleep(3)
                page.keyboard.press("r")
                time.sleep(2)
            else:
                page.goto(
                    "https://x.com", wait_until="load", timeout=30000
                )
                time.sleep(2)
                post_btn = page.locator(
                    'a[data-testid="SideNav_NewTweet_Button"]'
                ).first
                if not post_btn.is_visible(timeout=5000):
                    raise Exception("Post button not found")
                post_btn.click()
                time.sleep(2)

            textbox = page.locator(
                '[data-testid="tweetTextarea_0"]'
            ).first
            if not textbox.is_visible(timeout=5000):
                textbox = page.locator('div[role="textbox"]').first
            textbox.wait_for(state="visible", timeout=10000)
            textbox.click()
            page.keyboard.type(text, delay=10)

            if image_path:
                self._upload_image(page, image_path)

            tweet_id[0] = None

            submit_btn = self._find_submit_button(page)
            submit_btn.click()

            try:
                page.wait_for_selector(
                    '[data-testid="tweetTextarea_0"]',
                    state="detached",
                    timeout=15000,
                )
            except Exception:
                pass

            time.sleep(2)

            tid = tweet_id[0]
            if tid:
                username = self._get_username(page)
                if username:
                    return f"https://x.com/{username}/status/{tid}"
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
