# main.py
import os
import re
import time
import random
import requests
import html
from pathlib import Path
from io import BytesIO

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip, concatenate_audioclips, vfx
)

from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.http import MediaFileUpload

# ---------------- Config ----------------
WORKDIR = Path("./workdir")
WORKDIR.mkdir(exist_ok=True)
LAST_FILE = WORKDIR / "uploaded.txt"

VIDEO_W, VIDEO_H = 720, 1280
FPS = 30
ZOOM_RATE = 0.02
CAPTION_HEIGHT = 100
BASE_FONT_SIZE = 40
MAX_CAPTION_CHARS = 80

NEWS_API_KEY = os.getenv("NEWS_API_KEY")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
genai = None
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


# ---------------- Helpers ----------------
def log(msg):
    print(f"[LOG] {msg}")


def sanitize_text(s):
    if not s:
        return ""
    # Remove things like "Hash #123"
    s = re.sub(r'\bHash\s*#?\d+\b', " ", s, flags=re.I)
    s = re.sub(r'\bNo\.?\s*0*39\b', "'", s, flags=re.I)
    s = re.sub(r'\bNo\.?\s*\d+\b', " ", s, flags=re.I)

    # Convert HTML entities like &#039; → '
    s = re.sub(r'&[#A-Za-z0-9]+;?', lambda m: html.unescape(m.group(0)), s)

    # Remove control characters
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', "", s)

    # Fix spacing before punctuation
    s = re.sub(r'\s+([,.\-:;!?])', r'\1', s)

    # Normalize whitespace
    s = re.sub(r'\s+', ' ', s).strip()

    # Remove extra quotes at ends
    s = s.strip(" \t\n\"'")
    return s


def has_uploaded(title):
    if not LAST_FILE.exists():
        return False
    with open(LAST_FILE, "r", encoding="utf-8") as f:
        return title.strip().lower() in f.read().lower()


def save_uploaded(title):
    with open(LAST_FILE, "a", encoding="utf-8") as f:
        f.write(title.strip() + "\n")


def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {}


# ---------------- Title builder ----------------
def short_title_from_text(text: str) -> str:
    text = sanitize_text(text)

    words = re.findall(r"[A-Za-z0-9']+", text)
    stop = {"the", "a", "an", "in", "on", "at", "by", "for", "to", "of", "and", "vs", "vs."}
    words = [w for w in words if w.lower() not in stop]

    short_words = words[:4] if words else ["Hot", "News"]
    short = " ".join(short_words).strip()

    emoji = random.choice(["🔥", "🎬", "⭐", "⚡", "📸", "🔔"])

    hashtags = []
    if genai and GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel("gemini-pro")
            prompt = f"Give 3 hashtags for: {text}. Output only hashtags."
            res = model.generate_content(prompt)
            raw = (res.text or "").strip()
            hashtags = re.findall(r"#\w+", raw)[:3]
        except Exception:
            pass

    if not hashtags:
        hs = [w.lower() for w in short_words][:3]
        hashtags = [f"#{re.sub(r'[^A-Za-z0-9]', '', x)}" for x in hs]

    final_title = f"{short} {emoji} {' '.join(hashtags)}"
    final_title = re.sub(r"[^\x00-\x7F]+", "", final_title)[:120]
    return final_title


# ---------------- News fetch ----------------
def get_news_article():
    if not NEWS_API_KEY:
        raise RuntimeError("NEWS_API_KEY missing")

    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=6&apiKey={NEWS_API_KEY}"
    r = requests.get(url, timeout=15)
    data = safe_json(r)

    if data.get("status") != "ok":
        raise RuntimeError("NewsAPI error")

    for art in data.get("articles", []):
        raw_title = art.get("title") or "Entertainment Update"
        title = sanitize_text(raw_title)
        if not has_uploaded(title):
            description = sanitize_text(art.get("description") or "")
            image_url = art.get("urlToImage")
            article_url = art.get("url")
            lead = fetch_article_lead(article_url) if article_url else ""
            lead = sanitize_text(lead)
            if not description and lead:
                description = lead
            return title, description, image_url, article_url, lead

    raise RuntimeError("All articles already used")


def fetch_article_lead(url):
    if not url:
        return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=8)
        html_text = r.text

        m = re.search(r'og:description" content="([^"]+)"', html_text)
        if m:
            return sanitize_text(m.group(1).split(".")[0])

        p = re.search(r"<p[^>]*>(.*?)</p>", html_text, re.S)
        if p:
            clean = re.sub(r"<[^>]+>", "", p.group(1))
            clean = clean.strip().split(".")[0]
            return sanitize_text(clean)

        return ""
    except Exception:
        return ""


# ---------------- Remaining functions ----------------
# fetch_and_prepare_bg, generate_script, create_tts_per_line, render_bottom_caption,
# build_final_video, get_youtube_service, upload_public
# ... (same as your original code, just ensure proper indentation)

# ---------------- MAIN ----------------
def main():
    log("Starting pipeline...")
    title, desc, img_url, article_url, lead = get_news_article()

    lines = generate_script(title, desc, lead)
    lines = [sanitize_text(x) for x in lines]

    bg = fetch_and_prepare_bg(img_url)
    tts_paths, durations = create_tts_per_line(lines)

    out_video = WORKDIR / "final.mp4"
    video_path = build_final_video(bg, lines, tts_paths, durations, out_video)

    yt_title = short_title_from_text(title)
    yt_desc = desc or lead or "Trending entertainment update."

    upload_public(video_path, yt_title, yt_desc)
    save_uploaded(title)

    log("DONE — video uploaded PUBLIC.")


if __name__ == "__main__":
    main()

