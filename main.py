# main.py
import os
import re
import time
import math
import random
import requests
from pathlib import Path
from io import BytesIO

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from moviepy.editor import (
    ImageClip, AudioFileClip, concatenate_videoclips,
    CompositeVideoClip, CompositeAudioClip, afx, vfx
)

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ---------------- CONFIG ----------------
WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

VIDEO_W, VIDEO_H = 1080, 1920
NUM_IMAGES = int(os.getenv("NUM_IMAGES", "5"))
IMAGE_FETCH_TRIES = 5
PER_LINE_MIN = 1.4  # minimum seconds per caption if TTS returns too small
FONT_PATH = None  # add a TTF in repo and set path for better visuals

# Secrets / env
NEWS_API_KEY = os.getenv("NEWS_API_KEY")       # optional for trending topic
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")   # optional (free) — higher quality images
MUSIC_URL = os.getenv("MUSIC_URL")             # optional direct mp3 link
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")

# Visual config
CAPTION_HEIGHT = int(VIDEO_H * 0.22)
CAPTION_FONT_SIZE = 56
CAPTION_Y = int(VIDEO_H * 0.72)  # vertical position of caption band
ZOOM_RATE = 0.018  # Ken Burns zoom per second
CROSSFADE = 0.4

# ---------------- Logging ----------------
def log(*a):
    print("[BOT]", *a)

# ---------------- Helpers: topic + lines ----------------
def get_trending_topic():
    if not NEWS_API_KEY:
        return "Celebrity rumor: big reaction online"
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    articles = data.get("articles") or []
    if not articles:
        return "Celebrity rumor: big reaction online"
    return articles[0].get("title") or articles[0].get("description") or "Entertainment buzz"

def create_lines(topic):
    # create short lines suitable for shorts (one sentence each)
    return [
        f"Fans are reacting to: {topic}",
        "Social media is buzzing.",
        "People are shocked!",
        "Rumors are spreading fast.",
        "What do YOU think?"
    ]

# ---------------- Image fetching (Pexels primary -> Unsplash fallback) ----------------
def fetch_url_to_path(url, path):
    try:
        r = requests.get(url, timeout=20, stream=True)
        r.raise_for_status()
        # validate image bytes via PIL
        content = r.content
        try:
            img = Image.open(BytesIO(content))
            img.verify()
        except Exception:
            return False
        with open(path, "wb") as f:
            f.write(content)
        return True
    except Exception as e:
        return False

def query_pexels(query, per_page=8):
    if not PEXELS_API_KEY:
        return []
    headers = {"Authorization": PEXELS_API_KEY}
    try:
        r = requests.get("https://api.pexels.com/v1/search", params={"query": query, "per_page": per_page}, headers=headers, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        urls = []
        for p in data.get("photos", []):
            src = p.get("src", {})
            # prefer portrait for vertical video
            url = src.get("portrait") or src.get("large2x") or src.get("large")
            if url:
                urls.append(url)
        return urls
    except Exception:
        return []

def unsplash_source(query, size=(VIDEO_W, VIDEO_H)):
    return f"https://source.unsplash.com/{size[0]}x{size[1]}/?{query}&sig={random.randint(1,999999)}"

def download_images(topic, n=NUM_IMAGES):
    keywords = re.sub(r"[^\w\s]", " ", topic).split()
    keywords = keywords[:6] or [topic]
    images = []
    for i in range(n):
        saved = False
        random.shuffle(keywords)
        for kw in keywords:
            # first try Pexels
            pex_urls = query_pexels(kw, per_page=6)
            for u in pex_urls:
                out = WORKDIR / f"img_{i}.jpg"
                if fetch_url_to_path(u, out):
                    images.append(str(out))
                    saved = True
                    break
            if saved:
                break
            # fallback to unsplash source
            uns_url = unsplash_source(kw)
            out = WORKDIR / f"img_{i}.jpg"
            if fetch_url_to_path(uns_url, out):
                images.append(str(out))
                saved = True
                break
        if not saved:
            # placeholder
            out = WORKDIR / f"img_fallback_{i}.jpg"
            Image.new("RGB", (VIDEO_W, VIDEO_H), (24,24,24)).save(out, "JPEG")
            images.append(str(out))
    log("Images ready:", images)
    return images

# ---------------- Captions rendering (PIL) ----------------
def get_font(size):
    try:
        if FONT_PATH and Path(FONT_PATH).exists():
            return ImageFont.truetype(FONT_PATH, size)
        else:
            # DejaVu is commonly available
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()

def render_caption_image(text, out_path):
    font = get_font(CAPTION_FONT_SIZE)
    w, h = VIDEO_W, CAPTION_HEIGHT
    img = Image.new("RGBA", (w, h), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    # draw semi-transparent band
    band_margin = int(VIDEO_W*0.03)
    draw.rectangle([band_margin, 0, w-band_margin, h], fill=(0,0,0,180))
    # wrap text
    max_w = w - 2*(band_margin + 20)
    words = text.split()
    lines = []
    cur = ""
    for word in words:
        test = (cur + " " + word).strip() if cur else word
        tw, th = draw.textsize(test, font=font)
        if tw <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    # draw lines centered
    total_h = sum(draw.textsize(l, font=font)[1] for l in lines) + (len(lines)-1)*8
    y = (h - total_h)//2
    for line in lines:
        tw, th = draw.textsize(line, font=font)
        x = (w - tw)//2
        # stroke
        for ox in (-1,0,1):
            for oy in (-1,0,1):
                draw.text((x+ox, y+oy), line, font=font, fill=(0,0,0,200))
        draw.text((x,y), line, font=font, fill=(255,255,255,255))
        y += th + 8
    img.save(out_path, "PNG")
    return str(out_path)

# ---------------- TTS (per-line) ----------------
def create_tts_per_line(lines, lang="en"):
    paths = []
    durations = []
    for i, line in enumerate(lines):
        safe = line.strip()
        out = WORKDIR / f"tts_{i}.mp3"
        log("gTTS generating:", safe[:60])
        tts = gTTS(text=safe, lang=lang, slow=False)
        tts.save(str(out))
        # use moviepy to measure duration
        a = AudioFileClip(str(out))
        dur = a.duration
        a.close()
        if dur < PER_LINE_MIN:
            dur = max(PER_LINE_MIN, len(safe.split()) * 0.35)
        paths.append(str(out))
        durations.append(float(dur))
        log("Saved TTS:", out, "dur:", dur)
    return paths, durations

# ---------------- Build video with MoviePy ----------------
def build_short(image_paths, lines, tts_paths, durations, music_path=None, outpath="final.mp4"):
    # create clips with ken-burns and caption overlays, timed to each TTS line
    clips = []
    caption_clips = []
    start = 0.0
    for idx, (img_p, dur, line) in enumerate(zip(image_paths, durations, lines)):
        # ImageClip
        clip = ImageClip(img_p).set_duration(dur).resize((VIDEO_W, VIDEO_H))
        # apply slow zoom by scaling frames (MoviePy resize with lambda)
        clip = clip.fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)
        clip = clip.set_start(start)
        clips.append(clip)
        # caption image
        cap_path = WORKDIR / f"cap_{idx}.png"
        render_caption_image(line, cap_path)
        cap_clip = ImageClip(str(cap_path)).set_duration(dur).set_start(start).set_position(("center", CAPTION_Y))
        caption_clips.append(cap_clip)
        start += dur

    # Add small crossfades between clips
    # To apply crossfade, concatenate with crossfade and then use composite
    base = concatenate_videoclips([c.crossfadein(CROSSFADE) if i>0 else c for i,c in enumerate(clips)], method="compose")
    # Audio: combine TTS audios by placing each at its start
    audio_clips = []
    cur = 0.0
    for p, d in zip(tts_paths, durations):
        a = AudioFileClip(p).set_start(cur)
        audio_clips.append(a)
        cur += d
    tts_audio = CompositeAudioClip(audio_clips).set_duration(base.duration) if audio_clips else None

    # Music
    music_audio = None
    if music_path:
        try:
            music_audio = AudioFileClip(music_path).fx(afx.audio_loop, duration=base.duration).volumex(0.12)
        except Exception as e:
            log("Music load failed:", e)
            music_audio = None

    if music_audio and tts_audio:
        # duck music using volumex or sidechain approx: lower music volume
        final_audio = CompositeAudioClip([music_audio.volumex(0.2), tts_audio])
    elif tts_audio:
        final_audio = tts_audio
    elif music_audio:
        final_audio = music_audio
    else:
        final_audio = None

    if final_audio:
        base = base.set_audio(final_audio)

    # Composite captions on top
    all_clips = [base] + caption_clips
    final = CompositeVideoClip(all_clips, size=(VIDEO_W, VIDEO_H)).set_duration(base.duration)

    log("Rendering final video (this may take a minute)...")
    final.write_videofile(outpath, fps=24, codec="libx264", audio_codec="aac", threads=2, preset="medium")
    log("Saved video:", outpath)
    # close audio clips
    for a in audio_clips:
        try:
            a.close()
        except:
            pass
    if music_audio:
        music_audio.close()
    return outpath

# ---------------- YouTube upload helpers (unlisted) ----------------
def get_youtube_service():
    if not (YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise RuntimeError("YouTube credentials missing (YT_CLIENT_ID/SECRET/REFRESH must be set).")
    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload","https://www.googleapis.com/auth/youtube"]
    )
    creds.refresh(Request())
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    return youtube

def upload_to_youtube(video_file, title, description, tags):
    yt = get_youtube_service()
    body = {
        "snippet": {"title": title, "description": description, "tags": tags},
        "status": {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}
    }
    media = MediaFileUpload(video_file, chunksize=-1, resumable=True, mimetype="video/*")
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log("Upload progress:", int(status.progress()*100), "%")
    log("YouTube upload done. id:", response.get("id"))
    return response.get("id")

# ---------------- Main pipeline ----------------
def create_short_and_upload():
    topic = get_trending_topic()
    log("Topic:", topic)
    lines = create_lines(topic)
    images = download_images(topic, n=NUM_IMAGES) if 'download_images' in globals() else download_images(topic)
    # ensure we have as many images as lines; rotate if not
    if len(images) < len(lines):
        images = (images * (math.ceil(len(lines)/len(images))))[:len(lines)]

    tts_paths, durations = create_tts_per_line(lines)
    # music (optional)
    music_path = None
    if MUSIC_URL:
        try:
            r = requests.get(MUSIC_URL, timeout=30, stream=True)
            r.raise_for_status()
            mp = WORKDIR / "bgmusic.mp3"
            with open(mp, "wb") as fh:
                for chunk in r.iter_content(4096):
                    if chunk:
                        fh.write(chunk)
            music_path = str(mp)
        except Exception as e:
            log("Could not download MUSIC_URL:", e)
            music_path = None

    out = WORKDIR / "final_short.mp4"
    built = build_short(images, lines, tts_paths, durations, music_path=music_path, outpath=str(out))

    # Title: 3 words + emoji
    title_words = re.findall(r"\w+", topic)[:3]
    title = (" ".join(title_words) + " 🔥 #shorts")[:100]
    description = f"This Short covers fan reactions and trending commentary about: {topic}\n\n#shorts #gossip"
    tags = ["shorts","gossip","celebrity"]
    vid_id = upload_to_youtube(str(built), title, description, tags)
    log("Published video id:", vid_id)

if __name__ == "__main__":
    create_short_and_upload()

