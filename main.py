# main.py
import os
import re
import random
import requests
import html
from pathlib import Path
from io import BytesIO

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip,
    concatenate_audioclips, concatenate_videoclips,
    VideoFileClip, vfx
)

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ---------------- CONFIG ----------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

OUTRO_DIR = Path("Outtro")

LAST_FILE = WORKDIR / "last_titles.txt"

VIDEO_W, VIDEO_H = 1080, 1920
CAPTION_HEIGHT = int(VIDEO_H * 0.16)
FPS = 24
ZOOM_RATE = 0.015
BASE_FONT_SIZE = 56
MAX_CAPTION_CHARS = 220


def log(*a):
    print("[BOT]", *a)


# ---------------- TEXT CLEAN ----------------
def sanitize_text(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


# ---------------- DUPLICATE CHECK ----------------
def has_uploaded(title):
    if not LAST_FILE.exists():
        return False
    return title.lower() in LAST_FILE.read_text(encoding="utf-8").lower()


def save_uploaded(title):
    with open(LAST_FILE, "a", encoding="utf-8") as f:
        f.write(title + "\n")


# ---------------- NEWS FETCH ----------------
def get_news_article():
    url = f"https://gnews.io/api/v4/top-headlines?topic=entertainment&lang=en&max=6&token={GNEWS_API_KEY}"
    r = requests.get(url, timeout=15).json()

    for art in r.get("articles", []):
        title = sanitize_text(art.get("title"))
        if not has_uploaded(title):
            return (
                title,
                sanitize_text(art.get("description")),
                art.get("image"),
                art.get("url"),
                ""
            )

    raise RuntimeError("No new articles")


# ---------------- BACKGROUND IMAGE ----------------
def fetch_and_prepare_bg(image_url):
    try:
        raw = BytesIO(requests.get(image_url, timeout=10).content)
    except:
        raw = BytesIO(requests.get(
            f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/entertainment"
        ).content)

    img = Image.open(raw).convert("RGB")
    img = img.resize((VIDEO_W, VIDEO_H))
    out = WORKDIR / "bg.jpg"
    img.save(out)
    return str(out)


# ---------------- SCRIPT ----------------
def generate_script(title, desc):
    return [
        title,
        "Here’s the latest update.",
        desc or "New details are emerging.",
        "Follow Gossip Hub for more 🔥"
    ]


# ---------------- TTS ----------------
def create_tts(lines):
    paths, durations = [], []
    for i, line in enumerate(lines):
        out = WORKDIR / f"tts_{i}.mp3"
        gTTS(text=line).save(out)
        clip = AudioFileClip(str(out))
        durations.append(max(clip.duration, 1))
        clip.close()
        paths.append(str(out))
    return paths, durations


# ---------------- CAPTIONS ----------------
def render_caption(text, idx):
    img = Image.new("RGBA", (VIDEO_W, CAPTION_HEIGHT), (0, 0, 0, 200))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            BASE_FONT_SIZE
        )
    except:
        font = ImageFont.load_default()

    w, h = draw.textsize(text, font)
    draw.text(((VIDEO_W - w)//2, (CAPTION_HEIGHT - h)//2),
              text, fill="white", font=font)

    out = WORKDIR / f"cap_{idx}.png"
    img.save(out)
    return str(out)


# ---------------- VIDEO BUILD + OUTRO ----------------
def build_final_video(bg_path, lines, tts_paths, durations, out_file):
    bg = ImageClip(bg_path).set_duration(sum(durations) + 1)
    bg = bg.fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)

    caps, cursor = [], 0
    for i, (line, dur) in enumerate(zip(lines, durations)):
        cap = ImageClip(render_caption(line, i))\
            .set_duration(dur)\
            .set_start(cursor)\
            .set_position(("center", VIDEO_H * 0.78))
        caps.append(cap)
        cursor += dur

    audio = concatenate_audioclips([AudioFileClip(p) for p in tts_paths])
    main = CompositeVideoClip([bg] + caps).set_audio(audio)

    clips = [main]

    if OUTRO_DIR.exists():
        outros = list(OUTRO_DIR.glob("*.mp4"))
        if outros:
            outro = VideoFileClip(str(random.choice(outros)))
            outro = outro.resize((VIDEO_W, VIDEO_H))
            clips.append(outro)

    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(str(out_file), fps=FPS, codec="libx264", audio_codec="aac")
    return str(out_file)


# ---------------- YOUTUBE UPLOAD ----------------
def get_youtube_service():
    creds = Credentials(
        None,
        refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def upload_youtube(video, title, desc):
    yt = get_youtube_service()
    body = {
        "snippet": {
            "title": title,
            "description": desc,
            "tags": ["shorts", "gossip", "news"]
        },
        "status": {"privacyStatus": "public"}
    }
    media = MediaFileUpload(video)
    yt.videos().insert(part="snippet,status", body=body, media_body=media).execute()


# ---------------- MAIN ----------------
def main():
    log("Starting pipeline...")

    title, desc, img_url, link, _ = get_news_article()
    lines = generate_script(title, desc)

    bg = fetch_and_prepare_bg(img_url)
    tts, durs = create_tts(lines)

    out = WORKDIR / "final.mp4"
    video = build_final_video(bg, lines, tts, durs, out)

    upload_youtube(video, title, desc)
    save_uploaded(title)

    log("DONE ✅ Video uploaded with outro!")


if __name__ == "__main__":
    main()
