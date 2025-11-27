import os
import re
import time
import random
import requests
from pathlib import Path
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip, CompositeAudioClip, vfx
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ---------------- CONFIG ----------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")  # use GitHub Secret
WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

VIDEO_W, VIDEO_H = 1080, 1920
TEXT_AREA_H = int(VIDEO_H * 0.20)
NUM_IMAGES = 5

def log(*args):
    print("[BOT]", *args)

# ---------------- NEWS ----------------
def get_news():
    log("Fetching news...")
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    r = requests.get(url)
    data = r.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {data.get('message')}")
    articles = data.get("articles")
    if not articles:
        raise RuntimeError("No articles returned from NewsAPI.")
    article = articles[0]
    title = article.get("title", "No title")
    desc = article.get("description", "")
    image_url = article.get("urlToImage")
    return title, desc, image_url

# ---------------- IMAGE ----------------
def download_image(url, out_path):
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            with open(out_path, "wb") as f:
                f.write(r.content)
            return True
    except:
        return False
    return False

def get_images_from_title(title, main_image_url=None):
    keywords = title.split()[:3]
    images = []

    # use main article image first
    if main_image_url:
        pth = WORKDIR / f"img_0.jpg"
        if download_image(main_image_url, pth):
            images.append(str(pth))

    # fetch from Pexels
    headers = {"Authorization": PEXELS_API_KEY}
    for kw in keywords:
        url = f"https://api.pexels.com/v1/search?query={kw}&per_page=3"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                for p in data.get("photos", []):
                    img_url = p["src"].get("portrait") or p["src"].get("large")
                    if img_url and len(images) < NUM_IMAGES:
                        pth = WORKDIR / f"img_{len(images)}.jpg"
                        if download_image(img_url, pth):
                            images.append(str(pth))
        except Exception as e:
            log("Pexels fetch failed:", e)
        if len(images) >= NUM_IMAGES:
            break

    # fallback Unsplash
    while len(images) < NUM_IMAGES:
        kw = random.choice(keywords)
        url = f"https://source.unsplash.com/1080x1920/?{kw}&sig={random.randint(1,999999)}"
        pth = WORKDIR / f"img_{len(images)}.jpg"
        if download_image(url, pth):
            images.append(str(pth))

    log("Images ready:", images)
    return images[:NUM_IMAGES]

# ---------------- TTS ----------------
def create_tts(lines):
    audio_files = []
    durations = []
    for idx, line in enumerate(lines):
        safe = re.sub(r"\s+", " ", line).strip()
        path = WORKDIR / f"tts_{idx}.mp3"
        tts = gTTS(text=safe, lang="en")
        tts.save(str(path))
        a = AudioFileClip(str(path))
        durations.append(a.duration)
        audio_files.append(str(path))
        a.close()
    return audio_files, durations

# ---------------- CAPTIONS ----------------
def render_caption_image(text):
    h = TEXT_AREA_H
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 56)
    except:
        font = ImageFont.load_default()
    img = Image.new("RGBA", (VIDEO_W, h), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0,0,VIDEO_W,h], fill=(0,0,0,160))
    margin = 36
    max_w = VIDEO_W - 2*margin
    words = text.split(" ")
    lines = []
    cur = ""
    for w in words:
        test = cur + (" " if cur else "") + w
        tw, th = draw.textsize(test, font=font)
        if tw <= max_w:
            cur = test
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    total_h = sum(draw.textsize(l, font=font)[1] for l in lines) + (len(lines)-1)*6
    y = (h - total_h)//2
    for line in lines:
        tw, th = draw.textsize(line, font=font)
        x = (VIDEO_W - tw)//2
        draw.text((x, y), line, font=font, fill=(255,255,255,255))
        y += th + 6
    out = WORKDIR / f"caption_{abs(hash(text))}.png"
    img.save(out, "PNG")
    return str(out)

# ---------------- VIDEO BUILD ----------------
def build_video(images, lines, audios, durations, outpath="final.mp4"):
    clips = []
    cumulative = 0.0
    zoom_rate = 0.015
    for i, (img_path, line, dur, aud_path) in enumerate(zip(images, lines, durations, audios)):
        clip = ImageClip(img_path).set_duration(dur).fx(vfx.resize, lambda t:1+zoom_rate*t)
        clip = clip.set_start(cumulative)
        caption_path = render_caption_image(line)
        caption_clip = ImageClip(caption_path).set_duration(dur).set_position(("center", int(VIDEO_H*0.76))).set_start(cumulative)
        clips.append((clip, caption_clip))
        cumulative += dur
    video_clips = [c.set_start(c.start) for (c,_) in clips]
    base_video = CompositeVideoClip(video_clips, size=(VIDEO_W, VIDEO_H)).set_duration(cumulative)
    audio_pieces = [AudioFileClip(a).set_start(sum(durations[:i])) for i,a in enumerate(audios)]
    tts_composite = CompositeAudioClip(audio_pieces).set_duration(cumulative)
    final = CompositeVideoClip([base_video] + [cap for _,cap in clips]).set_audio(tts_composite)
    final.write_videofile(outpath, fps=24, codec="libx264", audio_codec="aac")
    for a in audio_pieces: a.close()
    return outpath

# ---------------- YOUTUBE ----------------
def yt_service():
    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    creds.refresh(Request())
    return build("youtube","v3",credentials=creds)

def upload_video(video_path, title, description):
    yt = yt_service()
    body = {
        "snippet": {"title": title[:90], "description": description, "tags":["shorts","celebrity"]},
        "status": {"privacyStatus":"unlisted"}
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
    title, desc, main_image = get_news()
    log("News title:", title)
    lines = [
        f"Fans are reacting: {title}",
        "Social media is buzzing.",
        "Rumors are spreading fast.",
        "Nothing confirmed yet — just chatter.",
        "What do YOU think?"
    ]
    images = get_images_from_title(title, main_image)
    audios, durations = create_tts(lines)
    video_path = WORKDIR / "final.mp4"
    build_video(images, lines, audios, durations, outpath=str(video_path))
    short_title = " ".join(title.split()[:3]) + " 🔥"
    upload_video(str(video_path), short_title, desc)
    log("All done!")

if __name__ == "__main__":
    main()

