import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

def export_cookies():
    session_file = Path("fb_session.json")
    print("=" * 60)
    print("Facebook Cookie Exporter for GitHub Actions Automation")
    print("=" * 60)
    print("Launching Chromium browser...")
    print("Please log into your Facebook account in the opened window.")
    print("You will have 60 seconds to complete login.")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.facebook.com")

        # Wait for user to log in manually
        print("Waiting for login... (Press Enter in terminal when logged in, or wait 60s)")
        try:
            # Wait up to 60 seconds or until user presses enter
            page.wait_for_timeout(60000)
        except KeyboardInterrupt:
            pass

        # Save cookies and local storage state
        context.storage_state(path=session_file)
        print(f"\n[SUCCESS] Session saved to {session_file.resolve()}")
        browser.close()

    if session_file.exists():
        content = session_file.read_text(encoding="utf-8")
        print("\n" + "=" * 60)
        print("COPY THE CONTENT BELOW AND ADD IT AS A GITHUB SECRET 'FB_COOKIES_JSON':")
        print("=" * 60)
        print(content)
        print("=" * 60)

if __name__ == "__main__":
    export_cookies()
