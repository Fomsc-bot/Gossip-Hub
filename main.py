# main.py
import os
import re
import time
import random
import requests
import html
from pathlib import Path
from io import BytesIO
import textwrap

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip, concatenate_audioclips, vfx
)

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ---------------- Try to import Gemini safely ----------------
genai = None
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
try:
    import google.generativeai as genai_lib
    genai = genai_lib
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
        except Exception:
            pass
except Exception:
    genai = None

# ---------------- CONFIG ----------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)
LAST_FILE = WORKDIR / "last_titles.txt"

VIDEO_W, VIDEO_H = 1080, 1920
CAPTION_HEIGHT = int(VIDEO_H * 0.16)
FONT_PATH = None

ZOOM_RATE = 0.015
FPS = 24
BASE_FONT_SIZE = 56
SMALL_FONT_SIZE = 46
MAX_CAPTION_CHARS = 220


def log(*a):
    print("[BOT]", *a)


# ---------------- FIXED sanitize_text() ----------------
def sanitize_text(s: str) -> str:
    """
    Fixed version — ALL re.sub() calls now include the required 'string' argument.
    Prevents the missing-argument TypeError that you got before.
    """
    if not s:
        return ""

    # HTML decode
    s = html.unescape(s)

    # Fix "Hash 039" → '
    s = re.sub(r'\bHash\s*0*39\b', "'", s, flags=re.I)
    s = re.sub(r'\bHash\s*#?\d+\b', " ", s, flags=re.I)
    s = re.sub(r'\bNo\.?\s*0*39\b', "'", s, flags=re.I)
    s = re.sub(r'\bNo\.?\s*\d+\b', " ", s, flags=re.I)

    # Convert &#039; → '
    s = re.sub(r'&[#A-Za-z0-9]+;?', lambda m: html.unescape(m.group(0)), s)

    # Remove control characters
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', "", s)

    # Fix spacing before punctuation
    s = re.sub(r'\s+([,.\-:;!?])', r'\1', s)

    # Normalize whitespace
    s = re.sub(r'\s+', ' ', s).strip()

    # Remove extra quotes at ends
    s = s.strip(" \t\n\"'")

    return s


# ---------------- Duplicate Filter ----------------
def has_uploaded(title):
    if not LAST_FILE.exists():
        return False
    with open(LAST_FILE, "r", encoding="utf-8") as f:
        return title.strip().lower() in f.read().lower()


def save_uploaded(title):
    with open(LAST_FILE, "a", encoding="utf-8") as f:
        f.write(title.strip() + "\n")


# ---------------- Helpers ----------------
def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {}


# ---------------- Title builder ----------------
def short_title_from_text(text: str) -> str:
    text = sanitize_text(text)

    words = re.findall(r"[A-Za-z0-9']+", text)
    stop = {"the", "a", "an", "in", "on", "at", "by", "for", "to", "of", "and", "vs", "vs."}
    words = [w for w in words if w.lower() not in stop]

    short_words = words[:4] if words else ["Hot", "News"]
    short = " ".join(short_words).strip()

    emoji = random.choice(["🔥", "🎬", "⭐", "⚡", "📸", "🔔"])

    hashtags = []
    if genai and GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel("gemini-pro")
            prompt = f"Give 3 hashtags for: {text}. Output only hashtags."
            res = model.generate_content(prompt)
            raw = (res.text or "").strip()
            hashtags = re.findall(r"#\w+", raw)[:3]
        except Exception:
            pass

    if not hashtags:
        hs = [w.lower() for w in short_words][:3]
        hashtags = [f"#{re.sub(r'[^A-Za-z0-9]', '', x)}" for x in hs]

    final_title = f"{short} {emoji} {' '.join(hashtags)}"
    final_title = re.sub(r"[^\x00-\x7F]+", "", final_title)[:120]
    return final_title


# ---------------- News fetch ----------------
def get_news_article():
    if not NEWS_API_KEY:
        raise RuntimeError("NEWS_API_KEY missing")

    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=6&apiKey={NEWS_API_KEY}"
    r = requests.get(url, timeout=15)
    data = safe_json(r)

    if data.get("status") != "ok":
        raise RuntimeError("NewsAPI error")

    for art in data.get("articles", []):
        raw_title = art.get("title") or "Entertainment Update"
        title = sanitize_text(raw_title)
        if not has_uploaded(title):
            description = sanitize_text(art.get("description") or "")
            image_url = art.get("urlToImage")
            article_url = art.get("url")
            lead = fetch_article_lead(article_url) if article_url else ""
            lead = sanitize_text(lead)
            if not description and lead:
                description = lead
            return title, description, image_url, article_url, lead

    raise RuntimeError("All articles already used")


def fetch_article_lead(url):
    if not url:
        return ""
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
            clean = clean.strip().split(".")[0]
            return sanitize_text(clean)

        return ""
    except Exception:
        return ""


# ---------------- BG Image ----------------
def fetch_and_prepare_bg(image_url, fallback_query="entertainment"):
    raw_img = None
    # Try the provided image URL first (if any)
    if image_url:
        try:
            r = requests.get(image_url, timeout=15)
            # Only accept successful responses
            if r.status_code == 200 and r.content:
                raw_img = BytesIO(r.content)
        except Exception:
            raw_img = None

    # If no valid image bytes from url, fetch Unsplash fallback
    if not raw_img:
        try:
            unsplash_url = f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/?{fallback_query}"
            r2 = requests.get(unsplash_url, timeout=15)
            if r2.status_code == 200 and r2.content:
                raw_img = BytesIO(r2.content)
        except Exception:
            raw_img = None

    # Final check: try to open image bytes, if fails, raise or create a plain background
    img = None
    if raw_img:
        try:
            raw_img.seek(0)
            img = Image.open(raw_img).convert("RGB")
        except Exception:
            img = None

    # If still no valid image, create a simple solid-color background
    if img is None:
        log("Warning: couldn't fetch a valid image; using solid color fallback.")
        img = Image.new("RGB", (VIDEO_W, VIDEO_H), (30, 30, 30))

    # Resize / crop to fit target resolution
    w, h = img.size
    scale = max(VIDEO_W / w, VIDEO_H / h)
    img = img.resize((int(w * scale), int(h * scale)))

    left = (img.size[0] - VIDEO_W) // 2
    top = (img.size[1] - VIDEO_H) // 2
    img = img.crop((left, top, left + VIDEO_W, top + VIDEO_H))

    out = WORKDIR / "bg.jpg"
    img.save(out)
    return str(out)


# ---------------- Script generator ----------------
def generate_script(headline, description="", lead=""):
    # Fallback only — to keep code shorter
    return [
        headline,
        "Here’s the latest update on this story.",
        description or lead or "Sources confirm new developments.",
        "People are reacting strongly to the news.",
        "Follow for more updates 🔔"
    ]


# ---------------- TTS ----------------
def create_tts_per_line(lines):
    tts_paths, durations = [], []
    for i, line in enumerate(lines):
        out = WORKDIR / f"tts_{i}.mp3"
        tts = gTTS(text=line, lang="en")
        tts.save(str(out))
        audio = AudioFileClip(str(out))
        durations.append(max(audio.duration, 1))
        audio.close()
        tts_paths.append(str(out))
    return tts_paths, durations


# ---------------- Captions ----------------
def render_bottom_caption(text, index, h=CAPTION_HEIGHT, base_font_size=BASE_FONT_SIZE):
    text = sanitize_text(text)[:MAX_CAPTION_CHARS]

    # Try to load a decent TTF, otherwise load default
    try:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        font = ImageFont.truetype(font_path, base_font_size)
    except Exception:
        font = ImageFont.load_default()
        # Default font does not support size changes; set base_font_size to a safe small value
        base_font_size = 14

    # Create the caption image (fixed height h)
    img = Image.new("RGBA", (VIDEO_W, h), (0, 0, 0, 200))
    draw = ImageDraw.Draw(img)

    # Padding inside the caption box
    pad_x = 40
    pad_y = 20

    max_width = VIDEO_W - pad_x * 2
    max_height = h - pad_y * 2

    # Wrap text into lines that fit the width
    words = text.split()
    if not words:
        lines = [""]
    else:
        # Try with the given font size, reduce if necessary
        font_size = base_font_size
        while True:
            try:
                if isinstance(font, ImageFont.FreeTypeFont):
                    font = ImageFont.truetype(font_path, font_size)
                else:
                    # default font: can't change size
                    pass
            except Exception:
                # fallback to default small font if truetype fails
                font = ImageFont.load_default()

            lines = []
            current = ""
            for w in words:
                test = f"{current} {w}".strip()
                tw, th = draw.textsize(test, font=font)
                if tw <= max_width:
                    current = test
                else:
                    if current:
                        lines.append(current)
                    # if single word too long, force-break it using textwrap
                    if draw.textsize(w, font=font)[0] > max_width:
                        broken = textwrap.wrap(w, width= max(1, int(len(w) * max_width / draw.textsize(w, font=font)[0])))
                        for b in broken:
                            lines.append(b)
                        current = ""
                    else:
                        current = w
            if current:
                lines.append(current)

            # compute total text height
            line_height = draw.textsize("Ay", font=font)[1]
            spacing = int(line_height * 0.15)
            total_h = len(lines) * line_height + (len(lines) - 1) * spacing

            # If fits, break; otherwise reduce font size
            if total_h <= max_height or font_size <= 18:
                break
            font_size -= 2
            if font_size < 12:
                font_size = 12
                break

        # If still too tall, truncate last line with ellipsis
        if total_h > max_height:
            # remove lines until it fits, then ellipsize last line
            while len(lines) > 1:
                lines.pop()
                line_height = draw.textsize("Ay", font=font)[1]
                spacing = int(line_height * 0.15)
                total_h = len(lines) * line_height + (len(lines) - 1) * spacing
                if total_h <= max_height:
                    break
            # ellipsize last line to fit
            last = lines[-1]
            ell = "..."
            while draw.textsize(last + ell, font=font)[0] > max_width and len(last) > 0:
                last = last[:-1]
            lines[-1] = last.strip() + ell

    # Final rendering: vertically center the block of lines within the caption area
    line_height = draw.textsize("Ay", font=font)[1]
    spacing = int(line_height * 0.15)
    total_h = len(lines) * line_height + (len(lines) - 1) * spacing

    y = (h - total_h) // 2
    for line in lines:
        tw, th = draw.textsize(line, font=font)
        x = (VIDEO_W - tw) // 2
        draw.text((x, y), line, font=font, fill="white")
        y += line_height + spacing

    out = WORKDIR / f"cap_{index}.png"
    img.save(out)
    return str(out)


# ---------------- Video ----------------
def build_final_video(bg_path, lines, tts_paths, durations, out_file):
    total = sum(durations) + 2

    bg = ImageClip(bg_path).set_duration(total)
    bg = bg.fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)

    caps = []
    cursor = 0
    for i, (line, dur) in enumerate(zip(lines, durations)):
        cap_img = render_bottom_caption(line, i)
        cap = ImageClip(cap_img).set_duration(dur).set_start(cursor)
        cap = cap.set_position(("center", VIDEO_H * 0.78))
        caps.append(cap)
        cursor += dur

    audio = concatenate_audioclips([AudioFileClip(p) for p in tts_paths])
    final = CompositeVideoClip([bg] + caps, size=(VIDEO_W, VIDEO_H))
    final = final.set_audio(audio)

    final.write_videofile(str(out_file), fps=FPS, codec="libx264", audio_codec="aac")

    return str(out_file)


# ---------------- Upload ----------------
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
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_public(video_file, title, description):
    yt = get_youtube_service()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["shorts", "news", "entertainment"]
        },
        "status": {"privacyStatus": "public"}
    }

    media = MediaFileUpload(video_file, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print("Upload:", int(status.progress() * 100), "%")

    return resp["id"]


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

    yt_title = short_title_from_text(title)
    yt_desc = desc or lead or "Trending entertainment update."

    upload_public(video_path, yt_title, yt_desc)
    save_uploaded(title)

    log("DONE — video uploaded PUBLIC.")


if __name__ == "__main__":
    main()

