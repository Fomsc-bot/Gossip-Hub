import os
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

DEFAULT_FB_PAGE_ID = "61584777925866"

def log(*args):
    print("[FB-PLAYWRIGHT]", *args)
    sys.stdout.flush()

def build_caption_with_hashtags(raw_caption: str) -> str:
    """
    Ensure caption has relevant hashtags with total hashtags <= 5.
    """
    HASHTAG_POOL = [
        "#News",
        "#Update",
        "#shorts",
        "#trending",
        "#viral",
    ]
    words = raw_caption.split()
    existing_tags = [w for w in words if w.startswith("#")]
    
    if len(existing_tags) > 5:
        clean_text = " ".join([w for w in words if not w.startswith("#")])
        tags_str = " ".join(existing_tags[:5])
        return f"{clean_text.strip()} {tags_str}".strip()

    remaining_slots = 5 - len(existing_tags)
    tags_to_add = [t for t in HASHTAG_POOL if t not in existing_tags][:remaining_slots]
    parts = [raw_caption.strip()] + tags_to_add
    return " ".join(parts).strip()


def safe_click(page, locator, label: str, timeout: int = 30000) -> bool:
    """
    Wait for element to be visible and enabled, then click it.
    Falls back to JavaScript click if force=True fails.
    """
    try:
        log(f"Waiting for '{label}' button (timeout={timeout}ms)...")
        locator.wait_for(state="visible", timeout=timeout)
        log(f"'{label}' is visible. Clicking...")
        try:
            locator.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            pass
        locator.click(force=True, timeout=10000)
        log(f"'{label}' clicked successfully.")
        return True
    except PlaywrightTimeoutError:
        log(f"WARNING: '{label}' not visible within {timeout}ms.")
        return False
    except Exception as e:
        log(f"WARNING: Standard click on '{label}' failed: {e}. Trying JS click...")
        try:
            locator.evaluate("el => el.click()")
            log(f"'{label}' JS-clicked successfully.")
            return True
        except Exception as e2:
            log(f"ERROR: JS click on '{label}' failed: {e2}")
            return False


def click_next_button(page, step_name: str, timeout: int = 40000) -> bool:
    """
    Polls for any visible & enabled 'Next' button (aria-disabled != 'true').
    Retries up to timeout ms to handle step transitions & video processing delays.
    """
    log(f"Waiting for '{step_name}' button (timeout={timeout}ms)...")
    start_time = time.time()
    while (time.time() - start_time) * 1000 < timeout:
        try:
            next_btns = page.locator(
                'div[aria-label="Next"][role="button"], '
                'div[role="button"]:has-text("Next"), '
                'button:has-text("Next")'
            )
            count = next_btns.count()
            for i in range(count):
                btn = next_btns.nth(i)
                if btn.is_visible():
                    aria_disabled = btn.get_attribute("aria-disabled")
                    if aria_disabled == "true":
                        log(f"'{step_name}' found at index {i} but aria-disabled=true. Waiting for video processing...")
                        continue
                    log(f"'{step_name}' found at index {i} and is enabled. Clicking...")
                    try:
                        btn.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    try:
                        btn.click(force=True, timeout=5000)
                    except Exception:
                        btn.evaluate("el => el.click()")
                    log(f"'{step_name}' clicked successfully!")
                    return True
        except Exception as e:
            log(f"Retry in click_next_button ({step_name}): {e}")
        time.sleep(2)

    log(f"WARNING: '{step_name}' button not found or not clickable within {timeout}ms.")
    return False


def click_post_button(page, timeout: int = 40000) -> bool:
    """
    Polls for any visible & enabled 'Post' button (aria-disabled != 'true').
    """
    log(f"Waiting for 'Post' button (timeout={timeout}ms)...")
    start_time = time.time()
    while (time.time() - start_time) * 1000 < timeout:
        try:
            post_btns = page.locator(
                'div[aria-label="Post"][role="button"], '
                'div[role="button"]:has-text("Post"), '
                'button:has-text("Post")'
            )
            count = post_btns.count()
            for i in range(count):
                btn = post_btns.nth(i)
                if btn.is_visible():
                    aria_disabled = btn.get_attribute("aria-disabled")
                    if aria_disabled == "true":
                        log(f"'Post' found at index {i} but aria-disabled=true. Waiting...")
                        continue
                    log(f"'Post' found at index {i} and is enabled. Clicking...")
                    try:
                        btn.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    try:
                        btn.click(force=True, timeout=5000)
                    except Exception:
                        btn.evaluate("el => el.click()")
                    log("'Post' button clicked successfully!")
                    return True
        except Exception as e:
            log(f"Retry in click_post_button: {e}")
        time.sleep(2)

    log(f"WARNING: 'Post' button not clickable within {timeout}ms.")
    return False


def type_into_editor(page, locator, text: str, label: str, timeout: int = 30000) -> bool:
    """
    Focus and type text into contenteditable / textbox container.
    """
    try:
        log(f"Waiting for '{label}' container (timeout={timeout}ms)...")
        locator.wait_for(state="visible", timeout=timeout)
        log(f"'{label}' container visible. Typing text...")
        try:
            locator.click(force=True, timeout=5000)
        except Exception:
            try:
                locator.evaluate("el => el.focus()")
            except Exception:
                pass
        page.keyboard.type(text, delay=20)
        log(f"Typed into '{label}' successfully.")
        return True
    except PlaywrightTimeoutError:
        log(f"WARNING: '{label}' container not visible within {timeout}ms.")
        return False
    except Exception as e:
        log(f"ERROR: Typing into '{label}' failed: {e}")
        return False


def switch_to_gossip_hub_profile(page, page_id: str):
    """
    Navigates to the Gossip Hub page URL and switches profile if needed.
    """
    log(f"Step 0: Navigating to Page profile to ensure switched: https://www.facebook.com/profile.php?id={page_id}")
    page.goto(f"https://www.facebook.com/profile.php?id={page_id}", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)
    page.screenshot(path="fb_step0_page_profile.png")

    if "login" in page.url.lower():
        log("ERROR: Session expired or invalid cookies. Redirected to Login.")
        raise RuntimeError("Facebook session expired. Re-export cookies using export_fb_cookies.py.")

    switch_selectors = [
        'div[role="button"]:has-text("Switch Now")',
        'button:has-text("Switch Now")',
        'div[role="button"]:has-text("Switch")',
        'button:has-text("Switch")',
        '[aria-label*="Switch into Gossip Hub"]'
    ]

    switched = False
    for sel in switch_selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=4000):
                log(f"Found profile switch button ('{sel}'). Clicking...")
                btn.click(force=True)
                switched = True
                page.wait_for_timeout(8000)
                log("Switched to Gossip Hub Page profile successfully!")
                break
        except Exception:
            continue

    if not switched:
        log("Checking top-right account menu for profile switch...")
        try:
            profile_btn = page.locator(
                'div[aria-label="Your profile"], '
                'div[role="button"][aria-label*="Controls"], '
                'div[role="button"][aria-label*="Account"]'
            ).first
            if profile_btn.is_visible(timeout=3000):
                profile_btn.click()
                page.wait_for_timeout(3000)
                page.screenshot(path="fb_step0_account_menu.png")

                gh_item = page.locator('span:has-text("Gossip Hub"), div[role="button"]:has-text("Gossip Hub")').first
                if gh_item.is_visible(timeout=3000):
                    log("Found Gossip Hub in profile menu. Clicking to switch...")
                    gh_item.click(force=True)
                    page.wait_for_timeout(8000)
                    log("Switched profile via menu!")
        except Exception as e:
            log(f"Account menu check complete ({e}).")

    page.screenshot(path="fb_step0_switched_confirm.png")


def publish_to_facebook():
    cookies_json = os.getenv("FB_COOKIES_JSON")
    page_id = os.getenv("FB_PAGE_ID", DEFAULT_FB_PAGE_ID)

    # Allow controlling headless mode via HEADLESS environment variable (default: false for headed GUI run)
    headless_env = os.getenv("HEADLESS", "false").lower()
    is_headless = headless_env == "true"

    video_path = os.getenv("VIDEO_PATH")
    if not video_path:
        work_dir = Path("work")
        possible_videos = list(work_dir.glob("*.mp4")) if work_dir.exists() else []
        if possible_videos:
            video_path = str(possible_videos[0])
        else:
            video_path = "work/final.mp4"

    raw_caption = os.getenv("CAPTION_TEXT", "Check out our latest gossip update!")
    caption = build_caption_with_hashtags(raw_caption)

    if not cookies_json:
        log("ERROR: FB_COOKIES_JSON environment secret is missing!")
        sys.exit(1)

    if not os.path.exists(video_path):
        log(f"WARNING: Video file '{video_path}' does not exist on disk!")
        sys.exit(1)
    else:
        log(f"Found video file at '{video_path}' (Size: {os.path.getsize(video_path)} bytes)")

    temp_session_file = Path("temp_fb_session.json")
    try:
        temp_session_file.write_text(cookies_json, encoding="utf-8")
    except Exception as e:
        log(f"Failed to write temporary session file: {e}")
        sys.exit(1)

    log(f"==================================================")
    log(f"Starting Facebook Photo/Video Workflow: Page {page_id}")
    log(f"Playwright Headless Mode: {is_headless}")
    log(f"Target video file: {video_path}")
    log(f"Caption (max 5 hashtags): {caption}")
    log(f"==================================================")

    video_recording_dir = Path("video_recording")
    video_recording_dir.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=is_headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        context = browser.new_context(
            storage_state=str(temp_session_file),
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            record_video_dir=str(video_recording_dir)
        )

        page = context.new_page()

        try:
            # ── 1. Switch to Gossip Hub Page Profile & Stay on Page Wall ──────────────
            switch_to_gossip_hub_profile(page, page_id)

            # Ensure we are directly on the Page profile feed to post on the Page wall
            page_profile_url = f"https://www.facebook.com/profile.php?id={page_id}"
            log(f"Step 1: Confirmed on Gossip Hub Page profile wall: {page_profile_url}")
            if page.url != page_profile_url:
                page.goto(page_profile_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)

            log(f"Page Title: {page.title()}")
            page.screenshot(path="fb_step1_page_wall.png")

            # ── 3. Click "Photo/video" button ─────────────────────────────────────────
            log('Step 2: Clicking "Photo/video" button...')
            photo_video_btn = page.locator(
                'div[aria-label="Photo/video"][role="button"], '
                '[aria-label="Photo/video"], '
                'div[role="button"]:has-text("Photo/video")'
            ).first

            clicked_pv = safe_click(page, photo_video_btn, "Photo/video", timeout=30000)
            if not clicked_pv:
                raise RuntimeError('"Photo/video" button not found or could not be clicked.')

            page.wait_for_timeout(4000)
            page.screenshot(path="fb_step3_photo_video_modal.png")

            # ── 4. Upload generated video file ──────────────────────────────────────
            log(f"Step 3: Uploading video file '{video_path}'...")
            try:
                log("Waiting for file input element in DOM (state='attached')...")
                page.wait_for_selector('input[type="file"]', state="attached", timeout=20000)
                file_inputs = page.locator('input[type="file"]')
                log(f"Found {file_inputs.count()} file input element(s). Attaching video file...")
                file_inputs.first.set_input_files(os.path.abspath(video_path))
                log("Video file attached successfully!")
            except PlaywrightTimeoutError:
                log("ERROR: File input element did not appear in DOM within timeout.")
                raise RuntimeError("File input missing after clicking Photo/video.")

            log("Waiting 30 seconds for video upload & preview processing...")
            page.wait_for_timeout(30000)
            page.screenshot(path="fb_step4_video_uploaded.png")

            # ── 5. Enter Caption in text box ─────────────────────────────────────────
            log("Step 4: Entering caption into text box...")
            caption_box = page.locator(
                'div[aria-placeholder*="What\'s on your mind"][role="textbox"], '
                'div[aria-placeholder="What\'s on your mind, Gossip Hub?"][role="textbox"], '
                'div[contenteditable="true"][data-lexical-editor="true"], '
                'div[contenteditable="true"][role="textbox"]'
            ).first

            type_into_editor(page, caption_box, caption, "What's on your mind", timeout=20000)
            page.wait_for_timeout(3000)
            page.screenshot(path="fb_step5_caption_entered.png")

            # ── 6. Step 5a: Click First 'Next' Button ───────────────────────────────
            log("Step 5a: Pressing first 'Next' button...")
            clicked_next1 = click_next_button(page, "First Next", timeout=40000)
            if not clicked_next1:
                log("First 'Next' button not found. Checking if direct 'Post' button exists...")
                clicked_direct_post = click_post_button(page, timeout=10000)
                if clicked_direct_post:
                    log("Direct 'Post' button clicked!")
                    page.wait_for_timeout(40000)
                    page.screenshot(path="fb_step9_published.png")
                    log("SUCCESS: Video successfully published to Facebook Page!")
                    return
                else:
                    raise RuntimeError("Neither 'Next' nor 'Post' button found.")

            page.wait_for_timeout(6000)
            page.screenshot(path="fb_step6_next1_pressed.png")

            # ── 7. Step 5b: Click Second 'Next' Button ──────────────────────────────
            log("Step 5b: Pressing second 'Next' button...")
            clicked_next2 = click_next_button(page, "Second Next", timeout=30000)
            if not clicked_next2:
                log("WARNING: Second 'Next' button not visible within timeout — trying to proceed to Describe step...")

            page.wait_for_timeout(6000)
            page.screenshot(path="fb_step7_next2_pressed.png")

            # ── 8. Step 5c: Enter text in 'Describe your reel...' section ───────────
            log('Step 5c: Entering text in "Describe your reel..." section...')
            describe_box = page.locator(
                'div[aria-placeholder="Describe your reel..."][role="textbox"], '
                'div[aria-placeholder*="Describe"][role="textbox"], '
                'div[contenteditable="true"][aria-placeholder*="Describe"]'
            ).first

            type_into_editor(page, describe_box, caption, "Describe your reel", timeout=20000)
            page.wait_for_timeout(3000)
            page.screenshot(path="fb_step8_describe_entered.png")

            # ── 9. Step 5d: Click final 'Post' Button ───────────────────────────────
            log("Step 5d: Pressing final 'Post' button...")
            clicked_post = click_post_button(page, timeout=40000)
            if not clicked_post:
                raise RuntimeError("Final 'Post' button could not be clicked.")

            # ── 10. Handle Post-Publication Popups & Wait for Processing ──────────
            log("Step 6: Checking for post-publication popups ('Not Now', 'Skip', 'Close')...")
            try:
                popups = page.locator(
                    'div[role="button"]:has-text("Not Now"), '
                    'button:has-text("Not Now"), '
                    'div[role="button"]:has-text("Skip"), '
                    'button:has-text("Skip")'
                )
                if popups.count() > 0 and popups.first.is_visible(timeout=5000):
                    log("Found post-publication popup. Clicking 'Not Now' / 'Skip'...")
                    popups.first.click(force=True)
                    page.wait_for_timeout(3000)
            except Exception:
                pass

            log("Waiting for Facebook creation modal to close & backend video encoding to complete...")
            try:
                page.locator('div[role="dialog"]').wait_for(state="detached", timeout=90000)
                log("Creation modal closed! Facebook completed post submission.")
            except Exception:
                log("Modal detach timeout — waiting an extra 40 seconds for background chunk upload...")
                page.wait_for_timeout(40000)

            # Extra wait to ensure Facebook indexes the new Reel/post
            log("Waiting 20 seconds for Facebook Page feed indexing...")
            page.wait_for_timeout(20000)

            # Navigate directly to the Page profile to verify post on feed
            log(f"Navigating to Page profile (id={page_id}) to verify published video on feed...")
            try:
                page.goto(f"https://www.facebook.com/profile.php?id={page_id}", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(8000)
                page.screenshot(path="fb_final_published_verification.png")
                log("Verification screenshot saved: fb_final_published_verification.png")
            except Exception as e:
                log(f"Verification navigation notice: {e}")

            log("SUCCESS: Video successfully published to Facebook Page!")

        except Exception as err:
            log(f"ERROR during Facebook workflow: {err}")
            try:
                page.screenshot(path="fb_error_fatal.png")
            except Exception:
                pass
            raise err
        finally:
            browser.close()
            if temp_session_file.exists():
                temp_session_file.unlink()

if __name__ == "__main__":
    publish_to_facebook()
