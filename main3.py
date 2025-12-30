#!/usr/bin/env python3
"""
YouTube Shorts Automator - Simple Version
No Gemini dependency to avoid conflicts
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

# Work directory setup
WORKDIR = Path("/tmp/youtube_shorts") if ON_GITHUB_ACTIONS else Path("work")
WORKDIR.mkdir(exist_ok=True, parents=True)

# Files
LAST_FILE = WORKDIR / "uploaded_titles.txt"
LOG_FILE = WORKDIR / "process.log"

# Video settings
VIDEO_W, VIDEO_H = 1080, 1920
FPS = 30
BASE_FONT_SIZE = 60
ZOOM_RATE = 0.005

# ---------------- LOGGING ----------------
def log(message, level="INFO"):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_message = f"[{timestamp}] [{level}] {message}"
    print(log_message)
    
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_message + "\n")
    except:
        pass

def setup_system():
    """Setup system dependencies"""
    log("Setting up system...")
    
    if ON_GITHUB_ACTIONS:
        try:
            # Install required packages
            packages = ["ffmpeg", "fonts-dejavu-core", "fonts-liberation"]
            log(f"Installing packages: {packages}")
            subprocess.run(['apt-get', 'update'], capture_output=True, check=False)
            subprocess.run(['apt-get', 'install', '-y'] + packages, capture_output=True, check=False)
            log("System packages installed", "SUCCESS")
        except Exception as e:
            log(f"Failed to install system packages: {e}", "ERROR")

# ---------------- TEXT PROCESSING ----------------
def sanitize_text(text):
    """Clean text for display"""
    if not text:
        return ""
    
    text = html.unescape(text)
    text = re.sub(r'&#?\w+;', '', text)
    text = re.sub(r'[^\x00-\x7F]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
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
    
    if title_lower in uploaded_titles:
        return True
    
    title_words = set(re.findall(r'\b\w{3,}\b', title_lower))
    for uploaded_title in uploaded_titles:
        uploaded_words = set(re.findall(r'\b\w{3,}\b', uploaded_title))
        common_words = title_words.intersection(uploaded_words)
        
        if len(common_words) > max(2, len(title_words) * 0.4):
            return True
    
    return False

# ---------------- NEWS FETCHING ----------------
def fetch_news():
    """Fetch entertainment news from available sources"""
    log("Fetching news...")
    uploaded_titles = load_uploaded_titles()
    
    # Get API keys from environment
    news_api_key = os.getenv("NEWS_API_KEY", "")
    gnews_api_key = os.getenv("GNEWS_API_KEY", "")
    
    # Try NewsAPI first
    if news_api_key and news_api_key not in ["", "your_newsapi_key_here"]:
        try:
            url = "https://newsapi.org/v2/top-headlines"
            params = {
                "category": "entertainment",
                "pageSize": 10,
                "apiKey": news_api_key,
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
            log(f"NewsAPI failed: {e}", "WARNING")
    
    # Try GNews as fallback
    if gnews_api_key and gnews_api_key not in ["", "your_gnews_api_key_here"]:
        try:
            url = "https://gnews.io/api/v4/top-headlines"
            params = {
                "token": gnews_api_key,
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
            log(f"GNews failed: {e}", "WARNING")
    
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
        log(f"Failed to download image: {e}", "WARNING")
        return None

def create_fallback_image():
    """Create a gradient background as fallback"""
    try:
        # Import PIL here to avoid dependency issues
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            log("Installing Pillow...", "INFO")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
            from PIL import Image, ImageDraw
        
        img = Image.new('RGB', (VIDEO_W, VIDEO_H), color=(40, 40, 60))
        draw = ImageDraw.Draw(img)
        
        # Simple gradient
        for i in range(VIDEO_H):
            r = int(40 + (i / VIDEO_H) * 80)
            g = int(40 + (i / VIDEO_H) * 40)
            b = int(60 + (i / VIDEO_H) * 80)
            draw.line([(0, i), (VIDEO_W, i)], fill=(r, g, b))
        
        img_path = WORKDIR / "fallback_bg.jpg"
        img.save(img_path, "JPEG", quality=85)
        return str(img_path)
    except Exception as e:
        log(f"Failed to create fallback image: {e}", "ERROR")
        return None

def get_images(article):
    """Get images for the video"""
    log("Getting images...")
    
    images = []
    
    # Try to download article image
    if article.get("image_url"):
        img_path = download_image(article["image_url"], "main_image.jpg")
        if img_path:
            images.append(img_path)
            log("Downloaded main article image", "SUCCESS")
    
    # If no image yet, try Unsplash
    if not images:
        try:
            search_term = article["title"].split()[0] if article["title"] else "celebrity"
            unsplash_url = f"https://source.unsplash.com/featured/{VIDEO_W}x{VIDEO_H}/?{search_term}"
            img_path = download_image(unsplash_url, "unsplash_image.jpg")
            if img_path:
                images.append(img_path)
                log("Downloaded Unsplash image", "SUCCESS")
        except:
            pass
    
    # Final fallback
    if not images:
        img_path = create_fallback_image()
        if img_path:
            images.append(img_path)
            log("Created fallback background", "INFO")
    
    return images

# ---------------- SCRIPT GENERATION ----------------
def generate_script(article):
    """Generate video script"""
    log("Generating script...")
    
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
        ]
    ]
    
    script = random.choice(templates)
    log(f"Generated script with {len(script)} lines", "SUCCESS")
    
    return script

# ---------------- TEXT-TO-SPEECH ----------------
def create_audio(script_lines):
    """Create TTS audio for script"""
    log("Creating audio...")
    
    try:
        from gtts import gTTS
    except ImportError:
        log("Installing gTTS...", "INFO")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "gtts"])
        from gtts import gTTS
    
    audio_paths = []
    durations = []
    
    for i, text in enumerate(script_lines):
        try:
            clean_text = text.strip()
            if not clean_text:
                continue
            
            tts = gTTS(text=clean_text, lang='en', slow=False)
            audio_path = WORKDIR / f"audio_{i:02d}.mp3"
            tts.save(str(audio_path))
            
            # Get duration using simple calculation (approx 15 chars per second)
            dur = len(clean_text) / 15 + 0.5
            durations.append(dur)
            
            audio_paths.append(str(audio_path))
            log(f"Created audio {i+1}: {dur:.2f}s", "INFO")
            
        except Exception as e:
            log(f"Failed to create audio for line {i}: {e}", "WARNING")
            durations.append(2.0)
    
    return audio_paths, durations

# ---------------- CAPTION CREATION ----------------
def create_caption(text, index):
    """Create caption image for text"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log("Installing Pillow for captions...", "INFO")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
        from PIL import Image, ImageDraw, ImageFont
    
    try:
        # Split text into lines
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            current_line.append(word)
            if len(' '.join(current_line)) > 35:
                lines.append(' '.join(current_line[:-1]))
                current_line = [word]
        
        if current_line:
            lines.append(' '.join(current_line))
        
        lines = lines[:3]
        
        # Calculate image size
        line_height = 70
        padding = 25
        total_height = (line_height * len(lines)) + (padding * 2)
        
        # Create image
        img = Image.new('RGBA', (VIDEO_W, total_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Create background
        bg_color = (0, 0, 0, 200)
        draw.rectangle([(20, 10), (VIDEO_W - 20, total_height - 10)], fill=bg_color)
        
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
        
        caption_path = WORKDIR / f"caption_{index:02d}.png"
        img.save(caption_path, "PNG")
        
        return str(caption_path), total_height
        
    except Exception as e:
        log(f"Failed to create caption: {e}", "ERROR")
        return None, 0

# ---------------- VIDEO CREATION ----------------
def create_video(images, script_lines, audio_paths, durations):
    """Create final video"""
    log("Creating video...")
    
    total_duration = sum(durations)
    log(f"Total duration: {total_duration:.2f} seconds", "INFO")
    
    try:
        # Import moviepy here to avoid dependency issues
        try:
            from moviepy.editor import (
                ImageClip, AudioFileClip, CompositeVideoClip,
                concatenate_audioclips, concatenate_videoclips, vfx, 
                CompositeAudioClip, ColorClip
            )
        except ImportError:
            log("Installing moviepy...", "INFO")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "moviepy"])
            from moviepy.editor import (
                ImageClip, AudioFileClip, CompositeVideoClip,
                concatenate_audioclips, concatenate_videoclips, vfx,
                CompositeAudioClip, ColorClip
            )
        
        # Prepare background
        bg_path = images[0] if images else None
        
        if bg_path:
            bg_clip = ImageClip(bg_path).set_duration(total_duration)
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
                y_pos = int(VIDEO_H * 0.75)
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
        
        # Combine everything
        if caption_clips:
            video = CompositeVideoClip([bg_clip] + caption_clips, size=(VIDEO_W, VIDEO_H))
        else:
            video = bg_clip
        
        video = video.set_audio(main_audio)
        video = video.set_fps(FPS)
        
        # Save video
        output_path = WORKDIR / "youtube_shorts_video.mp4"
        
        video.write_videofile(
            str(output_path),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            threads=2,
            preset='ultrafast',
            verbose=False,
            logger=None
        )
        
        if output_path.exists():
            file_size = output_path.stat().st_size / (1024 * 1024)
            log(f"Video created: {file_size:.2f} MB", "SUCCESS")
            return str(output_path)
        else:
            log("Video file was not created", "ERROR")
            return None
            
    except Exception as e:
        log(f"Failed to create video: {e}", "ERROR")
        return None

# ---------------- YOUTUBE UPLOAD ----------------
def upload_to_youtube(video_path, title, description):
    """Upload video to YouTube"""
    log("Uploading to YouTube...")
    
    # Get YouTube credentials
    yt_client_id = os.getenv("YT_CLIENT_ID", "")
    yt_client_secret = os.getenv("YT_CLIENT_SECRET", "")
    yt_refresh_token = os.getenv("YT_REFRESH_TOKEN", "")
    
    if not all([yt_client_id, yt_client_secret, yt_refresh_token]):
        log("YouTube credentials not available, skipping upload", "WARNING")
        return None
    
    if not video_path or not os.path.exists(video_path):
        log(f"Video file not found: {video_path}", "ERROR")
        return None
    
    try:
        # Import YouTube API client
        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
        except ImportError:
            log("Installing YouTube API client...", "INFO")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "google-api-python-client", "google-auth", "google-auth-oauthlib"])
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
        
        # Setup credentials
        creds = Credentials(
            token=None,
            refresh_token=yt_refresh_token,
            client_id=yt_client_id,
            client_secret=yt_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/youtube.upload"]
        )
        
        creds.refresh(Request())
        
        # Create YouTube service
        youtube = build("youtube", "v3", credentials=creds)
        
        # Prepare video metadata
        body = {
            "snippet": {
                "title": title[:90],
                "description": description[:4000],
                "tags": ["shorts", "entertainment", "news", "celebrity"],
                "categoryId": "24"
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
                    log(f"Upload: {progress}% ({elapsed:.1f}s)", "INFO")
                    
                    if elapsed > 600:
                        log("Upload timeout", "ERROR")
                        return None
            except Exception as e:
                log(f"Upload error: {e}", "ERROR")
                return None
        
        video_id = response["id"]
        log(f"✅ Upload successful! Video ID: {video_id}", "SUCCESS")
        log(f"📺 Watch: https://youtube.com/shorts/{video_id}", "SUCCESS")
        
        return video_id
        
    except Exception as e:
        log(f"❌ YouTube upload failed: {e}", "ERROR")
        return None

# ---------------- METADATA GENERATION ----------------
def generate_metadata(article):
    """Generate YouTube title and description"""
    title = article["title"]
    
    words = re.findall(r'\b[A-Z][a-z]+\b', title)
    subject = words[0] if words else "This"
    
    titles = [
        f"{subject} Just Did WHAT?! 😱",
        f"Breaking News About {subject}!",
        f"You Won't Believe What {subject} Did!",
        f"{subject}: The TRUTH Revealed!",
        f"SHOCKING News About {subject}!"
    ]
    
    youtube_title = random.choice(titles)
    
    hashtags = [
        "#shorts", "#youtubeshorts", "#entertainment",
        "#celebritynews", "#hollywood", "#breakingnews",
        "#viral", "#trending"
    ]
    
    random.shuffle(hashtags)
    selected_tags = hashtags[:6]
    
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
    log("=" * 50, "INFO")
    log("YouTube Shorts Automator", "INFO")
    log("=" * 50, "INFO")
    
    # Setup system
    setup_system()
    
    try:
        # Step 1: Fetch news
        log("\n📰 STEP 1: Fetching news article...", "INFO")
        article = fetch_news()
        
        if not article:
            log("No article found", "ERROR")
            return
        
        log(f"Article: {article['title'][:60]}...", "SUCCESS")
        
        # Step 2: Get images
        log("\n🖼️ STEP 2: Getting images...", "INFO")
        images = get_images(article)
        log(f"Got {len(images)} images", "SUCCESS")
        
        # Step 3: Generate script
        log("\n✍️ STEP 3: Generating script...", "INFO")
        script_lines = generate_script(article)
        
        # Step 4: Create audio
        log("\n🔊 STEP 4: Creating audio...", "INFO")
        audio_paths, durations = create_audio(script_lines)
        
        if not audio_paths:
            log("No audio created", "ERROR")
            return
        
        # Step 5: Create video
        log("\n🎬 STEP 5: Creating video...", "INFO")
        video_path = create_video(images, script_lines, audio_paths, durations)
        
        if not video_path or not os.path.exists(video_path):
            log("Video creation failed", "ERROR")
            return
        
        # Step 6: Generate metadata
        log("\n🏷️ STEP 6: Generating metadata...", "INFO")
        youtube_title, youtube_description = generate_metadata(article)
        log(f"Title: {youtube_title}", "SUCCESS")
        
        # Step 7: Upload to YouTube
        log("\n⬆️ STEP 7: Uploading to YouTube...", "INFO")
        video_id = upload_to_youtube(video_path, youtube_title, youtube_description)
        
        if video_id:
            save_uploaded_title(article["title"])
            log("\n✅ SUCCESS! Video uploaded!", "SUCCESS")
        else:
            log("\n⚠️ Upload skipped or failed", "WARNING")
        
        log("\n" + "=" * 50, "INFO")
        log("Process completed!", "SUCCESS")
        log("=" * 50, "INFO")
        
    except Exception as e:
        log(f"\n❌ CRITICAL ERROR: {e}", "ERROR")
        import traceback
        log(traceback.format_exc(), "ERROR")

if __name__ == "__main__":
    main()
