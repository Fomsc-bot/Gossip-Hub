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
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

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
BASE_FONT_SIZE = 56
MAX_CAPTION_CHARS = 220


def log(*a):
    print("[BOT]", *a)


# ---------------- Text Helpers ----------------
def sanitize_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', "", s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s.strip(" \"'")


# ---------------- Duplicate Filter ----------------
def has_uploaded(title):
    if not LAST_FILE.exists():
        return False
    return title.lower() in LAST_FILE.read_text().lower()


def save_uploaded(title):
    with open(LAST_FILE, "a", encoding="utf-8") as f:
        f.write(title + "\n")


# ---------------- News ----------------
def get_news_article():
    url = f"https://gnews.io/api/v4/top-headlines?topic=entertainment&lang=en&max=5&token={GNEWS_API_KEY}"
    data = requests.get(url, timeout=15).json()
    for art in data.get("articles", []):
        title = sanitize_text(art["title"])
        if not has_uploaded(title):
            return (
                title,
                sanitize_text(art.get("description", "")),
                art.get("image"),
            )
    raise RuntimeError("No new articles")


# ---------------- BG Image ----------------
def fetch_and_prepare_bg(image_url):
    if image_url:
        img = Image.open(BytesIO(requests.get(image_url).content)).convert("RGB")
    else:
        img = Image.open(BytesIO(
            requests.get(f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/entertainment").content
        )).convert("RGB")

    img = img.resize((VIDEO_W, VIDEO_H))
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
    paths, durations = [], []
    for i, line in enumerate(lines):
        path = WORKDIR / f"tts_{i}.mp3"
        gTTS(line).save(path)
        audio = AudioFileClip(str(path))
        durations.append(max(audio.duration, 1))
        audio.close()
        paths.append(str(path))
    return paths, durations


# ---------------- Captions ----------------
def render_caption(text, idx):
    img = Image.new("RGBA", (VIDEO_W, CAPTION_HEIGHT), (0, 0, 0, 200))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    w, h = draw.textsize(text, font)
    draw.text(((VIDEO_W - w)//2, (CAPTION_HEIGHT - h)//2), text, fill="white", font=font)
    out = WORKDIR / f"cap_{idx}.png"
    img.save(out)
    return str(out)


# ---------------- Video ----------------
def build_video(bg, lines, tts, durs):
    bg_clip = ImageClip(bg).set_duration(sum(durs)+1).fx(vfx.resize, lambda t: 1+ZOOM_RATE*t)
    clips, t = [], 0
    for i, (line, dur) in enumerate(zip(lines, durs)):
        cap = ImageClip(render_caption(line, i)).set_start(t).set_duration(dur)
        cap = cap.set_position(("center", VIDEO_H*0.78))
        clips.append(cap)
        t += dur

    audio = concatenate_audioclips([AudioFileClip(p) for p in tts])
    final = CompositeVideoClip([bg_clip]+clips).set_audio(audio)

    out = WORKDIR / "final.mp4"
    final.write_videofile(out, fps=FPS, codec="libx264", audio_codec="aac")
    return str(out)


# ---------------- YouTube Upload ----------------
def upload_youtube(video, title, desc):
    creds = Credentials(
        None, refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token"
    )
    creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds)

    body = {"snippet": {"title": title, "description": desc}, "status": {"privacyStatus": "public"}}
    req = yt.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(video))
    req.execute()
    log("Uploaded to YouTube")


# ---------------- Facebook Upload ----------------
def upload_facebook(video, title, desc):
    if not FB_PAGE_ID or not FB_PAGE_TOKEN:
        log("Facebook credentials missing — skipping FB upload")
        return

    url = f"https://graph.facebook.com/v24.0/{FB_PAGE_ID}/videos"
    files = {"source": open(video, "rb")}
    data = {
        "access_token": FB_PAGE_TOKEN,
        "title": title,
        "description": f"{desc}\n\n#GossipHub #Entertainment #News"
    }

    r = requests.post(url, files=files, data=data, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"FB upload failed: {r.text}")

    log("Uploaded to Facebook Page")


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
