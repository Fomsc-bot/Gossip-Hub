import os
import re
import requests
import html
import textwrap
from pathlib import Path
from io import BytesIO
from datetime import datetime

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip,
    concatenate_audioclips, vfx
)

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

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
def sanitize_text(s):
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
    """Return first news article with a trusted image URL."""
    for fetcher in (_try_newsapi_fetch, _try_gnews_fetch):
        try:
            for art in fetcher():
                title = sanitize_text(art.get("title", "Entertainment Update"))
                if has_uploaded(title):
                    continue
                img_url = art.get("urlToImage") or art.get("image")
                if not img_url:
                    continue  # Skip if no valid image
                return (
                    title,
                    sanitize_text(art.get("description", "")),
                    img_url,
                    art.get("url") or art.get("link"),
                    fetch_article_lead(art.get("url"))
                )
        except Exception:
            continue
    raise RuntimeError("Failed to fetch news with valid image")

# ---------------- AUTO EMOJI SELECTION ----------------
EMOJI_MAP = {
    "death": "🕯️💔😢",
    "killed": "🚨💥😱",
    "arrest": "🚓⚖️🔥",
    "scandal": "😳🔥📉",
    "award": "🏆✨🎉",
    "movie": "🎬🍿🔥",
    "film": "🎥✨🔥",
    "music": "🎵🎤🔥",
    "viral": "🚀🔥📱",
    "celebrity": "⭐📸🔥"
}

def select_emojis(text):
    text = text.lower()
    for key, emojis in EMOJI_MAP.items():
        if key in text:
            return emojis
    return "🔥🎬✨"

# ---------------- DAILY ROTATING HASHTAGS ----------------
CONTENT_HASHTAGS = {
    "movie": "#movie",
    "film": "#film",
    "music": "#music",
    "award": "#award",
    "celebrity": "#celebrity",
    "scandal": "#scandal",
    "arrest": "#breaking",
    "death": "#news",
    "viral": "#viral"
}

DAILY_HASHTAG_POOLS = [
    ["#shorts", "#trending", "#viral", "#fyp"],
    ["#shorts", "#ytshorts", "#breakingnews", "#explore"],
    ["#shorts", "#reels", "#hotnews", "#trendingnow"],
    ["#shorts", "#buzz", "#mustwatch", "#viralvideo"]
]

def select_hashtags(text):
    day_index = datetime.utcnow().timetuple().tm_yday % len(DAILY_HASHTAG_POOLS)
    base_tags = DAILY_HASHTAG_POOLS[day_index]

    content_tags = []
    text = text.lower()
    for key, tag in CONTENT_HASHTAGS.items():
        if key in text:
            content_tags.append(tag)

    tags = list(dict.fromkeys(content_tags + base_tags))
    return " ".join(tags[:5])

# ---------------- VIRAL SHORTS TITLE ----------------
def generate_title(headline):
    words = re.findall(r'\b[A-Za-z]{4,}\b', headline)
    core = words[:2] if len(words) >= 2 else words[:1]
    title = " ".join(core).title()
    hashtags = select_hashtags(headline)
    return f"{title} {select_emojis(headline)} {hashtags}"[:100]

# ---------------- VIRAL HOOK ----------------
def generate_hook(headline):
    h = headline.lower()
    if any(k in h for k in ["death", "killed", "dead"]):
        return "This news shocked everyone."
    if any(k in h for k in ["arrest", "charged", "court"]):
        return "This just took a serious turn."
    if any(k in h for k in ["scandal", "leak", "exposed"]):
        return "Nobody expected this."
    if any(k in h for k in ["award", "wins", "honored"]):
        return "This moment made history."
    return "This story is exploding online."

# ---------------- IMAGE FETCHING ----------------
def fetch_image_bytes(url, timeout=15):
    """Fetch image bytes only from trusted sources."""
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if not content_type.startswith("image/"):
        raise RuntimeError(f"URL did not return an image, got '{content_type}'")
    return resp.content

# ---------------- Background (BLUR-FILL + PARALLAX) ----------------
def fetch_and_prepare_bg(image_url: str) -> str:
    if not image_url:
        raise RuntimeError("No image URL provided.")

    try:
        img_bytes = fetch_image_bytes(image_url)
        img = Image.open(BytesIO(img_bytes)).convert("RGB")
    except Exception as e:
        raise RuntimeError(f"Cannot fetch image from URL: {image_url}\n{e}")

    # Create blurred background
    bg = img.resize((VIDEO_W, VIDEO_H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))

    img_ratio = img.width / img.height
    target_ratio = VIDEO_W / VIDEO_H

    if img_ratio > target_ratio:
        fg_width = VIDEO_W
        fg_height = int(fg_width / img_ratio)
    else:
        fg_height = VIDEO_H
        fg_width = int(fg_height * img_ratio)

    fg = img.resize((fg_width, fg_height), Image.LANCZOS)

    canvas = bg.copy()
    x = (VIDEO_W - fg_width) // 2
    y = (VIDEO_H - fg_height) // 2
    canvas.paste(fg, (x, y))

    out = WORKDIR / "bg.jpg"
    canvas.save(out, quality=95)
    return str(out)

# ---------------- Script generator ----------------
def generate_script(headline, description, lead):
    lines = [
        generate_hook(headline),
        headline,
        description or lead or "Here’s what we know so far.",
        "Fans are reacting fast.",
        "Follow for real entertainment updates."
    ]
    return generate_title(headline), lines

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

# ---------------- Video build ----------------
def build_video(bg_path, lines, tts, durs, out):
    total = sum(durs) + 1

    bg = ImageClip(bg_path).set_duration(total).fx(
        vfx.resize, lambda t: 1.06 + ZOOM_RATE * t
    )
    fg = ImageClip(bg_path).set_duration(total).fx(
        vfx.resize, lambda t: 1.02 + (ZOOM_RATE / 2) * t
    )

    clips, t = [], 0
    for i, d in enumerate(durs):
        cap = ImageClip(str(caption(lines[i], i))).set_duration(d).set_start(t)
        cap = cap.set_position(("center", VIDEO_H * 0.78))
        clips.append(cap)
        t += d

    audio = concatenate_audioclips([AudioFileClip(p) for p in tts])

    CompositeVideoClip([bg, fg] + clips).set_audio(audio).write_videofile(
        str(out), fps=FPS, codec="libx264", audio_codec="aac"
    )

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
            "title": title,
            "description": (description or "") + "\n\n#shorts",
            "tags": [
                "shorts",
                "ytshorts",
                "youtube shorts",
                "viral shorts",
                "trending shorts",
                "entertainment shorts"
            ]
        },
        "status": {"privacyStatus": "public"}
    }

    media = MediaFileUpload(video_path, resumable=True)
    yt.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    ).execute()

# ---------------- MAIN ----------------
def main():
    log("Starting pipeline...")

    title, desc, img_url, url, lead = get_news_article()
    yt_title, lines = generate_script(title, desc, lead)

    bg = fetch_and_prepare_bg(img_url)
    tts, durs = create_tts(lines)
    out = WORKDIR / "final.mp4"

    build_video(bg, lines, tts, durs, out)
    upload_to_youtube(out, yt_title, desc or lead)
    save_uploaded(title)

    log("DONE — uploaded to YouTube")

if __name__ == "__main__":
    main()
