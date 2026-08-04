import os
import sys
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

DEFAULT_FB_PAGE_ID = "61584777925866"

def log(*args):
    print("[FB-PLAYWRIGHT]", *args)

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

    caption = os.getenv("CAPTION_TEXT", "Check out our latest update!")
    
    if not cookies_json:
        log("ERROR: FB_COOKIES_JSON environment secret is missing!")
        sys.exit(1)

    # Save temp session file
    temp_session_file = Path("temp_fb_session.json")
    try:
        temp_session_file.write_text(cookies_json, encoding="utf-8")
    except Exception as e:
        log(f"Failed to write temporary session file: {e}")
        sys.exit(1)

    log(f"Starting Facebook publishing for Page ID: {page_id}")
    log(f"Target video file: {video_path}")
    log(f"Caption length: {len(caption)} chars")

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
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        page = context.new_page()

        try:
            # 1. Try Meta Business Suite Composer first
            composer_url = f"https://business.facebook.com/latest/composer?asset_id={page_id}"
            log(f"Navigating to Meta Business Suite Composer: {composer_url}")
            page.goto(composer_url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(5000)

            # Check if logged in or redirected to login
            if "login" in page.url.lower():
                log("Session expired or login required! Please update FB_COOKIES_JSON secret.")
                raise RuntimeError("Facebook session expired.")

            log("Current page title:", page.title())

            # Look for file upload input
            file_input = page.locator('input[type="file"]')
            if file_input.count() > 0 and os.path.exists(video_path):
                log("Uploading video file...")
                file_input.first.set_input_files(video_path)
                page.wait_for_timeout(10000) # Allow upload time
            else:
                log(f"Note: File input selector or video file ({video_path}) not found.")

            # Look for caption text area
            textbox = page.locator('div[contenteditable="true"], textarea').first
            if textbox.is_visible():
                log("Filling caption text...")
                textbox.click()
                textbox.fill(caption)

            # Look for Publish button
            publish_btn = page.locator('button:has-text("Publish"), div[role="button"]:has-text("Publish")').first
            if publish_btn.is_visible():
                log("Clicking Publish button...")
                publish_btn.click()
                page.wait_for_timeout(15000) # Wait for publish to finish
                log("Successfully triggered publish!")
            else:
                log("Publish button not found automatically. Saving page screenshot for verification...")
                page.screenshot(path="fb_composer_status.png")

        except Exception as err:
            log(f"Publishing attempt failed: {err}")
            page.screenshot(path="fb_error_debug.png")
            raise err
        finally:
            browser.close()
            if temp_session_file.exists():
                temp_session_file.unlink()

if __name__ == "__main__":
    publish_to_facebook()
