# main.py
import os
import re
import io
import time
import random
import requests
from pathlib import Path
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from moviepy.editor import (
    ImageClip, AudioFileClip, VideoFileClip, concatenate_videoclips,
    CompositeVideoClip, CompositeAudioClip, afx, vfx
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
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")  # <-- add to GitHub Secrets
MUSIC_URL = os.getenv("MUSIC_URL")   # optional direct mp3 URL
NUM_IMAGES = int(os.getenv("NUM_IMAGES") or "5")
IMAGE_FETCH_TRIES = 6

VIDEO_W, VIDEO_H = 1080, 1920
TEXT_AREA_H = int(VIDEO_H * 0.20)   # smaller caption band (bottom)
FONT_PATH = None  # optionally add a TTF font to repo and set path
WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

def log(*args):
    print("[BOT]", *args)

# ---------- Utilities ----------
def sanitize_title(title: str) -> str:
    t = re.sub(r"[^\x00-\x7F]+", "", title)   # drop non-ascii/emojis
    t = re.sub(r"\s+", " ", t).strip()
    return t[:100]

def clean_query(q: str) -> str:
    q = re.sub(r"[^\w\s]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q[:80]

def extract_keywords(topic: str, max_words=6):
    tokens = re.findall(r"[A-Z][a-z]{2,}", topic)
    if tokens:
        kws = tokens[:max_words]
    else:
        kws = topic.split()[:max_words]
    first_words = " ".join(topic.split()[:3])
    kws.append(first_words)
    return list(dict.fromkeys([clean_query(k) for k in kws if k]))

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

# ---------- 2) Lines ----------
def create_gossip_lines(topic):
    lines = [
        "Rumors are spreading fast...",
        f"Fans are talking about: {topic}",
        "People online are shocked.",
        "Unconfirmed reports are making waves.",
        "What do you think about this?"
    ]
    return lines

# ---------- Image helpers: Pexels primary, Unsplash fallback ----------
def save_bytes_to_path(bts: bytes, out_path: Path) -> bool:
    try:
        # validate image
        from io import BytesIO
        img = Image.open(BytesIO(bts))
        img.verify()
        with open(out_path, "wb") as f:
            f.write(bts)
        return True
    except Exception:
        return False

def fetch_from_pexels_photo(query: str, per_page=8):
    if not PEXELS_API_KEY:
        return []
    headers = {"Authorization": PEXELS_API_KEY}
    url = "https://api.pexels.com/v1/search"
    try:
        resp = requests.get(url, params={"query": query, "per_page": per_page}, headers=headers, timeout=15)
        if resp.status_code != 200:
            log("Pexels photo request failed:", resp.status_code)
            return []
        data = resp.json()
        photos = data.get("photos", []) or []
        results = []
        for p in photos:
            src = p.get("src", {})
            # prefer 'portrait' then 'large'
            img_url = src.get("portrait") or src.get("large2x") or src.get("large") or src.get("medium")
            if img_url:
                results.append(img_url)
        return results
    except Exception as e:
        log("Pexels photo error:", e)
        return []

def fetch_from_pexels_video_frame(query: str, per_page=5):
    if not PEXELS_API_KEY:
        return []
    headers = {"Authorization": PEXELS_API_KEY}
    url = "https://api.pexels.com/videos/search"
    try:
        resp = requests.get(url, params={"query": query, "per_page": per_page}, headers=headers, timeout=15)
        if resp.status_code != 200:
            log("Pexels video request failed:", resp.status_code)
            return []
        data = resp.json()
        vids = data.get("videos", []) or []
        frames = []
        for v in vids:
            files = v.get("video_files", []) or []
            # prefer mp4 with higher height (vertical-ish) or highest quality
            chosen = None
            for vf in sorted(files, key=lambda x: (x.get("height", 0), x.get("width", 0)), reverse=True):
                # take first mp4
                if vf.get("file_type", "").lower() == "video/mp4":
                    chosen = vf.get("link")
                    break
            if chosen:
                frames.append(chosen)
        return frames
    except Exception as e:
        log("Pexels video error:", e)
        return []

def fetch_from_unsplash(query: str, count=6):
    results = []
    for _ in range(count):
        try:
            url = f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/?{query}&sig={random.randint(1,999999)}"
            results.append(url)
        except Exception:
            pass
    return results

def try_download_image_url(url: str, out_path: Path) -> bool:
    try:
        r = requests.get(url, timeout=20, stream=True)
        if r.status_code != 200:
            return False
        content = r.content
        return save_bytes_to_path(content, out_path)
    except Exception as e:
        return False

# ---------- 3) Download images with Pexels first, Unsplash fallback ----------
def download_images(topic, n=NUM_IMAGES):
    log("Downloading images for topic (Pexels -> Unsplash) :", topic)
    images = []
    keywords = extract_keywords(topic)
    if not keywords:
        keywords = [clean_query(topic)]
    log("Image keywords:", keywords)
    # try Pexels photos first
    for i in range(n):
        saved = False
        # try through keywords list to find images
        for kw in keywords:
            # query pexels
            pex_photos = fetch_from_pexels_photo(kw, per_page=10)
            for p_url in pex_photos:
                out_path = WORKDIR / f"img_{i}.jpg"
                if try_download_image_url(p_url, out_path):
                    log(f"Pexels photo saved for '{kw}' -> {out_path}")
                    images.append(str(out_path))
                    saved = True
                    break
            if saved:
                break
            # if no photo, try pexels video frames (grab frame)
            pex_videos = fetch_from_pexels_video_frame(kw, per_page=5)
            for v_url in pex_videos:
                try:
                    out_vid = WORKDIR / f"tmp_vid_{i}.mp4"
                    r = requests.get(v_url, stream=True, timeout=30)
                    if r.status_code == 200:
                        with open(out_vid, "wb") as fh:
                            for chunk in r.iter_content(4096):
                                if chunk:
                                    fh.write(chunk)
                        # load video and save a frame
                        try:
                            vc = VideoFileClip(str(out_vid))
                            frame = vc.get_frame(min(1, vc.duration/2))  # middle frame
                            frame_img = Image.fromarray(frame)
                            frame_out = WORKDIR / f"img_{i}.jpg"
                            frame_img = frame_img.resize((VIDEO_W, VIDEO_H), Image.LANCZOS)
                            frame_img.save(frame_out, "JPEG", quality=85)
                            vc.close()
                            out_vid.unlink(missing_ok=True)
                            log(f"Pexels video frame saved for '{kw}' -> {frame_out}")
                            images.append(str(frame_out))
                            saved = True
                            break
                        except Exception as e:
                            log("Failed to extract frame from pexels video:", e)
                            try:
                                vc.close()
                            except Exception:
                                pass
                            out_vid.unlink(missing_ok=True)
                except Exception:
                    pass
            if saved:
                break

        # If not saved via Pexels, try Unsplash source
        if not saved:
            for kw in keywords:
                uns_urls = fetch_from_unsplash(kw, count=3)
                for u in uns_urls:
                    out_path = WORKDIR / f"img_{i}.jpg"
                    if try_download_image_url(u, out_path):
                        log(f"Unsplash image saved for '{kw}' -> {out_path}")
                        images.append(str(out_path))
                        saved = True
                        break
                if saved:
                    break

        # final fallback to placeholder
        if not saved:
            log("Using fallback placeholder image for slot", i)
            img = Image.new("RGB", (VIDEO_W, VIDEO_H), (18, 18, 18))
            p = WORKDIR / f"img_fallback_{i}.jpg"
            img.save(p, "JPEG")
            images.append(str(p))
    log("Final images used:", images)
    return images

# ---------- 4) Per-line TTS ----------
def create_tts_per_line(lines, lang="en"):
    tts_paths = []
    durations = []
    for idx, line in enumerate(lines):
        safe = re.sub(r"\s+", " ", line).strip()
        fname = WORKDIR / f"tts_line_{idx}.mp3"
        log("Creating TTS for line", idx, "text:", safe)
        tts = gTTS(text=safe, lang=lang, slow=False)
        tts.save(str(fname))
        a = AudioFileClip(str(fname))
        dur = a.duration
        a.close()
        log("TTS saved:", fname, "duration:", dur)
        tts_paths.append(str(fname))
        durations.append(float(dur))
    return tts_paths, durations

# ---------- 5) Caption rendering (bottom voice-synced only) ----------
def render_caption_image(text, h=TEXT_AREA_H, fontsize=56):
    try:
        if FONT_PATH and Path(FONT_PATH).exists():
            font = ImageFont.truetype(FONT_PATH, fontsize)
        else:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", fontsize)
    except Exception:
        font = ImageFont.load_default()

    img = Image.new("RGBA", (VIDEO_W, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Draw a semi-transparent rounded rectangle-like band (simple rectangle)
    draw.rectangle([int(VIDEO_W*0.03), 0, int(VIDEO_W*0.97), h], fill=(0,0,0,180))

    margin = 36
    max_w = int(VIDEO_W*0.94) - 2*margin
    words = text.split(" ")
    lines = []
    cur = ""
    for word in words:
        test = cur + (" " if cur else "") + word
        tw, th = draw.textsize(test, font=font)
        if tw <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)

    total_h = sum(draw.textsize(line, font=font)[1] for line in lines) + (len(lines)-1)*6
    y = (h - total_h)//2
    for line in lines:
        tw, th = draw.textsize(line, font=font)
        x = (VIDEO_W - tw)//2
        for ox in (-1,0,1):
            for oy in (-1,0,1):
                draw.text((x+ox, y+oy), line, font=font, fill=(0,0,0,200))
        draw.text((x, y), line, font=font, fill=(255,255,255,255))
        y += th + 6

    out = WORKDIR / f"caption_{abs(hash(text))}.png"
    img.save(out, "PNG")
    return str(out)

# ---------- 6) Build video: per-line images + captions bottom + Ken Burns ----------
def build_video_per_line(image_paths, lines, tts_paths, tts_durations, music_path=None, outpath="final.mp4"):
    log("Building professional short (per-line sync with Pexels/Unsplash images)...")
    clips = []
    cumulative_time = 0.0
    zoom_rate = 0.02  # slightly stronger zoom for pro look

    for i, (line, tts_dur) in enumerate(zip(lines, tts_durations)):
        img_path = image_paths[i % len(image_paths)]
        # Create ImageClip with slow zoom (Ken Burns)
        clip = ImageClip(img_path).set_duration(tts_dur).resize(width=VIDEO_W).set_position(("center", "center"))
        # apply gentle zoom using fx
        clip = clip.fx(vfx.resize, lambda t: 1 + zoom_rate * t)
        clip = clip.set_start(cumulative_time)
        # caption at bottom (voice-synced)
        caption_path = render_caption_image(line)
        caption_clip = ImageClip(caption_path).set_duration(tts_dur).set_start(cumulative_time).set_position(("center", int(VIDEO_H*0.76)))
        clips.append((clip, caption_clip))
        cumulative_time += tts_dur

    # Compose background images (clips may overlap but we used set_start)
    image_clips = [c.set_start(c.start) for (c, _) in clips]
    base_video = CompositeVideoClip(image_clips, size=(VIDEO_W, VIDEO_H)).set_duration(cumulative_time)

    # Create TTS composite audio timed per-line
    audio_pieces = []
    for i, tts_file in enumerate(tts_paths):
        start_t = sum(tts_durations[:i])
        a = AudioFileClip(tts_file).set_start(start_t)
        audio_pieces.append(a)
    tts_composite = CompositeAudioClip(audio_pieces).set_duration(cumulative_time) if audio_pieces else None

    # Music (optional)
    music_audio = None
    if music_path and Path(music_path).exists():
        try:
            music_audio = AudioFileClip(music_path).fx(afx.audio_loop, duration=cumulative_time).volumex(0.12)
        except Exception as e:
            log("Music load failed:", e)
            music_audio = None

    if music_audio and tts_composite:
        final_audio = CompositeAudioClip([music_audio, tts_composite]).set_duration(cumulative_time)
    elif tts_composite:
        final_audio = tts_composite
    elif music_audio:
        final_audio = music_audio
    else:
        final_audio = None

    if final_audio:
        base_video = base_video.set_audio(final_audio)

    # Add caption clips on top
    caption_clips = [cap.set_start(cap.start) for (_, cap) in clips]
    final = CompositeVideoClip([base_video] + caption_clips, size=(VIDEO_W, VIDEO_H)).set_duration(cumulative_time)

    log("Final duration (s):", final.duration)
    # Write file
    final.write_videofile(outpath, fps=24, codec="libx264", audio_codec="aac", threads=2, preset="fast")
    # cleanup audio objects
    for a in audio_pieces:
        try:
            a.close()
        except Exception:
            pass
    if music_audio:
        music_audio.close()
    log("Saved video to", outpath)
    return outpath

# ---------- 7) YouTube helpers ----------
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
        tts_paths, durations = create_tts_per_line(lines)

        music_path = None
        if MUSIC_URL:
            try:
                download_to = WORKDIR / "bgmusic.mp3"
                r = requests.get(MUSIC_URL, stream=True, timeout=30)
                r.raise_for_status()
                with open(download_to, "wb") as fh:
                    for chunk in r.iter_content(4096):
                        if chunk:
                            fh.write(chunk)
                music_path = str(download_to)
                log("Downloaded music to", music_path)
            except Exception as e:
                log("Failed to download MUSIC_URL:", e)
                music_path = None

        video_path = WORKDIR / "final.mp4"
        video_file = build_video_per_line(images, lines, tts_paths, durations, music_path=music_path, outpath=str(video_path))

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
