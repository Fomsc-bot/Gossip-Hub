#!/usr/bin/env python3
"""
YouTube Shorts Automator - Full Version for GitHub Actions
Automatically creates and uploads YouTube Shorts from news articles
"""

import os
import re
import sys
import json
import time
import random
import requests
import html
import subprocess
from pathlib import Path
from io import BytesIO
from datetime import datetime
import textwrap

# Check if running on GitHub Actions
ON_GITHUB_ACTIONS = os.getenv('GITHUB_ACTIONS') == 'true'

# Import with fallback for GitHub Actions
try:
    from gtts import gTTS
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    from moviepy.editor import (
        ImageClip, AudioFileClip, CompositeVideoClip,
        concatenate_audioclips, concatenate_videoclips, vfx, VideoFileClip,
        CompositeAudioClip, ColorClip
    )
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
except ImportError as e:
    print(f"Import error: {e}")
    if ON_GITHUB_ACTIONS:
        print("Installing missing packages...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", 
                              "gtts", "Pillow", "moviepy", "google-api-python-client"])
        # Retry imports
        from gtts import gTTS
        from PIL import Image, ImageDraw, ImageFont, ImageOps
        from moviepy.editor import (
            ImageClip, AudioFileClip, CompositeVideoClip,
            concatenate_audioclips, concatenate_videoclips, vfx, VideoFileClip,
            CompositeAudioClip, ColorClip
        )
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    else:
        raise

# ---------------- Gemini AI Integration ----------------
genai = None
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY and GEMINI_API_KEY not in ["", "your_gemini_api_key_here"]:
    try:
        import google.generativeai as genai_lib
        genai = genai_lib
        genai.configure(api_key=GEMINI_API_KEY)
        print("Gemini AI configured successfully")
    except ImportError:
        print("Warning: google.generativeai not available")
    except Exception as e:
        print(f"Warning: Gemini configuration failed: {e}")

# ---------------- CONFIG ----------------
# API Keys from environment variables
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY", "")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")

# Work directory setup
if ON_GITHUB_ACTIONS:
    WORKDIR = Path("/tmp/work")  # Use tmp directory on GitHub Actions
else:
    WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True, parents=True)

LAST_FILE = WORKDIR / "last_titles.txt"

# Video settings for YouTube Shorts (vertical format)
VIDEO_W, VIDEO_H = 1080, 1920  # 9:16 aspect ratio
CAPTION_HEIGHT = int(VIDEO_H * 0.12)
ZOOM_RATE = 0.008  # Slow zoom effect
FPS = 30
BASE_FONT_SIZE = 68
SMALL_FONT_SIZE = 48
MAX_CAPTION_CHARS = 45  # Characters per line

# Directory paths
OUTTRO_DIR = Path("outtro") if not ON_GITHUB_ACTIONS else Path("/tmp/outtro")
MUSIC_DIR = Path("music") if not ON_GITHUB_ACTIONS else Path("/tmp/music")

# Content niches to focus on
CONTENT_NICHES = [
    "celebrity_lifestyle",
    "entertainment_news", 
    "movie_updates",
    "tv_shows",
    "social_media_trends"
]

# ---------------- HELPER FUNCTIONS ----------------
def log(*args, **kwargs):
    """Enhanced logging function"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    message = ' '.join(str(arg) for arg in args)
    print(f"[{timestamp}] {message}", **kwargs)
    sys.stdout.flush()

def setup_fonts():
    """Setup fonts for GitHub Actions"""
    if ON_GITHUB_ACTIONS:
        log("Setting up fonts for GitHub Actions...")
        try:
            # Install fonts
            subprocess.run(['apt-get', 'update'], capture_output=True, check=False)
            subprocess.run(['apt-get', 'install', '-y', 'fonts-dejavu-core', 
                          'fonts-liberation', 'fonts-liberation2'], 
                         capture_output=True, check=False)
            log("Fonts installed successfully")
        except Exception as e:
            log(f"Font installation error: {e}")

def get_font(font_size=BASE_FONT_SIZE):
    """Get font for the current platform"""
    font_paths = [
        # Ubuntu paths
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        # macOS paths
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        # Windows paths
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    
    for path in font_paths:
        if os.path.exists(path):
            try:
                if path.endswith('.ttc'):  # TrueType Collection
                    return ImageFont.truetype(path, font_size, index=0)
                else:
                    return ImageFont.truetype(path, font_size)
            except Exception as e:
                log(f"Error loading font {path}: {e}")
                continue
    
    # Fallback to default font
    log("No system font found, using default")
    return ImageFont.load_default()

# ---------------- TEXT PROCESSING ----------------
def sanitize_text(s: str) -> str:
    """Clean and sanitize text for display and TTS"""
    if not s or not isinstance(s, str):
        return ""
    
    # Decode HTML entities
    s = html.unescape(s)
    
    # Remove special characters and codes
    s = re.sub(r'&[#A-Za-z0-9]+;?', ' ', s)
    s = re.sub(r'[^\x00-\x7F]+', ' ', s)  # Remove non-ASCII
    s = re.sub(r'\s+([.,!?])', r'\1', s)  # Remove space before punctuation
    s = re.sub(r'\s+', ' ', s).strip()  # Normalize whitespace
    
    # Clean up news source references
    s = re.sub(r'\b(via|source|according to|reports?):?\s+', '', s, flags=re.I)
    s = re.sub(r'\s*-\s*(Reuters|AP|CNN|BBC|Yahoo)\b', '', s, flags=re.I)
    
    return s[:500]  # Limit length

# ---------------- DUPLICATE FILTER ----------------
def has_uploaded(title):
    """Check if similar content has already been uploaded"""
    if not LAST_FILE.exists():
        return False
    
    try:
        with open(LAST_FILE, "r", encoding="utf-8") as f:
            existing_titles = f.read().lower().split('\n')
        
        title_lower = title.strip().lower()
        title_words = set(re.findall(r'\b\w+\b', title_lower))
        
        for existing_title in existing_titles:
            if not existing_title:
                continue
            existing_words = set(re.findall(r'\b\w+\b', existing_title))
            common_words = title_words.intersection(existing_words)
            
            # If more than 3 significant words match
            if len(common_words) >= min(3, len(title_words) // 2):
                return True
    
    except Exception as e:
        log(f"Error checking duplicates: {e}")
    
    return False

def save_uploaded(title):
    """Save uploaded title to prevent duplicates"""
    try:
        with open(LAST_FILE, "a", encoding="utf-8") as f:
            f.write(title.strip() + "\n")
        log(f"Saved title to history: {title[:50]}...")
    except Exception as e:
        log(f"Error saving title: {e}")

# ---------------- NEWS FETCHING ----------------
def get_high_quality_article():
    """Fetch trending entertainment/celebrity news from available sources"""
    log("Fetching news article...")
    
    articles_to_try = []
    
    # 1. Try NewsAPI
    if NEWS_API_KEY and NEWS_API_KEY not in ["", "your_newsapi_key_here"]:
        try:
            url = "https://newsapi.org/v2/top-headlines"
            params = {
                "category": "entertainment",
                "pageSize": 10,
                "apiKey": NEWS_API_KEY,
                "country": "us",
                "language": "en"
            }
            response = requests.get(url, params=params, timeout=20)
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") == "ok":
                articles = data.get("articles", [])
                for article in articles:
                    title = sanitize_text(article.get("title", ""))
                    description = sanitize_text(article.get("description", ""))
                    
                    if title and description and len(title) > 20 and len(description) > 30:
                        articles_to_try.append({
                            "title": title,
                            "description": description,
                            "content": "",
                            "image_url": article.get("urlToImage", ""),
                            "source_url": article.get("url", ""),
                            "source": "NewsAPI"
                        })
                log(f"Found {len(articles_to_try)} articles from NewsAPI")
        except Exception as e:
            log(f"NewsAPI error: {e}")
    
    # 2. Try GNews
    if GNEWS_API_KEY and GNEWS_API_KEY not in ["", "your_gnews_api_key_here"] and not articles_to_try:
        try:
            url = "https://gnews.io/api/v4/top-headlines"
            params = {
                "token": GNEWS_API_KEY,
                "lang": "en",
                "max": 10,
                "topic": "entertainment"
            }
            response = requests.get(url, params=params, timeout=20)
            response.raise_for_status()
            data = response.json()
            
            articles = data.get("articles", [])
            for article in articles:
                title = sanitize_text(article.get("title", ""))
                description = sanitize_text(article.get("description", ""))
                content = sanitize_text(article.get("content", ""))
                
                if title and description and len(title) > 15:
                    articles_to_try.append({
                        "title": title,
                        "description": description,
                        "content": content[:300] if content else description,
                        "image_url": article.get("image", ""),
                        "source_url": article.get("url", ""),
                        "source": "GNews"
                    })
            log(f"Found {len(articles_to_try)} articles from GNews")
        except Exception as e:
            log(f"GNews error: {e}")
    
    # 3. Process articles and check for duplicates
    random.shuffle(articles_to_try)  # Randomize selection
    
    for article in articles_to_try:
        if not has_uploaded(article["title"]):
            log(f"Selected article: {article['title'][:60]}...")
            return article
    
    # 4. Fallback if no articles found
    log("No suitable articles found, using fallback")
    entertainment_topics = [
        "Breaking celebrity news today",
        "Latest Hollywood updates",
        "Entertainment industry news",
        "Movie release announcements",
        "TV show updates and rumors"
    ]
    
    topic = random.choice(entertainment_topics)
    return {
        "title": f"{topic} - Entertainment Update",
        "description": "Stay tuned for the latest news in entertainment. More details coming soon.",
        "content": "",
        "image_url": "",
        "source_url": "",
        "source": "Fallback"
    }

# ---------------- IMAGE FETCHING ----------------
def fetch_and_save_images(query, count=3):
    """Fetch images from APIs and save them locally"""
    log(f"Fetching images for query: {query}")
    image_paths = []
    
    # Clean query for API
    clean_query = re.sub(r'[^\w\s]', '', query)[:50]
    
    # 1. Try Pexels
    if PEXELS_API_KEY and PEXELS_API_KEY not in ["", "your_pexels_api_key_here"]:
        try:
            url = "https://api.pexels.com/v1/search"
            headers = {"Authorization": PEXELS_API_KEY}
            params = {
                "query": clean_query,
                "per_page": count,
                "orientation": "portrait",
                "size": "large"
            }
            response = requests.get(url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            photos = data.get("photos", [])
            for i, photo in enumerate(photos[:count]):
                try:
                    img_url = photo["src"]["large"]
                    response = requests.get(img_url, timeout=10)
                    response.raise_for_status()
                    
                    img_path = WORKDIR / f"pexels_{i}.jpg"
                    with open(img_path, 'wb') as f:
                        f.write(response.content)
                    
                    image_paths.append(str(img_path))
                    log(f"Downloaded image {i+1} from Pexels")
                except Exception as e:
                    log(f"Error downloading Pexels image {i}: {e}")
        except Exception as e:
            log(f"Pexels API error: {e}")
    
    # 2. Fallback to Unsplash
    if not image_paths:
        try:
            for i in range(count):
                unsplash_url = f"https://source.unsplash.com/featured/{VIDEO_W}x{VIDEO_H}/?{clean_query},celebrity"
                response = requests.get(unsplash_url, timeout=10)
                response.raise_for_status()
                
                img_path = WORKDIR / f"unsplash_{i}.jpg"
                with open(img_path, 'wb') as f:
                    f.write(response.content)
                
                image_paths.append(str(img_path))
                log(f"Downloaded image {i+1} from Unsplash")
        except Exception as e:
            log(f"Unsplash error: {e}")
    
    # 3. Create fallback background if no images
    if not image_paths:
        try:
            # Create gradient background
            img = Image.new('RGB', (VIDEO_W, VIDEO_H), color=(30, 30, 50))
            draw = ImageDraw.Draw(img)
            
            # Add gradient effect
            for y in range(VIDEO_H):
                r = int(30 + (y / VIDEO_H) * 50)
                g = int(30 + (y / VIDEO_H) * 30)
                b = int(50 + (y / VIDEO_H) * 70)
                draw.line([(0, y), (VIDEO_W, y)], fill=(r, g, b))
            
            fallback_path = WORKDIR / "fallback_bg.jpg"
            img.save(fallback_path)
            image_paths.append(str(fallback_path))
            log("Created fallback background")
        except Exception as e:
            log(f"Error creating fallback background: {e}")
    
    return image_paths

# ---------------- AI CONTENT ENHANCEMENT ----------------
def enhance_content_with_gemini(headline, description, niche=""):
    """Use Gemini AI to create engaging content"""
    if not genai:
        return None
    
    try:
        prompt = f"""Create an engaging 25-35 second YouTube Shorts script about this entertainment news:
        
        HEADLINE: {headline}
        DETAILS: {description}
        
        Requirements:
        1. Start with a HOOK (question or surprising statement)
        2. Provide 2-3 key points about the story
        3. Keep each line short (under 50 characters if possible)
        4. End with a call-to-action
        
        Format: Return as a simple JSON object with:
        - "hook": First line to grab attention
        - "points": Array of 2-3 story points
        - "cta": Call to action line
        
        Make it engaging, conversational, and suitable for YouTube Shorts."""
        
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(prompt)
        
        try:
            # Try to parse JSON response
            content = json.loads(response.text.strip())
            
            # Validate structure
            if isinstance(content, dict) and "hook" in content:
                log("Successfully generated AI-enhanced content")
                return content
        except json.JSONDecodeError:
            # If not JSON, parse lines
            lines = [line.strip() for line in response.text.strip().split('\n') if line.strip()]
            if len(lines) >= 3:
                return {
                    "hook": lines[0],
                    "points": lines[1:-1],
                    "cta": lines[-1] if lines[-1].lower().startswith(("subscribe", "follow", "like")) else "Subscribe for more updates!"
                }
    
    except Exception as e:
        log(f"Gemini AI error: {e}")
    
    return None

# ---------------- SCRIPT GENERATION ----------------
def generate_engaging_script(article_data, niche=None):
    """Create engaging script for the video"""
    title = article_data["title"]
    description = article_data["description"]
    
    log("Generating script...")
    
    # Try AI enhancement first
    enhanced = enhance_content_with_gemini(title, description, niche)
    
    if enhanced and isinstance(enhanced, dict):
        script_lines = [enhanced["hook"]]
        
        if "points" in enhanced and enhanced["points"]:
            script_lines.extend(enhanced["points"][:3])  # Take up to 3 points
        
        script_lines.append(enhanced.get("cta", "Subscribe for more updates!"))
        script_lines.append("Turn on notifications! 🔔")
        
        log(f"Generated AI-enhanced script with {len(script_lines)} lines")
        return script_lines
    
    # Fallback templates
    templates = [
        # Template 1: Question-based
        [
            f"Did you hear about {title.split()[0]}?",
            "This just broke in Hollywood...",
            f"{description[:80]}{'...' if len(description) > 80 else ''}",
            "Everyone is talking about this!",
            "What do YOU think? Comment below! 👇",
            "Subscribe for daily updates! 👍"
        ],
        # Template 2: Breaking news style
        [
            f"BREAKING NEWS: {title[:60]}",
            "Here's what just happened...",
            f"{description[:100]}{'...' if len(description) > 100 else ''}",
            "This is HUGE news!",
            "Want more updates like this?",
            "Hit that SUBSCRIBE button! 🔔"
        ],
        # Template 3: Casual style
        [
            f"Okay, so {title.split()[0]} just did this...",
            "You won't believe what happened!",
            f"{description[:90]}{'...' if len(description) > 90 else ''}",
            "This is going viral right now!",
            "What's your take on this?",
            "Follow for more tea! ☕"
        ]
    ]
    
    selected_template = random.choice(templates)
    log(f"Generated template-based script with {len(selected_template)} lines")
    return selected_template

# ---------------- TEXT-TO-SPEECH ----------------
def create_tts_with_emphasis(lines):
    """Create TTS audio with proper pacing"""
    log("Creating text-to-speech audio...")
    tts_paths = []
    durations = []
    
    for i, line in enumerate(lines):
        out_path = WORKDIR / f"tts_{i:02d}.mp3"
        
        try:
            # Clean line for better TTS
            clean_line = line.strip()
            if not clean_line:
                clean_line = "Next point."
            
            # Add pauses for punctuation
            clean_line = clean_line.replace('!', '. ')
            clean_line = clean_line.replace('?', '. ')
            clean_line = re.sub(r'\.+', '. ', clean_line)
            
            # Create TTS
            tts = gTTS(
                text=clean_line,
                lang='en',
                slow=False,
                lang_check=False
            )
            tts.save(str(out_path))
            
            # Get duration
            audio = AudioFileClip(str(out_path))
            dur = audio.duration
            durations.append(dur + 0.15)  # Add small pause between lines
            audio.close()
            
            tts_paths.append(str(out_path))
            log(f"Created TTS line {i+1}: {dur:.2f}s")
            
        except Exception as e:
            log(f"Error creating TTS for line {i}: {e}")
            # Create silent audio as fallback
            silent_path = WORKDIR / f"silent_{i:02d}.mp3"
            silent_audio = AudioFileClip.silent(duration=2.0)
            silent_audio.write_audiofile(str(silent_path))
            silent_audio.close()
            
            tts_paths.append(str(silent_path))
            durations.append(2.0)
    
    return tts_paths, durations

# ---------------- CAPTION RENDERING ----------------
def render_modern_caption(text, index):
    """Render styled captions for video"""
    text = sanitize_text(text)
    
    # Split text into lines
    words = text.split()
    lines = []
    current_line = []
    
    for word in words:
        current_line.append(word)
        current_text = ' '.join(current_line)
        
        if len(current_text) > MAX_CAPTION_CHARS:
            if len(current_line) > 1:
                lines.append(' '.join(current_line[:-1]))
                current_line = [word]
            else:
                lines.append(word)
                current_line = []
    
    if current_line:
        lines.append(' '.join(current_line))
    
    # Limit to 3 lines max
    lines = lines[:3]
    
    # Calculate image size
    line_height = 75
    padding = 30
    total_height = (line_height * len(lines)) + (padding * 2)
    
    # Create caption image with semi-transparent background
    img = Image.new('RGBA', (VIDEO_W, total_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Create rounded rectangle background
    bg_color = (0, 0, 0, 200)
    draw.rounded_rectangle(
        [(10, 10), (VIDEO_W - 10, total_height - 10)],
        radius=20,
        fill=bg_color
    )
    
    # Get font
    font = get_font(BASE_FONT_SIZE - 4)
    
    # Draw each line
    for i, line in enumerate(lines):
        y_pos = padding + (i * line_height) + (line_height // 2)
        
        # Text shadow for readability
        shadow_offset = 2
        draw.text(
            (VIDEO_W // 2 + shadow_offset, y_pos + shadow_offset),
            line,
            font=font,
            fill=(0, 0, 0, 220),
            anchor="mm"
        )
        
        # Main text
        draw.text(
            (VIDEO_W // 2, y_pos),
            line,
            font=font,
            fill=(255, 255, 255, 255),
            anchor="mm"
        )
    
    out_path = WORKDIR / f"caption_{index:02d}.png"
    img.save(out_path, "PNG")
    
    return str(out_path), total_height

# ---------------- VIDEO CREATION ----------------
def create_video_with_transitions(bg_images, script_lines, tts_paths, durations, out_file):
    """Create the final video with all components"""
    log("Creating final video...")
    
    total_duration = sum(durations)
    log(f"Total video duration: {total_duration:.2f} seconds")
    
    # Prepare background clips
    image_clips = []
    
    if len(bg_images) > 1:
        # Multiple images with transitions
        duration_per_image = total_duration / len(bg_images)
        log(f"Using {len(bg_images)} images, {duration_per_image:.2f}s each")
        
        for i, img_path in enumerate(bg_images):
            try:
                img_clip = ImageClip(img_path).set_duration(duration_per_image)
                
                # Alternate zoom directions
                if i % 2 == 0:
                    img_clip = img_clip.fx(vfx.resize, lambda t: 1 + (ZOOM_RATE * t))
                else:
                    img_clip = img_clip.fx(vfx.resize, lambda t: 1 + (ZOOM_RATE * (duration_per_image - t)))
                
                img_clip = img_clip.set_start(i * duration_per_image)
                image_clips.append(img_clip)
                
            except Exception as e:
                log(f"Error loading image {img_path}: {e}")
                continue
    else:
        # Single image with zoom
        try:
            img_clip = ImageClip(bg_images[0]).set_duration(total_duration)
            img_clip = img_clip.fx(vfx.resize, lambda t: 1 + (ZOOM_RATE * t))
            image_clips.append(img_clip)
        except:
            # Fallback to black background
            img_clip = ColorClip((VIDEO_W, VIDEO_H), color=(0, 0, 0)).set_duration(total_duration)
            image_clips.append(img_clip)
    
    # Create background video
    if len(image_clips) > 1:
        bg_video = concatenate_videoclips(image_clips, method="compose", padding=-0.3)
    else:
        bg_video = image_clips[0]
    
    # Add captions
    caption_clips = []
    current_time = 0
    
    for i, (line, dur) in enumerate(zip(script_lines, durations)):
        try:
            caption_path, caption_height = render_modern_caption(line, i)
            caption = ImageClip(caption_path).set_duration(dur).set_start(current_time)
            
            # Position caption (slightly different positions for variety)
            y_offset = random.choice([0.72, 0.75, 0.78])  # Vary vertical position
            y_pos = int(VIDEO_H * y_offset)
            
            caption = caption.set_position(("center", y_pos - caption_height//2))
            
            # Add fade in effect
            if i == 0:
                caption = caption.crossfadein(0.3)
            
            caption_clips.append(caption)
            log(f"Added caption {i+1}: {line[:30]}...")
            
        except Exception as e:
            log(f"Error creating caption {i}: {e}")
        
        current_time += dur
    
    # Create audio track
    audio_clips = []
    for p in tts_paths:
        try:
            audio_clips.append(AudioFileClip(p))
        except Exception as e:
            log(f"Error loading audio {p}: {e}")
    
    if audio_clips:
        main_audio = concatenate_audioclips(audio_clips)
    else:
        main_audio = AudioFileClip.silent(duration=total_duration)
    
    # Add background music if available
    bg_music_path = get_background_music()
    if bg_music_path and os.path.exists(bg_music_path):
        try:
            bg_music = AudioFileClip(bg_music_path)
            # Adjust volume and duration
            bg_music = bg_music.volumex(0.15)
            if bg_music.duration < total_duration:
                bg_music = bg_music.loop(duration=total_duration)
            else:
                bg_music = bg_music.subclip(0, total_duration)
            
            main_audio = CompositeAudioClip([main_audio, bg_music])
            log("Added background music")
        except Exception as e:
            log(f"Error adding background music: {e}")
    
    # Create final video composite
    if caption_clips:
        final_video = CompositeVideoClip([bg_video] + caption_clips, size=(VIDEO_W, VIDEO_H))
    else:
        final_video = bg_video
    
    final_video = final_video.set_audio(main_audio)
    final_video = final_video.set_fps(FPS)
    
    # Write video file (optimized for GitHub Actions)
    log("Writing video file...")
    
    try:
        ffmpeg_params = [
            "-c:v", "libx264",
            "-preset", "fast" if ON_GITHUB_ACTIONS else "medium",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p"
        ]
        
        final_video.write_videofile(
            str(out_file),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            threads=2 if ON_GITHUB_ACTIONS else 4,
            ffmpeg_params=ffmpeg_params,
            verbose=False,
            logger=None
        )
        
        log(f"Video created successfully: {out_file}")
        log(f"File size: {os.path.getsize(out_file) / (1024*1024):.2f} MB")
        
    except Exception as e:
        log(f"Error writing video file: {e}")
        return None
    
    return str(out_file)

def get_background_music():
    """Get background music file path"""
    # For GitHub Actions, you can add music files to your repo
    music_files = [
        "music/background.mp3",
        "music/background1.mp3",
        "music/background2.mp3"
    ]
    
    for file_path in music_files:
        if os.path.exists(file_path):
            return file_path
    
    return None

# ---------------- METADATA GENERATION ----------------
def create_youtube_metadata(title, description):
    """Create engaging YouTube title and description"""
    # Extract main subject from title
    words = re.findall(r'\b[A-Z][a-z]+\b', title)
    main_subject = words[0] if words else "This"
    
    # Title templates
    title_templates = [
        f"Did {main_subject} Really Do This? 🤔",
        f"Why {main_subject} Is Trending Right Now! 🔥",
        f"BREAKING: {title[:45]}...",
        f"SHOCKING News About {main_subject}! 😱",
        f"You Won't Believe What {main_subject} Just Did!",
        f"{main_subject} Just Dropped This Bombshell! 💣"
    ]
    
    youtube_title = random.choice(title_templates)
    
    # Create description with hashtags
    hashtags = [
        "#shorts", "#celebritynews", "#hollywood",
        "#entertainment", "#breakingnews", "#viral",
        "#trending", "#youtubeshorts", "#news",
        "#update", "#celebrity", "#gossip"
    ]
    
    random.shuffle(hashtags)
    selected_hashtags = hashtags[:8]
    
    description_text = f"""🎬 {description[:120]}...

👉 FOLLOW for daily entertainment updates!
🔥 Turn on notifications so you never miss breaking news!

💬 What's your take on this? Comment below!

{" ".join(selected_hashtags)}

#shortsfeed #entertainmentnews #viralshorts

📌 Note: Content aggregated from various news sources.
⚠️ Disclaimer: For entertainment purposes only."""

    return youtube_title, description_text

# ---------------- YOUTUBE UPLOAD ----------------
def upload_to_youtube(video_file, title, description):
    """Upload video to YouTube"""
    if not all([YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN]):
        log("YouTube credentials not configured, skipping upload")
        return None
    
    if not os.path.exists(video_file):
        log(f"Video file not found: {video_file}")
        return None
    
    try:
        log("Preparing YouTube upload...")
        
        # Create credentials
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
                "title": title[:100],  # YouTube title limit
                "description": description[:5000],  # YouTube description limit
                "tags": ["shorts", "news", "entertainment", "celebrity", "youtubeshorts"],
                "categoryId": "24"  # Entertainment category
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
                "madeForKids": False
            }
        }
        
        # Check file size
        file_size = os.path.getsize(video_file)
        log(f"Video file size: {file_size / (1024*1024):.2f} MB")
        
        if file_size > 128 * 1024 * 1024:  # 128MB limit for YouTube
            log("Warning: Video file may be too large for YouTube upload")
        
        # Create media upload
        media = MediaFileUpload(
            video_file,
            mimetype='video/mp4',
            resumable=True,
            chunksize=1024*1024  # 1MB chunks
        )
        
        # Insert video
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )
        
        # Execute upload
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                log(f"Upload progress: {progress}%")
        
        video_id = response["id"]
        log(f"✅ Upload successful! Video ID: {video_id}")
        log(f"🎥 Video URL: https://youtu.be/{video_id}")
        
        return video_id
        
    except Exception as e:
        log(f"❌ YouTube upload failed: {e}")
        return None

# ---------------- MAIN EXECUTION ----------------
def main():
    """Main execution function"""
    log("=" * 60)
    log("YouTube Shorts Automator")
    log(f"Running on GitHub Actions: {ON_GITHUB_ACTIONS}")
    log("=" * 60)
    
    # Setup fonts for GitHub Actions
    if ON_GITHUB_ACTIONS:
        setup_fonts()
    
    try:
        # Step 1: Get article
        log("\n📰 Step 1: Fetching news article...")
        article = get_high_quality_article()
        
        if not article:
            log("❌ No article found, exiting...")
            return
        
        log(f"📝 Article found: {article['title'][:80]}...")
        
        # Step 2: Check for duplicates
        log("\n🔍 Step 2: Checking for duplicates...")
        if has_uploaded(article['title']):
            log("⏭️ Similar content already uploaded, skipping...")
            return
        
        # Step 3: Generate script
        log("\n✍️ Step 3: Generating script...")
        niche = random.choice(CONTENT_NICHES)
        script_lines = generate_engaging_script(article, niche)
        
        for i, line in enumerate(script_lines):
            log(f"  Line {i+1}: {line[:50]}...")
        
        # Step 4: Get images
        log("\n🖼️ Step 4: Fetching images...")
        search_query = re.sub(r'[^\w\s]', '', article['title'].split()[0])[:20]
        image_paths = fetch_and_save_images(search_query, count=2)
        log(f"  Downloaded {len(image_paths)} images")
        
        # Step 5: Create TTS
        log("\n🔊 Step 5: Creating text-to-speech...")
        tts_paths, durations = create_tts_with_emphasis(script_lines)
        total_duration = sum(durations)
        log(f"  Total audio duration: {total_duration:.2f} seconds")
        
        # Step 6: Create video
        log("\n🎬 Step 6: Creating video...")
        out_video = WORKDIR / "final_video.mp4"
        video_path = create_video_with_transitions(
            image_paths, script_lines, tts_paths, durations, out_video
        )
        
        if not video_path or not os.path.exists(video_path):
            log("❌ Video creation failed!")
            return
        
        # Step 7: Create metadata
        log("\n🏷️ Step 7: Creating metadata...")
        youtube_title, youtube_description = create_youtube_metadata(
            article['title'], article['description']
        )
        
        log(f"  YouTube Title: {youtube_title}")
        log(f"  Description preview: {youtube_description[:100]}...")
        
        # Step 8: Upload to YouTube
        log("\n⬆️ Step 8: Uploading to YouTube...")
        video_id = upload_to_youtube(video_path, youtube_title, youtube_description)
        
        if video_id:
            save_uploaded(article['title'])
            log("\n✅ SUCCESS! Video uploaded to YouTube!")
            log(f"   Title: {youtube_title}")
            log(f"   Video ID: {video_id}")
        else:
            log("\n⚠️ Upload failed - video saved locally")
            if not ON_GITHUB_ACTIONS:
                # Save locally for debugging
                backup_dir = Path("videos")
                backup_dir.mkdir(exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = backup_dir / f"video_{timestamp}.mp4"
                
                import shutil
                shutil.copy2(video_path, backup_path)
                log(f"  Video saved to: {backup_path}")
        
        # Cleanup
        log("\n🧹 Cleaning up temporary files...")
        try:
            for file in WORKDIR.glob("*"):
                if file.is_file():
                    file.unlink()
            log("  Cleanup complete")
        except Exception as e:
            log(f"  Cleanup error: {e}")
        
        log("\n" + "=" * 60)
        log("Process completed!")
        log("=" * 60)
        
    except Exception as e:
        log(f"\n❌ CRITICAL ERROR: {e}")
        log("Traceback:", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
