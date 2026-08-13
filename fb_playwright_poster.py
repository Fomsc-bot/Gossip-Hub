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
    Append up to 5 relevant hashtags to the caption.
    Keeps total hashtags <= 5.
    """
    HASHTAG_POOL = [
        "#GossipHub",
        "#EntertainmentNews",
        "#CelebrityNews",
        "#Trending",
        "#Viral",
    ]
    existing = [w for w in raw_caption.split() if w.startswith("#")]
    remaining_slots = max(0, 5 - len(existing))
    tags_to_add = [t for t in HASHTAG_POOL if t not in existing][:remaining_slots]
    parts = [raw_caption.strip()] + tags_to_add
    return " ".join(parts)


def safe_click(page, locator, label: str, timeout: int = 60000) -> bool:
    """
    Wait for an element to be visible, then click it.
    Falls back to JavaScript click if force=True still fails.
    Returns True on success, False on failure.
    """
    selector = locator._selector if hasattr(locator, "_selector") else str(locator)
    try:
        log(f"Waiting for '{label}' to be visible (timeout={timeout}ms)...")
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
        log(f"WARNING: '{label}' did not become visible within {timeout}ms.")
        # Take a screenshot to aid debugging
        page.screenshot(path=f"fb_debug_{label.lower().replace(' ', '_')}_timeout.png")
        return False
    except Exception as e:
        log(f"WARNING: Normal click on '{label}' failed ({e}). Trying JS click...")
        try:
            locator.evaluate("el => el.click()")
            log(f"'{label}' JS-clicked successfully.")
            return True
        except Exception as e2:
            log(f"ERROR: JS click on '{label}' also failed: {e2}")
            return False


def type_into_editor(page, locator, text: str, label: str, timeout: int = 30000) -> bool:
    """
    Wait for a contenteditable/textarea editor to be visible and type text into it.
    Returns True on success, False if not found.
    """
    try:
        log(f"Waiting for '{label}' editor to be visible...")
        locator.wait_for(state="visible", timeout=timeout)
        log(f"'{label}' editor is visible. Typing text...")
        try:
            locator.click(force=True, timeout=5000)
        except Exception:
            try:
                locator.evaluate("el => el.focus()")
            except Exception:
                pass
        page.keyboard.type(text, delay=30)
        log(f"Text typed into '{label}' successfully.")
        return True
    except PlaywrightTimeoutError:
        log(f"WARNING: '{label}' editor not visible within {timeout}ms.")
        page.screenshot(path=f"fb_debug_{label.lower().replace(' ', '_')}_timeout.png")
        return False
    except Exception as e:
        log(f"ERROR: Failed to type into '{label}': {e}")
        return False


def publish_to_facebook():
    cookies_json = os.getenv("FB_COOKIES_JSON")
    page_id = os.getenv("FB_PAGE_ID", DEFAULT_FB_PAGE_ID)

    # Locate video file to upload
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

    # Save temp session file
    temp_session_file = Path("temp_fb_session.json")
    try:
        temp_session_file.write_text(cookies_json, encoding="utf-8")
    except Exception as e:
        log(f"Failed to write temporary session file: {e}")
        sys.exit(1)

    log(f"==================================================")
    log(f"Starting Facebook Photo/Video post flow")
    log(f"Target video file: {video_path}")
    log(f"Caption (with hashtags): {caption}")
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
            # ── Step 1: Navigate to facebook.com home feed ──────────────────────────
            log("Step 1: Navigating to https://www.facebook.com/")
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            log(f"Page Title: {page.title()}")
            page.screenshot(path="fb_step1_home.png")

            # Check if redirected to login
            if "login" in page.url.lower():
                log("ERROR: Session expired or invalid cookies. Redirected to Facebook Login page.")
                raise RuntimeError("Facebook session expired. Please re-export cookies using export_fb_cookies.py.")

            # ── Step 2: Navigate to the Page profile ────────────────────────────────
            page_url = f"https://www.facebook.com/profile.php?id={page_id}"
            log(f"Step 2: Navigating to Page profile: {page_url}")
            page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            page.screenshot(path="fb_step2_page_profile.png")

            # Detect and click "Switch Now" if prompted
            try:
                switch_btn = page.locator(
                    'div[role="button"]:has-text("Switch Now"), button:has-text("Switch Now")'
                ).first
                if switch_btn.is_visible(timeout=5000):
                    log("Found 'Switch Now' button. Clicking to switch profile...")
                    switch_btn.click()
                    page.wait_for_timeout(8000)
                    log("Profile switched successfully!")
            except Exception:
                log("No 'Switch Now' button found, continuing...")

            page.screenshot(path="fb_step2_switched.png")

            # ── Step 3: Click the "Photo/video" button in the post composer ──────────
            log('Step 3: Clicking "Photo/video" button in the post composer...')

            photo_video_clicked = False

            # Strategy 1: aria-label exact match — wait up to 15s for it to appear
            try:
                pv_btn = page.locator('[aria-label="Photo/video"]').first
                pv_btn.wait_for(state="visible", timeout=15000)
                log('Clicking "Photo/video" via aria-label...')
                pv_btn.click()
                photo_video_clicked = True
                page.wait_for_timeout(4000)
            except PlaywrightTimeoutError:
                log("Strategy 1 failed: aria-label 'Photo/video' not visible.")
            except Exception as e:
                log(f"Strategy 1 exception: {e}")

            # Strategy 2: text match inside role=button
            if not photo_video_clicked:
                try:
                    pv_btn2 = page.locator(
                        'div[role="button"]:has-text("Photo/video")'
                    ).first
                    pv_btn2.wait_for(state="visible", timeout=10000)
                    log('Clicking "Photo/video" via text selector...')
                    pv_btn2.click()
                    photo_video_clicked = True
                    page.wait_for_timeout(4000)
                except Exception as e:
                    log(f"Strategy 2 failed: {e}")

            # Strategy 3: iterate all visible role=button elements and match text/label
            if not photo_video_clicked:
                log("Fallback: iterating buttons to find 'Photo/video'...")
                btns = page.locator('[role="button"]')
                count = btns.count()
                for i in range(count):
                    btn = btns.nth(i)
                    try:
                        label_attr = btn.get_attribute("aria-label") or ""
                        inner = btn.inner_text(timeout=1000)
                        combined = (label_attr + " " + inner).strip().lower()
                        if "photo" in combined and "video" in combined:
                            log(f"Found 'Photo/video' at index {i}, clicking...")
                            btn.click()
                            photo_video_clicked = True
                            page.wait_for_timeout(4000)
                            break
                    except Exception:
                        continue

            if not photo_video_clicked:
                log('ERROR: Could not find "Photo/video" button!')
                raise RuntimeError('"Photo/video" button not found in post composer.')

            page.screenshot(path="fb_step3_photo_video_clicked.png")

            # ── Step 4: Upload the video file ───────────────────────────────────────
            log(f"Step 4: Uploading video file '{video_path}'...")

            # Wait for file input to appear (it may be inside a dialog now)
            try:
                page.wait_for_selector('input[type="file"]', timeout=20000)
                file_inputs = page.locator('input[type="file"]')
                log(f"Found {file_inputs.count()} file input(s). Using first...")
                file_inputs.first.set_input_files(os.path.abspath(video_path))
            except PlaywrightTimeoutError:
                log("ERROR: File input element did not appear within 20 seconds!")
                raise RuntimeError("File input missing after Photo/video click.")

            # ── Wait for video to fully upload and Facebook to generate preview ──────
            log("Waiting 45 seconds for video upload and processing to complete...")
            page.wait_for_timeout(45000)
            page.screenshot(path="fb_step4_video_uploaded.png")

            # ── Step 5: Enter caption in "What's on your mind?" box ─────────────────
            log("Step 5: Entering caption in \"What's on your mind?\" text box...")
            caption_box = page.locator(
                "[aria-placeholder=\"What's on your mind, Gossip Hub?\"], "
                "[aria-placeholder*=\"What's on your mind\"], "
                "div[contenteditable=\"true\"][data-lexical-editor=\"true\"]"
            ).first

            caption_typed = type_into_editor(page, caption_box, caption, "What's on your mind", timeout=30000)
            if not caption_typed:
                log("WARNING: Could not type caption — proceeding anyway.")

            page.wait_for_timeout(2000)
            page.screenshot(path="fb_step5_caption_entered.png")

            # ── Step 6: Click the first "Next" button ───────────────────────────────
            log("Step 6: Waiting for and clicking the first 'Next' button...")
            # Use wait_for_selector so we block until the button actually appears
            try:
                page.wait_for_selector(
                    '[aria-label="Next"][role="button"], div[role="button"]:has-text("Next")',
                    timeout=90000
                )
                log("First 'Next' button appeared in DOM.")
            except PlaywrightTimeoutError:
                log("ERROR: First 'Next' button did not appear within 90 seconds!")
                page.screenshot(path="fb_step6_next1_timeout.png")
                raise RuntimeError("First 'Next' button never appeared — video may still be uploading.")

            next_btn_1 = page.locator(
                '[aria-label="Next"][role="button"], '
                'div[role="button"]:has-text("Next")'
            ).first
            clicked_next1 = safe_click(page, next_btn_1, "First Next", timeout=30000)
            if not clicked_next1:
                raise RuntimeError("Could not click the first 'Next' button.")

            page.wait_for_timeout(5000)
            page.screenshot(path="fb_step6_next1_clicked.png")

            # ── Step 7: Click the second "Next" button ──────────────────────────────
            log("Step 7: Waiting for and clicking the second 'Next' button...")
            try:
                page.wait_for_selector(
                    '[aria-label="Next"][role="button"], div[role="button"]:has-text("Next")',
                    timeout=30000
                )
                log("Second 'Next' button appeared in DOM.")
            except PlaywrightTimeoutError:
                log("WARNING: Second 'Next' button did not appear within 30s — may have skipped a step.")

            next_btn_2 = page.locator(
                '[aria-label="Next"][role="button"], '
                'div[role="button"]:has-text("Next")'
            ).first
            clicked_next2 = safe_click(page, next_btn_2, "Second Next", timeout=20000)
            if not clicked_next2:
                log("WARNING: Could not click second 'Next' — trying to proceed to Post screen.")

            page.wait_for_timeout(5000)
            page.screenshot(path="fb_step7_next2_clicked.png")

            # ── Step 8: Enter description in "Describe your reel..." box ────────────
            log('Step 8: Entering description in "Describe your reel..." text box...')
            describe_box = page.locator(
                '[aria-placeholder="Describe your reel..."], '
                'div[contenteditable="true"][aria-placeholder*="Describe"]'
            ).first

            desc_typed = type_into_editor(page, describe_box, caption, "Describe your reel", timeout=20000)
            if not desc_typed:
                log("WARNING: Could not type into 'Describe your reel...' — proceeding to Post.")

            page.wait_for_timeout(2000)
            page.screenshot(path="fb_step8_describe_entered.png")

            # ── Step 9: Click the final "Post" button ───────────────────────────────
            log("Step 9: Waiting for and clicking the 'Post' button...")
            try:
                page.wait_for_selector(
                    '[aria-label="Post"][role="button"], div[role="button"]:has-text("Post")',
                    timeout=30000
                )
                log("'Post' button appeared in DOM.")
            except PlaywrightTimeoutError:
                log("ERROR: 'Post' button did not appear within 30 seconds!")
                page.screenshot(path="fb_step9_post_timeout.png")
                raise RuntimeError("'Post' button never appeared on final screen.")

            post_btn = page.locator(
                '[aria-label="Post"][role="button"], '
                'div[role="button"]:has-text("Post")'
            ).first
            clicked_post = safe_click(page, post_btn, "Post", timeout=20000)
            if not clicked_post:
                raise RuntimeError("Could not click the 'Post' button.")

            log("Waiting 45 seconds for post publication to finish...")
            page.wait_for_timeout(45000)
            page.screenshot(path="fb_step9_post_submitted.png")
            log("SUCCESS: Video successfully posted to Facebook Page!")

        except Exception as err:
            log(f"ERROR during Facebook post flow: {err}")
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
