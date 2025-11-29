# main.py
import os
import re
import time
import random
import requests
from pathlib import Path
from io import BytesIO

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip, concatenate_audioclips, vfx
)

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ----------- NEW: GEMINI ------------
import google.generativeai as genai
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ---------------- CONFIG ----------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)
LAST_FILE = WORKDIR / "last_titles.txt"

VIDEO_W, VIDEO_H = 1080, 1920
CAPTION_HEIGHT = int(VIDEO_H * 0.18)
FONT_PATH = None

ZOOM_RATE = 0.015
FPS = 24


def log(*a):
    print("[BOT]", *a)


# ---------------- Duplicate Filter ----------------
def has_uploaded(title):
    if not LAST_FILE.exists():
        return False
    with open(LAST_FILE, "r") as f:
        return title.strip().lower() in f.read().lower()


def save_uploaded(title):
    with open(LAST_FILE, "a") as f:
        f.write(title.strip() + "\n")


# ---------------- Helpers ----------------
def safe_json(resp):
    try: return resp.json()
    except: return {}


# ---------------- Short Title (max 3 words + 1 emoji) ----------------
def short_title_from_text(text):
    words = re.findall(r"[A-Za-z]+", text)
    short = " ".join(words[:3]) if words else "Breaking News"
    emoji = random.choice(["🔥", "🎬", "⭐", "⚡", "📸"])
    return f"{short} {emoji}"


# ---------------- News fetch ----------------
def get_news_article():
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    r = requests.get(url, timeout=15)
    data = safe_json(r)

    if data.get("status") != "ok":
        raise RuntimeError(data.get("message", "NewsAPI failure"))

    articles = data.get("articles") or []
    if not articles:
        raise RuntimeError("No articles returned.")

    for art in articles:
        title = art.get("title") or "Entertainment Update"
        if not has_uploaded(title):
            description = art.get("description") or ""
            image_url = art.get("urlToImage")
            return title, description, image_url

    raise RuntimeError("All today's articles already uploaded.")


# ---------------- Background Image ----------------
def fetch_and_prepare_bg(image_url, fallback_query="entertainment"):
    raw_img = None

    if image_url:
        try:
            r = requests.get(image_url, timeout=15)
            if r.status_code == 200:
                raw_img = BytesIO(r.content)
        except:
            pass

    if not raw_img:
        unsplash_url = f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/?{fallback_query}&sig={random.randint(1,999999)}"
        r = requests.get(unsplash_url, timeout=15)
        raw_img = BytesIO(r.content)

    img = Image.open(raw_img).convert("RGB")
    w, h = img.size
    scale = max(VIDEO_W / w, VIDEO_H / h)
    img_resized = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    left = (img_resized.size[0] - VIDEO_W) // 2
    top = (img_resized.size[1] - VIDEO_H) // 2
    crop = img_resized.crop((left, top, left + VIDEO_W, top + VIDEO_H))

    out_path = WORKDIR / "bg.jpg"
    crop.save(out_path, "JPEG", quality=90)
    return str(out_path)


# ---------------- Gemini Script Generator ----------------
def generate_script(headline):
    if not GEMINI_API_KEY:
        return [
            f"Fans react to {headline}!",
            "The internet is exploding with opinions.",
            "Some love it, some are shocked.",
            "Rumors keep spreading fast.",
            "What do YOU think?"
        ]

    prompt = f"""
    Create a very short, punchy, gossip-style 5-line script for a YouTube Shorts video.
    Format: 5 separate lines.
    Topic: "{headline}"

    Requirements:
    - Keep every line under 12 words.
    - Super engaging, fast paced.
    - No repeated phrases.
    - Conversational gossip tone.
    - DO NOT mention it's generated.
    """

    model = genai.GenerativeModel("gemini-pro")
    res = model.generate_content(prompt)
    text = res.text.strip()

    lines = [l.strip("-• ").strip() for l in text.split("\n") if l.strip()]
    return lines[:5]


# ---------------- TTS ----------------
def create_tts_per_line(lines):
    tts_paths = []
    durations = []
    for i, line in enumerate(lines):
        out = WORKDIR / f"tts_{i}.mp3"
        tts = gTTS(text=line, lang="en")
        tts.save(str(out))

        audio = AudioFileClip(str(out))
        dur = max(audio.duration, 1.1)
        audio.close()

        tts_paths.append(str(out))
        durations.append(dur)

    return tts_paths, durations


# ---------------- Captions ----------------
def render_bottom_caption(text, index):
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 54)
    except:
        font = ImageFont.load_default()

    img = Image.new("RGBA", (VIDEO_W, CAPTION_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rectangle([20, 0, VIDEO_W-20, CAPTION_HEIGHT], fill=(0,0,0,190))

    tw, th = draw.textsize(text, font)
    x = (VIDEO_W - tw) // 2
    y = (CAPTION_HEIGHT - th) // 2

    draw.text((x, y), text, font=font, fill=(255,255,255,255))

    path = WORKDIR / f"cap_{index}.png"
    img.save(path)
    return str(path)


# ---------------- Video Builder ----------------
def build_final_video(bg_path, lines, tts_paths, durations, out_file):
    total = sum(durations) + 2

    bg = ImageClip(bg_path).set_duration(total)
    bg = bg.fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)
    bg = bg.fx(vfx.fadein, 0.25).fx(vfx.fadeout, 0.25)

    caps = []
    cursor = 0
    for i, (line, d) in enumerate(zip(lines, durations)):
        cap = ImageClip(render_bottom_caption(line, i)).set_duration(d)
        cap = cap.set_start(cursor).set_position(("center", int(VIDEO_H * 0.78)))
        caps.append(cap)
        cursor += d

    cta = ImageClip(render_bottom_caption("Follow for more 🔔", "cta")).set_duration(2)
    cta = cta.set_start(cursor).set_position(("center", int(VIDEO_H * 0.78)))
    caps.append(cta)

    audio = concatenate_audioclips([AudioFileClip(p) for p in tts_paths])
    audio = audio.set_duration(total)

    final = CompositeVideoClip([bg] + caps, size=(VIDEO_W, VIDEO_H))
    final = final.set_audio(audio)

    final.write_videofile(str(out_file), fps=FPS, codec="libx264", audio_codec="aac")

    return str(out_file)


# ---------------- Upload PUBLIC ----------------
def get_youtube_service():
    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_public(video_file, title, description):
    yt = get_youtube_service()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["shorts", "entertainment", "gossip"]
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_file, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            log("Upload:", int(status.progress()*100), "%")

    return resp.get("id")


# ---------------- MAIN ----------------
def main():
    title, desc, img_url = get_news_article()

    log("Generating script using Gemini...")
    lines = generate_script(title)

    bg = fetch_and_prepare_bg(img_url)
    tts_paths, durations = create_tts_per_line(lines)

    out_video = WORKDIR / "final.mp4"
    video_path = build_final_video(bg, lines, tts_paths, durations, out_video)

    yt_title = short_title_from_text(title)
    yt_desc = desc or "Trending Entertainment Update"

    upload_public(video_path, yt_title, yt_desc)

    save_uploaded(title)

    log("DONE — video uploaded PUBLIC.")


if __name__ == "__main__":
    main()



