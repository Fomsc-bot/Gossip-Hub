import os
import re
import time
import random
import requests
from pathlib import Path
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip, concatenate_audioclips
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
WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

VIDEO_W, VIDEO_H = 1080, 1920
TEXT_AREA_H = int(VIDEO_H * 0.2)  # Caption area height
FONT_PATH = None  # optional: "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

def log(*args):
    print("[BOT]", *args)

# ---------------- NEWS ----------------
def get_news():
    log("Fetching news...")
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    r = requests.get(url, timeout=10)
    data = r.json()
    
    if data.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {data.get('message', 'unknown error')}")
    
    articles = data.get("articles")
    if not articles:
        raise RuntimeError("No articles returned from NewsAPI")
    
    article = articles[0]
    title = article.get("title", "No title")
    description = article.get("description", "")
    image_url = article.get("urlToImage")
    log(f"Fetched article title: {title}")
    return title, description, image_url

# ---------------- IMAGE ----------------
def download_image(url, fallback_query="entertainment"):
    if url:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                path = WORKDIR / "bg.jpg"
                with open(path, "wb") as f:
                    f.write(r.content)
                return str(path)
        except Exception:
            pass
    # fallback Unsplash
    log("Using fallback Unsplash image")
    url = f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/?{fallback_query}&sig={random.randint(1,9999)}"
    r = requests.get(url, timeout=10)
    path = WORKDIR / "bg_fallback.jpg"
    with open(path, "wb") as f:
        f.write(r.content)
    return str(path)

# ---------------- TTS ----------------
def create_tts(lines):
    audio_files = []
    durations = []
    for idx, line in enumerate(lines):
        path = WORKDIR / f"tts_{idx}.mp3"
        tts = gTTS(text=line, lang="en")
        tts.save(str(path))
        a = AudioFileClip(str(path))
        audio_files.append(str(path))
        durations.append(a.duration)
        a.close()
    return audio_files, durations

# ---------------- CAPTION ----------------
def render_caption_image(text, h=TEXT_AREA_H, fontsize=56):
    try:
        font = ImageFont.truetype(FONT_PATH or "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", fontsize)
    except Exception:
        font = ImageFont.load_default()
    
    img = Image.new("RGBA", (VIDEO_W, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
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
        draw.text((x, y), line, font=font, fill=(255,255,255,255))
        y += th + 6
    
    out = WORKDIR / f"caption_{abs(hash(text))}.png"
    img.save(out, "PNG")
    return str(out)

# ---------------- BUILD VIDEO ----------------
def build_video(bg_image, lines, audio_files, durations):
    total_duration = sum(durations)
    log(f"Total video duration: {total_duration:.2f}s")

    # Combine TTS audios
    combined_audio = concatenate_audioclips([AudioFileClip(aud) for aud in audio_files])

    # Background clip
    bg_clip = ImageClip(bg_image).set_duration(total_duration).resize((VIDEO_W, VIDEO_H))

    # Caption clips
    caption_clips = []
    cumulative = 0
    for line, dur in zip(lines, durations):
        caption_path = render_caption_image(line)
        caption_clip = ImageClip(caption_path).set_duration(dur).set_start(cumulative).set_position(("center", int(VIDEO_H*0.76)))
        caption_clips.append(caption_clip)
        cumulative += dur

    # Composite final video
    final = CompositeVideoClip([bg_clip] + caption_clips, size=(VIDEO_W, VIDEO_H))
    final = final.set_audio(combined_audio)

    out_path = WORKDIR / "final.mp4"
    log("Rendering video... this may take a few minutes at full HD")
    final.write_videofile(str(out_path), fps=24, codec="libx264", audio_codec="aac", threads=4, preset="ultrafast")

    combined_audio.close()
    return str(out_path)

# ---------------- YOUTUBE UPLOAD ----------------
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
    return build("youtube", "v3", credentials=creds)

def upload_video(video_path, title):
    yt = get_youtube_service()
    body = {
        "snippet": {
            "title": re.sub(r"[^\x00-\x7F]+", "", title)[:90],
            "description": "Trending entertainment short.\n#celebrity #shorts",
            "tags": ["shorts","celebrity","gossip"]
        },
        "status": {
            "privacyStatus": "unlisted"
        }
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
    log("Uploaded video ID:", resp["id"])
    return resp["id"]

# ---------------- MAIN ----------------
def main():
    log("Starting pipeline...")
    title, desc, img_url = get_news()
    bg_image = download_image(img_url, fallback_query="entertainment")
    lines = [
        f"Fans are reacting: {title}",
        "Social media is buzzing now.",
        "Rumors are spreading fast.",
        "Nothing confirmed yet.",
        "What do YOU think?"
    ]
    audio_files, durations = create_tts(lines)
    video_path = build_video(bg_image, lines, audio_files, durations)
    upload_video(video_path, title)
    log("Pipeline finished successfully!")

if __name__ == "__main__":
    main()
