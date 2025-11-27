import os
import requests
from moviepy.editor import (
    ImageClip,
    CompositeVideoClip,
    concatenate_videoclips,
    AudioFileClip
)
from PIL import Image
from io import BytesIO

NEWS_API_KEY = os.getenv("NEWS_API_KEY")
NUM_IMAGES = int(os.getenv("NUM_IMAGES", 5))
MUSIC_URL = os.getenv("MUSIC_URL", "")

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
IMAGE_DURATION = 5


# -------------------------------------------------------
# FETCH TOP NEWS
# -------------------------------------------------------
def get_news():
    print("[BOT] Fetching news...")

    try:
        r = requests.get(
            f"https://newsapi.org/v2/top-headlines?country=us&pageSize=5&apiKey={NEWS_API_KEY}",
            timeout=10
        ).json()
    except Exception as e:
        raise RuntimeError("❌ Network issue while fetching news") from e

    if not r or "articles" not in r or len(r["articles"]) == 0:
        raise ValueError("❌ No news returned from API")

    article = r["articles"][0]

    return (
        article.get("title", "Breaking News"),
        article.get("description", "No description available"),
        article.get("urlToImage"),
    )


# -------------------------------------------------------
# DOWNLOAD IMAGE
# -------------------------------------------------------
def download_image(url):
    if not url:
        return None

    try:
        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return None

        return Image.open(BytesIO(r.content)).convert("RGB")

    except Exception:
        return None


# -------------------------------------------------------
# FAST TEXT RENDER (NO IMAGEMAGICK)
# -------------------------------------------------------
def draw_text(text, fontsize=50):
    """Draw white text onto transparent PNG using PIL (very fast)."""
    from PIL import ImageDraw, ImageFont

    img = Image.new("RGBA", (VIDEO_WIDTH - 100, 300), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", fontsize)
    except:
        font = ImageFont.load_default()

    draw.text((10, 10), text, fill="white", font=font)
    return img


# -------------------------------------------------------
# CREATE FRAME (FAST)
# -------------------------------------------------------
def create_frame(background, title, desc):
    if background:
        background = background.resize((VIDEO_WIDTH, VIDEO_HEIGHT))
    else:
        background = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0))

    bg_path = "frame.jpg"
    background.save(bg_path)

    base = ImageClip(bg_path).set_duration(IMAGE_DURATION)

    title_img = draw_text(title, 60)
    desc_img = draw_text(desc, 40)

    title_clip = ImageClip(title_img).set_position(("center", 50)).set_duration(IMAGE_DURATION)
    desc_clip = ImageClip(desc_img).set_position(("center", VIDEO_HEIGHT - 250)).set_duration(IMAGE_DURATION)

    return CompositeVideoClip([base, title_clip, desc_clip])


# -------------------------------------------------------
# MAKE VIDEO
# -------------------------------------------------------
def create_video(frames):
    print("[BOT] Rendering video...")

    final = concatenate_videoclips(frames, method="compose")

    if MUSIC_URL:
        try:
            music_data = requests.get(MUSIC_URL, timeout=10).content
            with open("bg.mp3", "wb") as f:
                f.write(music_data)

            bg_audio = AudioFileClip("bg.mp3").volumex(0.2)
            final = final.set_audio(bg_audio)
        except Exception:
            print("[WARN] Could not load music.")

    final.write_videofile(
        "news_video.mp4",
        fps=30,
        codec="libx264",
        audio_codec="aac",
        threads=4,          # 🚀 MUCH FASTER
        preset="ultrafast"  # 🚀 SUPER FAST ENCODING
    )


# -------------------------------------------------------
# MAIN
# -------------------------------------------------------
def main():
    title, desc, img_url = get_news()

    img = download_image(img_url)
    if img is None:
        print("[BOT] No valid image; using black background.")

    frames = [create_frame(img, title, desc) for _ in range(NUM_IMAGES)]

    create_video(frames)


if __name__ == "__main__":
    main()
