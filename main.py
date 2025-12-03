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
CAPTION_HEIGHT = int(VIDEO_H * 0.16)  # slightly smaller to look sleeker
FONT_PATH = None  # set a TTF path if you want a specific font

ZOOM_RATE = 0.015
FPS = 24

# default font sizes
BASE_FONT_SIZE = 56
SMALL_FONT_SIZE = 46
MAX_CAPTION_CHARS = 220  # safety limit


def log(*a):
    print("[BOT]", *a)


# ---------------- Text sanitization helpers ----------------
def sanitize_text(s: str) -> str:
    """
    Decode HTML entities, remove weird "Hash 039"/"No. 039" tokens and stray numeric tokens,
    remove control characters, normalize whitespace.
    """
    if not s:
        return ""
    # html unescape first to convert numeric entities like &#039; -> '
    s = html.unescape(s)

    # Some sources/models may produce "Hash 039" or "No. 039" from "#039;".
    # Replace common weird patterns with apostrophe or just remove them.
    s = re.sub(r'\bHash\s*0*39\b', "'", flags=re.I)
    s = re.sub(r'\bHash\s*#?\d+\b', ' ', s, flags=re.I)
    s = re.sub(r'\bNo\.?\s*0*39\b', "'", flags=re.I)
    s = re.sub(r'\bNo\.?\s*\d+\b', ' ', s, flags=re.I)
    s = re.sub(r'\b[#]\s*0*39\b', "'", flags=re.I)

    # remove leftover tokens like "&#039;" or other numeric references
    s = re.sub(r'&[#A-Za-z0-9]+;?', lambda m: html.unescape(m.group(0)), s)

    # Remove invisible/control characters
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', s)

    # Fix weird repeated punctuation patterns
    s = re.sub(r'\s+([,.\-:;!?])', r'\1', s)
    s = re.sub(r'\s+', ' ', s).strip()

    # Final safety: strip surrounding quotes from triple sources
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


# ---------------- Title builder: max 4 words + emoji + hashtags ----------------
def short_title_from_text(text: str) -> str:
    text = sanitize_text(text)
    # Extract words, drop common stopwords
    words = re.findall(r"[A-Za-z0-9']+", text)
    stop = {"the", "a", "an", "in", "on", "at", "by", "for", "to", "of", "and", "vs", "vs.", "vs"}
    words = [w for w in words if w.lower() not in stop]
    short_words = words[:4] if words else ["Hot", "News"]
    short = " ".join(short_words).strip()
    # emoji
    emoji = random.choice(["🔥", "🎬", "⭐", "⚡", "📸", "🔔"])

    # Attempt to generate trending hashtags via Gemini if available
    hashtags = []
    if genai and GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel("gemini-pro")
            prompt = f"Give me up to 3 short trending hashtags related to: \"{text}\". Output only hashtags separated by spaces (e.g. #Tag1 #Tag2)."
            res = model.generate_content(prompt)
            raw = (res.text or "").strip()
            # extract hashtags
            hashtags = re.findall(r"#\w+", raw)[:3]
        except Exception:
            hashtags = []

    # Fallback: make hashtags using title words
    if not hashtags:
        hs_words = [w.lower() for w in short_words if len(w) > 2][:3]
        hashtags = [f"#{re.sub(r'[^A-Za-z0-9]', '', h)}" for h in hs_words] or ["#Trending", "#Viral"]

    hashtag_str = " ".join(hashtags)
    # Final title: up to 4 words + emoji + hashtags
    final_title = f"{short} {emoji} {hashtag_str}"
    # ensure ascii-safe and length limit
    final_title = re.sub(r"[^\x00-\x7F]+", "", final_title)[:120].strip()
    return final_title


# ---------------- News fetch ----------------
def get_news_article():
    if not NEWS_API_KEY:
        raise RuntimeError("NEWS_API_KEY not set.")
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=6&apiKey={NEWS_API_KEY}"
    r = requests.get(url, timeout=15)
    data = safe_json(r)

    if data.get("status") != "ok":
        raise RuntimeError(data.get("message", "NewsAPI failure"))

    articles = data.get("articles") or []
    if not articles:
        raise RuntimeError("No articles returned.")

    for art in articles:
        raw_title = art.get("title") or "Entertainment Update"
        title = sanitize_text(raw_title)
        if not has_uploaded(title):
            raw_description = art.get("description") or ""
            description = sanitize_text(raw_description)
            image_url = art.get("urlToImage")
            article_url = art.get("url")
            lead = fetch_article_lead(article_url) if article_url else ""
            lead = sanitize_text(lead)
            if not description and lead:
                description = lead
            return title, description, image_url, article_url, lead

    raise RuntimeError("All today's articles already uploaded.")


def fetch_article_lead(url):
    """
    Try to fetch a short informative lead sentence from the article page:
    - look for og:description or meta description
    - otherwise pick first reasonable <p> content
    - return empty string on failure
    """
    if not url:
        return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible)"}
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code != 200 or not r.text:
            return ""
        html_text = r.text
        # check og:description and meta description
        m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']', html_text, re.I)
        if not m:
            m = re.search(r'<meta[^>]+name=["\']description["\'][^>]*content=["\']([^"\']+)["\']', html_text, re.I)
        if m:
            desc = re.sub(r'\s+', ' ', m.group(1)).strip()
            s = re.split(r'[.!?]', desc.strip())[0]
            return sanitize_text(s.strip())
        # fallback: first meaningful <p> tag (strip HTML)
        p = re.search(r'<p[^>]*>(.*?)</p>', html_text, re.I | re.S)
        if p:
            text = re.sub(r'<[^>]+>', '', p.group(1))
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text.split()) > 5:
                s = re.split(r'[.!?]', text.strip())[0]
                return sanitize_text(s.strip())
        return ""
    except Exception:
        return ""


# ---------------- Background Image ----------------
def fetch_and_prepare_bg(image_url, fallback_query="entertainment"):
    raw_img = None

    if image_url:
        try:
            r = requests.get(image_url, timeout=15)
            if r.status_code == 200 and r.content:
                raw_img = BytesIO(r.content)
        except Exception:
            pass

    if not raw_img:
        # Unsplash fallback
        unsplash_url = f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/?{fallback_query}&sig={random.randint(1,999999)}"
        r = requests.get(unsplash_url, timeout=15)
        raw_img = BytesIO(r.content)

    img = Image.open(raw_img).convert("RGB")
    w, h = img.size
    scale = max(VIDEO_W / w, VIDEO_H / h)
    img_resized = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    left = (img_resized.size[0] - VIDEO_W) // 2
    top = (img_resized.size[1] - VIDEO_H) // 2
    crop = img_resized.crop((left, top, left + VIDEO_W, top + VIDEO_H))

    out_path = WORKDIR / "bg.jpg"
    crop.save(out_path, "JPEG", quality=90)
    return str(out_path)


# ---------------- Natural (non-AI template) Script Generator ----------------
def _fallback_script_from_headline(headline, description="", lead=""):
    """
    Create a more 'human' 5-line mini-story using headline + description/lead.
    The fallback uses the lead if available to strengthen factual coverage.
    """
    # Extract some candidates from headline
    words = re.findall(r"[A-Za-z0-9']+", headline)
    candidates = [w for w in words if len(w) > 3]
    subj = candidates[0] if candidates else (headline.split()[0] if headline.split() else "This")

    # Variation pools
    hooks = [
        f"Breaking: {headline}",
        f"Quick update — {headline}",
        f"Here's what's happening: {headline}",
        f"Hot: {headline}",
        f"Today: {headline}"
    ]
    explainers = [
        "What happened: a new report or announcement changed the story.",
        "Here's the key development you need to know.",
        "Official sources and people close to the topic confirm the update.",
        "This update adds an important new detail to the situation."
    ]
    details = []
    if lead:
        s = re.sub(r'\s+', ' ', lead).strip()
        if len(s.split()) > 3:
            details.append(s if len(s.split()) <= 18 else " ".join(s.split()[:18]) + "...")
    if description:
        ds = re.split(r'[.!?]', description.strip())[0]
        ds = re.sub(r'\s+', ' ', ds).strip()
        if ds and ds not in details:
            details.append(ds if len(ds.split()) <= 18 else " ".join(ds.split()[:18]) + "...")

    if not details:
        details = [
            "Sources say the situation is evolving and more info is expected.",
            "The timeline and impact are still being clarified."
        ]

    context_impact = [
        f"People online have been reacting strongly to the news about {subj}.",
        "Industry voices and fans are already weighing in.",
        "This could shape upcoming coverage for days to come."
    ]
    ctas = [
        "What do you think? Drop your thoughts below.",
        "Sound off in the comments — would you be surprised?",
        "Follow for more updates as this develops."
    ]

    line1 = random.choice(hooks)
    line2 = random.choice(explainers)
    line3 = details[0]
    line4 = random.choice(context_impact)
    line5 = random.choice(ctas)

    seq = [line1, line2, line3, line4, line5]
    final = []
    prev = None
    for l in seq:
        s = sanitize_text(re.sub(r'\s+', ' ', l).strip())
        if s != prev and s:
            parts = s.split()
            if len(parts) > 16:
                s = " ".join(parts[:16]) + "..."
            final.append(s)
        prev = s
    while len(final) < 5:
        final.append(random.choice(ctas))
    return final[:5]


def generate_script(headline, description="", lead=""):
    """
    Generate a 5-line script that reads like a human mini-story.
    Uses Gemini if available; otherwise uses fallback enhanced with lead.
    """
    if genai and GEMINI_API_KEY:
        try:
            prompt = f"""
Write a natural, human-sounding 5-line mini-story (short sentences) suitable for a YouTube Shorts voiceover.
Topic headline:
\"\"\"{headline}\"\"\" 

Extra context (if any):
\"\"\"{description or ''}\"\"\" 

Lead detail (if available):
\"\"\"{lead or ''}\"\"\" 

Requirements:
- Output exactly 5 lines, each on its own line (no numbering).
- Make lines short and coherent so they together tell the whole mini-story.
- Include one clear factual detail drawn from the extra context or lead.
- Use varied sentence structure; avoid repetitive templates.
- End with an engaging, human CTA (question or invite).
- Keep each line roughly 6-14 words.
"""
            model = genai.GenerativeModel("gemini-pro")
            res = model.generate_content(prompt)
            text = (res.text or "").strip()
            # sanitize output strongly
            lines = [sanitize_text(l.strip(" -•\t")) for l in text.splitlines() if l.strip()]
            # filter empties
            lines = [l for l in lines if l]
            if len(lines) >= 5:
                out = []
                for l in lines[:5]:
                    s = re.sub(r'\s+', ' ', l).strip()
                    parts = s.split()
                    if len(parts) > 18:
                        s = " ".join(parts[:18]) + "..."
                    out.append(s)
                # dedupe adjacent duplicates
                final = []
                prev = None
                for l in out:
                    if l != prev:
                        final.append(l)
                    prev = l
                while len(final) < 5:
                    final.append("What do you think about this?")
                return final[:5]
            else:
                return _fallback_script_from_headline(headline, description, lead)
        except Exception:
            return _fallback_script_from_headline(headline, description, lead)
    else:
        return _fallback_script_from_headline(headline, description, lead)


# ---------------- TTS ----------------
def create_tts_per_line(lines):
    tts_paths = []
    durations = []
    for i, line in enumerate(lines):
        out = WORKDIR / f"tts_{i}.mp3"
        safe_line = sanitize_text(line)
        # create TTS
        tts = gTTS(text=safe_line, lang="en")
        tts.save(str(out))

        audio = AudioFileClip(str(out))
        dur = max(audio.duration, 1.0)
        audio.close()

        tts_paths.append(str(out))
        durations.append(dur)

    return tts_paths, durations


# ---------------- Captions (optimized) ----------------
def render_bottom_caption(text, index, h=CAPTION_HEIGHT, base_font_size=BASE_FONT_SIZE):
    """
    Renders a bottom caption with:
    - automatic wrap
    - dynamic font-size reduction to fit
    - stroke/outline for readability
    """
    try:
        font_path = FONT_PATH or "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        font = ImageFont.truetype(font_path, base_font_size)
    except Exception:
        font = ImageFont.load_default()

    w = VIDEO_W
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    band_margin = int(w * 0.03)
    draw.rectangle([band_margin, 0, w - band_margin, h], fill=(0, 0, 0, 200))

    # trim very long inputs to a safety limit
    text = sanitize_text(re.sub(r'\s+', ' ', text).strip())[:MAX_CAPTION_CHARS]

    # wrap and adjust font size until fits
    font_size = base_font_size
    while font_size >= 26:
        try:
            font = ImageFont.truetype(FONT_PATH or "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()
        words = text.split(" ")
        lines = []
        cur = ""
        for wpart in words:
            test = (cur + " " + wpart).strip() if cur else wpart
            tw, th = draw.textsize(test, font=font)
            if tw <= (VIDEO_W - 2 * (band_margin + 16)):
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = wpart
        if cur:
            lines.append(cur)
        total_h = sum(draw.textsize(l, font=font)[1] for l in lines) + (len(lines) - 1) * 6
        if total_h <= h - 16 and len(lines) <= 4:
            break
        font_size -= 4

    y = (h - total_h) // 2 if total_h < h else 6
    for ln in lines:
        tw, th = draw.textsize(ln, font)
        x = (VIDEO_W - tw) // 2
        # stroke
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                draw.text((x + ox, y + oy), ln, font=font, fill=(0, 0, 0, 220))
        draw.text((x, y), ln, font=font, fill=(255, 255, 255, 255))
        y += th + 6

    out = WORKDIR / f"cap_{index}.png"
    img.save(out)
    return str(out)


# ---------------- Video Builder ----------------
def build_final_video(bg_path, lines, tts_paths, durations, out_file):
    total = sum(durations) + 2.0  # CTA gap
    log(f"Rendering video, duration {total:.1f}s...")

    bg = ImageClip(bg_path).set_duration(total)
    bg = bg.fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)
    bg = bg.fx(vfx.fadein, 0.25).fx(vfx.fadeout, 0.25)

    caps = []
    cursor = 0.0
    for i, (line, d) in enumerate(zip(lines, durations)):
        cap_img = render_bottom_caption(line, i)
        cap = ImageClip(cap_img).set_duration(d).set_start(cursor).set_position(("center", int(VIDEO_H * 0.78)))
        caps.append(cap)
        cursor += d

    # CTA final caption
    cta = ImageClip(render_bottom_caption("Follow for more 🔔", "cta")).set_duration(2.0)
    cta = cta.set_start(cursor).set_position(("center", int(VIDEO_H * 0.78)))
    caps.append(cta)

    audio = concatenate_audioclips([AudioFileClip(p) for p in tts_paths])
    audio = audio.set_duration(total)

    final = CompositeVideoClip([bg] + caps, size=(VIDEO_W, VIDEO_H)).set_duration(total)
    final = final.set_audio(audio)

    log("Writing video file (this can take several minutes)...")
    final.write_videofile(str(out_file), fps=FPS, codec="libx264", audio_codec="aac", threads=4, preset="fast")

    try:
        audio.close()
    except Exception:
        pass

    return str(out_file)


# ---------------- Upload PUBLIC ----------------
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

    safe_title = re.sub(r"[^\x00-\x7F]+", "", title)[:100]
    body = {
        "snippet": {
            "title": safe_title,
            "description": description,
            "tags": ["shorts", "entertainment", "gossip"]
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_file, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            log("Upload:", int(status.progress() * 100), "%")

    return resp.get("id")


# ---------------- MAIN ----------------
def main():
    log("Starting pipeline...")
    title, desc, img_url, article_url, lead = get_news_article()
    log("Article:", title)
    if lead:
        log("Extracted lead:", lead)

    log("Generating script (Gemini fallback safe) ...")
    lines = generate_script(title, desc, lead)

    # sanitize generated lines once more (remove any stray tokens)
    lines = [sanitize_text(l) for l in lines]

    # Build background image properly cropped
    bg = fetch_and_prepare_bg(img_url)
    tts_paths, durations = create_tts_per_line(lines)

    out_video = WORKDIR / "final.mp4"
    video_path = build_final_video(bg, lines, tts_paths, durations, out_video)

    yt_title = short_title_from_text(title)
    yt_desc = desc or lead or "Trending Entertainment Update"

    upload_public(video_path, yt_title, yt_desc)

    save_uploaded(title)

    log("DONE — video uploaded PUBLIC.")


if __name__ == "__main__":
    main()






