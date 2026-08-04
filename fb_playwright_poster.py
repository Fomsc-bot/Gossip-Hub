import os
import sys
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

DEFAULT_FB_PAGE_ID = "61584777925866"

def log(*args):
    print("[FB-PLAYWRIGHT]", *args)
    sys.stdout.flush()

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

    caption = os.getenv("CAPTION_TEXT", "Check out our latest gossip update!")
    
    if not cookies_json:
        log("ERROR: FB_COOKIES_JSON environment secret is missing!")
        sys.exit(1)

    if not os.path.exists(video_path):
        log(f"WARNING: Video file '{video_path}' does not exist on disk!")
        # Create dummy text / placeholder if no video generated
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
    log(f"Starting Facebook publishing for Page ID: {page_id}")
    log(f"Target video file: {video_path}")
    log(f"Caption: {caption}")
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
            # Step 1: Open Facebook Page directly
            page_url = f"https://www.facebook.com/profile.php?id={page_id}"
            log(f"Step 1: Navigating to Facebook Page URL: {page_url}")
            page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            log(f"Page Title: {page.title()}")
            log(f"Current URL: {page.url}")

            # Take initial screenshot for verification
            page.screenshot(path="fb_step1_page.png")

            # Check if redirected to login
            if "login" in page.url.lower():
                log("ERROR: Session expired or invalid cookies. Redirected to Facebook Login page.")
                raise RuntimeError("Facebook session expired. Please re-export cookies using export_fb_cookies.py.")

            # Check for Meta Business Suite composer or Facebook Page Composer
            composer_url = f"https://business.facebook.com/latest/composer?asset_id={page_id}"
            log(f"Step 2: Navigating to Meta Business Suite Composer: {composer_url}")
            page.goto(composer_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(7000)

            log(f"Business Suite URL: {page.url}")
            page.screenshot(path="fb_step2_composer.png")

            # Check for file input element in composer
            file_inputs = page.locator('input[type="file"]')
            file_count = file_inputs.count()
            log(f"Found {file_count} file input elements in composer.")

            if file_count > 0 and os.path.exists(video_path):
                log(f"Step 3: Setting video file '{video_path}' into file input...")
                file_inputs.first.set_input_files(os.path.abspath(video_path))
                log("Waiting 15 seconds for video processing/upload...")
                page.wait_for_timeout(15000)
                page.screenshot(path="fb_step3_after_upload.png")

            # Look for contenteditable box / text box
            textbox = page.locator('div[contenteditable="true"], textarea[placeholder*="mind"], textarea').first
            if textbox.is_visible():
                log("Step 4: Filling caption into textbox...")
                textbox.click()
                textbox.fill(caption)
                page.wait_for_timeout(3000)

            # Look for Publish button
            publish_selectors = [
                'button:has-text("Publish")',
                'div[role="button"]:has-text("Publish")',
                'button:has-text("Post")',
                'div[role="button"]:has-text("Post")',
                'button[type="submit"]'
            ]

            published = False
            for sel in publish_selectors:
                btn = page.locator(sel).first
                if btn.is_visible():
                    log(f"Step 5: Clicking Publish button found via selector '{sel}'...")
                    btn.click()
                    published = True
                    log("Waiting 20 seconds for publication to complete...")
                    page.wait_for_timeout(20000)
                    page.screenshot(path="fb_step5_after_publish.png")
                    break

            if not published:
                log("WARNING: Publish button was not automatically identified.")
                page.screenshot(path="fb_publish_button_missing.png")
            else:
                log("SUCCESS: Content publication flow executed!")

        except Exception as err:
            log(f"ERROR during execution: {err}")
            page.screenshot(path="fb_error_fatal.png")
            raise err
        finally:
            browser.close()
            if temp_session_file.exists():
                temp_session_file.unlink()

if __name__ == "__main__":
    publish_to_facebook()
