# main.py
import os
import re
import time
import random
import requests
from pathlib import Path
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import (
    ImageClip, AudioFileClip, concatenate_videoclips,
    CompositeVideoClip, CompositeAudioClip, afx
)

# YouTube upload libs
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ---------------- CONFIG ----------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")
MUSIC_URL = os.getenv("MUSIC_URL")   # direct mp3 link (optional)
NUM_IMAGES = int(os.getenv("NUM_IMAGES") or "5")
IMAGE_FETCH_TRIES = 6

VIDEO_W, VIDEO_H = 1080, 1920
# PER_IMAGE_DURATION will be computed from TTS duration to keep voice in sync
TEXT_AREA_W = int(VIDEO_W * 0.92)
TEXT_AREA_H = int(VIDEO_H * 0.28)
FONT_PATH = None  # optional: add a .ttf to repo and set this to its path

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

def log(*args):
    print("[BOT]", *args)

# ---------- Utilities ----------
def sanitize_title(title: str) -> str:
    # Remove emojis & non-ascii, collapse whitespace, truncate to 100 chars
    t = re.sub(r"[^\x00-\x7F]+", "", title)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:100]

def clean_query(q: str) -> str:
    # remove punctuation and limit length for safe image queries
    q = re.sub(r"[^\w\s]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q[:80]  # limit query length

# ---------- 1) Trending topic ----------
def get_trending_topic():
    log("Fetching trending entertainment topic...")
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("articles"):
        raise RuntimeError("No articles returned from NewsAPI.")
    title = data["articles"][0]["title"]
    log("Topic:", title)
    return title

# ---------- 2) Simple gossip lines ----------
def create_gossip_lines(topic):
    lines = [
        "Rumors are spreading fast...",
        f"Fans are talking about: {topic}",
        "People online are shocked.",
        "Unconfirmed reports are making waves.",
        "What do you think about this?"
    ]
    return lines

# ---------- 3) Images ----------
def download_images(topic, n=NUM_IMAGES):
    log("Downloading images for topic:", topic)
    images = []
    q = clean_query(topic)
    for i in range(n):
        tries = 0
        success = False
        while tries < IMAGE_FETCH_TRIES and not success:
            try:
                # Unsplash Source endpoint: returns random image for query
                url = f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/?{q}&sig={random.randint(1,999999)}"
                resp = requests.get(url, timeout=20)
                resp.raise_for_status()
                path = WORKDIR / f"img_{i}.jpg"
                with open(path, "wb") as f:
                    f.write(resp.content)
                images.append(str(path))
                success = True
                log(f"Saved image {i}")
            except Exception as e:
                tries += 1
                log("Image fetch failed, retry", tries, "err:", e)
                time.sleep(1)
        if not success:
            log("Failed to fetch image, using placeholder color")
            img = Image.new("RGB", (VIDEO_W, VIDEO_H), (18, 18, 18))
            p = WORKDIR / f"img_fallback_{i}.jpg"
            img.save(p, "JPEG")
            images.append(str(p))
    log("Downloaded images:", images)
    return images

# ---------- 4) TTS ----------
def create_tts_audio(lines, filename="tts.mp3", lang="en"):
    combined = " ".join(lines)
    log("Generating TTS audio...")
    tts = gTTS(text=combined, lang=lang, slow=False)
    outpath = WORKDIR / filename
    tts.save(str(outpath))
    log("Saved TTS to", outpath)
    # return path and duration
    audio_clip = AudioFileClip(str(outpath))
    dur = audio_clip.duration
    audio_clip.close()
    log("TTS duration (s):", dur)
    return str(outpath), dur

# ---------- 5) Music ----------
def download_music(url, filename="music.mp3"):
    if not url:
        log("No MUSIC_URL provided; skipping background music.")
        return None
    log("Downloading background music:", url)
    r = requests.get(url, stream=True, timeout=30)
    r.raise_for_status()
    path = WORKDIR / filename
    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=4096):
            if chunk:
                f.write(chunk)
    log("Saved music to", path)
    return str(path)

# ---------- 6) Text overlay rendering (full-width caption panels) ----------
def render_caption_image(text, filename_prefix="caption", w=TEXT_AREA_W, h=TEXT_AREA_H, fontsize=56):
    # Render a full-width image (VIDEO_W x TEXT_AREA_H) with semi-transparent black rectangle + white text
    try:
        if FONT_PATH and Path(FONT_PATH).exists():
            font = ImageFont.truetype(FONT_PATH, fontsize)
        else:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", fontsize)
    except Exception:
        font = ImageFont.load_default()

    img = Image.new("RGBA", (VIDEO_W, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw semi-transparent rounded rectangle as background for text
    rect_h = h
    rect_w = VIDEO_W
    rect_x0, rect_y0 = 0, 0
    rect_x1, rect_y1 = rect_w, rect_h
    # semi-transparent black
    draw.rectangle([rect_x0, rect_y0, rect_x1, rect_y1], fill=(0, 0, 0, 180))

    # Wrap text
    margin = 40
    max_w = VIDEO_W - margin * 2
    words = text.split(" ")
    lines = []
    cur = ""
    for word in words:
        test = cur + (" " if cur else "") + word
        tw, th = draw.textsize(test, font=font)
        if tw <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)

    # Compute vertical start
    total_h = sum(draw.textsize(line, font=font)[1] for line in lines) + (len(lines)-1) * 6
    y = (h - total_h) // 2
    for line in lines:
        tw, th = draw.textsize(line, font=font)
        x = (VIDEO_W - tw) // 2
        # stroke for readability
        for ox in (-1,0,1):
            for oy in (-1,0,1):
                draw.text((x+ox, y+oy), line, font=font, fill=(0,0,0,200))
        draw.text((x, y), line, font=font, fill=(255,255,255,255))
        y += th + 6

    fname = WORKDIR / f"{filename_prefix}_{abs(hash(text))}.png"
    img.save(fname, "PNG")
    return str(fname)

# ---------- 7) Build video (sync durations to TTS) ----------
def build_video(image_paths, lines, tts_path, tts_duration, music_path=None, outpath="final.mp4"):
    log("Building video...")
    # compute per-image duration from tts_duration so voice and images align
    per_image_duration = max(1.6, float(tts_duration) / max(1, len(image_paths)))
    crossfade = min(0.5, per_image_duration * 0.25)
    log(f"Per image duration: {per_image_duration}s, crossfade: {crossfade}s")

    img_clips = []
    for p in image_paths:
        clip = ImageClip(p).set_duration(per_image_duration).resize((VIDEO_W, VIDEO_H))
        img_clips.append(clip)

    for i in range(1, len(img_clips)):
        img_clips[i] = img_clips[i].crossfadein(crossfade)

    base_video = concatenate_videoclips(img_clips, method="compose")
    log("Base video duration (s):", base_video.duration)

    # Audio: TTS + optional music (looped and ducked)
    tts_audio = None
    if tts_path and Path(tts_path).exists():
        tts_audio = AudioFileClip(tts_path)
        log("Loaded TTS audio duration:", tts_audio.duration)

    music_audio = None
    if music_path and Path(music_path).exists():
        music_audio = AudioFileClip(music_path).fx(afx.audio_loop, duration=base_video.duration)
        log("Loaded music audio")

    final_audio = None
    if music_audio and tts_audio:
        # duck music: low volume overall, keep voice at normal
        music_audio = music_audio.volumex(0.12)
        # ensure tts lasts and music loops behind
        final_audio = CompositeAudioClip([music_audio.set_duration(base_video.duration), tts_audio.set_start(0)])
    elif music_audio:
        final_audio = music_audio.volumex(0.6).set_duration(base_video.duration)
    elif tts_audio:
        final_audio = tts_audio.set_duration(base_video.duration)
    else:
        final_audio = None

    if final_audio:
        base_video = base_video.set_audio(final_audio)

    # Overlays: create caption images for each line and show sequentially
    total_line_time = base_video.duration
    line_time = max(1.2, total_line_time / max(1, len(lines)))
    overlays = []
    start = 0.4
    for line in lines:
        caption_path = render_caption_image(line, fontsize=56)
        # show caption near bottom (y ~ 65% of video)
        clip = ImageClip(caption_path).set_duration(line_time).set_start(start).set_position(("center", int(VIDEO_H*0.62))).crossfadein(0.15).crossfadeout(0.15)
        overlays.append(clip)
        start += line_time

    # Composite overlays onto base video
    all_clips = [base_video] + overlays
    final = CompositeVideoClip(all_clips, size=(VIDEO_W, VIDEO_H))
    log("Final composite duration (s):", final.duration)

    # write file
    log("Writing final video file (this can take a minute)...")
    final.write_videofile(outpath, fps=24, codec="libx264", audio_codec="aac", threads=2, preset="medium")
    log("Saved video to", outpath)
    # close audio clips to free resources
    if tts_audio:
        tts_audio.close()
    if music_audio:
        music_audio.close()
    return outpath

# ---------- 8) YouTube helpers ----------
def get_youtube_service():
    log("Preparing YouTube credentials via refresh token...")
    if not (YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise RuntimeError("YouTube OAuth secrets not set.")
    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]
    )
    creds.refresh(Request())
    log("Refreshed YouTube access token.")
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    return youtube

def upload_video_to_youtube(video_file, title, description, tags):
    yt = get_youtube_service()
    safe_title = sanitize_title(title)
    body = {
        "snippet": {"title": safe_title, "description": description, "tags": tags},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    }
    log("Uploading video to YouTube:", safe_title)
    media = MediaFileUpload(video_file, chunksize=-1, resumable=True, mimetype="video/*")
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log(f"Upload progress: {int(status.progress() * 100)}%")
    log("Upload complete. Video ID:", response.get("id"))
    return response.get("id")

# ---------- Main pipeline ----------
def main():
    try:
        topic = get_trending_topic()
        lines = create_gossip_lines(topic)
        images = download_images(topic, n=NUM_IMAGES)
        tts_path, tts_dur = create_tts_audio(lines)
        music_path = download_music(MUSIC_URL) if MUSIC_URL else None

        video_path = WORKDIR / "final.mp4"
        video_file = build_video(images, lines, tts_path, tts_dur, music_path=music_path, outpath=str(video_path))

        title = f"{topic} — Fans React #shorts"
        description = (
            f"This short covers online rumors about: {topic}\n\n"
            "This video presents fan speculation and public discussion. Not confirmed information.\n\n"
            "#gossip #shorts #celebrity"
        )
        tags = ["gossip", "celebrity", "shorts"]

        vid_id = upload_video_to_youtube(str(video_file), title, description, tags)
        log("Done! Video published with id:", vid_id)
    except Exception as e:
        log("ERROR:", e)
        raise

if __name__ == "__main__":
    main()
