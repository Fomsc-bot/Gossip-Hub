import os
import re
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

DEFAULT_FB_PAGE_ID = "100081981528810"


def get_fb_session_json():
    """Load Facebook storage state from a file or the GitHub Actions secret."""
    session_path = os.getenv("FB_COOKIES_FILE")
    if session_path:
        return Path(session_path).read_text(encoding="utf-8")
    return os.getenv("FB_COOKIES_JSON")

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
    
    while (time.time() - start_time) * 1000 < timeout:
        try:
            # Use Playwright's exact accessible-name match first. The old
            # :has-text selectors also matched larger controls such as
            # "Add to feed post", which can make the click appear successful
            # while leaving the actual blue Post button untouched.
            for text in button_texts:
                exact_buttons = page.get_by_role("button", name=text, exact=True)
                for i in range(exact_buttons.count()):
                    btn = exact_buttons.nth(i)
                    if not btn.is_visible():
                        continue
                    aria_disabled = btn.get_attribute("aria-disabled")
                    disabled = btn.get_attribute("disabled")
                    is_enabled = btn.is_enabled()
                    if aria_disabled == "true" or disabled is not None or not is_enabled:
                        log(f"'{text}' button found but disabled (aria-disabled={aria_disabled}). Waiting for processing...")
                        continue
                    log(f"Exact '{text}' button found and active. Clicking...")
                    try:
                        btn.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    try:
                        btn.click(timeout=7000)
                    except Exception:
                        # Use DOM click only on the exact-name matched button.
                        btn.evaluate("el => el.click()")
                    log(f"Exact '{text}' button click dispatched.")
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


def open_video_composer(page) -> None:
    """Open the Page's Reel composer, handling Facebook's varying Page layouts."""
    for label, pattern in (
        ("Photo/video", re.compile(r"photo.?video", re.I)),
        ("Create reel", re.compile(r"create\s+reel", re.I)),
        ("Create post", re.compile(r"create\s+(a\s+)?post|what'?s on your mind", re.I)),
    ):
        try:
            log(f"Looking for the '{label}' button...")
            button = page.get_by_role("button", name=pattern).first
            if button.is_visible(timeout=2500):
                log(f"Found '{label}'. Clicking it to open the Page composer...")
                button.click(timeout=10000)
                page.wait_for_timeout(2500)
                log(f"Clicked '{label}'; composer had time to open.")
                if label in ("Photo/video", "Create reel"):
                    return
                break
        except Exception:
            continue

    selectors = [
        ("Photo/video or video button", page.get_by_role("button", name=re.compile(r"photo.?video|add video|video|create reel", re.I)).first),
        ("Photo/video or Create reel control", page.locator('[aria-label*="Photo/video" i], [aria-label*="Create reel" i], [aria-label*="Add video" i]').first),
        ("Photo/video or Create reel text", page.get_by_text(re.compile(r"photo.?video|add video|create reel", re.I)).first),
    ]
    for label, candidate in selectors:
        try:
            log(f"Trying fallback control: {label}...")
            if candidate.is_visible(timeout=2500):
                log(f"Found {label}. Clicking to open the video composer...")
                candidate.click(timeout=10000)
                page.wait_for_timeout(2500)
                log(f"Clicked {label}; composer had time to open.")
                return
        except Exception:
            continue

    page.screenshot(path="fb_composer_not_found.png", full_page=True)
    try:
        buttons = page.locator('button, [role="button"]').evaluate_all(
            "els => els.map(el => (el.innerText || el.getAttribute('aria-label') || '').trim()).filter(Boolean)"
        )
    except Exception:
        buttons = []
    log(f"Composer diagnostics: URL={page.url!r}; title={page.title()!r}; buttons={buttons[:60]!r}")
    raise RuntimeError(
        "Could not find the Page's Create Reel/Create Post controls. "
        "Check fb_composer_not_found.png and confirm the FB_COOKIES_JSON account "
        "still has Page publishing access."
    )


def ensure_video_upload_control(page):
    """Verify the Reel composer exposes a file input before rendering starts."""
    log("Checking whether the Reel composer has a video upload control...")
    file_inputs = page.locator('input[type="file"]')
    if file_inputs.count() == 0:
        log("No file input yet. Looking for an Add video/Upload video/Add media button...")
        for label in ("Add video", "Upload video", "Add media"):
            try:
                button = page.get_by_role("button", name=re.compile(label, re.I)).first
                if button.is_visible(timeout=2000):
                    log(f"Found '{label}'. Clicking it to reveal the upload control...")
                    button.click(timeout=10000)
                    log(f"Clicked '{label}'.")
                    break
            except Exception:
                continue
    else:
        log(f"Upload control is already present ({file_inputs.count()} file input(s)); no button click needed.")
    try:
        page.wait_for_selector('input[type="file"]', state="attached", timeout=15000)
    except PlaywrightTimeoutError as exc:
        page.screenshot(path="fb_upload_control_missing.png", full_page=True)
        raise RuntimeError(
            "Facebook Page video composer opened, but its video file input was not found. "
            "See fb_upload_control_missing.png."
        ) from exc

    count = file_inputs.count()
    log(f"Reel composer verified: {count} file input(s) are attached and ready for video.")
    page.screenshot(path="fb_preflight_reel_composer_ready.png", full_page=True)


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


def open_facebook_page(page, page_id: str) -> str:
    """Log in, switch to the Page identity, then open the feed without switching back."""
    switch_to_gossip_hub_profile(page, page_id)
    # page_id is the personal profile used to reach the identity switcher.
    # Reopening it after switching changes Facebook back to that personal profile.
    page_url = "https://www.facebook.com/"
    log(f"Opening Facebook feed as the selected Page identity: {page_url}")
    page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(7000)
    current_url = page.url.lower()
    page.screenshot(path="fb_step1_page_wall.png", full_page=True)
    if "login" in current_url or "checkpoint" in current_url or "two_factor" in current_url:
        raise RuntimeError(
            "Facebook session expired or requires a security check. "
            "Re-export FB_COOKIES_JSON using an account with Page publishing access."
        )
    body_text = page.locator("body").inner_text(timeout=10000).lower()
    if "use another profile" in body_text and "remove profiles from this browser" in body_text:
        page.screenshot(path="fb_profile_chooser.png", full_page=True)
        raise RuntimeError(
            "Facebook displayed its saved-profile chooser instead of the logged-in feed. "
            "The workflow cannot safely choose between the listed accounts. Log in to the "
            "correct Facebook account locally, export a fresh storage state with "
            "export_fb_cookies.py, update the FB_COOKIES_JSON repository secret, and rerun. "
            "See fb_profile_chooser.png."
        )
    email_field = page.locator(
        'input[name="email"], input[autocomplete="username"], '
        'input[placeholder*="Email or phone" i]'
    ).first
    password_field = page.locator('input[name="pass"], input[type="password"]').first
    if email_field.is_visible() and password_field.is_visible():
        page.screenshot(path="fb_login_required.png", full_page=True)
        raise RuntimeError(
            "Facebook displayed its login form, so FB_COOKIES_JSON was not accepted. "
            "Re-export the current logged-in browser state with export_fb_cookies.py, "
            "replace the repository secret, and rerun preflight. See fb_login_required.png."
        )
    if "this content isn't available" in body_text or "content isn't available" in body_text:
        raise RuntimeError(
            "Facebook reports this Page is unavailable to the saved account. "
            "Confirm FB_PAGE_ID and that FB_COOKIES_JSON can manage the Page."
        )
    log(f"Facebook Page loaded: title={page.title()!r}; URL={page.url!r}")
    return page_url


def publish_to_facebook():
    cookies_json = get_fb_session_json()
    page_id = os.getenv("FB_PAGE_ID") or DEFAULT_FB_PAGE_ID

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
            viewport={"width": 1100, "height": 700},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            record_video_dir=str(video_recording_dir)
        )

        page = context.new_page()

        try:
            # ── Step 1: Switch to Page Profile & Confirm Wall Access ──────────────────
            page_url = open_facebook_page(page, page_id)
            handle_popups(page)

            page.screenshot(path="fb_step1_page_wall.png", full_page=True)

            # ── Step 2: Click "Photo/video" button ────────────────────────────────────
            log("Step 2: Opening the Page Reel/video composer...")
            open_video_composer(page)
            ensure_video_upload_control(page)
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
                'div[aria-placeholder*="Describe your reel" i][role="textbox"], '
                'div[contenteditable="true"][aria-placeholder*="Describe your reel" i], '
                'div[role="dialog"] div[contenteditable="true"], '
                'div[aria-placeholder*="What\'s on your mind"][role="textbox"], '
                'div[aria-placeholder="What\'s on your mind, Gossip Hub?"][role="textbox"], '
                'div[contenteditable="true"][data-lexical-editor="true"], '
                'div[contenteditable="true"][role="textbox"]'
            ).first

            caption_placeholder = (caption_box.get_attribute("aria-placeholder") or "").lower()
            caption_is_reel_description = "describe" in caption_placeholder
            typed_caption = type_into_editor(page, caption_box, caption, "Caption Editor", timeout=20000)
            if not typed_caption:
                log("No caption editor is visible yet; will check for the Reel description step after Next.")

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
                    if describe_box.is_visible(timeout=3000) and not caption_is_reel_description:
                        type_into_editor(page, describe_box, caption, "Describe Reel", timeout=10000)

                    # Step 5d: Click final Post/Publish/Share button
                    pub_success = click_action_button(page, ["Post", "Publish", "Share", "Share now"], "Final Post/Publish", timeout=40000)

            if not pub_success:
                raise RuntimeError("Could not find or click an active 'Post', 'Publish', 'Share', or 'Next' button.")

            log("Post/Publish/Share button successfully clicked!")
            log("Waiting for Facebook to complete the post submission...")
            # Depending on the composer variant, Facebook either closes the
            # composer after submission or shows a confirmation with a Done
            # button. A Done button is not guaranteed, so watch for either
            # completion signal instead of failing on that single UI detail.
            done_button = page.get_by_role("button", name="Done", exact=True)
            composer_dialog = page.locator('div[role="dialog"]').first
            submission_complete = False
            deadline = time.time() + 10
            try:
                while time.time() < deadline:
                    if done_button.is_visible():
                        page.screenshot(path="fb_step8_published_click.png")
                        log("Facebook showed a submission confirmation. Clicking 'Done'...")
                        done_button.click(timeout=15000)
                        submission_complete = True
                        break
                    if composer_dialog.count() == 0 or not composer_dialog.is_visible():
                        page.screenshot(path="fb_step8_published_click.png")
                        log("Facebook closed the composer after submission.")
                        submission_complete = True
                        break
                    page.wait_for_timeout(1000)
            except Exception as e:
                page.screenshot(path="fb_step8_publish_confirmation_error.png")
                raise RuntimeError("Facebook's post submission confirmation could not be handled.") from e

            if not submission_complete:
                page.screenshot(path="fb_step8_publish_confirmation_error.png")
                raise RuntimeError(
                    "Facebook kept the publish dialog open for 10 seconds without showing confirmation. "
                    "Check fb_step8_publish_confirmation_error.png to see whether the upload is still processing or Facebook reported an error."
                )

            # ── Step 6: Wait for Background Video Upload & Server Encoding ────────────
            log("Step 6: Waiting for Facebook post submission & video upload completion...")
            handle_popups(page)

            try:
                log("Waiting for creation dialog to detach...")
                page.locator('div[role="dialog"]').wait_for(state="detached", timeout=90000)
                log("Creation dialog detached! Post submitted to Facebook.")
            except Exception:
                # Submission was already confirmed above; a remaining dialog
                # here indicates Facebook still has an unresolved composer.
                page.screenshot(path="fb_upload_dialog_still_open.png")
                raise RuntimeError(
                    "Facebook confirmed the publish action, but the upload composer did not close. "
                    "Check fb_upload_dialog_still_open.png before retrying to avoid publishing a duplicate."
                )

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
                page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
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
    if "--preflight" in sys.argv:
        # Use the same session, Page selection, and composer navigation as the
        # publish flow, but stop before attaching a video or publishing anything.
        cookies_json = get_fb_session_json()
        page_id = os.getenv("FB_PAGE_ID") or DEFAULT_FB_PAGE_ID
        if not cookies_json:
            raise RuntimeError("FB_COOKIES_JSON environment secret is missing")

        session_file = Path("temp_fb_preflight_session.json")
        session_file.write_text(cookies_json, encoding="utf-8")
        video_recording_dir = Path("video_recording")
        video_recording_dir.mkdir(exist_ok=True)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=os.getenv("HEADLESS", "false").lower() == "true",
                    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
                )
                context = browser.new_context(
                    storage_state=str(session_file),
                    viewport={"width": 1100, "height": 700},
                    record_video_dir=str(video_recording_dir),
                )
                page = context.new_page()
                try:
                    log("Preflight: validating Facebook login and Page publishing access...")
                    open_facebook_page(page, page_id)
                    log("Preflight step 2: opening the Reel/video composer and reporting the button used...")
                    open_video_composer(page)
                    log("Preflight step 3: checking the video upload control...")
                    ensure_video_upload_control(page)
                    log("Preflight passed. Video generation may start.")
                finally:
                    browser.close()
        finally:
            session_file.unlink(missing_ok=True)
    else:
        publish_to_facebook()
