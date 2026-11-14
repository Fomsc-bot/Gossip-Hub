# Gossip Hub

Gossip Hub automates entertainment-news content creation. It gathers a story, prepares a script and voice-over, renders a video, and publishes it through the configured platform workflow.

[![Test workflow status](https://github.com/Fomsc-bot/Gossip-Hub/actions/workflows/test1.yml/badge.svg)](https://github.com/Fomsc-bot/Gossip-Hub/actions/workflows/test1.yml)

The badge shows the latest status for the **Test** workflow (`test1.yml`). Click it to open that workflow's runs. GitHub's badge editor may also offer optional branch or event filters; this README uses no extra filters.

## GitHub Actions workflows

These are the workflows documented here:

| Workflow | Purpose | Runs |
|---|---|---|
| [Test (`test1.yml`)](https://github.com/Fomsc-bot/Gossip-Hub/actions/workflows/test1.yml) | Runs `main3.py` to create and upload a YouTube Short. | Scheduled and manual (`workflow_dispatch`) |
| [Facebook Reels (`facebook_reels.yml`)](https://github.com/Fomsc-bot/Gossip-Hub/actions/workflows/facebook_reels.yml) | Renders a short and publishes it to the Facebook Page using Playwright. | Scheduled and manual |
| [Blogger (`publish_blogger.yml`)](https://github.com/Fomsc-bot/Gossip-Hub/actions/workflows/publish_blogger.yml) | Publishes a Blogger article and then shares related content to connected social platforms. | Scheduled and manual |
| [Long-form video (`run_long.yml`)](https://github.com/Fomsc-bot/Gossip-Hub/actions/workflows/run_long.yml) | Runs `main_long.py` to create and upload a long-form YouTube video. | Scheduled and manual |

To run one manually, open its workflow page, choose **Run workflow**, select the branch, and start the run. Automated schedules use UTC in the workflow files.

## How content is made

The Python pipeline uses NewsAPI and/or GNews to find entertainment stories, generates narration, creates voice-over audio, and renders video assets. The Facebook Reels workflow uses Playwright to operate the Facebook Reel composer. The other publishing integrations use the platform APIs configured for each workflow.

The main entry points are:

- `main3.py` — YouTube Shorts pipeline used by `test1.yml`.
- `facebook_main.py` and `fb_playwright_poster.py` — Facebook Reel rendering and browser publishing.
- `blogger_publisher.py` — Blogger article and related social publishing.
- `main_long.py` — long-form YouTube video pipeline.

Generated and intermediate files are stored under `work/` and are excluded from Git.

## Configure GitHub Actions secrets

Add credentials under **Repository → Settings → Secrets and variables → Actions**. Never commit API keys, OAuth tokens, Facebook cookies, or client-secret files.

| Secret | Used by | Purpose |
|---|---|---|
| `NEWS_API_KEY` | All four workflows | NewsAPI access. |
| `GNEWS_API_KEY` | Facebook Reels, Blogger, Long-form | GNews access/fallback. |
| `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` | Test, Blogger, Long-form | YouTube OAuth credentials. |
| `FB_COOKIES_JSON` | Facebook Reels | Playwright Facebook session storage state. |
| `FB_PAGE_ID` | Blogger | Facebook Page target for Blogger's social sharing. |
| `FB_PAGE_ACCESS_TOKEN` | Blogger | Facebook Graph API Page publishing. |
| `BLOGGER_CLIENT_ID`, `BLOGGER_CLIENT_SECRET`, `BLOGGER_REFRESH_TOKEN` | Blogger | Blogger OAuth credentials. |
| `BLOGGER_BLOG_ID` | Blogger | Numeric Blogger blog ID. |
| `GROQ_API_KEY` | Blogger | Article drafting. |
| `GEMINI_API_KEY` | Long-form | Gemini-powered scripting. |
| `PEXELS_API_KEY` | Long-form | Optional/fallback visual assets. |
| `MUSIC_URL` | Long-form | Optional music asset URL. |

The Facebook Reels workflow also sets `FB_PAGE_ID` directly in its YAML, so it does not require that secret for its current target. It does require `FB_COOKIES_JSON`.

## Run locally

Use Python 3.11, install FFmpeg, and install the Python requirements:

```bash
git clone https://github.com/Fomsc-bot/Gossip-Hub.git
cd Gossip-Hub
python -m venv .venv
```

Activate the virtual environment, then run:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Set the environment variables needed by the selected pipeline before running it. For Facebook browser publishing, install Playwright and Chromium as well:

```bash
pip install playwright
playwright install chromium
```

The workflow's browser preflight checks Facebook login and Page publishing access before it renders and uploads a Reel.

## Troubleshooting

- **Workflow fails because a credential is missing:** check the workflow's secrets table above and confirm the secret name matches exactly.
- **Facebook shows “Use another profile” or cannot open the Reel composer:** the saved session is not reaching the logged-in feed. Export a fresh `FB_COOKIES_JSON` while logged into the correct account (see [Facebook setup](FB_PLAYWRIGHT_SETUP.md)), update the repository secret, and rerun the preflight. The workflow will save `fb_profile_chooser.png` when it detects the saved-profile chooser.
- **Check screenshots from a Facebook run:** open the run's `facebook-reel-browser-debug` artifact. It contains browser screenshots and, when available, the recorded browser video.
- **A workflow badge is red:** click the badge or workflow link to inspect the failing run's logs.
