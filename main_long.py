# main_long.py
import os
import re
import time
import random
import requests
import html
import json
import textwrap
from pathlib import Path
from io import BytesIO
from typing import Optional, Tuple, List

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip,
    concatenate_audioclips, concatenate_videoclips, vfx, VideoFileClip, afx
)
from moviepy.audio.AudioClip import CompositeAudioClip

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ---------------- Safely Configure Gemini ----------------
genai = None
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
try:
    import google.generativeai as genai_lib
    genai = genai_lib
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
        except Exception as e:
            print("[BOT] Failed to configure Gemini library:", e)
except Exception as e:
    print("[BOT] Failed to import google.generativeai:", e)
    genai = None

# ---------------- CONFIG ----------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)
LAST_FILE = WORKDIR / "last_titles.txt"

# Landscape standard dimensions
VIDEO_W, VIDEO_H = 1920, 1080
FPS = 24
ZOOM_RATE = 0.012

# Common image extensions for validation
COMMON_IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp', '.gif']

def log(*args):
    print("[BOT-LONG]", *args)

# ---------------- Text cleanup ----------------
def sanitize_text(s: str) -> str:
    """Clean and sanitize text."""
    if not s:
        return ""
    s = html.unescape(s)
    # Remove control characters
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', "", s)
    return re.sub(r'\s+', ' ', s).strip(" \"'")

# ---------------- Duplicate filter ----------------
def has_uploaded(title: str) -> bool:
    """Check if title has already been uploaded."""
    if not LAST_FILE.exists():
        return False
    try:
        content = LAST_FILE.read_text(encoding="utf-8").lower()
        return title.lower() in content
    except Exception:
        return False

def save_uploaded(title: str) -> None:
    """Save uploaded title to prevent duplicates."""
    with open(LAST_FILE, "a", encoding="utf-8") as f:
        f.write(title + "\n")

# ---------------- News fetching ----------------
def _try_newsapi_fetch() -> List[dict]:
    """Fetch news from NewsAPI."""
    try:
        url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=15&apiKey={NEWS_API_KEY}"
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()
        return data.get("articles", [])
    except Exception as e:
        log(f"NewsAPI fetch failed: {e}")
        return []

def _try_gnews_fetch() -> List[dict]:
    """Fetch news from GNews."""
    try:
        url = f"https://gnews.io/api/v4/top-headlines?topic=entertainment&lang=en&max=15&token={GNEWS_API_KEY}"
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()
        return data.get("articles", [])
    except Exception as e:
        log(f"GNews fetch failed: {e}")
        return []

def fetch_article_lead(url: str) -> str:
    """Extract article lead from meta description."""
    if not url:
        return ""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        
        patterns = [
            r'og:description["\']\s*content=["\']([^"\']+)["\']',
            r'description["\']\s*content=["\']([^"\']+)["\']',
            r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, r.text, re.IGNORECASE)
            if match:
                return sanitize_text(match.group(1))
        
        return ""
    except Exception as e:
        log(f"Failed to fetch article lead from {url}: {e}")
        return ""

def validate_image_url(url: str) -> bool:
    """Validate if URL is likely an image."""
    if not url:
        return False
    url_lower = url.lower()
    if any(url_lower.endswith(ext) for ext in COMMON_IMAGE_EXTENSIONS):
        return True
    image_patterns = ['/image/', '/images/', '/photo/', '/photos/', '/img/', 'image.', 'photo.']
    if any(pattern in url_lower for pattern in image_patterns):
        return True
    return False

def get_news_article(max_attempts: int = 15) -> Tuple[str, str, str, str, str]:
    """Fetch a news article with a valid image URL."""
    attempts = 0
    articles = []
    
    # Collect articles from both sources
    if NEWS_API_KEY:
        articles.extend(_try_newsapi_fetch())
    if GNEWS_API_KEY:
        articles.extend(_try_gnews_fetch())
        
    if not articles:
        raise RuntimeError("No articles fetched from any source. Ensure API keys are set correctly.")
    
    # Shuffle for variety
    random.shuffle(articles)
    
    for art in articles:
        if attempts >= max_attempts:
            break
            
        try:
            title = sanitize_text(art.get("title", ""))
            if not title or has_uploaded(title):
                continue
                
            img_url = art.get("urlToImage") or art.get("image")
            if not img_url or not validate_image_url(img_url):
                continue
                
            description = sanitize_text(art.get("description", ""))
            url = art.get("url") or art.get("link") or ""
            
            lead = ""
            if url and url.startswith(('http://', 'https://')):
                lead = fetch_article_lead(url)
                time.sleep(0.5)
            
            log(f"Selected article for long-form: {title[:60]}...")
            log(f"Article Image URL: {img_url}")
            return title, description, img_url, url, lead
            
        except Exception as e:
            log(f"Error processing article attempt: {e}")
            attempts += 1
            continue
            
    raise RuntimeError("No valid un-uploaded articles found with images after max attempts.")

# ---------------- Script Generator (Gemini + Fallback) ----------------
def generate_fallback_script(headline: str, description: str, lead: str) -> Tuple[str, List[dict]]:
    """Fallback script generator if Gemini fails."""
    log("Using fallback script generator.")
    title = f"{headline[:70]} - Entertainment News Update"
    lines = [
        f"Welcome back to Gossip Hub. Today, we are diving deep into some major news that is shaking up the entertainment world: {headline}.",
        description or lead or "Details are just coming in, but here is what we know so far about this massive development.",
        "Insiders and fans are reacting heavily across social media, debating the implications of this news.",
        "As always, we are keeping a close eye on this story. We will bring you more updates as they break.",
        "What are your thoughts on this story? Let us know in the comments below, make sure to like this video, and hit that subscribe button for more gossip updates!"
    ]
    queries = ["entertainment", "celebrity gossip", "social media drama", "reporter microphone", "subscribe button"]
    captions = [
        "Major Entertainment Update",
        headline[:65],
        "Trending Reactions & Buzz",
        "Story Details Developing",
        "Subscribe to Gossip Hub!"
    ]
    segments = []
    for i, (line, query, cap) in enumerate(zip(lines, queries, captions)):
        segments.append({
            "segment_index": i + 1,
            "narration": line,
            "search_query": query,
            "caption": cap
        })
    return title, segments

def generate_script_with_gemini(headline: str, description: str, lead: str) -> Tuple[str, List[dict]]:
    """Generate script and visual prompts via Gemini API."""
    if not genai or not GEMINI_API_KEY:
        return generate_fallback_script(headline, description, lead)
        
    prompt = f"""
You are a professional celebrity reporter and scriptwriter for the "Gossip Hub" YouTube channel.
Your goal is to write a highly attractive and engaging narrator script for a long-form landscape video based on these news details:
Title: {headline}
Description: {description}
Lead: {lead}

Instructions:
- Write a detailed, gossipy narration script of about 250-350 words.
- Divide the script into 4 to 6 logical narration segments.
- Make the content detailed and informative to keep viewers hooked and get more subscribers.
- Segment 1 MUST contain a powerful, attention-grabbing hook.
- The final segment MUST contain a Call To Action (like, comment, and subscribe).
- For each segment, provide:
  1. "narration": The script lines for the voiceover.
  2. "search_query": A clean search query (1-3 words) to search for a relevant background photo on Pexels (e.g. "celebrity walking", "concert stage", "shocked fan", "hollywood star").
  3. "caption": A concise, attractive caption/headline (1-2 sentences) to display as a news graphic on screen.
- Generate a highly click-worthy, clickbait-style YouTube video title (under 90 characters).

Format the response strictly as a JSON object with this schema:
{{
  "video_title": "Engaging Video Title Here",
  "segments": [
    {{
      "segment_index": 1,
      "narration": "...",
      "search_query": "...",
      "caption": "..."
    }},
    ...
  ]
}}
Do not include any markdown formatting (like ```json or ```) in the response. Return raw JSON only.
"""
    try:
        log("Querying Gemini API for long-form script...")
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        data = json.loads(response.text)
        title = data.get("video_title", headline)
        segments = data.get("segments", [])
        if not segments:
            raise ValueError("No segments returned in Gemini response.")
        log(f"Successfully generated script with Gemini. Title: {title}")
        return title, segments
    except Exception as e:
        log(f"Gemini generation failed: {e}")
        return generate_fallback_script(headline, description, lead)

# ---------------- Pexels Image Fetching ----------------
def fetch_pexels_image(query: str, index: int) -> str:
    """Fetch a landscape image from Pexels matching the query."""
    if not PEXELS_API_KEY:
        return ""
    
    url = f"https://api.pexels.com/v1/search?query={query}&per_page=5&orientation=landscape"
    headers = {"Authorization": PEXELS_API_KEY}
    
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        photos = data.get("photos", [])
        if not photos:
            log(f"No photos found on Pexels for query: {query}")
            return ""
            
        photo = random.choice(photos[:min(len(photos), 3)])
        img_url = photo.get("src", {}).get("large2x") or photo.get("src", {}).get("large")
        if not img_url:
            return ""
            
        img_r = requests.get(img_url, timeout=15)
        img_r.raise_for_status()
        
        out_path = WORKDIR / f"pexels_{index}.jpg"
        with open(out_path, "wb") as f:
            f.write(img_r.content)
        log(f"Downloaded Pexels image for segment {index}: {img_url}")
        return str(out_path)
    except Exception as e:
        log(f"Pexels fetch failed for query '{query}': {e}")
        return ""

def get_segment_image(query: str, index: int, article_img_url: Optional[str]) -> str:
    """Get the visual image for a segment with layers of fallback."""
    # 1. Try Pexels
    img_path = fetch_pexels_image(query, index)
    if img_path and Path(img_path).exists():
        return img_path
        
    # 2. Try download article image as fallback
    if article_img_url:
        try:
            log(f"Downloading article image fallback for segment {index}...")
            img_r = requests.get(article_img_url, timeout=15)
            img_r.raise_for_status()
            out_path = WORKDIR / f"article_fallback_{index}.jpg"
            with open(out_path, "wb") as f:
                f.write(img_r.content)
            return str(out_path)
        except Exception as e:
            log(f"Failed to download article fallback image: {e}")
            
    # 3. Create a beautiful gradient image as the absolute fallback
    out_path = WORKDIR / f"fallback_gradient_{index}.jpg"
    img = Image.new('RGB', (VIDEO_W, VIDEO_H))
    draw = ImageDraw.Draw(img)
    
    # Unique color palettes for each fallback slide
    seed = index * 40
    color_start = (
        int(15 + (seed % 50)),
        int(10 + ((seed * 2) % 30)),
        int(25 + ((seed * 3) % 70))
    )
    color_end = (
        int(color_start[0] + 60),
        int(color_start[1] + 50),
        int(color_start[2] + 80)
    )
    
    for y in range(VIDEO_H):
        ratio = y / VIDEO_H
        r = int(color_start[0] + ratio * (color_end[0] - color_start[0]))
        g = int(color_start[1] + ratio * (color_end[1] - color_start[1]))
        b = int(color_start[2] + ratio * (color_end[2] - color_start[2]))
        draw.line([(0, y), (VIDEO_W, y)], fill=(r, g, b))
        
    img.save(out_path, quality=95)
    log(f"Created fallback gradient for segment {index}")
    return str(out_path)

# ---------------- Font Helper ----------------
def load_font(size: int) -> ImageFont.ImageFont:
    """Helper to load standard fonts across Windows and Ubuntu environments."""
    font_paths = [
        # Windows Fonts
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\segoeuib.ttf",
        "C:\\Windows\\Fonts\\tahomabd.ttf",
        # Linux / Ubuntu Fonts
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    ]
    for font_path in font_paths:
        if font_path.startswith("C:") or os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            continue
    return ImageFont.load_default()

# ---------------- Premium News Card Overlay (PIL) ----------------
def create_landscape_caption(text: str, index: int) -> str:
    """Create a premium landscape news card overlay image (glassmorphic style)."""
    img = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Lower-third card dimensions
    card_h = 220
    card_w = 1800
    card_x = (VIDEO_W - card_w) // 2  # 60
    card_y = VIDEO_H - card_h - 60   # 800
    
    # 1. Glassmorphic Card Container
    try:
        draw.rounded_rectangle(
            [(card_x, card_y), (card_x + card_w, card_y + card_h)],
            radius=15,
            fill=(15, 15, 20, 225)
        )
    except AttributeError:
        draw.rectangle(
            [(card_x, card_y), (card_x + card_w, card_y + card_h)],
            fill=(15, 15, 20, 225)
        )
        
    # 2. Vertical left accent stripe (Red)
    accent_w = 12
    try:
        draw.rounded_rectangle(
            [(card_x, card_y), (card_x + accent_w, card_y + card_h)],
            radius=15,
            fill=(235, 10, 30, 255)
        )
        draw.rectangle(
            [(card_x + 5, card_y), (card_x + accent_w, card_y + card_h)],
            fill=(235, 10, 30, 255)
        )
    except AttributeError:
        draw.rectangle(
            [(card_x, card_y), (card_x + accent_w, card_y + card_h)],
            fill=(235, 10, 30, 255)
        )

    # Fonts
    font_main = load_font(38)
    font_badge = load_font(20)
    font_category = load_font(18)
    
    # 3. OVERLAP "GOSSIP HUB" Badge
    badge_w = 180
    badge_h = 36
    badge_x = card_x + 35
    badge_y = card_y - 18
    try:
        draw.rounded_rectangle(
            [(badge_x, badge_y), (badge_x + badge_w, badge_y + badge_h)],
            radius=8,
            fill=(235, 10, 30, 255)
        )
    except AttributeError:
        draw.rectangle(
            [(badge_x, badge_y), (badge_x + badge_w, badge_y + badge_h)],
            fill=(235, 10, 30, 255)
        )
        
    badge_text = "GOSSIP HUB"
    try:
        bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
        bt_w = bbox[2] - bbox[0]
        bt_h = bbox[3] - bbox[1]
    except AttributeError:
        bt_w, bt_h = 90, 18
        
    bx = badge_x + (badge_w - bt_w) // 2
    by = badge_y + (badge_h - bt_h) // 2 - 2
    draw.text((bx, by), badge_text, font=font_badge, fill="white")
    
    # 4. Category Tag
    cat_text = "BREAKING NEWS"
    try:
        bbox_cat = draw.textbbox((0, 0), cat_text, font=font_category)
        cat_w = bbox_cat[2] - bbox_cat[0]
    except AttributeError:
        cat_w = 150
    cat_x = card_x + card_w - cat_w - 35
    cat_y = card_y + 16
    draw.text((cat_x, cat_y), cat_text, font=font_category, fill=(255, 193, 7, 255))
    
    # 5. Wrapped Caption Text
    max_chars = 80
    wrapped = textwrap.wrap(text, width=max_chars)
    
    line_y = card_y + 65
    for line in wrapped[:3]:  # Max 3 lines
        # Drop shadow
        draw.text((card_x + 52, line_y + 2), line, font=font_main, fill=(0, 0, 0, 255))
        # Foreground
        draw.text((card_x + 50, line_y), line, font=font_main, fill="white")
        line_y += 48
        
    out_path = WORKDIR / f"cap_{index}.png"
    img.save(out_path)
    return str(out_path)

# ---------------- Background Music Downloader ----------------
def download_background_music() -> str:
    """Download a default copyright-free ambient/lofi track if not present."""
    music_path = WORKDIR / "background_music.mp3"
    if music_path.exists() and music_path.stat().st_size > 100000:
        return str(music_path)
        
    music_url = os.getenv("MUSIC_URL")
    if not music_url:
        # Fallback to a public-domain archive example track
        music_url = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-8.mp3"
        
    try:
        log(f"Downloading background music from: {music_url}")
        r = requests.get(music_url, timeout=30)
        r.raise_for_status()
        with open(music_path, "wb") as f:
            f.write(r.content)
        log("Successfully downloaded background music track.")
        return str(music_path)
    except Exception as e:
        log(f"Failed to download background music: {e}. Video will render without background track.")
        return ""

# ---------------- Outtro Loader ----------------
def load_outtro_clip() -> Optional[VideoFileClip]:
    """Load and resize a random outro clip if available."""
    outro_dir = Path("Outtro")
    if not outro_dir.exists():
        return None
    videos = list(outro_dir.glob("*.mp4"))
    if not videos:
        return None
    outro_path = random.choice(videos)
    try:
        outro = VideoFileClip(str(outro_path))
        outro = outro.resize(newsize=(VIDEO_W, VIDEO_H))
        return outro
    except Exception as e:
        log(f"Failed to load outro clip: {e}")
        return None

# ---------------- Video Compilation ----------------
def build_composite_slide(image_path: str) -> str:
    """Generate a high-quality frame slide (blurred background + sharp centered foreground)."""
    img = Image.open(image_path).convert("RGB")
    
    # 1. Blurred background scaled to fill 1920x1080
    bg = img.resize((VIDEO_W, VIDEO_H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(30))
    
    # 2. Sharp foreground scaled to fit inside 1920x1080 keeping aspect ratio
    img_ratio = img.width / img.height
    target_ratio = VIDEO_W / VIDEO_H
    
    if img_ratio > target_ratio:
        fg_w = VIDEO_W
        fg_h = int(fg_w / img_ratio)
    else:
        fg_h = VIDEO_H
        fg_w = int(fg_h * img_ratio)
        
    fg = img.resize((fg_w, fg_h), Image.LANCZOS)
    
    # Paste foreground centered on blurred background
    x = (VIDEO_W - fg_w) // 2
    y = (VIDEO_H - fg_h) // 2
    bg.paste(fg, (x, y))
    
    out_path = Path(image_path).parent / f"composed_{Path(image_path).name}"
    bg.save(out_path, quality=95)
    return str(out_path)

def build_final_video(segments: List[dict], article_img_url: Optional[str], output_path: Path) -> None:
    """Build landscape long-form video combining images, captions, voiceovers, and background music."""
    log("Compiling long-form landscape video...")
    
    segment_clips = []
    tts_audio_clips = []
    
    for i, seg in enumerate(segments):
        idx = seg["segment_index"]
        text = seg["narration"]
        query = seg["search_query"]
        caption = seg["caption"]
        
        # 1. TTS Generation
        tts_path = WORKDIR / f"tts_{idx}.mp3"
        tts = gTTS(text=text, lang="en", slow=False)
        tts.save(str(tts_path))
        
        # Get voiceover duration
        audio_clip = AudioFileClip(str(tts_path))
        dur = audio_clip.duration + 0.5 # Add small buffer pause
        audio_clip.close()
        
        # Save audio clips for concatenation
        tts_audio_clips.append(AudioFileClip(str(tts_path)))
        
        # 2. Curation and Visual Preparation
        raw_img = get_segment_image(query, idx, article_img_url)
        composed_img = build_composite_slide(raw_img)
        
        # 3. Create Slide Clip (Ken Burns Zoom effect)
        slide_clip = ImageClip(composed_img).set_duration(dur).fx(
            vfx.resize, lambda t: 1.0 + 0.03 * (t / dur)
        )
        
        # 4. Overlay Subtitle Card
        caption_img = create_landscape_caption(caption, idx)
        caption_clip = ImageClip(caption_img).set_duration(dur).set_position(("center", "center"))
        
        # Composite Visual elements for the segment
        segment_video = CompositeVideoClip([slide_clip, caption_clip], size=(VIDEO_W, VIDEO_H))
        segment_clips.append(segment_video)
        
    # Concatenate visual elements
    main_video = concatenate_videoclips(segment_clips, method="compose")
    
    # Concatenate voiceover audio
    voiceover_audio = concatenate_audioclips(tts_audio_clips)
    
    total_duration = main_video.duration
    
    # 5. Background Music integration
    bg_music_path = download_background_music()
    if bg_music_path and Path(bg_music_path).exists():
        try:
            bg_music = AudioFileClip(bg_music_path)
            # Loop background music to match total duration
            bg_music = bg_music.fx(afx.audio_loop, duration=total_duration)
            # Reduce background music volume to be soft (-20dB equivalent or 12%)
            bg_music = bg_music.fx(afx.volumex, 0.12)
            
            # Combine voiceover and background music
            final_audio = CompositeAudioClip([voiceover_audio, bg_music])
            main_video = main_video.set_audio(final_audio)
            log("Soft background music mixed successfully.")
        except Exception as e:
            log(f"Failed to mix background music: {e}. Using narration voiceover only.")
            main_video = main_video.set_audio(voiceover_audio)
    else:
        main_video = main_video.set_audio(voiceover_audio)
        
    # 6. Outro Clip
    outro_clip = load_outtro_clip()
    if outro_clip:
        log("Appending Outro clip to video.")
        final_video = concatenate_videoclips([main_video, outro_clip], method="compose")
    else:
        final_video = main_video
        
    # Write the output file
    final_video.write_videofile(
        str(output_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile=str(WORKDIR / "temp_audio_long.m4a"),
        remove_temp=True,
        preset='medium',
        ffmpeg_params=['-crf', '23', '-pix_fmt', 'yuv420p']
    )
    
    # Cleanup memory
    for clip in tts_audio_clips:
        clip.close()
    final_video.close()
    log(f"Long-form video generated successfully: {output_path}")

# ---------------- YouTube Upload ----------------
def upload_to_youtube(video_path: Path, title: str, description: str) -> str:
    """Upload landscape video to YouTube as public long-form video."""
    if not YT_REFRESH_TOKEN:
        log("YT_REFRESH_TOKEN missing. Skipping upload.")
        return ""
        
    try:
        creds = Credentials(
            token=None,
            refresh_token=YT_REFRESH_TOKEN,
            client_id=YT_CLIENT_ID,
            client_secret=YT_CLIENT_SECRET,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/youtube.upload"]
        )
        
        creds.refresh(Request())
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        
        body = {
            "snippet": {
                "title": title[:100], # YouTube limit
                "description": description,
                "tags": ["entertainment", "celebrity gossip", "news update", "hollywood", "gossiphub"],
                "categoryId": "24" # Entertainment
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }
        
        media = MediaFileUpload(str(video_path), chunksize=1024*1024, resumable=True)
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )
        
        log(f"Uploading long-form video '{title}' to YouTube...")
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log(f"Upload progress: {int(status.progress() * 100)}%")
                
        video_id = response.get("id", "")
        log(f"Video uploaded successfully! Video ID: {video_id}")
        return video_id
        
    except Exception as e:
        log(f"YouTube upload failed: {e}")
        raise

# ---------------- Cleanup Temp Files ----------------
def cleanup():
    """Remove temporary segment assets to keep workspace clean."""
    temp_files = list(WORKDIR.glob("*.mp3")) + list(WORKDIR.glob("*.png")) + list(WORKDIR.glob("pexels_*.jpg")) + list(WORKDIR.glob("article_fallback_*.jpg")) + list(WORKDIR.glob("fallback_gradient_*.jpg")) + list(WORKDIR.glob("composed_*.jpg"))
    for temp_file in temp_files:
        try:
            # Don't delete background_music.mp3 or last_titles.txt
            if "background_music" not in temp_file.name:
                temp_file.unlink()
        except Exception:
            pass

# ---------------- MAIN PIPELINE ----------------
def main():
    log("Starting Gossip Hub Long-form Video Pipeline...")
    
    try:
        # Step 1: News retrieval
        news_title, desc, img_url, article_url, lead = get_news_article()
        
        # Step 2: Generate detailed narration script via Gemini
        yt_title, segments = generate_script_with_gemini(news_title, desc, lead)
        
        # Step 3: Compile final video
        out_video = WORKDIR / "final_long_form.mp4"
        build_final_video(segments, img_url, out_video)
        
        # Step 4: Upload to YouTube
        yt_desc = f"{desc or lead}\n\nRead the full story here: {article_url}\n\nSubscribe to Gossip Hub for daily entertainment updates!"
        upload_to_youtube(out_video, yt_title, yt_desc)
        
        # Step 5: Prevent duplicate runs
        save_uploaded(news_title)
        log("Long-form video pipeline completed successfully!")
        
    except Exception as e:
        log(f"Pipeline failed: {e}")
        raise
    finally:
        cleanup()

if __name__ == "__main__":
    main()
