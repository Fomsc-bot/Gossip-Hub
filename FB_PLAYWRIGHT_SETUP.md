# Automated Facebook Page Publishing (No Graph API)

This solution automates posting to your Facebook Page (`https://www.facebook.com/profile.php?id=61584777925866`) directly from **GitHub Actions** using **Playwright headless browser automation**, bypassing the Facebook Graph API entirely.

---

## 🛠️ One-Time Setup Instructions

### 1. Export Facebook Session Cookies (Locally)
To allow GitHub Actions to post without hitting 2FA / CAPTCHA security prompts, log into Facebook once on your local computer and export your session:

1. Install Playwright on your computer:
   ```bash
   pip install playwright
   playwright install
   ```
2. Run the cookie export script:
   ```bash
   python export_fb_cookies.py
   ```
3. A Chrome window will open. Log into your Facebook account (if not already logged in).
4. After logging in, return to your terminal. The script will save `fb_session.json` and print out the raw JSON text.

---

### 2. Add Secret to GitHub Repository
1. Go to your GitHub Repository: `https://github.com/Fomsc-bot/Gossip-Hub`
2. Navigate to **Settings** -> **Secrets and variables** -> **Actions**.
3. Click **New repository secret**.
4. Set **Name**: `FB_COOKIES_JSON`
5. Set **Value**: Paste the entire JSON content from `fb_session.json` generated in Step 1.
6. Click **Add secret**.

---

## 🚀 How It Runs Automatically

- **Automatic Trigger**: The workflow `.github/workflows/auto_facebook_playwright.yml` runs twice daily on schedule or automatically after your YouTube shorts workflow completes.
- **Manual Trigger**: You can also trigger it manually anytime under the **Actions** tab on GitHub by selecting **"Auto Facebook Playwright Post (No Graph API)"** and clicking **Run workflow**.

---

## 📁 Added Files Summary
- [`export_fb_cookies.py`](file:///C:/Users/User/.gemini/antigravity/scratch/Gossip-Hub/export_fb_cookies.py): One-time local script to export your Facebook browser cookies.
- [`fb_playwright_poster.py`](file:///C:/Users/User/.gemini/antigravity/scratch/Gossip-Hub/fb_playwright_poster.py): Playwright script that logs into Facebook on GitHub Actions using saved cookies and publishes the video/caption to your Page.
- [`.github/workflows/auto_facebook_playwright.yml`](file:///C:/Users/User/.gemini/antigravity/scratch/Gossip-Hub/.github/workflows/auto_facebook_playwright.yml): The GitHub Actions workflow for zero-human-involvement publishing.
