# main.py - GITHUB ACTIONS OPTIMIZED VERSION
import os
import re
import time
import random
import requests
import html
import json
import sys
from pathlib import Path
from io import BytesIO
from datetime import datetime
import textwrap
import subprocess

# Check if running on GitHub Actions
ON_GITHUB_ACTIONS = os.getenv('GITHUB_ACTIONS') == 'true'

try:
    from gtts import gTTS
    from PIL import Image, ImageDraw, ImageFont
    from moviepy.editor import (
        ImageClip, AudioFileClip, CompositeVideoClip,
        concatenate_audioclips, concatenate_videoclips, vfx, VideoFileClip,
        CompositeAudioClip
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
        from PIL import Image, ImageDraw, ImageFont
        from moviepy.editor import (
            ImageClip, AudioFileClip, CompositeVideoClip,
            concatenate_audioclips, concatenate_videoclips, vfx, VideoFileClip,
            CompositeAudioClip
        )
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

# ---------------- Gemini AI Integration ----------------
genai = None
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY and GEMINI_API_KEY != "your_gemini_api_key_here":
    try:
        import google.generativeai as genai_lib
        genai = genai_lib
        genai.configure(api_key=GEMINI_API_KEY)
    except ImportError:
        print("Warning: google.generativeai not available")
    except Exception as e:
        print(f"Warning: Gemini configuration failed: {e}")

# ---------------- CONFIG ----------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")

# Work directory setup
if ON_GITHUB_ACTIONS:
    WORKDIR = Path("/tmp/work")  # Use tmp directory on GitHub Actions
else:
    WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True, parents=True)

LAST_FILE = WORKDIR / "last_titles.txt"

# Video settings
VIDEO_W, VIDEO_H = 1080, 1920
CAPTION_HEIGHT = int(VIDEO_H * 0.12)
ZOOM_RATE = 0.008
FPS = 30
BASE_FONT_SIZE = 68
SMALL_FONT_SIZE = 48
MAX_CAPTION_CHARS = 45

# Directory paths
OUTTRO_DIR = Path("outtro") if not ON_GITHUB_ACTIONS else Path("/tmp/outtro")
MUSIC_DIR = Path("music") if not ON_GITHUB_ACTIONS else Path("/tmp/music")

# Content niches
CONTENT_NICHES = [
    "celebrity_lifestyle",
    "entertainment_news", 
    "movie_updates",
    "tv_shows",
    "social_media_trends"
]

def log(*args):
    print(f"[{datetime.now().strftime('%H:%M:%S')}]", *args)

# ---------------- Font Setup for GitHub Actions ----------------
def get_font():
    """Get font path for GitHub Actions or local"""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
        "C:/Windows/Fonts/arial.ttf",  # Windows
    ]
    
    for path in font_paths:
        if os.path.exists(path):
            return path
    
    # If no font found, create a simple fallback
    log("No system font found, using default")
    return None

# ---------------- Text Sanitization ----------------
def sanitize_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r'&[#A-Za-z0-9]+;?', ' ', s)
    s = re.sub(r'[^\x00-\x7F]+', ' ', s)
    s = re.sub(r'\s+([.,!?])', r'\1', s)
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'\b(via|source|according to|reports?):?\s+', '', s, flags=re.I)
    s = re.sub(r'\s*-\s*(Reuters|AP|CNN|BBC)\b', '', s, flags=re.I)
    return s[:500]

# ---------------- Duplicate Filter ----------------
def has_uploaded(title):
    if not LAST_FILE.exists():
        return False
    try:
        with open(LAST_FILE, "r", encoding="utf-8") as f:
            existing = f.read().lower()
            title_lower = title.strip().lower()
            words = set(title_lower.split())
            for existing_title in existing.split('\n'):
                existing_words = set(existing_title.split())
                if len(words & existing_words) > 3:
                    return True
    except:
        pass
    return False

def save_uploaded(title):
    with open(LAST_FILE, "a", encoding="utf-8") as f:
        f.write(title.strip() + "\n")

# ---------------- News Fetching ----------------
def get_high_quality_article():
    """Get trending entertainment news"""
    
    # Try NewsAPI first
    if NEWS_API_KEY and NEWS_API_KEY != "your_newsapi_key_here":
        try:
            url = "https://newsapi.org/v2/top-headlines"
            params = {
                "category": "entertainment",
                "pageSize": 10,
                "apiKey": NEWS_API_KEY,
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
                    
                    if (len(title) > 20 and len(title) < 120 and 
                        len(description) > 50 and 
                        not has_uploaded(title)):
                        
                        return {
                            "title": title,
                            "description": description,
                            "content": "",
                            "image_url": article.get("urlToImage", ""),
                            "source_url": article.get("url", ""),
                            "source": "NewsAPI"
                        }
        except Exception as e:
            log(f"NewsAPI error: {e}")
    
    # Try GNews
    if GNEWS_API_KEY and GNEWS_API_KEY != "your_gnews_api_key_here":
        try:
            url = "https://gnews.io/api/v4/top-headlines"
            params = {
                "token": GNEWS_API_KEY,
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
                content = sanitize_text(article.get("content", ""))
                
                if (len(title) > 15 and len(description) > 40 and 
                    not has_uploaded(title)):
                    
                    return {
                        "title": title,
                        "description": description,
                        "content": content,
                        "image_url": article.get("image", ""),
                        "source_url": article.get("url", ""),
                        "source": "GNews"
                    }
        except Exception as e:
            log(f"GNews error: {e}")
    
    # Fallback
    entertainment_topics = [
        "Celebrity news today",
        "Hollywood updates",
        "Entertainment industry",
        "Movie releases",
        "TV show announcements"
    ]
    
    topic = random.choice(entertainment_topics)
    return {
        "title": f"{topic} - Latest Updates",
        "description": f"Breaking news in the entertainment world. Stay tuned for more details.",
        "content": "",
        "image_url": "",
        "source_url": "",
        "source": "Fallback"
    }

# ---------------- Image Fetching ----------------
def fetch_multiple_images(query, count=3):
    """Fetch images from Pexels or Unsplash"""
    images = []
    
    # Try Pexels
    if PEXELS_API_KEY and PEXELS_API_KEY != "your_pexels_api_key_here":
        try:
            url = "https://api.pexels.com/v1/search"
            headers = {"Authorization": PEXELS_API_KEY}
            params = {
                "query": query,
                "per_page": count,
                "orientation": "portrait"
            }
            response = requests.get(url, headers=headers, params=params, timeout=15)
            data = response.json()
            
            for photo in data.get("photos", [])[:count]:
                images.append(photo["src"]["large"])
                
        except Exception as e:
            log(f"Pexels error: {e}")
    
    # Fallback to Unsplash
    if not images:
        try:
            for i in range(count):
                unsplash_url = f"https://source.unsplash.com/featured/{VIDEO_W}x{VIDEO_H}/?{query}"
                images.append(unsplash_url)
        except:
            pass
    
    return images

# ---------------- Script Generation ----------------
def generate_engaging_script(article_data, niche=None):
    """Create engaging script"""
    
    title = article_data["title"]
    description = article_data["description"]
    
    # Try AI enhancement
    enhanced = enhance_content_with_gemini(title, description, niche) if genai else None
    
    if enhanced and isinstance(enhanced, dict):
        if "hook" in enhanced:
            script_parts = [enhanced["hook"]]
            if "story_points" in enhanced:
                script_parts.extend(enhanced["story_points"][:3])
            script_parts.extend([
                "What do YOU think about this?",
                "Follow for more updates!",
                "Turn on notifications! 🔔"
            ])
            return script_parts
    
    # Templates
    templates = [
        [
            f"Did you hear about {title.split()[0]}?",
            "This just happened...",
            description[:80] + ("..." if len(description) > 80 else ""),
            "Everyone is talking about this!",
            "What's your opinion? Comment below!",
            "Subscribe for daily updates! 👍"
        ],
        [
            f"BREAKING: {title[:60]}",
            "This is unexpected news...",
            "Here's what we know:",
            description[:100],
            "This changes everything!",
            "Want more news? Hit subscribe! 🔔"
        ]
    ]
    
    return random.choice(templates)

# ---------------- Gemini Enhancement ----------------
def enhance_content_with_gemini(headline, description, niche=""):
    """Use Gemini AI to enhance content"""
    if not genai:
        return None
    
    try:
        prompt = f"""Create a short YouTube Shorts script (25-35 seconds) about:
        Headline: {headline}
        Details: {description}
        
        Create 4-5 short engaging sentences for a YouTube Short.
        Start with a hook, then give info, end with CTA.
        Return as JSON: {{"hook": "", "story_points": []}}
        """
        
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(prompt)
        
        try:
            return json.loads(response.text.strip())
        except:
            lines = response.text.strip().split('\n')
            return {
                "hook": lines[0] if lines else headline,
                "story_points": lines[1:3] if len(lines) > 1 else []
            }
            
    except Exception as e:
        log(f"Gemini error: {e}")
        return None

# ---------------- TTS Creation ----------------
def create_tts_with_emphasis(lines):
    """Create TTS audio files"""
    tts_paths = []
    durations = []
    
    for i, line in enumerate(lines):
        out_path = WORKDIR / f"tts_{i}.mp3"
        
        # Clean line for TTS
        line = line.replace('!', '. ').replace('?', '. ')
        
        try:
            tts = gTTS(text=line, lang='en', slow=False)
            tts.save(str(out_path))
            
            audio = AudioFileClip(str(out_path))
            durations.append(audio.duration + 0.2)
            audio.close()
            
            tts_paths.append(str(out_path))
        except Exception as e:
            log(f"TTS error for line {i}: {e}")
            # Add placeholder duration
            durations.append(2.0)
            # Create empty audio file
            silent_audio = AudioFileClip.silent(duration=2.0)
            silent_audio.write_audiofile(str(out_path))
            silent_audio.close()
            tts_paths.append(str(out_path))
    
    return tts_paths, durations

# ---------------- Caption Rendering ----------------
def render_modern_caption(text, index):
    """Render captions with fallback for GitHub Actions"""
    text = sanitize_text(text)
    
    # Split text
    words = text.split()
    lines = []
    current_line = []
    
    for word in words:
        current_line.append(word)
        if len(' '.join(current_line)) > MAX_CAPTION_CHARS:
            lines.append(' '.join(current_line[:-1]))
            current_line = [word]
    
    if current_line:
        lines.append(' '.join(current_line))
    
    # Create image
    line_height = 70
    total_height = len(lines) * line_height + 40
    img = Image.new('RGBA', (VIDEO_W, total_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Try to load font
    font_path = get_font()
    if font_path:
        try:
            font = ImageFont.truetype(font_path, BASE_FONT_SIZE)
        except:
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()
    
    # Draw text
    for i, line in enumerate(lines):
        y = 20 + i * line_height
        
        # Shadow
        for dx, dy in [(2, 2)]:
            draw.text((VIDEO_W//2 + dx, y + dy), line, font=font, 
                     fill=(0, 0, 0, 180), anchor="mm")
        
        # Main text
        draw.text((VIDEO_W//2, y), line, font=font, 
                 fill=(255, 255, 255, 255), anchor="mm")
    
    out_path = WORKDIR / f"caption_{index}.png"
    img.save(out_path, "PNG")
    
    return str(out_path), total_height

# ---------------- Video Creation ----------------
def create_video_with_transitions(bg_images, lines, tts_paths, durations, out_file):
    """Create final video"""
    
    total_duration = sum(durations)
    
    # Prepare background clips
    image_clips = []
    if len(bg_images) > 1:
        duration_per_image = total_duration / len(bg_images)
        for i, img_path in enumerate(bg_images):
            try:
                img_clip = ImageClip(img_path).set_duration(duration_per_image)
                img_clip = img_clip.fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)
                img_clip = img_clip.set_start(i * duration_per_image)
                image_clips.append(img_clip)
            except Exception as e:
                log(f"Error loading image {img_path}: {e}")
                continue
    else:
        try:
            img_clip = ImageClip(bg_images[0]).set_duration(total_duration)
            img_clip = img_clip.fx(vfx.resize, lambda t: 1 + ZOOM_RATE * t)
            image_clips.append(img_clip)
        except:
            # Create black background
            img_clip = ColorClip((VIDEO_W, VIDEO_H), color=(0, 0, 0)).set_duration(total_duration)
            image_clips.append(img_clip)
    
    if not image_clips:
        img_clip = ColorClip((VIDEO_W, VIDEO_H), color=(0, 0, 0)).set_duration(total_duration)
        image_clips.append(img_clip)
    
    # Create background video
    if len(image_clips) > 1:
        bg_video = concatenate_videoclips(image_clips, method="compose")
    else:
        bg_video = image_clips[0]
    
    # Add captions
    caption_clips = []
    current_time = 0
    
    for i, (line, dur) in enumerate(zip(lines, durations)):
        try:
            caption_path, caption_height = render_modern_caption(line, i)
            caption = ImageClip(caption_path).set_duration(dur).set_start(current_time)
            
            y_pos = int(VIDEO_H * 0.75)
            caption = caption.set_position(("center", y_pos - caption_height//2))
            
            caption_clips.append(caption)
        except Exception as e:
            log(f"Error creating caption {i}: {e}")
        
        current_time += dur
    
    # Add audio
    audio_clips = []
    for p in tts_paths:
        try:
            audio_clips.append(AudioFileClip(p))
        except:
            pass
    
    if audio_clips:
        main_audio = concatenate_audioclips(audio_clips)
    else:
        main_audio = AudioFileClip.silent(duration=total_duration)
    
    # Create final video
    if caption_clips:
        final_video = CompositeVideoClip([bg_video] + caption_clips, size=(VIDEO_W, VIDEO_H))
    else:
        final_video = bg_video
    
    final_video = final_video.set_audio(main_audio)
    
    # Write video (optimized for GitHub Actions)
    try:
        final_video.write_videofile(
            str(out_file),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            threads=2 if ON_GITHUB_ACTIONS else 4,
            preset='ultrafast' if ON_GITHUB_ACTIONS else 'fast',
            ffmpeg_params=["-crf", "28"]  # Higher CRF for faster encoding
        )
    except Exception as e:
        log(f"Video write error: {e}")
        # Try simpler encoding
        try:
            final_video.write_videofile(str(out_file), fps=FPS, logger=None)
        except:
            return None
    
    return str(out_file)

# ---------------- Metadata Creation ----------------
def create_youtube_metadata(title, content):
    """Create YouTube title and description"""
    
    title_templates = [
        f"Did {title.split()[0]} Really Do This? 👀",
        f"Why Is Everyone Talking About {title.split()[0]}?",
        f"BREAKING: {title[:40]}...",
        f"SHOCKING: {title[:40]}",
        f"{title.split()[0]} Just Revealed This! 🔥"
    ]
    
    youtube_title = random.choice(title_templates)
    
    hashtags = [
        "#shorts", "#celebritynews", "#hollywood", 
        "#entertainment", "#breakingnews", "#viral"
    ]
    
    description = f"""🎬 {content[:100]}...

👉 FOLLOW for daily entertainment updates!

💬 What do you think? Comment below!

{" ".join(random.sample(hashtags, 5))}

#shortsfeed #youtubeshorts"""
    
    return youtube_title, description

# ---------------- YouTube Upload ----------------
def upload_to_youtube(video_file, title, description):
    """Upload video to YouTube"""
    if not all([YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN]):
        log("YouTube credentials not configured")
        return None
    
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
        
        youtube = build("youtube", "v3", credentials=creds)
        
        body = {
            "snippet": {
                "title": title[:100],  # Limit title length
                "description": description,
                "tags": ["shorts", "news", "entertainment", "celebrity"],
                "categoryId": "24"
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }
        
        # Check file size
        file_size = os.path.getsize(video_file)
        log(f"Video file size: {file_size / (1024*1024):.2f} MB")
        
        media = MediaFileUpload(video_file, chunksize=1024*1024, resumable=True)
        
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )
        
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log(f"Upload: {int(status.progress() * 100)}%")
        
        video_id = response["id"]
        log(f"Upload successful! Video ID: {video_id}")
        return video_id
        
    except Exception as e:
        log(f"YouTube upload failed: {e}")
        return None

# ---------------- Main Function ----------------
def main():
    log("Starting YouTube Shorts Creator...")
    log(f"Running on GitHub Actions: {ON_GITHUB_ACTIONS}")
    
    # Step 1: Get article
    article = get_high_quality_article()
    if not article:
        log("No article found")
        return
    
    log(f"Article: {article['title'][:50]}...")
    
    # Step 2: Check duplicates
    if has_uploaded(article['title']):
        log("Similar content already uploaded")
        return
    
    # Step 3: Generate script
    niche = random.choice(CONTENT_NICHES)
    script_lines = generate_engaging_script(article, niche)
    log(f"Script lines: {len(script_lines)}")
    
    # Step 4: Get images
    search_query = article['title'].split()[0] + " celebrity"
    image_urls = fetch_multiple_images(search_query, count=3)
    
    bg_images = []
    for i, img_url in enumerate(image_urls[:3]):
        try:
            response = requests.get(img_url, timeout=10)
            img_path = WORKDIR / f"image_{i}.jpg"
            with open(img_path, 'wb') as f:
                f.write(response.content)
            bg_images.append(str(img_path))
        except:
            pass
    
    # Fallback to black background
    if not bg_images:
        black_img = WORKDIR / "black.jpg"
        img = Image.new('RGB', (VIDEO_W, VIDEO_H), color=(0, 0, 0))
        img.save(black_img)
        bg_images = [str(black_img)]
    
    # Step 5: Create TTS
    tts_paths, durations = create_tts_with_emphasis(script_lines)
    
    # Step 6: Create video
    out_video = WORKDIR / "final_video.mp4"
    video_path = create_video_with_transitions(
        bg_images, script_lines, tts_paths, durations, out_video
    )
    
    if not video_path or not os.path.exists(video_path):
        log("Video creation failed")
        return
    
    # Step 7: Upload to YouTube
    youtube_title, youtube_description = create_youtube_metadata(
        article['title'], article['description']
    )
    
    video_id = upload_to_youtube(video_path, youtube_title, youtube_description)
    
    if video_id:
        save_uploaded(article['title'])
        log(f"Success! Uploaded: {youtube_title}")
        # Cleanup large files
        try:
            for file in WORKDIR.glob("*"):
                if file.suffix in ['.mp4', '.jpg', '.png']:
                    file.unlink()
        except:
            pass
    else:
        log("Upload failed - saving video locally")
        if not ON_GITHUB_ACTIONS:
            backup_path = Path("videos") / f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
            backup_path.parent.mkdir(exist_ok=True)
            try:
                import shutil
                shutil.copy2(video_path, backup_path)
                log(f"Video saved to: {backup_path}")
            except:
                pass

if __name__ == "__main__":
    main()
