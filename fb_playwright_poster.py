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
    log(f"Starting Facebook Reel creation flow: {page_id}")
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
            page.screenshot(path="fb_step1_page.png")

            # Check if redirected to login
            if "login" in page.url.lower():
                log("ERROR: Session expired or invalid cookies. Redirected to Facebook Login page.")
                raise RuntimeError("Facebook session expired. Please re-export cookies using export_fb_cookies.py.")

            # Step 1b: Detect and click "Switch Now" button to switch to Page profile if needed
            switch_btn = page.locator('div[role="button"]:has-text("Switch Now"), button:has-text("Switch Now")').first
            if switch_btn.is_visible():
                log("Found 'Switch Now' button. Clicking to switch profile...")
                switch_btn.click()
                page.wait_for_timeout(8000)
                log("Profile switched successfully!")

            page.screenshot(path="fb_step1_switched.png")

            # Step 2: Click the "Reel" button next to "Photo/video" on the feed
            log("Step 2: Clicking the 'Reel' button on the Page feed...")
            reel_triggers = [
                'span:has-text("Reel")',
                'div[role="button"]:has-text("Reel")',
                'div:has-text("Reel")[role="button"]'
            ]

            reel_clicked = False
            for sel in reel_triggers:
                btn = page.locator(sel).first
                if btn.is_visible():
                    log(f"Clicking Reel button via selector '{sel}'...")
                    btn.click()
                    reel_clicked = True
                    page.wait_for_timeout(5000)
                    break

            if not reel_clicked:
                log("Fallback: Trying to click 'Add Reel' or 'Create Reel' button...")
                page.locator('div[role="button"]:has-text("Create reel")').first.click()
                page.wait_for_timeout(5000)

            page.screenshot(path="fb_step2_create_reel_modal.png")

            # Step 3: Attach Video File to the "Create reel" modal (Add Video / drag and drop)
            log(f"Step 3: Uploading video file '{video_path}' into Create reel modal...")
            file_inputs = page.locator('div[role="dialog"] input[type="file"], input[type="file"]')
            if file_inputs.count() > 0:
                file_inputs.first.set_input_files(os.path.abspath(video_path))
                log("Waiting 15 seconds for video upload and preview generation...")
                page.wait_for_timeout(15000)
                page.screenshot(path="fb_step3_video_uploaded.png")
            else:
                log("ERROR: Could not find file input element in Create reel modal!")
                raise RuntimeError("File input missing in Create reel modal.")

            # Step 4: Click 'Next' (First Next button after video upload)
            log("Step 4a: Clicking the first 'Next' button...")
            next_btn_1 = page.locator('div[role="dialog"] div[role="button"]:has-text("Next"), div[role="dialog"] button:has-text("Next"), div[role="dialog"] div[aria-label="Next"]').first
            if next_btn_1.is_visible():
                next_btn_1.click(force=True)
                page.wait_for_timeout(5000)
                page.screenshot(path="fb_step4a_next1_clicked.png")

            # Step 4b: Click 'Next' again if a 2nd step (Audio/Edit) appears before Reel settings
            next_btn_2 = page.locator('div[role="dialog"] div[role="button"]:has-text("Next"), div[role="dialog"] button:has-text("Next"), div[role="dialog"] div[aria-label="Next"]').first
            if next_btn_2.is_visible() and page.locator('text="Reel settings"').count() == 0:
                log("Step 4b: Clicking the second 'Next' button...")
                next_btn_2.click(force=True)
                page.wait_for_timeout(5000)
                page.screenshot(path="fb_step4b_next2_clicked.png")

            # Step 5: Fill caption in "Reel settings" ("Describe your reel...")
            log("Step 5: Entering caption into 'Describe your reel...' text editor...")
            page.screenshot(path="fb_step5_reel_settings.png")

            describe_box = page.locator('div[role="dialog"] div[aria-label*="Describe your reel"], div[role="dialog"] div[contenteditable="true"], div[role="dialog"] textarea').first
            if describe_box.is_visible():
                log("Focusing 'Describe your reel...' editor...")
                try:
                    describe_box.click(force=True)
                except Exception:
                    describe_box.evaluate("el => el.focus()")

                page.keyboard.type(caption)
                page.wait_for_timeout(3000)
                page.screenshot(path="fb_step5_caption_entered.png")
            else:
                log("WARNING: Could not locate 'Describe your reel...' text box.")

            # Step 6: Press final "Post" button at the bottom left of Reel settings
            log("Step 6: Clicking final 'Post' button...")
            post_btn = page.locator('div[role="dialog"] div[role="button"]:has-text("Post"), div[role="dialog"] button:has-text("Post"), div[role="dialog"] div[aria-label="Post"]').first
            
            if post_btn.is_visible():
                log("Clicking blue 'Post' button...")
                try:
                    post_btn.click(force=True)
                except Exception:
                    post_btn.evaluate("el => el.click()")
                
                log("Waiting 30 seconds for Reel publication to finish...")
                page.wait_for_timeout(30000)
                page.screenshot(path="fb_step6_reel_posted.png")
                log("SUCCESS: Reel successfully posted to Facebook Page!")
            else:
                log("ERROR: Could not locate 'Post' button on Reel settings screen!")
                raise RuntimeError("Post button missing on Reel settings screen.")

        except Exception as err:
            log(f"ERROR during Reel creation flow: {err}")
            page.screenshot(path="fb_error_fatal.png")
            raise err
        finally:
            browser.close()
            if temp_session_file.exists():
                temp_session_file.unlink()

if __name__ == "__main__":
    publish_to_facebook()
