import os
import re
import requests
import html
import textwrap
import time
import random
from pathlib import Path
from io import BytesIO
from datetime import datetime
from typing import Optional, Tuple, List

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip,
    concatenate_audioclips, vfx
)

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ---------------- CONFIG ----------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")

YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)
LAST_FILE = WORKDIR / "last_titles.txt"

VIDEO_W, VIDEO_H = 1080, 1920
CAPTION_HEIGHT = int(VIDEO_H * 0.16)
FPS = 24
ZOOM_RATE = 0.015
BASE_FONT_SIZE = 56

# Common image extensions for fallback
COMMON_IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp', '.gif']

def log(*args):
    print("[BOT]", *args)

# ---------------- Text cleanup ----------------
def sanitize_text(s: str) -> str:
    """Clean and sanitize text."""
    if not s:
        return ""
    s = html.unescape(s)
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
    except:
        return False

def save_uploaded(title: str) -> None:
    """Save uploaded title to prevent duplicates."""
    with open(LAST_FILE, "a", encoding="utf-8") as f:
        f.write(title + "\n")

# ---------------- News fetching ----------------
def _try_newsapi_fetch() -> List[dict]:
    """Fetch news from NewsAPI."""
    try:
        url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=10&apiKey={NEWS_API_KEY}"
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
        url = f"https://gnews.io/api/v4/top-headlines?topic=entertainment&lang=en&max=10&token={GNEWS_API_KEY}"
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
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        
        # Try multiple meta description patterns
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
    
    # Check if URL ends with common image extensions
    url_lower = url.lower()
    if any(url_lower.endswith(ext) for ext in COMMON_IMAGE_EXTENSIONS):
        return True
    
    # Check if URL contains common image path patterns
    image_patterns = ['/image/', '/images/', '/photo/', '/photos/', '/img/', 'image.', 'photo.']
    if any(pattern in url_lower for pattern in image_patterns):
        return True
    
    return False

def get_news_article(max_attempts: int = 10) -> Tuple[str, str, str, str, str]:
    """Fetch a news article with a valid image URL."""
    attempts = 0
    articles = []
    
    # Collect articles from both sources
    newsapi_articles = _try_newsapi_fetch()
    gnews_articles = _try_gnews_fetch()
    articles = newsapi_articles + gnews_articles
    
    if not articles:
        raise RuntimeError("No articles fetched from any source")
    
    # Filter and shuffle articles for variety
    random.shuffle(articles)
    
    for art in articles:
        if attempts >= max_attempts:
            break
            
        try:
            title = sanitize_text(art.get("title", "Entertainment Update"))
            if not title or has_uploaded(title):
                continue
                
            img_url = art.get("urlToImage") or art.get("image")
            if not img_url or not validate_image_url(img_url):
                continue
                
            description = sanitize_text(art.get("description", ""))
            url = art.get("url") or art.get("link") or ""
            
            # Fetch lead only if we have a valid URL
            lead = ""
            if url and url.startswith(('http://', 'https://')):
                lead = fetch_article_lead(url)
                # Add small delay to avoid rate limiting
                time.sleep(0.5)
            
            log(f"Attempting article: {title[:50]}...")
            log(f"Image URL: {img_url}")
            
            return title, description, img_url, url, lead
            
        except Exception as e:
            log(f"Error processing article: {e}")
            attempts += 1
            continue
    
    raise RuntimeError("No valid articles found after max attempts")

# ---------------- AUTO EMOJI SELECTION ----------------
EMOJI_MAP = {
    "death": "🕯️💔😢",
    "killed": "🚨💥😱",
    "arrest": "🚓⚖️🔥",
    "scandal": "😳🔥📉",
    "award": "🏆✨🎉",
    "movie": "🎬🍿🔥",
    "film": "🎥✨🔥",
    "music": "🎵🎤🔥",
    "viral": "🚀🔥📱",
    "celebrity": "⭐📸🔥",
    "news": "📰🔥⚡",
    "update": "🔄📢✨",
    "breaking": "🚨⚡📢",
    "exclusive": "🔒✨🎯"
}

def select_emojis(text: str) -> str:
    """Select relevant emojis based on text content."""
    text = text.lower()
    for key, emojis in EMOJI_MAP.items():
        if key in text:
            return emojis
    return "🔥🎬✨"

# ---------------- HASHTAGS ----------------
CONTENT_HASHTAGS = {
    "movie": ["#MovieNews", "#FilmUpdate", "#Cinema"],
    "film": ["#FilmNews", "#MovieUpdate", "#Hollywood"],
    "music": ["#MusicNews", "#ArtistUpdate", "#SongRelease"],
    "award": ["#Awards", "#Oscars", "#Grammys"],
    "celebrity": ["#CelebrityNews", "#StarUpdate", "#Famous"],
    "scandal": ["#Scandal", "#Controversy", "#Exposed"],
    "arrest": ["#BreakingNews", "#Arrest", "#Legal"],
    "death": ["#RIP", "#Tribute", "#Remembering"],
    "viral": ["#Viral", "#Trending", "#Buzz"],
    "news": ["#News", "#Update", "#Latest"],
    "breaking": ["#Breaking", "#Alert", "#Emergency"]
}

DAILY_HASHTAG_POOLS = [
    ["#shorts", "#trending", "#viral", "#fyp", "#youtubeshorts"],
    ["#shorts", "#ytshorts", "#breakingnews", "#explore", "#entertainment"],
    ["#shorts", "#reels", "#hotnews", "#trendingnow", "#viralvideo"],
    ["#shorts", "#buzz", "#mustwatch", "#viralpost", "#update"],
    ["#shorts", "#news", "#media", "#currentevents", "#popculture"]
]

def select_hashtags(text: str) -> str:
    """Select relevant hashtags based on text and day."""
    day_index = datetime.utcnow().timetuple().tm_yday % len(DAILY_HASHTAG_POOLS)
    base_tags = DAILY_HASHTAG_POOLS[day_index]
    
    content_tags = []
    text_lower = text.lower()
    
    for key, tags in CONTENT_HASHTAGS.items():
        if key in text_lower:
            content_tags.extend(tags[:2])  # Take first 2 tags from each category
    
    # Remove duplicates while preserving order
    all_tags = []
    seen = set()
    for tag in content_tags + base_tags:
        if tag not in seen:
            seen.add(tag)
            all_tags.append(tag)
    
    return " ".join(all_tags[:8])  # Limit to 8 hashtags

# ---------------- VIRAL SHORTS TITLE ----------------
def generate_title(headline: str) -> str:
    """Generate a viral title for the video."""
    # Extract key words
    words = re.findall(r'\b[A-Za-z]{4,}\b', headline)
    if len(words) >= 3:
        core = words[:3]
    elif len(words) >= 2:
        core = words[:2]
    else:
        core = words[:1] if words else ["Entertainment"]
    
    title = " ".join(core).title()
    hashtags = select_hashtags(headline)
    emojis = select_emojis(headline)
    
    result = f"{title} {emojis} {hashtags}"
    return result[:100]  # YouTube title limit

# ---------------- VIRAL HOOK ----------------
def generate_hook(headline: str) -> str:
    """Generate an engaging hook for the video."""
    h = headline.lower()
    
    hooks = [
        ("death|killed|dead|passed away|rip", "This news shocked everyone."),
        ("arrest|charged|court|lawsuit|jail", "This just took a serious turn."),
        ("scandal|leak|exposed|controversy", "Nobody expected this."),
        ("award|wins|honored|nomination", "This moment made history."),
        ("marry|engaged|wedding|propose", "This love story just went viral."),
        ("break up|split|divorce|separate", "This breakup has everyone talking."),
        ("new album|release|single|song", "This release is breaking records."),
        ("movie|film|trailer|premiere", "This is the talk of Hollywood."),
    ]
    
    for pattern, hook in hooks:
        if re.search(pattern, h):
            return hook
    
    # Default hooks
    default_hooks = [
        "This story is exploding online.",
        "You won't believe what happened.",
        "This just broke the internet.",
        "Everyone is talking about this.",
        "This update just went viral."
    ]
    
    return random.choice(default_hooks)

# ---------------- IMAGE FETCHING ----------------
def fetch_image_bytes(url: str, timeout: int = 30, retries: int = 3) -> bytes:
    """Fetch image bytes with retries and proper headers."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'image',
        'Sec-Fetch-Mode': 'no-cors',
        'Sec-Fetch-Site': 'cross-site',
        'Referer': 'https://www.google.com/',
    }
    
    for attempt in range(retries):
        try:
            log(f"Fetching image (attempt {attempt + 1}/{retries}): {url}")
            resp = requests.get(url, headers=headers, timeout=timeout, stream=True)
            resp.raise_for_status()
            
            # Check content type
            content_type = resp.headers.get('Content-Type', '').lower()
            if not any(img_type in content_type for img_type in ['image/', 'octet-stream']):
                log(f"Warning: Unexpected content type: {content_type}")
                # Still try to process if it might be an image
                
            # Read content
            img_bytes = resp.content
            
            # Verify it's actually an image by trying to open it
            try:
                Image.open(BytesIO(img_bytes)).verify()
                return img_bytes
            except:
                if attempt < retries - 1:
                    log("Downloaded content is not a valid image, retrying...")
                    continue
                else:
                    raise RuntimeError("Downloaded content is not a valid image")
                    
        except requests.exceptions.RequestException as e:
            log(f"Request failed (attempt {attempt + 1}): {e}")
            if attempt < retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff
                log(f"Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
            else:
                raise
    
    raise RuntimeError(f"Failed to fetch image after {retries} attempts")

def get_fallback_image() -> Image.Image:
    """Create a fallback gradient background image."""
    img = Image.new('RGB', (VIDEO_W, VIDEO_H))
    draw = ImageDraw.Draw(img)
    
    # Create gradient
    for y in range(VIDEO_H):
        r = int(30 + (y / VIDEO_H) * 50)
        g = int(40 + (y / VIDEO_H) * 40)
        b = int(50 + (y / VIDEO_H) * 30)
        draw.line([(0, y), (VIDEO_W, y)], fill=(r, g, b))
    
    return img

# ---------------- Background preparation ----------------
def fetch_and_prepare_bg(image_url: str) -> str:
    """Download and prepare background image with fallback."""
    if not image_url:
        log("No image URL provided, using fallback")
        bg_img = get_fallback_image()
    else:
        try:
            img_bytes = fetch_image_bytes(image_url)
            img = Image.open(BytesIO(img_bytes)).convert("RGB")
            log(f"Successfully fetched image: {image_url}")
            
            # Create blurred background
            bg_img = img.resize((VIDEO_W, VIDEO_H), Image.LANCZOS).filter(
                ImageFilter.GaussianBlur(40)
            )
            
            # Calculate and paste foreground
            img_ratio = img.width / img.height
            target_ratio = VIDEO_W / VIDEO_H
            
            if img_ratio > target_ratio:
                fg_width = VIDEO_W
                fg_height = int(fg_width / img_ratio)
            else:
                fg_height = VIDEO_H
                fg_width = int(fg_height * img_ratio)
            
            fg = img.resize((fg_width, fg_height), Image.LANCZOS)
            
            x = (VIDEO_W - fg_width) // 2
            y = (VIDEO_H - fg_height) // 2
            bg_img.paste(fg, (x, y))
            
        except Exception as e:
            log(f"Error processing image from URL {image_url}: {e}")
            log("Using fallback image")
            bg_img = get_fallback_image()
    
    # Save the final image
    out_path = WORKDIR / "bg.jpg"
    bg_img.save(out_path, quality=95, optimize=True)
    log(f"Background saved to: {out_path}")
    return str(out_path)

# ---------------- Script generator ----------------
def generate_script(headline: str, description: str, lead: str) -> Tuple[str, List[str]]:
    """Generate script lines for the video."""
    lines = [
        generate_hook(headline),
        headline,
        description or lead or "Here's what we know so far.",
        "Fans are reacting fast online.",
        "Follow for more entertainment updates!"
    ]
    
    # Filter out empty lines
    lines = [line for line in lines if line.strip()]
    
    title = generate_title(headline)
    return title, lines

# ---------------- TTS ----------------
def create_tts(lines: List[str]) -> Tuple[List[str], List[float]]:
    """Generate TTS audio for each line."""
    paths = []
    durations = []
    
    for i, line in enumerate(lines):
        try:
            out_path = WORKDIR / f"tts_{i}.mp3"
            
            # Add small delay between TTS requests
            if i > 0:
                time.sleep(0.5)
            
            # Generate TTS with error handling
            tts = gTTS(text=line, lang='en', slow=False)
            tts.save(str(out_path))
            
            # Verify audio file was created
            if out_path.exists() and out_path.stat().st_size > 0:
                clip = AudioFileClip(str(out_path))
                durations.append(clip.duration + 0.3)  # Add small pause
                clip.close()
                paths.append(str(out_path))
                log(f"Created TTS for line {i}: {line[:50]}...")
            else:
                raise RuntimeError(f"TTS file was not created properly for line {i}")
                
        except Exception as e:
            log(f"Error creating TTS for line {i}: {e}")
            # Use a fallback: create silent audio
            out_path = WORKDIR / f"tts_{i}.mp3"
            # Create minimal silent audio (1 second)
            silent_clip = AudioFileClip(str(WORKDIR / "silent.mp3")) if (WORKDIR / "silent.mp3").exists() else None
            if silent_clip:
                durations.append(1.0)
                paths.append(str(WORKDIR / "silent.mp3"))
            else:
                # Estimate duration based on word count
                estimated_duration = max(2.0, len(line.split()) * 0.5)
                durations.append(estimated_duration)
                paths.append(str(out_path))
    
    return paths, durations

# ---------------- Captions ----------------
def create_caption(text: str, index: int) -> Path:
    """Create caption image for text."""
    # Try multiple font paths
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "Arial",  # Will use PIL's default if others fail
    ]
    
    font = None
    for font_path in font_paths:
        try:
            font = ImageFont.truetype(font_path, BASE_FONT_SIZE)
            break
        except:
            continue
    
    if font is None:
        font = ImageFont.load_default()
    
    # Create caption image
    img = Image.new("RGBA", (VIDEO_W, CAPTION_HEIGHT), (0, 0, 0, 200))
    draw = ImageDraw.Draw(img)
    
    # Wrap text
    max_chars_per_line = 35
    wrapped_lines = textwrap.wrap(text, width=max_chars_per_line)
    
    # Calculate text position
    line_height = BASE_FONT_SIZE + 10
    total_height = len(wrapped_lines) * line_height
    y_start = (CAPTION_HEIGHT - total_height) // 2
    
    # Draw each line
    for i, line in enumerate(wrapped_lines):
        # Calculate text width for centering
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        x = (VIDEO_W - text_width) // 2
        y = y_start + i * line_height
        
        # Draw text with shadow for better readability
        draw.text((x+2, y+2), line, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), line, font=font, fill="white")
    
    out_path = WORKDIR / f"cap_{index}.png"
    img.save(out_path)
    return out_path

# ---------------- Video build ----------------
def build_video(bg_path: str, lines: List[str], tts_paths: List[str], 
                durations: List[float], output_path: Path) -> None:
    """Build the final video with effects."""
    total_duration = sum(durations) + 1.0  # Add extra second
    
    # Create background clip with zoom effect
    bg_clip = ImageClip(bg_path).set_duration(total_duration).fx(
        vfx.resize, lambda t: 1.05 + ZOOM_RATE * t
    )
    
    # Create foreground clip with different zoom
    fg_clip = ImageClip(bg_path).set_duration(total_duration).fx(
        vfx.resize, lambda t: 1.02 + (ZOOM_RATE / 2) * t
    )
    
    # Create caption clips
    caption_clips = []
    current_time = 0
    
    for i, (line, duration) in enumerate(zip(lines, durations)):
        if i < len(tts_paths):  # Safety check
            cap_img_path = create_caption(line, i)
            cap_clip = ImageClip(str(cap_img_path)).set_duration(duration).set_start(current_time)
            cap_clip = cap_clip.set_position(("center", VIDEO_H * 0.78))
            caption_clips.append(cap_clip)
            current_time += duration
    
    # Load and concatenate audio
    audio_clips = []
    for tts_path in tts_paths:
        if Path(tts_path).exists():
            try:
                audio_clip = AudioFileClip(tts_path)
                audio_clips.append(audio_clip)
            except Exception as e:
                log(f"Error loading audio {tts_path}: {e}")
                continue
    
    if audio_clips:
        final_audio = concatenate_audioclips(audio_clips)
    else:
        # Create silent audio if no TTS files
        from moviepy.audio.AudioClip import AudioClip
        final_audio = AudioClip(lambda t: 0, duration=total_duration)
    
    # Composite all clips
    final_video = CompositeVideoClip([bg_clip, fg_clip] + caption_clips)
    final_video = final_video.set_audio(final_audio)
    
    # Write video file with compression
    final_video.write_videofile(
        str(output_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile=str(WORKDIR / "temp_audio.m4a"),
        remove_temp=True,
        preset='medium',
        ffmpeg_params=['-crf', '23', '-pix_fmt', 'yuv420p']
    )
    
    # Clean up
    for clip in audio_clips:
        clip.close()
    final_video.close()
    log(f"Video created: {output_path}")

# ---------------- YouTube upload ----------------
def upload_to_youtube(video_path: Path, title: str, description: str) -> None:
    """Upload video to YouTube."""
    try:
        creds = Credentials(
            token=None,
            refresh_token=YT_REFRESH_TOKEN,
            client_id=YT_CLIENT_ID,
            client_secret=YT_CLIENT_SECRET,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/youtube.upload"]
        )
        
        # Refresh token
        creds.refresh(Request())
        
        # Build YouTube service
        youtube = build("youtube", "v3", credentials=creds)
        
        # Prepare video metadata
        body = {
            "snippet": {
                "title": title,
                "description": f"{description}\n\n{select_hashtags(title)}",
                "tags": ["shorts", "ytshorts", "entertainment", "news", "celebrity", "viral"],
                "categoryId": "24"  # Entertainment category
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }
        
        # Upload video
        media = MediaFileUpload(str(video_path), chunksize=1024*1024, resumable=True)
        
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )
        
        response = request.execute()
        log(f"Video uploaded successfully! Video ID: {response.get('id')}")
        
    except Exception as e:
        log(f"YouTube upload failed: {e}")
        raise

# ---------------- MAIN ----------------
def main():
    """Main execution function."""
    log("Starting pipeline...")
    
    try:
        # Step 1: Fetch news article
        title, desc, img_url, url, lead = get_news_article()
        log(f"Selected article: {title}")
        log(f"Description: {desc[:100]}...")
        
        # Step 2: Generate script
        yt_title, lines = generate_script(title, desc, lead)
        log(f"YouTube title: {yt_title}")
        log(f"Script lines: {len(lines)}")
        
        # Step 3: Prepare background
        bg_path = fetch_and_prepare_bg(img_url)
        
        # Step 4: Create TTS
        tts_paths, durations = create_tts(lines)
        
        # Step 5: Build video
        output_path = WORKDIR / "final.mp4"
        build_video(bg_path, lines, tts_paths, durations, output_path)
        
        # Step 6: Upload to YouTube
        upload_to_youtube(output_path, yt_title, desc or lead)
        
        # Step 7: Save uploaded title
        save_uploaded(title)
        
        log("Pipeline completed successfully!")
        
    except Exception as e:
        log(f"Pipeline failed: {e}")
        raise

# ---------------- Cleanup ----------------
def cleanup():
    """Clean up temporary files."""
    temp_files = list(WORKDIR.glob("*.mp3")) + list(WORKDIR.glob("*.png"))
    for temp_file in temp_files:
        try:
            temp_file.unlink()
        except:
            pass

if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
