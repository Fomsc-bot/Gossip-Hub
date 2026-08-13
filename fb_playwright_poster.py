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
    Ensure the caption has relevant hashtags with total hashtags <= 5.
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
    
    # If caption already has > 5 hashtags, trim to 5
    if len(existing_tags) > 5:
        clean_text = " ".join([w for w in words if not w.startswith("#")])
        tags_str = " ".join(existing_tags[:5])
        return f"{clean_text.strip()} {tags_str}".strip()

    # Otherwise append from pool up to 5 total hashtags
    remaining_slots = 5 - len(existing_tags)
    tags_to_add = [t for t in HASHTAG_POOL if t not in existing_tags][:remaining_slots]
    parts = [raw_caption.strip()] + tags_to_add
    return " ".join(parts).strip()


def safe_click(page, locator, label: str, timeout: int = 60000) -> bool:
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
        page.screenshot(path=f"fb_debug_{label.lower().replace(' ', '_')}_timeout.png")
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


def type_into_editor(page, locator, text: str, label: str, timeout: int = 30000) -> bool:
    """
    Focus and type text into contenteditable / textbox container.
    """
    try:
        log(f"Waiting for '{label}' container (timeout={timeout}ms)...")
        locator.wait_for(state="visible", timeout=timeout)
        log(f"'{label}' container visible. Typing caption...")
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
        page.screenshot(path=f"fb_debug_{label.lower().replace(' ', '_')}_timeout.png")
        return False
    except Exception as e:
        log(f"ERROR: Typing into '{label}' failed: {e}")
        return False


def publish_to_facebook():
    cookies_json = os.getenv("FB_COOKIES_JSON")
    page_id = os.getenv("FB_PAGE_ID", DEFAULT_FB_PAGE_ID)

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
    log(f"Starting Facebook Photo/Video Workflow")
    log(f"Target video file: {video_path}")
    log(f"Caption (with max 5 hashtags): {caption}")
    log(f"==================================================")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        page = context.new_page()

        try:
            # ── 1. Navigate to https://www.facebook.com/ ─────────────────────────────
            log("Step 1: Navigating to https://www.facebook.com/")
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            log(f"Page Title: {page.title()}")
            page.screenshot(path="fb_step1_home.png")

            if "login" in page.url.lower():
                log("ERROR: Session expired or invalid cookies. Redirected to Login.")
                raise RuntimeError("Facebook session expired. Re-export cookies using export_fb_cookies.py.")

            # Check if profile switch is required
            try:
                switch_btn = page.locator(
                    'div[role="button"]:has-text("Switch Now"), button:has-text("Switch Now")'
                ).first
                if switch_btn.is_visible(timeout=4000):
                    log("Found 'Switch Now' button. Switching profile...")
                    switch_btn.click()
                    page.wait_for_timeout(8000)
                    log("Profile switched successfully!")
            except Exception:
                pass

            page.screenshot(path="fb_step2_ready.png")

            # ── 2. Click "Photo/video" button ─────────────────────────────────────────
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

            # ── 3. Upload generated video file ──────────────────────────────────────
            log(f"Step 3: Uploading video file '{video_path}'...")
            try:
                page.wait_for_selector('input[type="file"]', timeout=20000)
                file_inputs = page.locator('input[type="file"]')
                file_inputs.first.set_input_files(os.path.abspath(video_path))
                log("Video file set on input element.")
            except PlaywrightTimeoutError:
                log("ERROR: File input element did not appear.")
                raise RuntimeError("File input missing after clicking Photo/video.")

            log("Waiting 30 seconds for video upload & preview processing...")
            page.wait_for_timeout(30000)
            page.screenshot(path="fb_step4_video_uploaded.png")

            # ── 4. Enter Caption with Hashtags in "What's on your mind?" box ────────
            log("Step 4: Entering caption into 'What\'s on your mind, Gossip Hub?' box...")
            caption_box = page.locator(
                'div[aria-placeholder="What\'s on your mind, Gossip Hub?"][role="textbox"], '
                'div[aria-placeholder*="What\'s on your mind"][role="textbox"], '
                'div[contenteditable="true"][data-lexical-editor="true"]'
            ).first

            type_into_editor(page, caption_box, caption, "What's on your mind", timeout=20000)
            page.wait_for_timeout(2000)
            page.screenshot(path="fb_step5_caption_entered.png")

            # ── 5. Press first "Next" button ─────────────────────────────────────────
            log("Step 5: Pressing first 'Next' button...")
            next_btn_1 = page.locator(
                'div[aria-label="Next"][role="button"], '
                'div[role="button"]:has-text("Next")'
            ).first

            clicked_next1 = safe_click(page, next_btn_1, "First Next", timeout=60000)
            if not clicked_next1:
                raise RuntimeError("First 'Next' button not visible or could not be clicked.")

            page.wait_for_timeout(5000)
            page.screenshot(path="fb_step6_next1_pressed.png")

            # ── 6. Press second "Next" button ────────────────────────────────────────
            log("Step 6: Pressing second 'Next' button...")
            next_btn_2 = page.locator(
                'div[aria-label="Next"][role="button"], '
                'div[role="button"]:has-text("Next")'
            ).first

            clicked_next2 = safe_click(page, next_btn_2, "Second Next", timeout=30000)
            if not clicked_next2:
                log("WARNING: Second 'Next' button not visible — proceeding to Describe step.")

            page.wait_for_timeout(5000)
            page.screenshot(path="fb_step7_next2_pressed.png")

            # ── 7. Enter text in "Describe your reel..." section ────────────────────
            log('Step 7: Entering text in "Describe your reel..." section...')
            describe_box = page.locator(
                'div[aria-placeholder="Describe your reel..."][role="textbox"], '
                'div[aria-placeholder*="Describe"][role="textbox"], '
                'div[contenteditable="true"][aria-placeholder*="Describe"]'
            ).first

            type_into_editor(page, describe_box, caption, "Describe your reel", timeout=20000)
            page.wait_for_timeout(2000)
            page.screenshot(path="fb_step8_describe_entered.png")

            # ── 8. Press "Post" button ────────────────────────────────────────────────
            log("Step 8: Pressing 'Post' button...")
            post_btn = page.locator(
                'div[aria-label="Post"][role="button"], '
                'div[role="button"]:has-text("Post")'
            ).first

            clicked_post = safe_click(page, post_btn, "Post", timeout=30000)
            if not clicked_post:
                raise RuntimeError("'Post' button not visible or could not be clicked.")

            log("Waiting 40 seconds for video publication to complete...")
            page.wait_for_timeout(40000)
            page.screenshot(path="fb_step9_published.png")
            log("SUCCESS: Video successfully posted to Facebook!")

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
