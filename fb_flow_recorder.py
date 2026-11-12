"""
fb_flow_recorder.py
====================
Run this script LOCALLY to:
  1. Open a VISIBLE browser logged into your Facebook session
  2. Navigate to the Gossip Hub page (Switch Now handled automatically)
  3. PAUSE — you perform the Photo/Video upload flow manually
  4. Record every click (aria-label, role, text, placeholder) to click_log.json
  5. Save a full Playwright trace to trace.zip (view with: playwright show-trace trace.zip)

HOW TO RUN:
-----------
  Option A — using fb_cookies.json file (place it next to this script):
      python fb_flow_recorder.py

  Option B — using environment variable:
      set FB_COOKIES_JSON=<paste your json here>
      python fb_flow_recorder.py

REQUIREMENTS:
  pip install playwright
  playwright install chromium
"""

import os
import sys
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_FB_PAGE_ID = "100081981528810"
PAGE_URL = f"https://www.facebook.com/profile.php?id={DEFAULT_FB_PAGE_ID}"


def banner(msg: str):
    border = "=" * 62
    print(f"\n{border}")
    print(f"  {msg}")
    print(f"{border}\n")


def log(*args):
    print("[RECORDER]", *args)
    sys.stdout.flush()


def load_session() -> str:
    """Load FB session JSON from local file or environment variable."""
    # Priority 1: local cookie files
    for fname in ["fb_cookies.json", "temp_fb_session.json", "fb_session.json"]:
        p = Path(fname)
        if p.exists():
            log(f"Loaded session from local file: {fname}")
            return p.read_text(encoding="utf-8")

    # Priority 2: env var
    env_val = os.getenv("FB_COOKIES_JSON", "")
    if env_val.strip():
        log("Loaded session from FB_COOKIES_JSON environment variable.")
        return env_val

    log("ERROR: No session found!")
    log("  Place your exported fb_cookies.json in the same folder as this script, OR")
    log("  set the FB_COOKIES_JSON environment variable.")
    sys.exit(1)


CLICK_RECORDER_SCRIPT = """
(() => {
    if (window.__recorderInjected) return;
    window.__recorderInjected = true;

    const getInfo = (el) => {
        let target = el;
        for (let i = 0; i < 5; i++) {
            if (!target || target === document.body) break;
            const role = target.getAttribute('role');
            const label = target.getAttribute('aria-label');
            const ph = target.getAttribute('aria-placeholder');
            const ce = target.getAttribute('contenteditable');
            if (role || label || ph || ce) break;
            target = target.parentElement;
        }
        const isEditable = el.matches?.('input, textarea, [contenteditable="true"]') || el.isContentEditable;
        return {
            tag: el.tagName,
            role: target?.getAttribute('role'),
            ariaLabel: target?.getAttribute('aria-label'),
            ariaPlaceholder: target?.getAttribute('aria-placeholder'),
            contenteditable: target?.getAttribute('contenteditable'),
            dataLexical: target?.getAttribute('data-lexical-editor'),
            innerText: isEditable ? '' : (target?.innerText || '').slice(0, 100).trim(),
            id: target?.id || null,
        };
    };

    const report = (event, el, extra = {}) => {
        const info = {event, ...getInfo(el), ...extra};
        if (window.__recordAction) window.__recordAction(info);
    };

    document.addEventListener('click', e => report('click', e.target), true);
    document.addEventListener('input', e => {
        const el = e.target;
        const value = el.value || el.innerText || '';
        report('input', el, {inputType: el.type || el.tagName, valueLength: value.length});
    }, true);
    document.addEventListener('change', e => {
        const el = e.target;
        report('change', el, {
            inputType: el.type || el.tagName,
            selectedFileCount: el.files ? el.files.length : undefined,
        });
    }, true);
    document.addEventListener('keydown', e => {
        if (['Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
            report('keydown', e.target, {key: e.key});
        }
    }, true);
})();
"""


def print_click_log(click_log: list) -> None:
    banner("RECORDED ACTIONS")
    for i, c in enumerate(click_log, 1):
        parts = [f"#{i:02d}", f"event={c.get('event', 'click')}"]
        if c.get("url"): parts.append(f"url={c['url']}")
        if c.get("tag"):          parts.append(f"tag={c['tag']}")
        if c.get("role"):         parts.append(f"role={c['role']}")
        if c.get("ariaLabel"):    parts.append(f"aria-label={repr(c['ariaLabel'])}")
        if c.get("ariaPlaceholder"): parts.append(f"aria-placeholder={repr(c['ariaPlaceholder'])}")
        if c.get("inputType"):    parts.append(f"input-type={c['inputType']}")
        if c.get("valueLength") is not None: parts.append(f"value-length={c['valueLength']} (value omitted)")
        if c.get("selectedFileCount") is not None: parts.append(f"selected-files={c['selectedFileCount']} (names omitted)")
        if c.get("key"):          parts.append(f"key={c['key']}")
        if c.get("contenteditable"): parts.append(f"contenteditable={c['contenteditable']}")
        if c.get("innerText"):    parts.append(f"text={repr(c['innerText'][:60])}")
        print("  " + " | ".join(parts))
    print()


def main():
    login_first = "--login-first" in sys.argv
    temp_session = Path("temp_recorder_session.json")
    if login_first:
        temp_session.unlink(missing_ok=True)
    else:
        cookies_json = load_session()
        temp_session.write_text(cookies_json, encoding="utf-8")

    banner("FB FLOW RECORDER — STARTING VISIBLE BROWSER")
    log(f"Target Page URL: {PAGE_URL}")
    if login_first:
        log("A fresh browser session will open. Sign in manually; credentials are not recorded.")
    log("The browser will navigate to the target profile.")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=200,          # slight slow-down so clicks are readable
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ]
        )

        context_options = {
            "viewport": None,        # use the maximized window size
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        }
        if not login_first:
            context_options["storage_state"] = str(temp_session)
        context = browser.new_context(**context_options)

        click_log = []
        context.expose_binding("__recordAction", lambda source, info: click_log.append(info))
        context.add_init_script(CLICK_RECORDER_SCRIPT)

        page = context.new_page()

        def record_navigation(frame):
            if frame == page.main_frame:
                parsed = urlsplit(frame.url)
                safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                click_log.append({"event": "navigation", "url": safe_url})

        page.on("framenavigated", record_navigation)

        if login_first:
            log("Opening Facebook sign-in. Complete sign-in and any security check in the browser.")
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=60000)
            input(">>> After you are fully logged in, press ENTER here to save the session and continue: ")
            login_form = page.locator('input[name="email"], input[autocomplete="username"]').first
            if login_form.is_visible():
                raise RuntimeError("Facebook still shows its login form; complete sign-in before continuing.")
            context.storage_state(path="fb_session.json")
            session_bytes = Path("fb_session.json").stat().st_size
            log(f"Fresh session saved locally to fb_session.json ({session_bytes} bytes); values were not printed.")

        context.tracing.start(screenshots=True, snapshots=True, sources=True)

        # ── Step 1: Facebook home ────────────────────────────────────────────
        log("Step 1: Navigating to https://www.facebook.com/ ...")
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        log(f"Loaded: {page.title()}")

        if "login" in page.url.lower():
            log("ERROR: Session is expired — browser was redirected to Facebook login.")
            log("Please re-export your cookies and try again.")
            context.tracing.stop(path="trace.zip")
            browser.close()
            temp_session.unlink()
            sys.exit(1)

        # ── Step 2: Gossip Hub page ──────────────────────────────────────────
        log(f"Step 2: Navigating to Gossip Hub page: {PAGE_URL}")
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        log(f"Loaded: {page.title()}")

        # ── Step 3: Handle "Switch Now" ──────────────────────────────────────
        try:
            switch_btn = page.locator(
                'div[role="button"]:has-text("Switch Now"), button:has-text("Switch Now")'
            ).first
            if switch_btn.is_visible(timeout=6000):
                log("Found 'Switch Now' — clicking to switch into Gossip Hub page profile...")
                switch_btn.click()
                page.wait_for_timeout(7000)
                log("Switched to Gossip Hub page profile successfully!")
        except Exception:
            log("No 'Switch Now' button found — already in page context.")

        page.wait_for_timeout(2000)

        # ── Inject click recorder ────────────────────────────────────────────
        banner(
            "BROWSER READY - YOU ARE ON THE TARGET PROFILE\n"
            "  Perform the desired flow manually; stop before Post.\n"
            "  Stop before Post. Return here and press ENTER when done."
        )

        input(">>> Press ENTER here when you have finished the upload flow: ")

        # ── Collect results ──────────────────────────────────────────────────
        log("Collecting recorded data...")

        # Screenshot of final state
        try:
            page.screenshot(path="recorder_final_state.png", full_page=False)
            log("Screenshot saved: recorder_final_state.png")
        except Exception as e:
            log(f"Screenshot failed: {e}")

        # Click log
        try:
            print_click_log(click_log)
            with open("click_log.json", "w", encoding="utf-8") as f:
                json.dump(click_log, f, indent=2, ensure_ascii=False)
            log("Click log saved: click_log.json")
        except Exception as e:
            log(f"Could not retrieve click log: {e}")
            click_log = []

        # Playwright trace
        try:
            context.tracing.stop(path="trace.zip")
            log("Trace saved: trace.zip")
            log("  → View it with:  playwright show-trace trace.zip")
        except Exception as e:
            log(f"Could not save trace: {e}")

        browser.close()

    # Cleanup temp session
    if temp_session.exists():
        temp_session.unlink()

    banner("RECORDING COMPLETE")
    log("Files saved in this directory:")
    log("  click_log.json         — list of every element you clicked")
    log("  trace.zip              — full Playwright trace (screenshots + DOM snapshots)")
    log("  recorder_final_state.png — screenshot of the final page state")
    log()
    log("Share click_log.json with your developer to fix the automation selectors.")


if __name__ == "__main__":
    main()
