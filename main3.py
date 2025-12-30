# main.py
import os
import re
import random
import requests
import html
import textwrap
from pathlib import Path
from io import BytesIO

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip,
    concatenate_audioclips, concatenate_videoclips, vfx, VideoFileClip
)

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ---------------- Gemini (optional) ----------------
genai = None
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

try:
    import google.generativeai as genai_lib
    if GEMINI_API_KEY:
        genai = genai_lib
        genai.configure(api_key=GEMINI_API_KEY)
except Exception:
    genai = None

# ---------------- CONFIG ----------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)
LAST_FILE = WORKDIR / "last_titles.txt"

VIDEO_W, VIDEO_H = 1080, 1920
CAPTION_HEIGHT = int(VIDEO_H * 0.16)

ZOOM_RATE = 0.015
FPS = 24
BASE_FONT_SIZE = 56
MAX_CAPTION_CHARS = 220

OUTTRO_DIR = Path("Outtro")

def log(*a):
    print("[BOT]", *a)

# ---------------- Text cleanup ----------------
def sanitize_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', "", s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s.strip(" \"'")

# ---------------- Gemini summarizer ----------------
def summarize_with_gemini(headline, description, lead):
    """
    Returns a list of 4–5 short, natural, spoken-style lines.
    Falls back silently if Gemini fails.
    """
    if not genai:
        return None

    prompt = f"""
You are a professional YouTube Shorts script writer.

Write a clear, natural, HUMAN-sounding voiceover script
for an entertainment news short.

Rules:
- 4 to 5 short lines
- Each line must be spoken naturally
- No hashtags
- No emojis
- No filler
- No clickbait
- Final line should be a soft subscription suggestion

Headline:
{headline}

Article summary:
{description or lead}
"""

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        text = response.text.strip()

        lines = [
            sanitize_text(l)
            for l in text.split("\n")
            if sanitize_text(l)
        ]

        # Hard limit safety
        return lines[:5] if len(lines) >= 3 else None

    except Exception:
        return None

# ---------------- Script generator ----------------
def generate_script(headline, description="", lead=""):
    # 🔹 Try Gemini first
    gemini_lines = summarize_with_gemini(headline, description, lead)
    if gemini_lines:
        return gemini_lines

    # 🔹 Fallback (original but improved)
    headline = sanitize_text(headline)
    description = sanitize_text(description)
    lead = sanitize_text(lead)

    source = description or lead or ""
    sentences = re.split(r'(?<=[.!?])\s+', source)

    lines = [
        headline if headline.endswith(".") else headline + ".",
        sentences[0] if sentences else "Here’s the latest update.",
        "Fans are already reacting online.",
        "Follow for more clear entertainment updates."
    ]

    return [sanitize_text(l) for l in lines]

# ---------------- TTS ----------------
def create_tts_per_line(lines):
    tts_paths, durations = [], []
    for i, line in enumerate(lines):
        out = WORKDIR / f"tts_{i}.mp3"
        tts = gTTS(text=line, lang="en")
        tts.save(str(out))
        audio = AudioFileClip(str(out))
        durations.append(max(audio.duration + 0.25, 0.8))
        audio.close()
        tts_paths.append(str(out))
    return tts_paths, durations

# ---------------- Captions ----------------
def render_bottom_caption(text, index):
    text = sanitize_text(text)[:MAX_CAPTION_CHARS]
    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        BASE_FONT_SIZE
    )

    wrapped = "\n".join(textwrap.wrap(text, 28))
    img = Image.new("RGBA", (VIDEO_W, CAPTION_HEIGHT), (0, 0, 0, 200))
    draw = ImageDraw.Draw(img)

    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=4)
    x = (VIDEO_W - (bbox[2] - bbox[0])) // 2
    y = (CAPTION_HEIGHT - (bbox[3] - bbox[1])) // 2

    draw.multiline_text((x, y), wrapped, font=font, fill="white", align="center", spacing=4)
    out = WORKDIR / f"cap_{index}.png"
    img.save(out)
    return str(out)

# ---------------- Video build ----------------
def build_final_video(bg_path, lines, tts_paths, durations, out_file):
    total = sum(durations) + 1.5
    bg = ImageClip(bg_path).set_duration(total)
    bg = bg.fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)

    caps, cursor = [], 0
    for i, dur in enumerate(durations):
        cap_img = render_bottom_caption(lines[i], i)
        cap = ImageClip(cap_img).set_duration(dur).set_start(cursor)
        cap = cap.set_position(("center", VIDEO_H * 0.78))
        caps.append(cap)
        cursor += dur

    audio = concatenate_audioclips([AudioFileClip(p) for p in tts_paths])
    video = CompositeVideoClip([bg] + caps).set_audio(audio)
    video.write_videofile(str(out_file), fps=FPS, codec="libx264", audio_codec="aac")
    return str(out_file)

# ---------------- MAIN ----------------
def main():
    log("Starting pipeline...")
    title, desc, img_url, article_url, lead = get_news_article()
    lines = generate_script(title, desc, lead)

    bg = fetch_and_prepare_bg(img_url)
    tts_paths, durations = create_tts_per_line(lines)
    out_video = WORKDIR / "final.mp4"
    build_final_video(bg, lines, tts_paths, durations, out_video)

    log("DONE")

if __name__ == "__main__":
    main()
