# Gossip Hub

An automated, end-to-end video production and publishing pipeline that curates trending entertainment news, generates AI-driven scripts, formats dynamic video content with automated voiceovers and captions, and publishes directly to YouTube and Facebook via scheduled GitHub Actions.

---

## Architecture & Workflow

Gossip Hub operates as an autonomous content creation pipeline. The automated workflow follows four main stages:

```
[News APIs / Web Scraping] ➔ [Script Generation (Gemini AI / Rules)] ➔ [TTS & Video Composition (MoviePy & PIL)] ➔ [Automated Upload (YouTube & FB Graph APIs)]
```

1. **Content Ingestion & De-duplication**
   - Fetches real-time entertainment headlines using **NewsAPI** and **GNews**.
   - Scrapes article leads (`og:description` or fallback meta tags) for detailed context.
   - Logs processed headlines in `work/last_titles.txt` to prevent duplicate video generation.

2. **AI Scripting & Voice Synthesis**
   - Summarizes and formats news articles into engaging voiceover scripts via **Google Gemini API** (with fallback structured templates).
   - Generates natural-sounding speech files for each line using **gTTS** (Google Text-to-Speech).

3. **Dynamic Video Rendering**
   - Pulls high-resolution background imagery matching the news topic (via article images or fallback stock assets).
   - Applies visual effects including smooth zoom animations (`vfx.resize`) and vertical/horizontal cropping.
   - Renders semi-transparent caption overlays with custom typography using **Pillow**.
   - Combines background visuals, speech audio, caption overlays, and outro clips into final MP4 files using **MoviePy**.

4. **Multi-Platform Automated Publishing**
   - **YouTube Data API v3**: Uploads vertical YouTube Shorts (1080x1920) or landscape long-form videos (1920x1080) complete with titles, descriptions, and hashtags.
   - **Facebook Graph API**: Publishes short-form reels directly to a targeted Facebook Page.

---

## File Structure & Entry Points

- `main.py` — Pipeline for generating and uploading **YouTube Shorts** (vertical 1080x1920 format with outro clip support).
- `main2.py` — Pipeline dedicated to **Facebook Reels** integration using the Facebook Graph API.
- `main3.py` — Enhanced YouTube Shorts generator with image validation and multi-attempt news fetching.
- `main_long.py` — **Long-Form YouTube Video** generator (landscape 1920x1080 format) powered by Gemini AI script synthesis.
- `Outtro/` — Stores promotional outro video snippets appended to generated videos.
- `.github/workflows/` — Automated execution scripts for GitHub Actions:
  - `run.yml` — Runs YouTube Shorts automation on a scheduled cron trigger.
  - `run_long.yml` — Schedules daily long-form video generation.
  - `auto_gossip_shorts.yml` — Schedules Facebook Reels publishing.

---

## Tech Stack

- **Language**: Python 3.11
- **Video & Audio Processing**: `moviepy`, `gTTS`, `Pillow` (PIL), `mutagen`
- **AI & Data APIs**: `google-generativeai` (Gemini API), NewsAPI, GNews, Pexels API
- **Publishing & Authentication**: `google-api-python-client`, `google-auth-oauthlib`, Facebook Graph API
- **Automation**: GitHub Actions (Cron triggers & workflow dispatch)

---

## Setup & Local Execution

### Prerequisites

1. Python 3.10+ installed.
2. FFmpeg installed on your system (required by MoviePy for video encoding).

### Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/Fomsc-bot/Gossip-Hub.git
cd Gossip-Hub
pip install -r requirements.txt
```

### Environment Variables

Configure the following environment variables (or add them as GitHub Repository Secrets for automated runs):

| Variable | Description |
|---|---|
| `NEWS_API_KEY` | API Key from NewsAPI.org |
| `GNEWS_API_KEY` | API Key from GNews.io |
| `GEMINI_API_KEY` | Google Gemini API key for script synthesis |
| `YT_CLIENT_ID` | Google OAuth2 Client ID for YouTube Uploads |
| `YT_CLIENT_SECRET` | Google OAuth2 Client Secret |
| `YT_REFRESH_TOKEN` | OAuth2 Refresh Token with YouTube upload scope |
| `FB_PAGE_ID` | Facebook Page ID (for `main2.py`) |
| `FB_PAGE_ACCESS_TOKEN` | Facebook Page Access Token (for `main2.py`) |
| `PEXELS_API_KEY` | (Optional) Pexels API Key for fallback background visuals |

### Running Locally

To generate and upload a YouTube Short:
```bash
python main.py
```

To generate a Facebook Reel:
```bash
python main2.py
```

To generate a Long-Form YouTube Video:
```bash
python main_long.py
```

Generated assets, intermediate audio files, background images, and tracking logs will be stored in the `work/` directory during execution.

---

## CI/CD & Automated Scheduling

The project leverages GitHub Actions to run fully headless content generation workflows. Workflows trigger automatically on specified cron schedules or can be executed manually via `workflow_dispatch`.

Secrets are passed securely to the runner environment, enabling zero-maintenance scheduled publishing.
