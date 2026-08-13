import os
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

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

            # ── Step 2: Navigate to the Page profile so the composer is scoped to our Page ──
            page_url = f"https://www.facebook.com/profile.php?id={page_id}"
            log(f"Step 2: Navigating to Page profile: {page_url}")
            page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            page.screenshot(path="fb_step2_page_profile.png")

            # Detect and click "Switch Now" if prompted
            switch_btn = page.locator('div[role="button"]:has-text("Switch Now"), button:has-text("Switch Now")').first
            if switch_btn.is_visible():
                log("Found 'Switch Now' button. Clicking to switch profile...")
                switch_btn.click()
                page.wait_for_timeout(8000)
                log("Profile switched successfully!")
            page.screenshot(path="fb_step2_switched.png")

            # ── Step 3: Click the "Photo/video" button in the post composer ──────────
            log('Step 3: Clicking "Photo/video" button in the post composer...')

            photo_video_clicked = False

            # Strategy 1: aria-label exact match
            pv_btn = page.locator('[aria-label="Photo/video"]').first
            if pv_btn.is_visible():
                log('Clicking "Photo/video" via aria-label...')
                pv_btn.click()
                photo_video_clicked = True
                page.wait_for_timeout(5000)

            # Strategy 2: span text match inside a role=button
            if not photo_video_clicked:
                pv_btn2 = page.locator('div[role="button"]:has-text("Photo/video"), span:text("Photo/video")').first
                if pv_btn2.is_visible():
                    log('Clicking "Photo/video" via text selector...')
                    pv_btn2.click()
                    photo_video_clicked = True
                    page.wait_for_timeout(5000)

            # Strategy 3: iterate all role=button elements and match text
            if not photo_video_clicked:
                log("Fallback: iterating buttons to find 'Photo/video'...")
                btns = page.locator('[role="button"]')
                count = btns.count()
                for i in range(count):
                    btn = btns.nth(i)
                    label = (btn.get_attribute("aria-label") or btn.inner_text()).strip().lower()
                    if "photo" in label and "video" in label:
                        log(f"Found 'Photo/video' at index {i}, clicking...")
                        btn.click()
                        photo_video_clicked = True
                        page.wait_for_timeout(5000)
                        break

            if not photo_video_clicked:
                log('ERROR: Could not find "Photo/video" button!')
                raise RuntimeError('"Photo/video" button not found in post composer.')

            page.screenshot(path="fb_step3_photo_video_clicked.png")

            # ── Step 4: Upload the video file ───────────────────────────────────────
            log(f"Step 4: Uploading video file '{video_path}'...")
            file_inputs = page.locator('input[type="file"]')
            if file_inputs.count() > 0:
                file_inputs.first.set_input_files(os.path.abspath(video_path))
                log("Waiting 20 seconds for video upload and preview generation...")
                page.wait_for_timeout(20000)
                page.screenshot(path="fb_step4_video_uploaded.png")
            else:
                log("ERROR: Could not find file input element after clicking Photo/video!")
                raise RuntimeError("File input missing after Photo/video click.")

            # ── Step 5: Enter caption + hashtags in "What's on your mind?" box ──────
            log("Step 5: Entering caption in \"What's on your mind?\" text box...")
            caption_box = page.locator(
                '[aria-placeholder="What\'s on your mind, Gossip Hub?"], '
                '[aria-placeholder*="What\'s on your mind"], '
                'div[contenteditable="true"][data-lexical-editor="true"]'
            ).first

            if caption_box.is_visible():
                log("Clicking caption box and typing caption...")
                try:
                    caption_box.click(force=True)
                except Exception:
                    caption_box.evaluate("el => el.focus()")
                page.keyboard.type(caption)
                page.wait_for_timeout(3000)
                page.screenshot(path="fb_step5_caption_entered.png")
            else:
                log("WARNING: Could not locate 'What's on your mind?' text box.")

            # ── Step 6: Click the first "Next" button ───────────────────────────────
            log("Step 6: Clicking the first 'Next' button...")
            next_btn_1 = page.locator(
                '[aria-label="Next"][role="button"], '
                'div[role="button"]:has-text("Next"), '
                'button:has-text("Next")'
            ).first
            if next_btn_1.is_visible():
                next_btn_1.click(force=True)
                page.wait_for_timeout(5000)
                page.screenshot(path="fb_step6_next1_clicked.png")
            else:
                log("WARNING: First 'Next' button not visible, trying anyway...")
                next_btn_1.click(force=True)
                page.wait_for_timeout(5000)

            # ── Step 7: Click the second "Next" button ──────────────────────────────
            log("Step 7: Clicking the second 'Next' button...")
            next_btn_2 = page.locator(
                '[aria-label="Next"][role="button"], '
                'div[role="button"]:has-text("Next"), '
                'button:has-text("Next")'
            ).first
            if next_btn_2.is_visible():
                next_btn_2.click(force=True)
                page.wait_for_timeout(5000)
                page.screenshot(path="fb_step7_next2_clicked.png")
            else:
                log("WARNING: Second 'Next' button not visible.")

            # ── Step 8: Enter description in "Describe your reel..." box ────────────
            log('Step 8: Entering description in "Describe your reel..." text box...')
            describe_box = page.locator(
                '[aria-placeholder="Describe your reel..."], '
                'div[contenteditable="true"][aria-placeholder*="Describe"]'
            ).first

            if describe_box.is_visible():
                log('Clicking "Describe your reel..." box and typing description...')
                try:
                    describe_box.click(force=True)
                except Exception:
                    describe_box.evaluate("el => el.focus()")
                page.keyboard.type(caption)
                page.wait_for_timeout(3000)
                page.screenshot(path="fb_step8_describe_entered.png")
            else:
                log('WARNING: Could not locate "Describe your reel..." text box.')

            # ── Step 9: Click the final "Post" button ───────────────────────────────
            log("Step 9: Clicking the final 'Post' button...")
            post_btn = page.locator(
                '[aria-label="Post"][role="button"], '
                'div[role="button"]:has-text("Post"), '
                'button:has-text("Post")'
            ).first

            if post_btn.is_visible():
                log("Clicking 'Post' button...")
                try:
                    post_btn.click(force=True)
                except Exception:
                    post_btn.evaluate("el => el.click()")

                log("Waiting 30 seconds for post publication to finish...")
                page.wait_for_timeout(30000)
                page.screenshot(path="fb_step9_post_submitted.png")
                log("SUCCESS: Video successfully posted to Facebook Page!")
            else:
                log("ERROR: Could not locate 'Post' button!")
                raise RuntimeError("Post button missing on final screen.")

        except Exception as err:
            log(f"ERROR during Facebook post flow: {err}")
            page.screenshot(path="fb_error_fatal.png")
            raise err
        finally:
            browser.close()
            if temp_session_file.exists():
                temp_session_file.unlink()

if __name__ == "__main__":
    publish_to_facebook()
