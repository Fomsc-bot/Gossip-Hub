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
    log(f"Starting Facebook direct page publishing: {page_id}")
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

            # Step 1b: Detect and click "Switch Now" button to switch to Page profile
            switch_selectors = [
                'div[role="button"]:has-text("Switch Now")',
                'button:has-text("Switch Now")',
                'div[aria-label="Switch Now"]',
                'span:has-text("Switch Now")',
                'div:has-text("Switch into") div[role="button"]'
            ]

            for sel in switch_selectors:
                switch_btn = page.locator(sel).first
                if switch_btn.is_visible():
                    log(f"Found 'Switch Now' button. Clicking to switch into Page profile...")
                    switch_btn.click()
                    page.wait_for_timeout(8000)
                    log("Profile switched successfully!")
                    break

            page.screenshot(path="fb_step1_switched.png")

            # Step 2: Click "Photo/video" or "What's on your mind?" button on the Page
            log("Step 2: Looking for 'Photo/video' or post creation button on the Page...")
            post_triggers = [
                'span:has-text("Photo/video")',
                'div[role="button"]:has-text("Photo/video")',
                'span:has-text("What\'s on your mind")',
                'div[role="button"]:has-text("What\'s on your mind")',
                'div:has-text("Photo/video")[role="button"]'
            ]

            modal_opened = False
            for sel in post_triggers:
                trigger = page.locator(sel).first
                if trigger.is_visible():
                    log(f"Clicking post creation trigger '{sel}'...")
                    trigger.click()
                    modal_opened = True
                    page.wait_for_timeout(4000)
                    page.screenshot(path="fb_step2_modal_opened.png")
                    break

            if not modal_opened:
                log("Fallback: Trying to click main content editable container directly...")
                page.locator('div[role="main"] div[contenteditable="true"]').first.click()
                page.wait_for_timeout(3000)
                page.screenshot(path="fb_step2_modal_fallback.png")

            # Step 3: Attach Video File
            if os.path.exists(video_path):
                log(f"Step 3: Uploading video file '{video_path}'...")
                file_inputs = page.locator('input[type="file"]')
                if file_inputs.count() > 0:
                    file_inputs.first.set_input_files(os.path.abspath(video_path))
                    log("Waiting 20 seconds for video upload and processing preview...")
                    page.wait_for_timeout(20000)
                    page.screenshot(path="fb_step3_video_attached.png")
                else:
                    log("WARNING: File input element not found in post modal.")

            # Step 4: Type Caption
            log("Step 4: Filling caption into post editor...")
            editor = page.locator('div[role="dialog"] div[contenteditable="true"], div[contenteditable="true"]').first
            if editor.is_visible():
                editor.click()
                editor.fill(caption)
                page.wait_for_timeout(3000)
                page.screenshot(path="fb_step4_caption_filled.png")
            else:
                log("WARNING: Contenteditable editor element not found.")

            # Step 5: Click Post / Publish
            log("Step 5: Locating and clicking 'Post' button...")
            post_buttons = [
                'div[role="dialog"] div[aria-label="Post"]',
                'div[role="dialog"] div[role="button"]:has-text("Post")',
                'div[role="dialog"] button:has-text("Post")',
                'div[aria-label="Post"]',
                'button:has-text("Post")',
                'div[role="button"]:has-text("Post")'
            ]

            posted = False
            for sel in post_buttons:
                btn = page.locator(sel).first
                if btn.is_visible():
                    log(f"Clicking 'Post' button via selector '{sel}'...")
                    btn.click()
                    posted = True
                    log("Waiting 30 seconds for Facebook post publication...")
                    page.wait_for_timeout(30000)
                    page.screenshot(path="fb_step5_final_posted.png")
                    log("SUCCESS: Video & Caption successfully posted to Facebook Page!")
                    break

            if not posted:
                log("WARNING: Could not locate visible 'Post' button inside dialog.")
                page.screenshot(path="fb_post_btn_missing.png")

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
