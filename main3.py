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
        return sanitize_text(m.group(1)) if m else ""
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

# ---------------- Gemini deep summarizer ----------------
def summarize_with_gemini(headline, description, lead):
    if not genai:
        return None, None

    prompt = f"""
You are a professional YouTube Shorts journalist.

GOAL:
Create a HIGH-RETENTION Shorts script that explains the FULL story.

SCRIPT RULES:
- First line MUST hook instantly
- Cover all important facts
- Clear, human, conversational
- 6–8 short spoken lines
- No emojis in script
- Final line: soft subscribe CTA

TITLE RULES:
- EXACTLY 3 words
- Add at least 3 relevant emojis at the end
- Include hashtags
- Catchy but factual

ARTICLE CONTENT:
Headline:
{headline}

Description:
{description}

Article Lead:
{lead}

FORMAT (STRICT):
TITLE:
<one line>

SCRIPT:
<one sentence per line>
"""

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = model.generate_content(prompt)

        if not resp or not resp.text:
            return None, None

        text = resp.text.strip()

        title_match = re.search(r"TITLE:\s*(.+)", text)
        script_match = re.search(r"SCRIPT:\s*(.+)", text, re.S)

        if not title_match or not script_match:
            return None, None

        yt_title = sanitize_text(title_match.group(1))
        lines = [
            sanitize_text(l)
            for l in script_match.group(1).split("\n")
            if sanitize_text(l)
        ]

        if len(lines) < 4:
            return None, None

        return yt_title, lines[:8]

    except Exception as e:
        log("Gemini failed:", e)
        return None, None

# ---------------- Script generator ----------------
def generate_script(headline, description, lead):
    yt_title, lines = summarize_with_gemini(headline, description, lead)

    if yt_title and lines:
        return yt_title, lines

    fallback_title = "Breaking Star Update 🔥🎬✨ #Shorts #Entertainment #Celebs"

    fallback_lines = [
        "This story is taking over social media right now.",
        headline,
        description or lead or "Here’s what we know so far.",
        "Fans and critics are reacting fast.",
        "Follow for real entertainment updates."
    ]

    return fallback_title, fallback_lines

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
    img = Image.new("RGBA", (VIDEO_W, CAPTION_HEIGHT), (0, 0, 0, 200))
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
    yt_title, lines = generate_script(title, desc, lead)

    bg = fetch_and_prepare_bg(img)
    tts, durs = create_tts(lines)
    out = WORKDIR / "final.mp4"

    build_video(bg, lines, tts, durs, out)
    upload_to_youtube(out, yt_title, desc or lead)
    save_uploaded(title)

    log("DONE — uploaded to YouTube")

if __name__ == "__main__":
    main()
