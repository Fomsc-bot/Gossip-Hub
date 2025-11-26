# main.py
import os
import re
import time
import random
import requests
from pathlib import Path
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
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")
MUSIC_URL = os.getenv("MUSIC_URL")   # optional direct mp3 URL
NUM_IMAGES = int(os.getenv("NUM_IMAGES") or "5")
IMAGE_FETCH_TRIES = 6

VIDEO_W, VIDEO_H = 1080, 1920
TEXT_AREA_H = int(VIDEO_H * 0.28)
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
    # pick capitalized tokens (names) and first few words as fallback
    tokens = re.findall(r"[A-Z][a-z]{2,}", topic)
    if tokens:
        # add joined multi-word combos too
        kws = tokens[:max_words]
    else:
        kws = topic.split()[:max_words]
    # also include first 3 words as a backup query
    first_words = " ".join(topic.split()[:3])
    kws.append(first_words)
    # ensure unique & cleaned
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

# ---------- 3) Image download & validation ----------
def is_valid_image_bytes(bts: bytes) -> bool:
    try:
        im = Image.open(Path(io_bytes := Path(WORKDIR / "tmp_check.jpg")))
        # not used; we instead use PIL open directly from bytes below
    except Exception:
        pass
    # We'll validate using PIL directly in memory
    from io import BytesIO
    try:
        img = Image.open(BytesIO(bts))
        img.verify()  # will raise if invalid
        return True
    except Exception:
        return False

def try_download_image(url: str, save_path: Path) -> bool:
    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return False
        content = r.content
        # validate content is an image
        from io import BytesIO
        try:
            img = Image.open(BytesIO(content))
            img.verify()
        except UnidentifiedImageError:
            return False
        except Exception:
            return False
        # save
        with open(save_path, "wb") as f:
            f.write(content)
        return True
    except Exception as e:
        return False

def download_images(topic, n=NUM_IMAGES):
    log("Downloading images for topic:", topic)
    images = []
    keywords = extract_keywords(topic)
    log("Image keywords:", keywords)
    # For each image required, try keywords in rotation to get variety
    for i in range(n):
        saved = False
        random.shuffle(keywords)
        for kw in keywords:
            tries = 0
            while tries < IMAGE_FETCH_TRIES and not saved:
                try:
                    url = f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/?{kw}&sig={random.randint(1,999999)}"
                    path = WORKDIR / f"img_{i}.jpg"
                    ok = try_download_image(url, path)
                    if ok:
                        log(f"Image saved for keyword '{kw}' -> {path}")
                        images.append(str(path))
                        saved = True
                        break
                    else:
                        tries += 1
                        log("Image not valid or failed, try:", tries, "keyword:", kw)
                        time.sleep(0.5)
                except Exception as e:
                    tries += 1
                    log("Image download error:", e)
                    time.sleep(0.5)
            if saved:
                break
        # fallback to placeholder if none saved
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
        # measure duration
        a = AudioFileClip(str(fname))
        dur = a.duration
        a.close()
        log("TTS saved:", fname, "duration:", dur)
        tts_paths.append(str(fname))
        durations.append(float(dur))
    return tts_paths, durations

# ---------- 5) Caption rendering ----------
def render_caption_image(text, h=TEXT_AREA_H, fontsize=56):
    # Create caption image sized VIDEO_W x h with semi-transparent panel and centered lines
    try:
        if FONT_PATH and Path(FONT_PATH).exists():
            font = ImageFont.truetype(FONT_PATH, fontsize)
        else:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", fontsize)
    except Exception:
        font = ImageFont.load_default()

    img = Image.new("RGBA", (VIDEO_W, h), (0,0,0,0))
    draw = ImageDraw.Draw(img)

    # draw semi-transparent rounded rectangle or rectangle
    draw.rectangle([0, 0, VIDEO_W, h], fill=(0,0,0,160))

    # wrap text
    margin = 60
    max_w = VIDEO_W - 2*margin
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
        # stroke for readability
        for ox in (-1,0,1):
            for oy in (-1,0,1):
                draw.text((x+ox, y+oy), line, font=font, fill=(0,0,0,200))
        draw.text((x, y), line, font=font, fill=(255,255,255,255))
        y += th + 6

    out = WORKDIR / f"caption_{abs(hash(text))}.png"
    img.save(out, "PNG")
    return str(out)

# ---------- 6) Build video with per-line sync ----------
def build_video_per_line(image_paths, lines, tts_paths, tts_durations, music_path=None, outpath="final.mp4"):
    log("Building professional short (per-line sync)...")
    # For each line, select an image (rotate or reuse) and create an ImageClip with duration = line duration
    clips = []
    cumulative_time = 0.0
    zoom_rate = 0.015  # slow zoom per second
    for i, (line, tts_dur) in enumerate(zip(lines, tts_durations)):
        img_path = image_paths[i % len(image_paths)]
        # create clip with small Ken-Burns zoom (scale increases slowly)
        def make_resize_factor(t, base=1.0, rate=zoom_rate):
            return base + rate * t
        clip = ImageClip(img_path).set_duration(tts_dur).resize(lambda t: 1 + zoom_rate * t).set_position(("center","center"))
        # optional crossfade - will be applied during concatenate
        # caption overlay for this line
        caption_path = render_caption_image(line)
        caption_clip = ImageClip(caption_path).set_duration(tts_dur).set_position(("center", int(VIDEO_H*0.62))).set_start(cumulative_time)
        # set start time later when concatenated; instead we will build sequence and then composite with captions using start offsets
        clip = clip.set_start(cumulative_time)
        clips.append((clip, caption_clip))
        cumulative_time += tts_dur

    # Now build video timeline: concatenate image clips (they already have start times)
    # MoviePy prefers clips without overlapping for concatenate_videoclips, so instead we'll create a CompositeVideoClip with the clips each at their start
    video_clips = [c.set_start(c.start) for (c, _) in clips]
    base_video = CompositeVideoClip(video_clips, size=(VIDEO_W, VIDEO_H)).set_duration(cumulative_time)

    # audio: place each tts at corresponding start time
    audio_pieces = []
    for i, tts_file in enumerate(tts_paths):
        a = AudioFileClip(tts_file).set_start(sum(tts_durations[:i]))
        audio_pieces.append(a)
    tts_composite = CompositeAudioClip(audio_pieces).set_duration(cumulative_time) if audio_pieces else None

    # music (looped and ducked)
    music_audio = None
    if music_path and Path(music_path).exists():
        music_audio = AudioFileClip(music_path).fx(afx.audio_loop, duration=cumulative_time).volumex(0.12)

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

    # Now add caption clips (they include start times)
    caption_clips = [cap.set_start(cap.start) for (_, cap) in clips]
    final = CompositeVideoClip([base_video] + caption_clips, size=(VIDEO_W, VIDEO_H)).set_duration(cumulative_time)

    # small crossfade between segments for smoother transitions:
    # Instead of complex overlapping, we keep simple crossfade by writing with codec and small fade settings
    log("Final duration (s):", final.duration)
    log("Writing final video to", outpath)
    final.write_videofile(outpath, fps=24, codec="libx264", audio_codec="aac", threads=2, preset="fast")
    # close audio clips to free resources
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
        # per-line TTS + durations
        tts_paths, durations = create_tts_per_line(lines)
        music_path = None
        if MUSIC_URL:
            try:
                music_path = download_to = WORKDIR / "bgmusic.mp3"
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
