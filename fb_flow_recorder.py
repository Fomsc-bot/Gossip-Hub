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
from playwright.sync_api import sync_playwright

DEFAULT_FB_PAGE_ID = "61584777925866"
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


def inject_click_recorder(page) -> None:
    """
    Inject a JS listener that captures metadata about every clicked element.
    Results are stored in window.__clickLog and also printed via console.log.
    """
    page.evaluate("""
    () => {
        if (window.__recorderInjected) return;
        window.__recorderInjected = true;
        window.__clickLog = [];

        const getInfo = (el) => {
            // Walk up a few levels to find the meaningful role/label
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
            return {
                tag:              el.tagName,
                role:             target.getAttribute('role'),
                ariaLabel:        target.getAttribute('aria-label'),
                ariaPlaceholder:  target.getAttribute('aria-placeholder'),
                contenteditable:  target.getAttribute('contenteditable'),
                dataLexical:      target.getAttribute('data-lexical-editor'),
                innerText:        (target.innerText || '').slice(0, 100).trim(),
                id:               target.id || null,
            };
        };

        document.addEventListener('click', (e) => {
            const info = getInfo(e.target);
            window.__clickLog.push(info);
        }, true);

        console.log('[RECORDER] Click recorder active.');
    }
    """)


def print_click_log(click_log: list) -> None:
    banner("RECORDED CLICKS")
    for i, c in enumerate(click_log, 1):
        parts = [f"#{i:02d}"]
        if c.get("tag"):          parts.append(f"tag={c['tag']}")
        if c.get("role"):         parts.append(f"role={c['role']}")
        if c.get("ariaLabel"):    parts.append(f"aria-label={repr(c['ariaLabel'])}")
        if c.get("ariaPlaceholder"): parts.append(f"aria-placeholder={repr(c['ariaPlaceholder'])}")
        if c.get("contenteditable"): parts.append(f"contenteditable={c['contenteditable']}")
        if c.get("innerText"):    parts.append(f"text={repr(c['innerText'][:60])}")
        print("  " + " | ".join(parts))
    print()


def main():
    cookies_json = load_session()

    # Write temp session file
    temp_session = Path("temp_recorder_session.json")
    temp_session.write_text(cookies_json, encoding="utf-8")

    banner("FB FLOW RECORDER — STARTING VISIBLE BROWSER")
    log(f"Target Page URL: {PAGE_URL}")
    log("The browser will navigate to your Gossip Hub page.")
    log("Once the page is ready, perform the upload flow MANUALLY.")
    log("Come back here and press ENTER when you are finished.")

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

        context = browser.new_context(
            storage_state=str(temp_session),
            viewport=None,        # use the maximized window size
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        )

        # Start full Playwright trace recording
        context.tracing.start(screenshots=True, snapshots=True, sources=True)

        page = context.new_page()

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
        inject_click_recorder(page)
        # Re-inject on every navigation (Facebook is a SPA)
        page.on("framenavigated", lambda _: inject_click_recorder(page))

        banner(
            "BROWSER READY — YOU ARE ON THE GOSSIP HUB PAGE\n"
            "  Now manually: Photo/video → Upload video → Caption\n"
            "              → Next → Next → Describe → Post\n"
            "  Come back here when done and press ENTER."
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
            click_log = page.evaluate("() => window.__clickLog || []")
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
