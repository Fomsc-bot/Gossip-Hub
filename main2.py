# main.py
import os
import re
import time
import random
import requests
import html
from pathlib import Path
from io import BytesIO

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip,
    concatenate_audioclips, vfx
)

# ---------------- CONFIG ----------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")

FB_PAGE_ID = os.getenv("FB_PAGE_ID")  # Your Facebook Page ID
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")  # Page Access Token

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)
LAST_FILE = WORKDIR / "last_titles.txt"

VIDEO_W, VIDEO_H = 1080, 1920
CAPTION_HEIGHT = int(VIDEO_H * 0.16)
ZOOM_RATE = 0.015
FPS = 24
BASE_FONT_SIZE = 56
MAX_CAPTION_CHARS = 220

def log(*a):
    print("[BOT]", *a)

# ---------------- Helper functions ----------------
def sanitize_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r'&[#A-Za-z0-9]+;?', lambda m: html.unescape(m.group(0)), s)
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', "", s)
    s = re.sub(r'\s+([,.\-:;!?])', r'\1', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s.strip(" \t\n\"'")

def has_uploaded(title):
    if not LAST_FILE.exists():
        return False
    with open(LAST_FILE, "r", encoding="utf-8") as f:
        return title.strip().lower() in f.read().lower()

def save_uploaded(title):
    with open(LAST_FILE, "a", encoding="utf-8") as f:
        f.write(title.strip() + "\n")

def safe_json(resp):
    try:
        return resp.json()
    except:
        return {}

# ---------------- News fetch ----------------
def _try_newsapi_fetch():
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=6&apiKey={NEWS_API_KEY}"
    r = requests.get(url, timeout=15)
    data = safe_json(r)
    if data.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {data.get('message') or 'unknown'}")
    return data.get("articles", [])

def _try_gnews_fetch():
    url = f"https://gnews.io/api/v4/top-headlines?topic=entertainment&lang=en&max=6&token={GNEWS_API_KEY}"
    r = requests.get(url, timeout=15)
    data = safe_json(r)
    if not isinstance(data, dict) or "articles" not in data:
        raise RuntimeError(f"GNews error: {data.get('message') if isinstance(data, dict) else 'invalid response'}")
    return data.get("articles", [])

def get_news_article():
    def _normalize_article(art):
        title = sanitize_text(art.get("title") or art.get("headline") or "Entertainment Update")
        description = sanitize_text(art.get("description") or art.get("content") or "")
        image_url = art.get("urlToImage") or art.get("image")
        article_url = art.get("url") or art.get("link")
        return title, description, image_url, article_url

    try:
        arts = _try_newsapi_fetch()
        for art in arts:
            title, description, image_url, article_url = _normalize_article(art)
            if not has_uploaded(title):
                lead = fetch_article_lead(article_url) if article_url else ""
                return title, description or lead, image_url, article_url, lead
    except:
        pass

    try:
        arts = _try_gnews_fetch()
        for art in arts:
            title, description, image_url, article_url = _normalize_article(art)
            if not has_uploaded(title):
                lead = fetch_article_lead(article_url) if article_url else ""
                return title, description or lead, image_url, article_url, lead
    except:
        pass

    raise RuntimeError("Failed to fetch news")

def fetch_article_lead(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=8)
        html_text = r.text
        m = re.search(r'og:description" content="([^"]+)"', html_text)
        if m:
            return sanitize_text(m.group(1).split(".")[0])
        p = re.search(r"<p[^>]*>(.*?)</p>", html_text, re.S)
        if p:
            clean = re.sub(r"<[^>]+>", "", p.group(1))
            return sanitize_text(clean.split(".")[0])
    except:
        return ""
    return ""

# ---------------- Background Image ----------------
def fetch_and_prepare_bg(image_url, fallback_query="entertainment"):
    raw_img = None
    if image_url:
        try:
            r = requests.get(image_url, timeout=15)
            raw_img = BytesIO(r.content)
        except:
            pass
    if not raw_img:
        raw_img = BytesIO(requests.get(f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/?{fallback_query}").content)
    img = Image.open(raw_img).convert("RGB")
    w, h = img.size
    scale = max(VIDEO_W / w, VIDEO_H / h)
    img = img.resize((int(w * scale), int(h * scale)))
    left = (img.size[0] - VIDEO_W) // 2
    top = (img.size[1] - VIDEO_H) // 2
    img = img.crop((left, top, left + VIDEO_W, top + VIDEO_H))
    out = WORKDIR / "bg.jpg"
    img.save(out)
    return str(out)

# ---------------- Script & TTS ----------------
def generate_script(headline, description="", lead=""):
    return [
        headline,
        "Here’s the latest update on this story.",
        description or lead or "Sources confirm new developments.",
        "People are reacting strongly to the news.",
        "Follow for more updates"
    ]

def create_tts_per_line(lines):
    tts_paths, durations = [], []
    for i, line in enumerate(lines):
        out = WORKDIR / f"tts_{i}.mp3"
        gTTS(text=line, lang="en").save(str(out))
        audio = AudioFileClip(str(out))
        durations.append(max(audio.duration, 1))
        audio.close()
        tts_paths.append(str(out))
    return tts_paths, durations

# ---------------- Captions ----------------
def render_bottom_caption(text, index, h=CAPTION_HEIGHT, base_font_size=BASE_FONT_SIZE):
    text = sanitize_text(text)[:MAX_CAPTION_CHARS]
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", base_font_size)
    except:
        font = ImageFont.load_default()
    img = Image.new("RGBA", (VIDEO_W, h), (0,0,0,200))
    draw = ImageDraw.Draw(img)
    tw, th = draw.textsize(text, font)
    x = (VIDEO_W - tw) // 2
    y = (h - th) // 2
    draw.text((x, y), text, font=font, fill="white")
    out = WORKDIR / f"cap_{index}.png"
    img.save(out)
    return str(out)

# ---------------- Build Video ----------------
def build_final_video(bg_path, lines, tts_paths, durations, out_file):
    total = sum(durations) + 2
    bg = ImageClip(bg_path).set_duration(total).fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)
    caps = []
    cursor = 0
    for i, (line, dur) in enumerate(zip(lines, durations)):
        cap_img = render_bottom_caption(line, i)
        cap = ImageClip(cap_img).set_duration(dur).set_start(cursor)
        cap = cap.set_position(("center", VIDEO_H * 0.78))
        caps.append(cap)
        cursor += dur
    audio = concatenate_audioclips([AudioFileClip(p) for p in tts_paths])
    final = CompositeVideoClip([bg]+caps, size=(VIDEO_W, VIDEO_H)).set_audio(audio)
    final.write_videofile(str(out_file), fps=FPS, codec="libx264", audio_codec="aac")
    return str(out_file)

# ---------------- Upload to Facebook ----------------
def upload_to_facebook(video_file, message=""):
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        log("Facebook credentials missing, skipping upload.")
        return None
    url = f"https://graph.facebook.com/v17.0/{FB_PAGE_ID}/videos"
    files = {'file': open(video_file, 'rb')}
    data = {'access_token': FB_PAGE_ACCESS_TOKEN, 'description': message, 'title': message[:100]}
    r = requests.post(url, files=files, data=data)
    try:
        resp = r.json()
    except:
        resp = {}
    if "id" in resp:
        log(f"Video uploaded to Facebook: {resp['id']}")
        return resp['id']
    else:
        log("Facebook upload failed:", resp)
        return None

# ---------------- MAIN ----------------
def main():
    log("Starting pipeline...")
    title, desc, img_url, article_url, lead = get_news_article()
    lines = generate_script(title, desc, lead)
    lines = [sanitize_text(x) for x in lines]
    bg = fetch_and_prepare_bg(img_url)
    tts_paths, durations = create_tts_per_line(lines)
    out_video = WORKDIR / "final.mp4"
    video_path = build_final_video(bg, lines, tts_paths, durations, out_video)
    fb_message = title
    upload_to_facebook(video_path, fb_message)
    save_uploaded(title)
    log("DONE — video uploaded to Facebook page.")

if __name__ == "__main__":
    main()
