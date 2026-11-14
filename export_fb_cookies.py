from pathlib import Path
from playwright.sync_api import sync_playwright


def export_cookies():
    session_file = Path("fb_session.json")
    print("=" * 60)
    print("Facebook Cookie Exporter for GitHub Actions Automation")
    print("=" * 60)
    print("Launching Chromium browser...")
    print("Log into the Facebook account that can publish to the Page.")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto("https://www.facebook.com")
            input("After Facebook shows the logged-in feed, press Enter here to export the session...")
            body_text = page.locator("body").inner_text(timeout=10000).lower()
            if "use another profile" in body_text and "remove profiles from this browser" in body_text:
                raise RuntimeError(
                    "Facebook is still showing its saved-profile chooser. Select and log into "
                    "the correct account in the browser, then run this exporter again."
                )
            login_form = page.locator(
                'input[name="email"], input[autocomplete="username"], '
                'input[placeholder*="Email or phone" i]'
            ).first
            if login_form.is_visible():
                raise RuntimeError(
                    "Facebook still shows its login form. Log in before exporting the session."
                )

            context.storage_state(path=session_file)
            print(f"\n[SUCCESS] Session saved to {session_file.resolve()}")
        finally:
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
