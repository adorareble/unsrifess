import sys
import os
import json
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from twitter_client import STATE_FILE


def main():
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    print("=== Unsr!fess Login ===\n")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Installing Playwright...")
        os.system(f"{sys.executable} -m pip install playwright")
        os.system(f"{sys.executable} -m playwright install chromium")
        from playwright.sync_api import sync_playwright

    print("Opening browser for manual login to X.com...")
    print("A Chromium window will open. Log in to x.com, then come back and press Enter.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://x.com/login")
        input("Press Enter after logging in on the browser...")
        context.storage_state(path=STATE_FILE)
        browser.close()

    with open(STATE_FILE) as f:
        raw = json.load(f)
    cookies = {c["name"]: c["value"] for c in raw.get("cookies", [])}
    with open(STATE_FILE, "w") as f:
        json.dump(cookies, f, indent=2)
    print(f"\nLogin successful! Session saved to {STATE_FILE}")
    print(f"Cookies: {', '.join(cookies.keys())}")


if __name__ == "__main__":
    main()
