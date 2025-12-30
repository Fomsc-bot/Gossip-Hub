#!/usr/bin/env python3
"""
YouTube Shorts Automator - GitHub Actions Optimized
Creates and uploads YouTube Shorts automatically
"""

import os
import sys
import json
import time
import random
import requests
import re
import html
import subprocess
from pathlib import Path
from datetime import datetime
from io import BytesIO

# Check if running on GitHub Actions
ON_GITHUB_ACTIONS = os.getenv('GITHUB_ACTIONS') == 'true'

# Import with fallback for GitHub Actions
try:
    from gtts import gTTS
    from PIL import Image, ImageDraw, ImageFont
except ImportError as e:
    print(f"Error importing basic packages: {e}")
    if ON_GITHUB_ACTIONS:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "gtts", "Pillow"])
        from gtts import gTTS
        from PIL import Image, ImageDraw, ImageFont

try:
    from moviepy.editor import (
        ImageClip, AudioFileClip, CompositeVideoClip,
        concatenate_audioclips, concatenate_videoclips, vfx, VideoFileClip,
        CompositeAudioClip, ColorClip
    )
except ImportError as e:
    print(f"Error importing moviepy: {e}")
    if ON_GITHUB_ACTIONS:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "moviepy", "imageio", "imageio-ffmpeg"])
        from moviepy.editor import (
            ImageClip, AudioFileClip, CompositeVideoClip,
            concatenate_audioclips, concatenate_videoclips, vfx, VideoFileClip,
            CompositeAudioClip, ColorClip
        )

# ---------------- CONFIG ----------------
# API Keys (will be set from environment variables)
CONFIG = {
    "NEWS_API_KEY": os.getenv("NEWS_API_KEY", ""),
    "GNEWS_API_KEY": os.getenv("GNEWS_API_KEY", ""),
    "PEXELS_API_KEY": os.getenv("PEXELS_API_KEY", ""),
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
    "YT_CLIENT_ID": os.getenv("YT_CLIENT_ID", ""),
    "YT_CLIENT_SECRET": os.getenv("YT_CLIENT_SECRET", ""),
    "YT_REFRESH_TOKEN": os.getenv("YT_REFRESH_TOKEN", "")
}

# Work directory setup
WORKDIR = Path("/tmp/youtube_shorts") if ON_GITHUB_ACTIONS else Path("work")
WORKDIR.mkdir(exist_ok=True, parents=True)

# Files
LAST_FILE = WORKDIR / "uploaded_titles.txt"
LOG_FILE = WORKDIR / "process.log"

# Video settings
VIDEO_W, VIDEO_H = 1080, 1920  # Vertical format for Shorts
FPS = 30
BASE_FONT_SIZE = 60
ZOOM_RATE = 0.005

# Content niches
CONTENT_NICHES = ["celebrity", "movies", "tv", "music", "entertainment"]

# ---------------- LOGGING ----------------
class Logger:
    def __init__(self, log_file=None):
        self.log_file = log_file
    
    def log(self, message, level="INFO"):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_message = f"[{timestamp}] [{level}] {message}"
        print(log_message)
        
        if self.log_file:
            try:
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    f.write(log_message + "\n")
            except:
                pass
    
    def error(self, message):
        self.log(message, "ERROR")
    
    def warning(self, message):
        self.log(message, "WARNING")
    
    def success(self, message):
        self.log(message, "SUCCESS")

logger = Logger(LOG_FILE)

# ---------------- FONT SETUP ----------------
def setup_system():
    """Setup system dependencies"""
    logger.log("Setting up system...")
    
    if ON_GITHUB_ACTIONS:
        try:
            # Install required packages without problematic libgl1-mesa-glx
            packages = ["ffmpeg", "fonts-dejavu-core", "fonts-liberation"]
            logger.log(f"Installing packages: {packages}")
            subprocess.run(['apt-get', 'update'], capture_output=True, check=False)
            subprocess.run(['apt-get', 'install', '-y'] + packages, capture_output=True, check=False)
            logger.success("System packages installed")
        except Exception as e:
            logger.error(f"Failed to install system packages: {e}")

# ---------------- TEXT PROCESSING ----------------
def sanitize_text(text):
    """Clean text for display"""
    if not text:
        return ""
    
    # Decode HTML entities
    text = html.unescape(text)
    
    # Remove unwanted characters
    text = re.sub(r'&#?\w+;', '', text)
    text = re.sub(r'[^\x00-\x7F]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    
    # Remove source credits
    text = re.sub(r'\s*[\(\[]?(via|source|according to|reports?)[:\)\]]?\s*', '', text, flags=re.I)
    
    return text[:400]

# ---------------- DUPLICATE CHECK ----------------
def load_uploaded_titles():
    """Load list of previously uploaded titles"""
    titles = []
    if LAST_FILE.exists():
        try:
            with open(LAST_FILE, 'r', encoding='utf-8') as f:
                titles = [line.strip().lower() for line in f if line.strip()]
        except:
            pass
    return titles

def save_uploaded_title(title):
    """Save uploaded title"""
    try:
        with open(LAST_FILE, 'a', encoding='utf-8') as f:
            f.write(title.strip().lower() + "\n")
    except:
        pass

def is_duplicate(title, uploaded_titles):
    """Check if title is similar to previously uploaded content"""
    title_lower = title.strip().lower()
    
    # Check exact match
    if title_lower in uploaded_titles:
        return True
    
    # Check for significant word overlap
    title_words = set(re.findall(r'\b\w{3,}\b', title_lower))
    for uploaded_title in uploaded_titles:
        uploaded_words = set(re.findall(r'\b\w{3,}\b', uploaded_title))
        common_words = title_words.intersection(uploaded_words)
        
        # If more than 40% of significant words match
        if len(common_words) > max(2, len(title_words) * 0.4):
            return True
    
    return False

# ---------------- NEWS FETCHING ----------------
def fetch_news():
    """Fetch entertainment news from available sources"""
    logger.log("Fetching news...")
    uploaded_titles = load_uploaded_titles()
    
    # Try NewsAPI first
    if CONFIG["NEWS_API_KEY"] and CONFIG["NEWS_API_KEY"] not in ["", "your_newsapi_key_here"]:
        try:
            url = "https://newsapi.org/v2/top-headlines"
            params = {
                "category": "entertainment",
                "pageSize": 10,
                "apiKey": CONFIG["NEWS_API_KEY"],
                "country": "us",
                "language": "en"
            }
            
            response = requests.get(url, params=params, timeout=15)
            data = response.json()
            
            if data.get("status") == "ok":
                articles = data.get("articles", [])
                for article in articles:
                    title = sanitize_text(article.get("title", ""))
                    description = sanitize_text(article.get("description", ""))
                    
                    if title and description and len(title) > 15:
                        if not is_duplicate(title, uploaded_titles):
                            return {
                                "title": title,
                                "description": description,
                                "image_url": article.get("urlToImage", ""),
                                "source_url": article.get("url", ""),
                                "source": "NewsAPI"
                            }
        except Exception as e:
            logger.warning(f"NewsAPI failed: {e}")
    
    # Try GNews as fallback
    if CONFIG["GNEWS_API_KEY"] and CONFIG["GNEWS_API_KEY"] not in ["", "your_gnews_api_key_here"]:
        try:
            url = "https://gnews.io/api/v4/top-headlines"
            params = {
                "token": CONFIG["GNEWS_API_KEY"],
                "lang": "en",
                "max": 10,
                "topic": "entertainment"
            }
            
            response = requests.get(url, params=params, timeout=15)
            data = response.json()
            
            articles = data.get("articles", [])
            for article in articles:
                title = sanitize_text(article.get("title", ""))
                description = sanitize_text(article.get("description", ""))
                
                if title and description and len(title) > 15:
                    if not is_duplicate(title, uploaded_titles):
                        return {
                            "title": title,
                            "description": description,
                            "image_url": article.get("image", ""),
                            "source_url": article.get("url", ""),
                            "source": "GNews"
                        }
        except Exception as e:
            logger.warning(f"GNews failed: {e}")
    
    # Fallback entertainment topics
    fallback_topics = [
        "Breaking Celebrity News Today",
        "Latest Hollywood Updates",
        "Entertainment Industry News",
        "Movie Release Announcements",
        "TV Show Updates and Rumors"
    ]
    
    topic = random.choice(fallback_topics)
    return {
        "title": f"{topic} - Entertainment Update",
        "description": "Stay tuned for the latest developments in entertainment news. More details coming soon.",
        "image_url": "",
        "source_url": "",
        "source": "Fallback"
    }

# ---------------- IMAGE PROCESSING ----------------
def download_image(url, filename):
    """Download image from URL"""
    if not url:
        return None
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        img_path = WORKDIR / filename
        with open(img_path, 'wb') as f:
            f.write(response.content)
        
        return str(img_path)
    except Exception as e:
        logger.warning(f"Failed to download image: {e}")
        return None

def create_fallback_image():
    """Create a gradient background as fallback"""
    try:
        img = Image.new('RGB', (VIDEO_W, VIDEO_H), color=(30, 30, 50))
        draw = ImageDraw.Draw(img)
        
        # Simple gradient
        for i in range(VIDEO_H):
            r = int(30 + (i / VIDEO_H) * 100)
            g = int(30 + (i / VIDEO_H) * 50)
            b = int(50 + (i / VIDEO_H) * 100)
            draw.line([(0, i), (VIDEO_W, i)], fill=(r, g, b))
        
        # Add some random stars
        for _ in range(50):
            x = random.randint(0, VIDEO_W)
            y = random.randint(0, VIDEO_H)
            size = random.randint(1, 3)
            draw.ellipse([x, y, x+size, y+size], fill=(255, 255, 200))
        
        img_path = WORKDIR / "fallback_bg.jpg"
        img.save(img_path, "JPEG", quality=85)
        return str(img_path)
    except Exception as e:
        logger.error(f"Failed to create fallback image: {e}")
        return None

def get_images(article):
    """Get images for the video"""
    logger.log("Getting images...")
    
    images = []
    
    # Try to download article image
    if article.get("image_url"):
        img_path = download_image(article["image_url"], "main_image.jpg")
        if img_path:
            images.append(img_path)
            logger.success("Downloaded main article image")
    
    # If no image yet, try Unsplash
    if not images:
        try:
            search_term = article["title"].split()[0] if article["title"] else "celebrity"
            unsplash_url = f"https://source.unsplash.com/featured/{VIDEO_W}x{VIDEO_H}/?{search_term},entertainment"
            img_path = download_image(unsplash_url, "unsplash_image.jpg")
            if img_path:
                images.append(img_path)
                logger.success("Downloaded Unsplash image")
        except:
            pass
    
    # Final fallback
    if not images:
        img_path = create_fallback_image()
        if img_path:
            images.append(img_path)
            logger.log("Created fallback background")
    
    return images

# ---------------- SCRIPT GENERATION ----------------
def generate_script(article):
    """Generate video script"""
    logger.log("Generating script...")
    
    title = article["title"]
    description = article["description"]
    
    # Extract key words
    words = re.findall(r'\b[A-Z][a-z]+\b', title)
    celebrity = words[0] if words else "This celebrity"
    
    # Script templates
    templates = [
        # Template 1: Breaking news
        [
            f"BREAKING NEWS!",
            f"{celebrity} just made headlines...",
            f"{description[:80]}...",
            "This is huge news in Hollywood!",
            "What do YOU think about this?",
            "Follow for more updates! 🔥"
        ],
        # Template 2: Question style
        [
            f"Did {celebrity} really do this?",
            "The internet is going wild...",
            f"{description[:70]}...",
            "Fans are shocked by this news!",
            "Comment your thoughts below! 👇",
            "Subscribe for daily tea! ☕"
        ],
        # Template 3: Casual style
        [
            f"Okay, so about {celebrity}...",
            "This just dropped and it's big!",
            f"{description[:90]}...",
            "Everyone's talking about this!",
            "Want more entertainment news?",
            "Hit that SUBSCRIBE button! 🎬"
        ]
    ]
    
    script = random.choice(templates)
    logger.success(f"Generated script with {len(script)} lines")
    
    # Log script for debugging
    for i, line in enumerate(script):
        logger.log(f"  Line {i+1}: {line}")
    
    return script

# ---------------- TEXT-TO-SPEECH ----------------
def create_audio(script_lines):
    """Create TTS audio for script"""
    logger.log("Creating audio...")
    
    audio_paths = []
    durations = []
    
    for i, text in enumerate(script_lines):
        try:
            # Clean text for TTS
            clean_text = text.strip()
            if not clean_text:
                continue
            
            # Create TTS
            tts = gTTS(text=clean_text, lang='en', slow=False)
            audio_path = WORKDIR / f"audio_{i:02d}.mp3"
            tts.save(str(audio_path))
            
            # Get duration
            audio_clip = AudioFileClip(str(audio_path))
            dur = audio_clip.duration
            durations.append(dur + 0.15)  # Small pause between lines
            audio_clip.close()
            
            audio_paths.append(str(audio_path))
            logger.log(f"  Created audio {i+1}: {dur:.2f}s")
            
        except Exception as e:
            logger.warning(f"Failed to create audio for line {i}: {e}")
            # Add placeholder duration
            durations.append(2.0)
    
    return audio_paths, durations

# ---------------- CAPTION CREATION ----------------
def create_caption(text, index):
    """Create caption image for text"""
    try:
        # Split text into lines
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            current_line.append(word)
            if len(' '.join(current_line)) > 35:  # Characters per line
                lines.append(' '.join(current_line[:-1]))
                current_line = [word]
        
        if current_line:
            lines.append(' '.join(current_line))
        
        # Limit to 3 lines
        lines = lines[:3]
        
        # Calculate image size
        line_height = 70
        padding = 25
        total_height = (line_height * len(lines)) + (padding * 2)
        
        # Create image
        img = Image.new('RGBA', (VIDEO_W, total_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Create background with rounded corners
        bg_color = (0, 0, 0, 200)
        draw.rounded_rectangle(
            [(20, 10), (VIDEO_W - 20, total_height - 10)],
            radius=25,
            fill=bg_color
        )
        
        # Try to load font
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", BASE_FONT_SIZE)
        except:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", BASE_FONT_SIZE)
            except:
                font = ImageFont.load_default()
        
        # Draw text
        for i, line in enumerate(lines):
            y_pos = padding + (i * line_height) + (line_height // 2)
            
            # Text shadow
            draw.text(
                (VIDEO_W // 2 + 2, y_pos + 2),
                line,
                font=font,
                fill=(0, 0, 0, 180),
                anchor="mm"
            )
            
            # Main text
            draw.text(
                (VIDEO_W // 2, y_pos),
                line,
                font=font,
                fill=(255, 255, 255),
                anchor="mm"
            )
        
        # Save caption
        caption_path = WORKDIR / f"caption_{index:02d}.png"
        img.save(caption_path, "PNG")
        
        return str(caption_path), total_height
        
    except Exception as e:
        logger.error(f"Failed to create caption: {e}")
        return None, 0

# ---------------- VIDEO CREATION ----------------
def create_video(images, script_lines, audio_paths, durations):
    """Create final video"""
    logger.log("Creating video...")
    
    total_duration = sum(durations)
    logger.log(f"Total duration: {total_duration:.2f} seconds")
    
    # Prepare background
    bg_path = images[0] if images else None
    
    try:
        if bg_path:
            bg_clip = ImageClip(bg_path).set_duration(total_duration)
            # Add subtle zoom
            bg_clip = bg_clip.fx(vfx.resize, lambda t: 1 + (ZOOM_RATE * t))
        else:
            bg_clip = ColorClip((VIDEO_W, VIDEO_H), color=(0, 0, 0)).set_duration(total_duration)
        
        # Create caption clips
        caption_clips = []
        current_time = 0
        
        for i, (text, dur) in enumerate(zip(script_lines, durations)):
            caption_path, caption_height = create_caption(text, i)
            
            if caption_path:
                caption = ImageClip(caption_path).set_duration(dur).set_start(current_time)
                
                # Position caption (alternate positions)
                y_positions = [0.72, 0.75, 0.78]
                y_pos = int(VIDEO_H * y_positions[i % len(y_positions)])
                
                caption = caption.set_position(("center", y_pos - caption_height//2))
                caption_clips.append(caption)
            
            current_time += dur
        
        # Create audio track
        audio_clips = []
        for audio_path in audio_paths:
            try:
                audio_clip = AudioFileClip(audio_path)
                audio_clips.append(audio_clip)
            except:
                pass
        
        if audio_clips:
            main_audio = concatenate_audioclips(audio_clips)
        else:
            main_audio = AudioFileClip.silent(duration=total_duration)
        
        # Add subtle background music if available
        try:
            # Create simple background tone
            import numpy as np
            def make_tone(duration):
                frequency = 220  # Hz
                rate = 44100  # Samples per second
                t = np.linspace(0, duration, int(rate * duration))
                wave = 0.01 * np.sin(2 * np.pi * frequency * t)
                return wave
            
            tone_wave = make_tone(total_duration)
            tone_audio = AudioFileClip(lambda t: tone_wave[int(t * 44100)] if int(t * 44100) < len(tone_wave) else 0, 
                                      duration=total_duration)
            tone_audio = tone_audio.fx(vfx.audio_volumex, 0.03)
            
            main_audio = CompositeAudioClip([main_audio, tone_audio])
        except:
            pass
        
        # Combine everything
        if caption_clips:
            video = CompositeVideoClip([bg_clip] + caption_clips, size=(VIDEO_W, VIDEO_H))
        else:
            video = bg_clip
        
        video = video.set_audio(main_audio)
        video = video.set_fps(FPS)
        
        # Save video
        output_path = WORKDIR / "youtube_shorts_video.mp4"
        
        # Simple encoding for reliability
        video.write_videofile(
            str(output_path),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            bitrate="3000k",
            threads=2,
            preset='ultrafast',
            verbose=False,
            logger=None
        )
        
        # Verify video was created
        if output_path.exists():
            file_size = output_path.stat().st_size / (1024 * 1024)
            logger.success(f"Video created: {file_size:.2f} MB")
            return str(output_path)
        else:
            logger.error("Video file was not created")
            return None
            
    except Exception as e:
        logger.error(f"Failed to create video: {e}")
        return None

# ---------------- YOUTUBE UPLOAD ----------------
def upload_to_youtube(video_path, title, description):
    """Upload video to YouTube"""
    logger.log("Uploading to YouTube...")
    
    # Check if credentials are available
    if not all([CONFIG["YT_CLIENT_ID"], CONFIG["YT_CLIENT_SECRET"], CONFIG["YT_REFRESH_TOKEN"]]):
        logger.warning("YouTube credentials not available, skipping upload")
        return None
    
    if not video_path or not os.path.exists(video_path):
        logger.error(f"Video file not found: {video_path}")
        return None
    
    try:
        # Import YouTube API client
        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
        except ImportError:
            logger.error("YouTube API client not installed")
            return None
        
        # Setup credentials
        creds = Credentials(
            token=None,
            refresh_token=CONFIG["YT_REFRESH_TOKEN"],
            client_id=CONFIG["YT_CLIENT_ID"],
            client_secret=CONFIG["YT_CLIENT_SECRET"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/youtube.upload"]
        )
        
        # Refresh token
        creds.refresh(Request())
        
        # Create YouTube service
        youtube = build("youtube", "v3", credentials=creds)
        
        # Prepare video metadata
        body = {
            "snippet": {
                "title": title[:90],  # YouTube title limit
                "description": description[:4000],
                "tags": ["shorts", "entertainment", "news", "celebrity", "youtubeshorts"],
                "categoryId": "24"  # Entertainment
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }
        
        # Create media upload
        media = MediaFileUpload(
            video_path,
            mimetype='video/mp4',
            resumable=True,
            chunksize=1024*1024
        )
        
        # Upload video
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )
        
        # Execute upload
        response = None
        upload_start = time.time()
        
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    elapsed = time.time() - upload_start
                    logger.log(f"  Upload: {progress}% ({elapsed:.1f}s)")
                    
                    # Timeout after 10 minutes
                    if elapsed > 600:
                        logger.error("Upload timeout")
                        return None
            except Exception as e:
                logger.error(f"Upload error: {e}")
                return None
        
        video_id = response["id"]
        logger.success(f"✅ Upload successful! Video ID: {video_id}")
        logger.success(f"📺 Watch: https://youtube.com/shorts/{video_id}")
        
        return video_id
        
    except Exception as e:
        logger.error(f"❌ YouTube upload failed: {e}")
        return None

# ---------------- METADATA GENERATION ----------------
def generate_metadata(article):
    """Generate YouTube title and description"""
    title = article["title"]
    
    # Extract main subject
    words = re.findall(r'\b[A-Z][a-z]+\b', title)
    subject = words[0] if words else "This"
    
    # Title options
    titles = [
        f"{subject} Just Did WHAT?! 😱",
        f"Breaking News About {subject}!",
        f"You Won't Believe What {subject} Did!",
        f"{subject}: The TRUTH Revealed!",
        f"SHOCKING News About {subject}!",
        f"What {subject} Just Did Will Blow Your Mind!",
        f"{subject} Drops Bombshell Announcement! 💣"
    ]
    
    youtube_title = random.choice(titles)
    
    # Description
    hashtags = [
        "#shorts", "#youtubeshorts", "#entertainment",
        "#celebritynews", "#hollywood", "#breakingnews",
        "#viral", "#trending", "#news", "#update"
    ]
    
    random.shuffle(hashtags)
    selected_tags = hashtags[:7]
    
    description = f"""🎬 {article['description'][:100]}...

👉 Follow for daily entertainment updates!
🔥 Turn on notifications!

💬 What do you think? Comment below!

{' '.join(selected_tags)}

#shortsfeed #entertainmentnews #viralshorts

📌 Source: Various news outlets
⚠️ For entertainment purposes"""
    
    return youtube_title, description

# ---------------- MAIN FUNCTION ----------------
def main():
    """Main execution"""
    logger.log("=" * 50)
    logger.log("YouTube Shorts Automator")
    logger.log("=" * 50)
    
    # Setup system
    setup_system()
    
    try:
        # Step 1: Fetch news
        logger.log("\n📰 STEP 1: Fetching news article...")
        article = fetch_news()
        
        if not article:
            logger.error("No article found")
            return
        
        logger.success(f"Article: {article['title'][:60]}...")
        
        # Step 2: Get images
        logger.log("\n🖼️ STEP 2: Getting images...")
        images = get_images(article)
        logger.success(f"Got {len(images)} images")
        
        # Step 3: Generate script
        logger.log("\n✍️ STEP 3: Generating script...")
        script_lines = generate_script(article)
        
        # Step 4: Create audio
        logger.log("\n🔊 STEP 4: Creating audio...")
        audio_paths, durations = create_audio(script_lines)
        
        if not audio_paths:
            logger.error("No audio created")
            return
        
        # Step 5: Create video
        logger.log("\n🎬 STEP 5: Creating video...")
        video_path = create_video(images, script_lines, audio_paths, durations)
        
        if not video_path or not os.path.exists(video_path):
            logger.error("Video creation failed")
            
            # Create dummy video for debugging
            try:
                dummy_video = WORKDIR / "debug_video.mp4"
                with open(dummy_video, 'wb') as f:
                    f.write(b"dummy")
                logger.log("Created dummy video file for debugging")
            except:
                pass
            
            return
        
        # Step 6: Generate metadata
        logger.log("\n🏷️ STEP 6: Generating metadata...")
        youtube_title, youtube_description = generate_metadata(article)
        logger.success(f"Title: {youtube_title}")
        
        # Step 7: Upload to YouTube
        logger.log("\n⬆️ STEP 7: Uploading to YouTube...")
        video_id = upload_to_youtube(video_path, youtube_title, youtube_description)
        
        if video_id:
            save_uploaded_title(article["title"])
            logger.success("\n✅ SUCCESS! Video uploaded!")
        else:
            logger.warning("\n⚠️ Upload skipped or failed")
            
            # Save video locally for debugging
            if not ON_GITHUB_ACTIONS:
                import shutil
                backup_dir = Path("videos")
                backup_dir.mkdir(exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = backup_dir / f"video_{timestamp}.mp4"
                shutil.copy2(video_path, backup_path)
                logger.success(f"Video saved locally: {backup_path}")
        
        # Step 8: Cleanup
        logger.log("\n🧹 STEP 8: Cleaning up...")
        try:
            for file in WORKDIR.glob("*"):
                if file.name != "uploaded_titles.txt" and file.name != "process.log":
                    try:
                        file.unlink()
                    except:
                        pass
            logger.success("Cleanup complete")
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")
        
        logger.log("\n" + "=" * 50)
        logger.success("Process completed!")
        logger.log("=" * 50)
        
    except Exception as e:
        logger.error(f"\n❌ CRITICAL ERROR: {e}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    main()
