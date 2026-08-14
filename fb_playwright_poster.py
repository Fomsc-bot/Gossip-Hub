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


def click_action_button(page, button_texts: list, label: str, timeout: int = 40000) -> bool:
    """
    Polls for any visible & enabled button matching given text variations or aria-labels.
    Supports Next, Post, Publish, Share, and Share now buttons.
    Waits if aria-disabled='true' or disabled (indicating video processing/uploading in progress).
    """
    log(f"Waiting for '{label}' button with options {button_texts} (timeout={timeout}ms)...")
    start_time = time.time()
    
    selectors = []
    for text in button_texts:
        selectors.extend([
            f'div[role="dialog"] div[aria-label="{text}"][role="button"]',
            f'div[role="dialog"] div[role="button"]:has-text("{text}")',
            f'div[role="dialog"] button:has-text("{text}")',
            f'div[aria-label="{text}"][role="button"]',
            f'div[role="button"]:has-text("{text}")',
            f'button:has-text("{text}")',
            f'[aria-label="{text}"]'
        ])
    
    selector_str = ", ".join(selectors)

    while (time.time() - start_time) * 1000 < timeout:
        try:
            btns = page.locator(selector_str)
            count = btns.count()
            for i in range(count):
                btn = btns.nth(i)
                if btn.is_visible():
                    aria_disabled = btn.get_attribute("aria-disabled")
                    disabled = btn.get_attribute("disabled")
                    if aria_disabled == "true" or disabled is not None:
                        log(f"'{label}' found at index {i} but disabled (aria-disabled={aria_disabled}). Waiting for video upload/processing...")
                        continue
                    log(f"'{label}' found at index {i} and is active. Clicking...")
                    try:
                        btn.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    try:
                        btn.click(force=True, timeout=5000)
                    except Exception:
                        btn.evaluate("el => el.click()")
                    log(f"'{label}' clicked successfully!")
                    return True
        except Exception as e:
            log(f"Retry loop in click_action_button ({label}): {e}")
        time.sleep(2)

    log(f"WARNING: '{label}' button not found or not clickable within {timeout}ms.")
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


def handle_popups(page):
    """
    Dismiss common Facebook post-publication or promotional popups.
    """
    popup_texts = ["Not Now", "Skip", "Close", "Cancel", "No Thanks", "Maybe Later"]
    for text in popup_texts:
        try:
            btn = page.locator(
                f'div[role="dialog"] div[role="button"]:has-text("{text}"), '
                f'div[role="dialog"] button:has-text("{text}"), '
                f'div[role="button"]:has-text("{text}"), '
                f'button:has-text("{text}")'
            ).first
            if btn.is_visible(timeout=1500):
                log(f"Dismissing popup: '{text}'...")
                btn.click(force=True)
                page.wait_for_timeout(2000)
        except Exception:
            pass


def switch_to_gossip_hub_profile(page, page_id: str):
    """
    Navigates to the Gossip Hub page URL and switches profile if needed.
    Raises RuntimeError if session expired or security checkpoint hit.
    """
    page_url = f"https://www.facebook.com/profile.php?id={page_id}"
    log(f"Step 0: Navigating to Page profile: {page_url}")
    page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)
    page.screenshot(path="fb_step0_page_profile.png")

    current_url = page.url.lower()
    if "login" in current_url or "checkpoint" in current_url or "two_factor" in current_url:
        log(f"ERROR: Session invalid/expired or security checkpoint hit! Current URL: {page.url}")
        raise RuntimeError("Facebook session expired or hit security checkpoint. Re-export cookies using export_fb_cookies.py.")

    switch_selectors = [
        'div[role="button"]:has-text("Switch Now")',
        'button:has-text("Switch Now")',
        'div[role="button"]:has-text("Switch")',
        'button:has-text("Switch")',
        '[aria-label*="Switch into Gossip Hub"]',
        '[aria-label*="Switch profile"]'
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
                    switched = True
                    log("Switched profile via menu!")
        except Exception as e:
            log(f"Account menu check notice: {e}")

    page.screenshot(path="fb_step0_switched_confirm.png")


def publish_to_facebook():
    cookies_json = os.getenv("FB_COOKIES_JSON")
    page_id = os.getenv("FB_PAGE_ID", DEFAULT_FB_PAGE_ID)

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
        log(f"ERROR: Video file '{video_path}' does not exist on disk!")
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
    log(f"Starting Facebook Video Workflow: Page ID {page_id}")
    log(f"Playwright Headless Mode: {is_headless}")
    log(f"Target video file: {video_path}")
    log(f"Caption: {caption}")
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
            # ── Step 1: Switch to Page Profile & Confirm Wall Access ──────────────────
            switch_to_gossip_hub_profile(page, page_id)

            page_profile_url = f"https://www.facebook.com/profile.php?id={page_id}"
            log(f"Step 1: Navigating to Page wall: {page_profile_url}")
            page.goto(page_profile_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            handle_popups(page)

            log(f"Page Title: {page.title()}")
            page.screenshot(path="fb_step1_page_wall.png")

            # ── Step 2: Click "Photo/video" button ────────────────────────────────────
            log('Step 2: Clicking "Photo/video" button...')
            photo_video_btn = page.locator(
                'div[aria-label="Photo/video"][role="button"], '
                '[aria-label="Photo/video"], '
                'div[role="button"]:has-text("Photo/video"), '
                'span:has-text("Photo/video")'
            ).first

            clicked_pv = safe_click(page, photo_video_btn, "Photo/video", timeout=30000)
            if not clicked_pv:
                log("Primary 'Photo/video' button click missed. Trying alternative post creation trigger...")
                create_post_box = page.locator(
                    'div[role="button"]:has-text("What\'s on your mind"), '
                    'div[aria-label*="Create a post"], '
                    'div[role="textbox"]'
                ).first
                if not safe_click(page, create_post_box, "Create Post Box", timeout=15000):
                    raise RuntimeError('"Photo/video" or post creation box not found on Facebook Page wall.')

            page.wait_for_timeout(4000)
            page.screenshot(path="fb_step3_photo_video_modal.png")

            # ── Step 3: Attach Video File to Modal Input ──────────────────────────────
            log(f"Step 3: Attaching video file '{video_path}' to modal file input...")
            try:
                log("Waiting for file input in modal DOM...")
                file_input_selector = 'div[role="dialog"] input[type="file"], input[type="file"][accept*="video"], input[type="file"]'
                page.wait_for_selector(file_input_selector, state="attached", timeout=25000)
                file_inputs = page.locator(file_input_selector)
                log(f"Found {file_inputs.count()} file input element(s). Setting video path on target file input...")
                
                # Filter for input inside dialog or input accepting video
                dialog_inputs = page.locator('div[role="dialog"] input[type="file"]')
                if dialog_inputs.count() > 0:
                    target_input = dialog_inputs.first
                else:
                    target_input = file_inputs.first

                target_input.set_input_files(os.path.abspath(video_path))
                log("Video file successfully attached to post composer file input!")
            except PlaywrightTimeoutError:
                log("ERROR: File input element did not appear in DOM within timeout.")
                raise RuntimeError("File input element missing after opening post creation modal.")

            log("Waiting 20 seconds for video chunk upload & preview processing...")
            page.wait_for_timeout(20000)
            page.screenshot(path="fb_step4_video_uploaded.png")

            # ── Step 4: Enter Caption Text ───────────────────────────────────────────
            log("Step 4: Entering caption into post text editor...")
            caption_box = page.locator(
                'div[role="dialog"] div[contenteditable="true"], '
                'div[aria-placeholder*="What\'s on your mind"][role="textbox"], '
                'div[aria-placeholder="What\'s on your mind, Gossip Hub?"][role="textbox"], '
                'div[contenteditable="true"][data-lexical-editor="true"], '
                'div[contenteditable="true"][role="textbox"]'
            ).first

            typed_caption = type_into_editor(page, caption_box, caption, "Caption Editor", timeout=20000)
            if not typed_caption:
                log("WARNING: Could not type into primary caption editor. Trying keyboard fallback...")
                try:
                    page.keyboard.type(caption, delay=20)
                except Exception as e:
                    log(f"Fallback typing notice: {e}")

            page.wait_for_timeout(3000)
            page.screenshot(path="fb_step5_caption_entered.png")

            # ── Step 5: Handle Post / Publish / Next Workflow ─────────────────────────
            log("Step 5: Publishing post / reel (checking for Post, Publish, Share, Next buttons)...")
            handle_popups(page)
            
            # Check if direct "Post", "Publish", "Share", or "Share now" button exists in modal
            pub_success = click_action_button(page, ["Post", "Publish", "Share", "Share now"], "Post/Publish", timeout=30000)

            if not pub_success:
                log("Direct 'Post/Publish/Share' button not ready or modal is multi-step (Reels flow). Trying 'Next'...")
                clicked_next1 = click_action_button(page, ["Next"], "First Next Step", timeout=30000)
                if clicked_next1:
                    page.wait_for_timeout(4000)
                    page.screenshot(path="fb_step6_next1.png")

                    # Step 5b: Try Second Next or Publish/Post/Share
                    clicked_next2 = click_action_button(page, ["Next"], "Second Next Step", timeout=20000)
                    if clicked_next2:
                        page.wait_for_timeout(4000)
                        page.screenshot(path="fb_step7_next2.png")

                    # Step 5c: Reel description editor check
                    describe_box = page.locator(
                        'div[aria-placeholder*="Describe"][role="textbox"], '
                        'div[contenteditable="true"][aria-placeholder*="Describe"]'
                    ).first
                    if describe_box.is_visible(timeout=3000):
                        type_into_editor(page, describe_box, caption, "Describe Reel", timeout=10000)

                    # Step 5d: Click final Post/Publish/Share button
                    pub_success = click_action_button(page, ["Post", "Publish", "Share", "Share now"], "Final Post/Publish", timeout=40000)

            if not pub_success:
                raise RuntimeError("Could not find or click an active 'Post', 'Publish', 'Share', or 'Next' button.")

            log("Post/Publish/Share button successfully clicked!")
            page.screenshot(path="fb_step8_published_click.png")

            # ── Step 6: Wait for Background Video Upload & Server Encoding ────────────
            log("Step 6: Waiting for Facebook post submission & video upload completion...")
            handle_popups(page)

            try:
                log("Waiting for creation dialog to detach...")
                page.locator('div[role="dialog"]').wait_for(state="detached", timeout=90000)
                log("Creation dialog detached! Post submitted to Facebook.")
            except Exception:
                log("Dialog detach wait timed out — holding browser open for background chunk upload...")
                page.wait_for_timeout(40000)

            try:
                page.wait_for_load_state("networkidle", timeout=30000)
                log("Network idle reached — upload streams completed.")
            except Exception:
                log("Network idle wait timeout — keeping extra safety margin...")
                page.wait_for_timeout(15000)

            log("Holding 15 seconds to ensure feed indexing...")
            page.wait_for_timeout(15000)

            # ── Step 7: Verification ──────────────────────────────────────────────────
            log("Navigating to Page profile wall for final publication verification...")
            try:
                page.goto(page_profile_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(8000)
                page.screenshot(path="fb_final_published_verification.png")
                log("Verification screenshot saved: fb_final_published_verification.png")
            except Exception as e:
                log(f"Verification screenshot notice: {e}")

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
