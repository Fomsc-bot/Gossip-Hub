# main.py
import os
import re
import random
import requests
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageFilter, ImageDraw, ImageFont

from gtts import gTTS
from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip, concatenate_audioclips, vfx
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
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")  # optional
MUSIC_URL = os.getenv("MUSIC_URL")  # optional direct mp3 link

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

# vertical short resolution
VIDEO_W, VIDEO_H = 1080, 1920
CAPTION_HEIGHT = int(VIDEO_H * 0.18)   # bottom caption band height
FONT_PATH = None  # set to custom font ttf inside repo if you want
FPS = 24
ZOOM_RATE = 0.015

def log(*a): print("[BOT]", *a)

# ---------------- Helpers ----------------
def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {}

def short_title_from_text(text):
    words = re.findall(r"\w+", re.sub(r"https?:\/\/\S+", "", text))
    words = [w for w in words if len(w) > 2]
    if not words:
        words = re.findall(r"\w+", text)[:3]
    short = " ".join(words[:3]).strip()
    if not short:
        short = "Hot News"
    emojis = "🔥🎬"
    hashtags = " #Shorts #Entertainment"
    return f"{short} {emojis}{hashtags}"

# ---------------- News fetch ----------------
def get_news_article():
    if not NEWS_API_KEY:
        raise RuntimeError("NEWS_API_KEY not set.")
    log("Fetching trending article from NewsAPI...")
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    r = requests.get(url, timeout=15)
    data = safe_json(r)
    if data.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {data.get('message', 'unknown')}")
    articles = data.get("articles") or []
    if not articles:
        raise RuntimeError("No articles returned.")
    article = articles[0]
    title = article.get("title") or article.get("description") or "Entertainment Buzz"
    description = article.get("description") or ""
    image_url = article.get("urlToImage")
    return title, description, image_url

# ---------------- Image prep ----------------
def fetch_and_prepare_bg(image_url, fallback_query="entertainment", blur_top_fraction=0.16):
    raw_img = None
    if image_url:
        try:
            log("Downloading article image...")
            r = requests.get(image_url, timeout=15)
            if r.status_code == 200 and r.content:
                raw_img = BytesIO(r.content)
        except Exception as e:
            log("Article image download failed:", e)

    if not raw_img:
        log("Using Unsplash fallback image for query:", fallback_query)
        unsplash_url = f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/?{fallback_query}&sig={random.randint(1,999999)}"
        r = requests.get(unsplash_url, timeout=15)
        if r.status_code == 200:
            raw_img = BytesIO(r.content)
        else:
            raise RuntimeError("Failed to fetch fallback image")

    img = Image.open(raw_img).convert("RGB")
    w, h = img.size
    scale = max(VIDEO_W / w, VIDEO_H / h)
    new_w = int(w * scale + 0.5)
    new_h = int(h * scale + 0.5)
    img_resized = img.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - VIDEO_W) // 2
    top = (new_h - VIDEO_H) // 2
    right = left + VIDEO_W
    bottom = top + VIDEO_H
    img_cropped = img_resized.crop((left, top, right, bottom))

    try:
        blur_h = int(VIDEO_H * blur_top_fraction)
        if blur_h > 10:
            top_region = img_cropped.crop((0, 0, VIDEO_W, blur_h))
            top_blurred = top_region.filter(ImageFilter.GaussianBlur(radius=10))
            overlay = Image.new("RGBA", top_blurred.size, (0,0,0,60))
            top_blurred = Image.alpha_composite(top_blurred.convert("RGBA"), overlay)
            img_cropped.paste(top_blurred.convert("RGB"), (0, 0))
    except Exception:
        pass

    out_path = WORKDIR / "bg_prepared.jpg"
    img_cropped.save(out_path, "JPEG", quality=90)
    log("Prepared background saved to", out_path)
    return str(out_path)

# ---------------- TTS ----------------
def create_tts_per_line(lines, lang="en"):
    tts_paths = []
    durations = []
    for i, line in enumerate(lines):
        safe_text = re.sub(r"\s+", " ", line).strip()
        out = WORKDIR / f"tts_{i}.mp3"
        log("Generating TTS for line", i)
        tts = gTTS(text=safe_text, lang=lang, slow=False)
        tts.save(str(out))
        a = AudioFileClip(str(out))
        dur = a.duration
        a.close()
        dur = max(dur, max(1.2, len(safe_text.split()) * 0.28))
        tts_paths.append(str(out))
        durations.append(float(dur))
    return tts_paths, durations

# ---------------- Caption ----------------
def render_bottom_caption(text, index, h=CAPTION_HEIGHT, fontsize=56):
    try:
        font = ImageFont.truetype(FONT_PATH or "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fontsize)
    except Exception:
        font = ImageFont.load_default()

    w = VIDEO_W
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    band_margin = int(w * 0.03)
    draw.rectangle([band_margin, 0, w - band_margin, h], fill=(0, 0, 0, 190))

    max_w = w - 2 * (band_margin + 16)
    words = text.split(" ")
    lines = []
    cur = ""
    for word in words:
        test = (cur + " " + word).strip() if cur else word
        tw, th = draw.textsize(test, font=font)
        if tw <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)

    total_h = sum(draw.textsize(l, font=font)[1] for l in lines) + (len(lines)-1) * 6
    y = (h - total_h) // 2
    for line in lines:
        tw, th = draw.textsize(line, font=font)
        x = (w - tw) // 2
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                draw.text((x + ox, y + oy), line, font=font, fill=(0, 0, 0, 200))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += th + 6

    out = WORKDIR / f"caption_bottom_{index}.png"
    img.save(out, "PNG")
    return str(out)

# ---------------- Build final video ----------------
def build_final_video(bg_image_path, lines, tts_paths, durations, out_file):
    total_duration = sum(durations) + 2.0
    log(f"Total duration: {total_duration:.2f}s")

    bg_clip = ImageClip(bg_image_path).set_duration(total_duration)
    bg_clip = bg_clip.fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)
    bg_clip = bg_clip.fx(vfx.fadein, 0.25).fx(vfx.fadeout, 0.35)

    caption_clips = []
    cursor = 0.0
    for i, (line, dur) in enumerate(zip(lines, durations)):
        cap_path = render_bottom_caption(line, i)
        cap_clip = ImageClip(cap_path).set_duration(dur).set_start(cursor).set_position(("center", int(VIDEO_H * 0.78)))
        caption_clips.append(cap_clip)
        cursor += dur

    cta_path = render_bottom_caption("Follow for more 🔔", "cta")
    caption_clips.append(
        ImageClip(cta_path).set_duration(2.0).set_start(cursor).set_position(("center", int(VIDEO_H * 0.78)))
    )

    audio_clips = [AudioFileClip(p) for p in tts_paths]
    combined_audio = concatenate_audioclips(audio_clips)
    if combined_audio.duration < total_duration:
        combined_audio = combined_audio.set_duration(total_duration)

    final = CompositeVideoClip([bg_clip] + caption_clips, size=(VIDEO_W, VIDEO_H)).set_duration(total_duration)
    final = final.set_audio(combined_audio.set_duration(total_duration))

    final.write_videofile(str(out_file), fps=24, codec="libx264", audio_codec="aac", threads=4, preset="fast")

    for a in audio_clips:
        try: a.close()
        except: pass
    try: combined_audio.close()
    except: pass

    return str(out_file)

# ---------------- YouTube helpers (CHANGED: PUBLIC) ----------------
def get_youtube_service():
    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]
    )
    creds.refresh(Request())
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    return youtube

def upload_unlisted(video_file, title, description, tags=None):
    yt = get_youtube_service()
    safe_title = re.sub(r"[^\x00-\x7F]+", "", title)[:100]
    
    body = {
        "snippet": {
            "title": safe_title,
            "description": description,
            "tags": tags or ["shorts", "entertainment"]
        },

        # 🔥🔥🔥 ONLY CHANGE MADE
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_file, chunksize=-1, resumable=True, mimetype="video/*")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            log("Upload progress:", int(status.progress() * 100), "%")
    log("Upload finished, video id:", resp.get("id"))
    return resp.get("id")

# ---------------- Main pipeline ----------------
def main():
    log("Starting pipeline...")
    title, desc, img_url = get_news_article()

    headline = re.sub(r"\s+", " ", title).strip()
    lines = [
        f"Fans react: {headline}",
        "Social media is buzzing.",
        "Rumors are spreading fast.",
        "Nothing confirmed — just chatter.",
        "What do YOU think?"
    ]

    bg_path = fetch_and_prepare_bg(img_url, fallback_query="entertainment")
    tts_paths, durations = create_tts_per_line(lines)

    out_video = WORKDIR / "final_short.mp4"
    video_file = build_final_video(bg_path, lines, tts_paths, durations, out_video)

    yt_title = short_title_from_text(title)
    yt_desc = f"{desc}\n\nSource: NewsAPI. Content is commentary and fan reaction."
    
    upload_unlisted(video_file, yt_title, yt_desc, tags=["shorts","gossip","entertainment"])

    log("Done. Video created and uploaded as PUBLIC:", video_file)

if __name__ == "__main__":
    main()


