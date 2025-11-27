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

# ---------------- CONFIG ----------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")  # optional, better images if present

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

# Full HD vertical short
VIDEO_W, VIDEO_H = 1080, 1920
CAPTION_HEIGHT = int(VIDEO_H * 0.18)   # bottom caption band height
FONT_PATH = None  # optional custom font file inside repo, e.g. "assets/MyFont.ttf"

# visual tuning
ZOOM_RATE = 0.015   # subtle Ken Burns zoom (per second)
FPS = 24

def log(*a):
    print("[BOT]", *a)

# ---------------- Utilities ----------------
def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {}

# Pick up to 3 meaningful words for title
def short_title_from_text(text):
    words = re.findall(r"\w+", re.sub(r"https?:\/\/\S+", "", text))
    # filter out super short words
    words = [w for w in words if len(w) > 2]
    if not words:
        words = re.findall(r"\w+", text)[:3]
    short = " ".join(words[:3])
    # choose emojis that fit "gossip/viral" style
    emoji = "🔥"
    emoji2 = "🎬"
    return f"{short} {emoji}{emoji2}"

# ---------------- News fetch ----------------
def get_news_article():
    if not NEWS_API_KEY:
        raise RuntimeError("NEWS_API_KEY not set.")
    log("Fetching trending entertainment article from NewsAPI...")
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    r = requests.get(url, timeout=15)
    data = safe_json(r)
    if data.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {data.get('message', 'unknown')}")
    articles = data.get("articles") or []
    if not articles:
        raise RuntimeError("No articles returned by NewsAPI.")
    article = articles[0]
    title = article.get("title") or article.get("description") or "Entertainment Buzz"
    description = article.get("description") or ""
    image_url = article.get("urlToImage")
    return title, description, image_url

# ---------------- Image preparation (cover & crop, keep aspect ratio) ----------------
def fetch_and_prepare_bg(image_url, fallback_query="entertainment"):
    # Download original (if provided) else fallback to unsplash
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
        # source.unsplash.com returns a redirected image; fetch final binary
        unsplash_url = f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/?{fallback_query}&sig={random.randint(1,999999)}"
        r = requests.get(unsplash_url, timeout=15)
        if r.status_code == 200:
            raw_img = BytesIO(r.content)
        else:
            raise RuntimeError("Failed to fetch fallback image from Unsplash")

    # open with PIL
    img = Image.open(raw_img).convert("RGB")
    w, h = img.size
    # scale to cover (no stretching) -> scale = max(target_w/w, target_h/h)
    scale = max(VIDEO_W / w, VIDEO_H / h)
    new_w = int(w * scale + 0.5)
    new_h = int(h * scale + 0.5)
    img_resized = img.resize((new_w, new_h), Image.LANCZOS)
    # center-crop to target
    left = (new_w - VIDEO_W) // 2
    top = (new_h - VIDEO_H) // 2
    right = left + VIDEO_W
    bottom = top + VIDEO_H
    img_cropped = img_resized.crop((left, top, right, bottom))
    out_path = WORKDIR / "bg_prepared.jpg"
    img_cropped.save(out_path, "JPEG", quality=90)
    log("Prepared background saved to", out_path)
    return str(out_path)

# ---------------- TTS per line ----------------
def create_tts_per_line(lines, lang="en"):
    tts_paths = []
    durations = []
    for i, line in enumerate(lines):
        safe_text = re.sub(r"\s+", " ", line).strip()
        out = WORKDIR / f"tts_{i}.mp3"
        log("Generating TTS for line", i, "->", safe_text[:60])
        tts = gTTS(text=safe_text, lang=lang, slow=False)
        tts.save(str(out))
        a = AudioFileClip(str(out))
        dur = a.duration
        a.close()
        # guard (if too short, increase to readability)
        dur = max(dur, max(1.2, len(safe_text.split()) * 0.28))
        tts_paths.append(str(out))
        durations.append(float(dur))
        log("Saved TTS:", out, "duration", dur)
    return tts_paths, durations

# ---------------- Caption rendering (bottom only) ----------------
def render_bottom_caption(text, out_path=None, h=CAPTION_HEIGHT, fontsize=56):
    try:
        font = ImageFont.truetype(FONT_PATH or "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fontsize)
    except Exception:
        font = ImageFont.load_default()

    w = VIDEO_W
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # semi-transparent rounded rectangle band (simple rectangle)
    band_margin = int(w * 0.03)
    draw.rectangle([band_margin, 0, w - band_margin, h], fill=(0, 0, 0, 190))

    # wrap text
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

    # draw centered vertically inside band
    total_h = sum(draw.textsize(l, font=font)[1] for l in lines) + (len(lines)-1) * 6
    y = (h - total_h) // 2
    for line in lines:
        tw, th = draw.textsize(line, font=font)
        x = (w - tw) // 2
        # text stroke for readability
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                draw.text((x + ox, y + oy), line, font=font, fill=(0, 0, 0, 200))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += th + 6

    out = out_path if out_path else (WORKDIR / f"caption_{abs(hash(text))}.png")
    img.save(out, "PNG")
    return str(out)

# ---------------- Build video (single background, bottom captions only) ----------------
def build_final_video(bg_image_path, lines, tts_paths, durations, out_file):
    total_duration = sum(durations)
    log(f"Total video duration: {total_duration:.2f}s — building final clip...")

    # build single background clip and apply gentle zoom (Ken-Burns) via resize lambda
    bg_clip = ImageClip(bg_image_path).set_duration(total_duration)
    # subtle zoom: per-frame resize lambda (keeps aspect ratio since image already cropped to exact size)
    bg_clip = bg_clip.fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)

    # create caption clips (bottom) — voice-synced
    caption_clips = []
    cursor = 0.0
    for i, (line, dur) in enumerate(zip(lines, durations)):
        cap_path = render_bottom_caption(line)
        cap_clip = ImageClip(cap_path).set_duration(dur).set_start(cursor).set_position(("center", int(VIDEO_H * 0.78)))
        caption_clips.append(cap_clip)
        cursor += dur

    # combine TTS audios into a single audio track (concatenate) to avoid composite overhead
    audio_clips = [AudioFileClip(p) for p in tts_paths]
    combined_audio = concatenate_audioclips(audio_clips).set_duration(total_duration)

    # Composite: background + captions
    final = CompositeVideoClip([bg_clip] + caption_clips, size=(VIDEO_W, VIDEO_H)).set_duration(total_duration)
    final = final.set_audio(combined_audio)

    log("Rendering (full HD). This may take several minutes depending on runner CPU...")
    final.write_videofile(str(out_file), fps=FPS, codec="libx264", audio_codec="aac", threads=4, preset="fast")

    # cleanup audio objects
    for a in audio_clips:
        try:
            a.close()
        except Exception:
            pass
    try:
        combined_audio.close()
    except Exception:
        pass

    return str(out_file)

# ---------------- YouTube helpers ----------------
def get_youtube_service():
    if not (YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise RuntimeError("YouTube OAuth credentials missing.")
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
        "status": {
            "privacyStatus": "unlisted",
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
    # Build lines for the short (kept short & punchy)
    lines = [
        f"Fans react: {title}",
        "Social media is buzzing.",
        "Rumors are spreading fast.",
        "Nothing confirmed — just chatter.",
        "What do YOU think?"
    ]

    # Prepare background (cover & crop to avoid stretching)
    bg_path = fetch_and_prepare_bg(img_url, fallback_query="entertainment")

    # TTS per-line and durations
    tts_paths, durations = create_tts_per_line(lines)

    # Build final full HD short
    out_video = WORKDIR / "final_short.mp4"
    video_file = build_final_video(bg_path, lines, tts_paths, durations, out_video)

    # Short title (max 3 words + emojis)
    yt_title = short_title_from_text(title)
    yt_description = f"{desc}\n\nSource: NewsAPI. This channel shares public discussion and fan reactions."
    upload_unlisted(video_file, yt_title, yt_description, tags=["shorts","gossip","entertainment"])

    log("Done. Video created and uploaded as unlisted:", video_file)

if __name__ == "__main__":
    main()
