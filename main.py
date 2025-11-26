# main.py
import os
import io
import math
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
NEWS_API_KEY = os.getenv("NEWS_API_KEY")           # NewsAPI.org key
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")           # OAuth client id
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")   # OAuth client secret
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")   # OAuth refresh token
MUSIC_URL = os.getenv("MUSIC_URL")                 # URL to a royalty-free mp3 (optional)
NUM_IMAGES = int(os.getenv("NUM_IMAGES") or "5")
IMAGE_FETCH_TRIES = 6

VIDEO_W, VIDEO_H = 1080, 1920
PER_IMAGE_DURATION = 2.0
CROSSFADE_DURATION = 0.5
TEXT_AREA_W = int(VIDEO_W * 0.9)
TEXT_AREA_H = int(VIDEO_H * 0.35)
FONT_PATH = None

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

def log(*args):
    print("[BOT]", *args)

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

def create_gossip_lines(topic):
    lines = [
        "Rumors are spreading fast...",
        f"Fans are talking about: {topic}",
        "People online are shocked.",
        "Unconfirmed reports are making waves.",
        "What do you think about this?"
    ]
    return lines

def download_images(topic, n=NUM_IMAGES):
    log("Downloading images for topic:", topic)
    images = []
    q = topic.replace(" ", "%20")
    for i in range(n):
        tries = 0
        success = False
        while tries < IMAGE_FETCH_TRIES and not success:
            try:
                url = f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/?{q}&sig={random.randint(1,999999)}"
                resp = requests.get(url, timeout=20)
                resp.raise_for_status()
                path = WORKDIR / f"img_{i}.jpg"
                with open(path, "wb") as f:
                    f.write(resp.content)
                images.append(str(path))
                success = True
            except Exception as e:
                tries += 1
                log("Image fetch failed, retry", tries, "err:", e)
                time.sleep(1)
        if not success:
            log("Failed to fetch image, using placeholder color")
            img = Image.new("RGB", (VIDEO_W, VIDEO_H), (30, 30, 30))
            p = WORKDIR / f"img_fallback_{i}.jpg"
            img.save(p, "JPEG")
            images.append(str(p))
    log("Downloaded images:", images)
    return images

def create_tts_audio(lines, filename="tts.mp3", lang="en"):
    combined = " ".join(lines)
    log("Generating TTS audio...")
    tts = gTTS(text=combined, lang=lang, slow=False)
    outpath = WORKDIR / filename
    tts.save(str(outpath))
    log("Saved TTS to", outpath)
    return str(outpath)

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

def render_text_image(text, w=TEXT_AREA_W, h=TEXT_AREA_H, fontsize=56, line_spacing=8):
    try:
        if FONT_PATH and Path(FONT_PATH).exists():
            font = ImageFont.truetype(FONT_PATH, fontsize)
        else:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", fontsize)
    except Exception:
        font = ImageFont.load_default()

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    words = text.split(" ")
    lines = []
    current = ""
    for word in words:
        test = current + (" " if current else "") + word
        tw, th = draw.textsize(test, font=font)
        if tw <= w - 20:
            current = test
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    total_h = sum(draw.textsize(line, font=font)[1] + line_spacing for line in lines)
    y = max((h - total_h) // 2, 0)
    for line in lines:
        tw, th = draw.textsize(line, font=font)
        x = (w - tw) // 2
        outline_range = 2
        for ox in range(-outline_range, outline_range+1):
            for oy in range(-outline_range, outline_range+1):
                draw.text((x+ox, y+oy), line, font=font, fill=(0,0,0,200))
        draw.text((x, y), line, font=font, fill=(255,255,255,255))
        y += th + line_spacing

    return img

def render_typewriter_frames_for_line(line, frames_per_char=1, fontsize=64):
    frames = []
    text = line
    for i in range(1, len(text)+1):
        sub = text[:i]
        img = render_text_image(sub, fontsize=fontsize)
        path = WORKDIR / f"tt_{abs(hash(line))}_{i}.png"
        img.save(path, "PNG")
        frames.append(str(path))
        if len(frames) > 150:
            break
    return frames

def build_video(image_paths, lines, tts_path, music_path=None, outpath="final.mp4"):
    log("Building video...")
    img_clips = []
    for p in image_paths:
        clip = ImageClip(p).set_duration(PER_IMAGE_DURATION).resize((VIDEO_W, VIDEO_H))
        img_clips.append(clip)

    for i in range(1, len(img_clips)):
        img_clips[i] = img_clips[i].crossfadein(CROSSFADE_DURATION)

    base_video = concatenate_videoclips(img_clips, method="compose")

    audio_clips = []
    if tts_path and Path(tts_path).exists():
        tts_audio = AudioFileClip(tts_path)
        audio_clips.append(tts_audio)
    music_audio = None
    if music_path and Path(music_path).exists():
        music_audio = AudioFileClip(music_path).fx(afx.audio_loop, duration=base_video.duration)

    if music_audio and audio_clips:
        music_audio = music_audio.volumex(0.15)
        composite = CompositeAudioClip([music_audio, audio_clips[0]])
    elif music_audio:
        composite = music_audio.volumex(0.6)
    elif audio_clips:
        composite = audio_clips[0]
    else:
        composite = None

    if composite:
        base_video = base_video.set_audio(composite.set_duration(base_video.duration))

    total_line_time = base_video.duration
    line_time = max(1.5, total_line_time / max(1, len(lines)))
    overlays = []
    start = 0.5
    for line in lines:
        frames = render_typewriter_frames_for_line(line, fontsize=56)
        frame_clips = []
        per_frame_duration = max(0.03, (line_time - 0.2) / max(1, len(frames)))
        for fp in frames:
            c = ImageClip(fp).set_duration(per_frame_duration).set_position(("center", int(VIDEO_H*0.15)))
            frame_clips.append(c)
        if not frame_clips:
            continue
        line_anim = concatenate_videoclips(frame_clips, method="compose")
        line_anim = line_anim.set_start(start)
        overlays.append(line_anim)
        final_img = render_text_image(line, fontsize=56)
        final_path = WORKDIR / f"final_line_{abs(hash(line))}.png"
        final_img.save(final_path)
        overlays.append(ImageClip(str(final_path)).set_duration(0.6).set_start(start + line_anim.duration).set_position(("center", int(VIDEO_H*0.15))))
        start += line_time

    all_clips = [base_video] + overlays
    final = CompositeVideoClip(all_clips, size=(VIDEO_W, VIDEO_H))
    log("Writing final video file (this can take a minute)...")
    final.write_videofile(outpath, fps=24, codec="libx264", audio_codec="aac", threads=2, preset="medium")
    log("Saved video to", outpath)
    return outpath

def get_youtube_service():
    log("Preparing YouTube credentials via refresh token...")
    if not (YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise RuntimeError("YouTube OAuth secrets not set. Please set YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN.")
    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]
    )
    req = Request()
    creds.refresh(req)
    log("Refreshed YouTube access token.")
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    return youtube

def upload_video_to_youtube(video_file, title, description, tags):
    yt = get_youtube_service()
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }
    log("Uploading video to YouTube:", title)
    media = MediaFileUpload(video_file, chunksize=-1, resumable=True, mimetype="video/*")
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log(f"Upload progress: {int(status.progress() * 100)}%")
    log("Upload complete. Video ID:", response.get("id"))
    return response.get("id")

def main():
    try:
        topic = get_trending_topic()
        lines = create_gossip_lines(topic)
        images = download_images(topic, n=NUM_IMAGES)
        tts_path = create_tts_audio(lines)
        music_path = download_music(MUSIC_URL) if MUSIC_URL else None

        video_path = WORKDIR / "final.mp4"
        video_file = build_video(images, lines, tts_path, music_path, outpath=str(video_path))

        title = f"{topic} — Fans React 😱 #shorts"
        description = (
            f"This short covers online rumors about: {topic}\n\n"
            "This video presents fan speculation and public discussion. Not confirmed information.\n\n"
            "#gossip #shorts #celebrity"
        )
        tags = ["gossip", "celebrity", "shorts", topic.split(" ")[0]]

        vid_id = upload_video_to_youtube(str(video_file), title, description, tags)
        log("Done! Video published with id:", vid_id)
    except Exception as e:
        log("ERROR:", e)
        raise

if __name__ == "__main__":
    main()
