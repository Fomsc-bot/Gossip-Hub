import requests
from pathlib import Path
from gtts import gTTS
from moviepy.editor import ImageClip, AudioFileClip, CompositeVideoClip, concatenate_videoclips, vfx
from PIL import Image, ImageDraw, ImageFont
import re

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

NEWS_API_KEY = "YOUR_NEWSAPI_KEY"

VIDEO_W, VIDEO_H = 1080, 1920

def log(*args):
    print("[BOT]", *args)

# ------------------- Fetch news -------------------
def get_news():
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    r = requests.get(url).json()
    article = r["articles"][0]
    title = article["title"]
    description = article.get("description","")
    image_url = article.get("urlToImage")
    if not image_url:
        raise RuntimeError("No image in the news article")
    return title, description, image_url

# ------------------- Download image -------------------
def download_image(url):
    resp = requests.get(url)
    path = WORKDIR / "news_image.jpg"
    with open(path,"wb") as f: f.write(resp.content)
    return str(path)

# ------------------- Create TTS -------------------
def create_tts(text):
    path = WORKDIR / "audio.mp3"
    tts = gTTS(text, lang="en")
    tts.save(path)
    return str(path)

# ------------------- Render caption -------------------
def render_caption(text, fontsize=60):
    img = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0,0,0,0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fontsize)
    
    # Wrap text
    lines = []
    words = text.split()
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textsize(test,font=font)[0] < VIDEO_W*0.9:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)

    # Draw centered
    y = VIDEO_H*0.7
    for line in lines:
        w, h = draw.textsize(line,font=font)
        x = (VIDEO_W - w)/2
        draw.text((x,y), line, font=font, fill="white")
        y += h + 5

    path = WORKDIR / "caption.png"
    img.save(path)
    return str(path)

# ------------------- Build video -------------------
def build_video(image_path, caption_path, audio_path):
    clip_img = ImageClip(image_path).set_duration(10).resize((VIDEO_W, VIDEO_H))
    # Ken Burns zoom
    clip_img = clip_img.fx(vfx.resize, lambda t:1 + 0.02*t)

    caption_clip = ImageClip(caption_path).set_duration(10)
    
    audio = AudioFileClip(audio_path)
    clip_img = clip_img.set_audio(audio)

    final = CompositeVideoClip([clip_img, caption_clip])
    out_path = WORKDIR / "final.mp4"
    final.write_videofile(str(out_path), fps=24, codec="libx264", audio_codec="aac")
    return str(out_path)

# ------------------- Main -------------------
def main():
    log("Fetching news...")
    title, desc, img_url = get_news()
    log("Downloading image...")
    image_path = download_image(img_url)
    log("Generating TTS...")
    audio_path = create_tts(title + ". " + desc)
    log("Rendering caption...")
    caption_path = render_caption(title + "\n" + desc)
    log("Building video...")
    video_path = build_video(image_path, caption_path, audio_path)
    log("Video saved at:", video_path)

if __name__ == "__main__":
    main()
