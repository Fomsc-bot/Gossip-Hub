import os
import re
import random
import requests
import html
import textwrap
from pathlib import Path
from io import BytesIO

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip,
    concatenate_audioclips, concatenate_videoclips, vfx, VideoFileClip
)

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ---------------- Gemini (OPTIONAL) ----------------
genai = None
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

try:
    import google.generativeai as genai_lib
    if GEMINI_API_KEY:
        genai = genai_lib
        genai.configure(api_key=GEMINI_API_KEY)
except Exception:
    genai = None

# ---------------- CONFIG ----------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")

YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)
LAST_FILE = WORKDIR / "last_titles.txt"

VIDEO_W, VIDEO_H = 1080, 1920
CAPTION_HEIGHT = int(VIDEO_H * 0.16)
FPS = 24
ZOOM_RATE = 0.015
BASE_FONT_SIZE = 56
MAX_CAPTION_CHARS = 220

OUTTRO_DIR = Path("Outtro")

def log(*a):
    print("[BOT]", *a)

# ---------------- Text cleanup ----------------
def sanitize_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', "", s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s.strip(" \"'")

# ---------------- Duplicate filter ----------------
def has_uploaded(title):
    if not LAST_FILE.exists():
        return False
    return title.lower() in LAST_FILE.read_text(encoding="utf-8").lower()

def save_uploaded(title):
    with open(LAST_FILE, "a", encoding="utf-8") as f:
        f.write(title + "\n")

# ---------------- News fetching ----------------
def _try_newsapi_fetch():
    if not NEWS_API_KEY:
        raise RuntimeError("NEWS_API_KEY missing")
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    return requests.get(url, timeout=15).json()["articles"]

def _try_gnews_fetch():
    if not GNEWS_API_KEY:
        raise RuntimeError("GNEWS_API_KEY missing")
    url = f"https://gnews.io/api/v4/top-headlines?topic=entertainment&lang=en&max=5&token={GNEWS_API_KEY}"
    return requests.get(url, timeout=15).json()["articles"]

def fetch_article_lead(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        html_text = r.text

        m = re.search(r'og:description" content="([^"]+)"', html_text)
        if m:
            return sanitize_text(m.group(1).split(".")[0])

        p = re.search(r"<p[^>]*>(.*?)</p>", html_text, re.S)
        if p:
            clean = re.sub(r"<[^>]+>", "", p.group(1))
            return sanitize_text(clean.split(".")[0])

    except Exception:
        pass
    return ""

def get_news_article():
    for fetcher in (_try_newsapi_fetch, _try_gnews_fetch):
        try:
            articles = fetcher()
            for art in articles:
                title = sanitize_text(art.get("title", "Entertainment Update"))
                if has_uploaded(title):
                    continue
                desc = sanitize_text(art.get("description", ""))
                img = art.get("urlToImage") or art.get("image")
                url = art.get("url") or art.get("link")
                lead = fetch_article_lead(url)
                return title, desc, img, url, lead
        except Exception:
            continue
    raise RuntimeError("Failed to fetch news")

# ---------------- Background image ----------------
def fetch_and_prepare_bg(image_url):
    try:
        r = requests.get(image_url, timeout=15)
        img = Image.open(BytesIO(r.content)).convert("RGB")
    except Exception:
        r = requests.get(f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/entertainment")
        img = Image.open(BytesIO(r.content)).convert("RGB")

    img = img.resize((VIDEO_W, VIDEO_H))
    out = WORKDIR / "bg.jpg"
    img.save(out)
    return str(out)

# ---------------- Gemini summarizer ----------------
def summarize_with_gemini(headline, description, lead):
    if not genai:
        return None

    prompt = f"""
Write a natural YouTube Shorts voiceover (4–5 lines).

Rules:
- Human tone
- Short sentences
- No emojis
- No hashtags
- Last line: soft subscribe CTA

Headline:
{headline}

Summary:
{description or lead}
"""

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        lines = [sanitize_text(l) for l in response.text.split("\n") if sanitize_text(l)]
        return lines[:5] if len(lines) >= 3 else None
    except Exception:
        return None

# ---------------- Script generator ----------------
def generate_script(headline, description="", lead=""):
    gemini_lines = summarize_with_gemini(headline, description, lead)
    if gemini_lines:
        return gemini_lines

    return [
        headline,
        "Here’s the latest update on this story.",
        description or lead or "Sources confirm new developments.",
        "People are reacting strongly online.",
        "Subscribe for clear entertainment updates."
    ]

# ---------------- TTS ----------------
def create_tts_per_line(lines):
    tts_paths, durations = [], []
    for i, line in enumerate(lines):
        out = WORKDIR / f"tts_{i}.mp3"
        gTTS(text=line, lang="en").save(out)
        audio = AudioFileClip(str(out))
        durations.append(audio.duration + 0.25)
        audio.close()
        tts_paths.append(str(out))
    return tts_paths, durations

# ---------------- Captions ----------------
def render_bottom_caption(text, index):
    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        BASE_FONT_SIZE
    )
    wrapped = "\n".join(textwrap.wrap(text, 28))
    img = Image.new("RGBA", (VIDEO_W, CAPTION_HEIGHT), (0, 0, 0, 200))
    draw = ImageDraw.Draw(img)

    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font)
    x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
    y = (CAPTION_HEIGHT - (bbox[3] - bbox[1])) // 2
    draw.multiline_text((x, y), wrapped, font=font, fill="white", align="center")

    out = WORKDIR / f"cap_{index}.png"
    img.save(out)
    return str(out)

# ---------------- Video build ----------------
def build_final_video(bg_path, lines, tts_paths, durations, out_file):
    total = sum(durations) + 1
    bg = ImageClip(bg_path).set_duration(total)
    bg = bg.fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)

    caps, cursor = [], 0
    for i, dur in enumerate(durations):
        cap = ImageClip(render_bottom_caption(lines[i], i)).set_duration(dur).set_start(cursor)
        cap = cap.set_position(("center", VIDEO_H * 0.78))
        caps.append(cap)
        cursor += dur

    audio = concatenate_audioclips([AudioFileClip(p) for p in tts_paths])
    final = CompositeVideoClip([bg] + caps).set_audio(audio)
    final.write_videofile(str(out_file), fps=FPS, codec="libx264", audio_codec="aac")
    return str(out_file)

# ---------------- MAIN ----------------
def main():
    log("Starting pipeline...")
    title, desc, img_url, article_url, lead = get_news_article()
    lines = generate_script(title, desc, lead)

    bg = fetch_and_prepare_bg(img_url)
    tts_paths, durations = create_tts_per_line(lines)
    out_video = WORKDIR / "final.mp4"

    build_final_video(bg, lines, tts_paths, durations, out_video)
    save_uploaded(title)
    log("DONE")

if __name__ == "__main__":
    main()
