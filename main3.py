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
    concatenate_audioclips, vfx
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

def log(*a):
    print("[BOT]", *a)

# ---------------- Text cleanup ----------------
def sanitize_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', "", s)
    return re.sub(r'\s+', ' ', s).strip(" \"'")

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
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    return requests.get(url, timeout=15).json()["articles"]

def _try_gnews_fetch():
    url = f"https://gnews.io/api/v4/top-headlines?topic=entertainment&lang=en&max=5&token={GNEWS_API_KEY}"
    return requests.get(url, timeout=15).json()["articles"]

def fetch_article_lead(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        m = re.search(r'og:description" content="([^"]+)"', r.text)
        return sanitize_text(m.group(1).split(".")[0]) if m else ""
    except Exception:
        return ""

def get_news_article():
    for fetcher in (_try_newsapi_fetch, _try_gnews_fetch):
        try:
            for art in fetcher():
                title = sanitize_text(art.get("title", "Entertainment Update"))
                if has_uploaded(title):
                    continue
                return (
                    title,
                    sanitize_text(art.get("description", "")),
                    art.get("urlToImage") or art.get("image"),
                    art.get("url") or art.get("link"),
                    fetch_article_lead(art.get("url"))
                )
        except Exception:
            continue
    raise RuntimeError("Failed to fetch news")

# ---------------- Background ----------------
def fetch_and_prepare_bg(image_url):
    try:
        img = Image.open(BytesIO(requests.get(image_url, timeout=15).content))
    except Exception:
        img = Image.open(BytesIO(
            requests.get(f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/celebrity").content
        ))
    img = img.resize((VIDEO_W, VIDEO_H))
    out = WORKDIR / "bg.jpg"
    img.save(out)
    return str(out)

# ---------------- Gemini summarizer ----------------
def summarize_with_gemini(headline, description, lead):
    if not genai:
        return None
    try:
        prompt = f"""
Write a natural YouTube Shorts voiceover (4–5 lines).
Human, short sentences, last line soft subscribe CTA.

Headline:
{headline}

Summary:
{description or lead}
"""
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = model.generate_content(prompt)
        lines = [sanitize_text(l) for l in resp.text.split("\n") if sanitize_text(l)]
        return lines[:5]
    except Exception:
        return None

# ---------------- Script generator ----------------
def generate_script(headline, description, lead):
    return summarize_with_gemini(headline, description, lead) or [
        headline,
        "Here’s the latest update.",
        description or lead,
        "People are reacting online.",
        "Subscribe for clear entertainment updates."
    ]

# ---------------- TTS ----------------
def create_tts(lines):
    paths, durs = [], []
    for i, line in enumerate(lines):
        out = WORKDIR / f"tts_{i}.mp3"
        gTTS(line).save(out)
        clip = AudioFileClip(str(out))
        durs.append(clip.duration + 0.25)
        clip.close()
        paths.append(str(out))
    return paths, durs

# ---------------- Captions ----------------
def caption(text, i):
    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        BASE_FONT_SIZE
    )
    img = Image.new("RGBA", (VIDEO_W, CAPTION_HEIGHT), (0,0,0,200))
    draw = ImageDraw.Draw(img)
    wrapped = "\n".join(textwrap.wrap(text, 28))
    draw.multiline_text((40, 20), wrapped, font=font, fill="white")
    out = WORKDIR / f"cap_{i}.png"
    img.save(out)
    return out

# ---------------- Video build ----------------
def build_video(bg, lines, tts, durs, out):
    total = sum(durs) + 1
    bg = ImageClip(bg).set_duration(total).fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)
    clips, t = [], 0
    for i, d in enumerate(durs):
        cap = ImageClip(str(caption(lines[i], i))).set_duration(d).set_start(t)
        cap = cap.set_position(("center", VIDEO_H * 0.78))
        clips.append(cap)
        t += d
    audio = concatenate_audioclips([AudioFileClip(p) for p in tts])
    CompositeVideoClip([bg] + clips).set_audio(audio).write_videofile(
        str(out), fps=FPS, codec="libx264", audio_codec="aac"
    )

# ---------------- YouTube upload ----------------
def upload_to_youtube(video_path, title, description):
    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": ["shorts", "entertainment", "news"]
        },
        "status": {"privacyStatus": "public"}
    }

    media = MediaFileUpload(video_path, resumable=True)
    yt.videos().insert(part="snippet,status", body=body, media_body=media).execute()

# ---------------- MAIN ----------------
def main():
    log("Starting pipeline...")
    title, desc, img, url, lead = get_news_article()
    lines = generate_script(title, desc, lead)

    bg = fetch_and_prepare_bg(img)
    tts, durs = create_tts(lines)
    out = WORKDIR / "final.mp4"

    build_video(bg, lines, tts, durs, out)
    upload_to_youtube(out, title, desc or lead)
    save_uploaded(title)

    log("DONE — uploaded to YouTube")

if __name__ == "__main__":
    main()
