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

    # Strategy 1: Look for "Switch Now" or "Switch" banner button on profile page
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

    # Strategy 2: If no direct Switch Now button, click top-right profile icon menu
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

                # Click "Switch to Gossip Hub" or click Gossip Hub item
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
    log(f"Target video file: {video_path}")
    log(f"Caption (max 5 hashtags): {caption}")
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
            # ── 1. Ensure Profile is Switched to Gossip Hub Page ──────────────────────
            switch_to_gossip_hub_profile(page, page_id)

            # ── 2. Navigate to https://www.facebook.com/ home feed ────────────────────
            log("Step 1: Navigating to https://www.facebook.com/ home feed...")
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)

            log(f"Page Title: {page.title()}")
            page.screenshot(path="fb_step1_home.png")

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
            page.wait_for_timeout(2000)
            page.screenshot(path="fb_step5_caption_entered.png")

            # ── 6. Check Modal Type & Submit ─────────────────────────────────────────
            log("Step 5: Checking for 'Next' vs direct 'Post' button...")
            
            next_btn_1 = page.locator('div[aria-label="Next"][role="button"], div[role="button"]:has-text("Next")').first
            post_btn = page.locator('div[aria-label="Post"][role="button"], div[role="button"]:has-text("Post")').first

            # Try Next flow first (Reel/Page Video modal format)
            if next_btn_1.is_visible(timeout=10000):
                log("Found 'Next' button (Page Reel flow). Proceeding with Next steps...")
                
                # Step 5a: Click First Next
                safe_click(page, next_btn_1, "First Next", timeout=20000)
                page.wait_for_timeout(5000)
                page.screenshot(path="fb_step6_next1_pressed.png")

                # Step 5b: Click Second Next
                next_btn_2 = page.locator('div[aria-label="Next"][role="button"], div[role="button"]:has-text("Next")').first
                safe_click(page, next_btn_2, "Second Next", timeout=15000)
                page.wait_for_timeout(5000)
                page.screenshot(path="fb_step7_next2_pressed.png")

                # Step 5c: Type in Describe your reel box
                describe_box = page.locator(
                    'div[aria-placeholder="Describe your reel..."][role="textbox"], '
                    'div[aria-placeholder*="Describe"][role="textbox"], '
                    'div[contenteditable="true"][aria-placeholder*="Describe"]'
                ).first
                type_into_editor(page, describe_box, caption, "Describe your reel", timeout=15000)
                page.wait_for_timeout(2000)
                page.screenshot(path="fb_step8_describe_entered.png")

                # Step 5d: Click final Post
                post_btn_final = page.locator('div[aria-label="Post"][role="button"], div[role="button"]:has-text("Post")').first
                clicked_post = safe_click(page, post_btn_final, "Final Post", timeout=20000)
                if not clicked_post:
                    raise RuntimeError("Could not click final Post button in Reel flow.")
            
            else:
                # Direct Post flow (Standard Video Post modal)
                log("No 'Next' button found. Proceeding with direct 'Post' button...")
                clicked_post = safe_click(page, post_btn, "Direct Post", timeout=30000)
                if not clicked_post:
                    raise RuntimeError("Neither 'Next' nor 'Post' button could be clicked.")

            log("Waiting 40 seconds for publication to finish...")
            page.wait_for_timeout(40000)
            page.screenshot(path="fb_step9_published.png")
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
