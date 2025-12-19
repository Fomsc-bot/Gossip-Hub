# main.py
import os
import re
import requests
import html
from pathlib import Path
from io import BytesIO

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

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

FB_PAGE_ID = os.getenv("FB_PAGE_ID")
FB_PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN")

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)
LAST_FILE = WORKDIR / "last_titles.txt"

VIDEO_W, VIDEO_H = 1080, 1920
CAPTION_HEIGHT = int(VIDEO_H * 0.16)
FPS = 24
ZOOM_RATE = 0.015


def log(*a):
    print("[BOT]", *a)


# ---------------- Text Helpers ----------------
def sanitize_text(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', "", s)
    return re.sub(r'\s+', ' ', s).strip(" \"'")


def ascii_only(s: str) -> str:
    return s.encode("ascii", "ignore").decode()


def has_uploaded(title):
    if not LAST_FILE.exists():
        return False
    return title.lower() in LAST_FILE.read_text().lower()


def save_uploaded(title):
    with open(LAST_FILE, "a", encoding="utf-8") as f:
        f.write(title + "\n")


# ---------------- News ----------------
def get_news_article():
    try:
        if GNEWS_API_KEY:
            url = (
                "https://gnews.io/api/v4/top-headlines"
                f"?topic=entertainment&lang=en&max=10&token={GNEWS_API_KEY}"
            )
            data = requests.get(url, timeout=15).json()
            for art in data.get("articles", []):
                title = sanitize_text(art.get("title"))
                if title and not has_uploaded(title):
                    return title, sanitize_text(art.get("description")), art.get("image")
    except Exception as e:
        log("GNews failed:", e)

    try:
        if NEWS_API_KEY:
            url = (
                "https://newsapi.org/v2/top-headlines"
                f"?category=entertainment&pageSize=10&apiKey={NEWS_API_KEY}"
            )
            data = requests.get(url, timeout=15).json()
            for art in data.get("articles", []):
                title = sanitize_text(art.get("title"))
                if title and not has_uploaded(title):
                    return title, sanitize_text(art.get("description")), art.get("urlToImage")
    except Exception as e:
        log("NewsAPI failed:", e)

    raise RuntimeError("No new articles found")


# ---------------- SAFE BG IMAGE ----------------
def fetch_and_prepare_bg(image_url):
    img = None
    if image_url:
        try:
            r = requests.get(image_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
                img = Image.open(BytesIO(r.content)).convert("RGB")
        except (UnidentifiedImageError, OSError):
            log("Invalid article image — fallback")

    if img is None:
        r = requests.get(
            f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/entertainment",
            timeout=15
        )
        img = Image.open(BytesIO(r.content)).convert("RGB")

    # Maintain aspect ratio and center-crop
    img_ratio = img.width / img.height
    video_ratio = VIDEO_W / VIDEO_H

    if img_ratio > video_ratio:
        new_height = VIDEO_H
        new_width = int(img_ratio * new_height)
    else:
        new_width = VIDEO_W
        new_height = int(new_width / img_ratio)

    img = img.resize((new_width, new_height), Image.LANCZOS)

    left = (new_width - VIDEO_W)//2
    top = (new_height - VIDEO_H)//2
    img = img.crop((left, top, left + VIDEO_W, top + VIDEO_H))

    out = WORKDIR / "bg.jpg"
    img.save(out)
    return str(out)


# ---------------- Script ----------------
def generate_script(title, desc):
    return [
        title,
        desc or "Breaking entertainment update.",
        "People are reacting online.",
        "Follow for more updates 🔥"
    ]


# ---------------- TTS ----------------
def create_tts(lines):
    paths, durs = [], []
    for i, line in enumerate(lines):
        path = WORKDIR / f"tts_{i}.mp3"
        gTTS(line).save(str(path))
        audio = AudioFileClip(str(path))
        durs.append(max(audio.duration, 1))
        audio.close()
        paths.append(str(path))
    return paths, durs


# ---------------- Captions ----------------
def render_caption(text, idx):
    safe_text = ascii_only(text)
    img = Image.new("RGBA", (VIDEO_W, CAPTION_HEIGHT), (0, 0, 0, 200))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    w, h = draw.textsize(safe_text, font)
    draw.text(
        ((VIDEO_W - w)//2, (CAPTION_HEIGHT - h)//2),
        safe_text,
        fill="white",
        font=font
    )
    out = WORKDIR / f"cap_{idx}.png"
    img.save(out)
    return str(out)


# ---------------- Video ----------------
def build_video(bg, lines, tts, durs):
    bg_clip = ImageClip(bg).set_duration(sum(durs)+1)
    bg_clip = bg_clip.fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)

    clips, t = [], 0
    for i, dur in enumerate(durs):
        cap = ImageClip(render_caption(lines[i], i)).set_start(t).set_duration(dur)
        cap = cap.set_position(("center", VIDEO_H*0.78))
        clips.append(cap)
        t += dur

    audio = concatenate_audioclips([AudioFileClip(p) for p in tts])
    final = CompositeVideoClip([bg_clip] + clips).set_audio(audio)

    out = WORKDIR / "final.mp4"
    final.write_videofile(str(out), fps=FPS, codec="libx264", audio_codec="aac")
    return str(out)


# ---------------- Uploads ----------------
def upload_youtube(video, title, desc):
    creds = Credentials(
        None,
        refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token"
    )
    creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds)
    yt.videos().insert(
        part="snippet,status",
        body={"snippet": {"title": title, "description": desc},
              "status": {"privacyStatus": "public"}},
        media_body=MediaFileUpload(video)
    ).execute()
    log("Uploaded to YouTube")


def upload_facebook(video, title, desc):
    if not FB_PAGE_ID or not FB_PAGE_TOKEN:
        return
    r = requests.post(
        f"https://graph.facebook.com/v24.0/{FB_PAGE_ID}/videos",
        files={"source": open(video, "rb")},
        data={
            "access_token": FB_PAGE_TOKEN,
            "title": title,
            "description": f"{desc}\n\n#GossipHub #Entertainment #News"
        },
        timeout=300
    )
    if r.status_code != 200:
        raise RuntimeError(r.text)
    log("Uploaded to Facebook")


# ---------------- MAIN ----------------
def main():
    log("Starting...")
    title, desc, img = get_news_article()
    lines = generate_script(title, desc)

    bg = fetch_and_prepare_bg(img)
    tts, durs = create_tts(lines)
    video = build_video(bg, lines, tts, durs)

    upload_youtube(video, title, desc)
    upload_facebook(video, title, desc)

    save_uploaded(title)
    log("DONE")


if __name__ == "__main__":
    main()
