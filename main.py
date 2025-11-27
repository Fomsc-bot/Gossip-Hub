import os
import requests
import random
from moviepy.editor import (
    ImageClip,
    TextClip,
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
IMAGE_DURATION = 5  # seconds each frame


# -------------------------------------------------------
# FETCH TOP NEWS
# -------------------------------------------------------
def get_news():
    print("[BOT] Fetching news...")

    url = f"https://newsapi.org/v2/top-headlines?country=us&pageSize=5&apiKey={NEWS_API_KEY}"
    r = requests.get(url).json()

    if "articles" not in r or len(r["articles"]) == 0:
        raise ValueError("❌ NEWS API returned no articles")

    article = r["articles"][0]

    title = article.get("title", "Breaking News")
    desc = article.get("description", "No description provided.")
    img_url = article.get("urlToImage")

    print("[BOT] News fetched:", title)
    return title, desc, img_url


# -------------------------------------------------------
# DOWNLOAD IMAGE OR CREATE BLACK BACKGROUND
# -------------------------------------------------------
def download_image(url):
    if not url:
        print("[BOT] No image URL — using black background.")
        return None

    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print("[BOT] Image download failed — using black background.")
            return None

        img = Image.open(BytesIO(response.content)).convert("RGB")
        return img

    except Exception:
        print("[BOT] Error downloading image — black background used.")
        return None


# -------------------------------------------------------
# CREATE HD FRAME
# -------------------------------------------------------
def create_frame(image, title, desc):
    if image:
        image = image.resize((VIDEO_WIDTH, VIDEO_HEIGHT))
    else:
        image = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), color=(0, 0, 0))

    image_path = "frame_temp.jpg"
    image.save(image_path)

    base_clip = ImageClip(image_path).set_duration(IMAGE_DURATION)

    title_clip = TextClip(
        title, fontsize=60, color="white", size=(VIDEO_WIDTH - 100, None), method="caption"
    ).set_position(("center", 50)).set_duration(IMAGE_DURATION)

    desc_clip = TextClip(
        desc, fontsize=40, color="white", size=(VIDEO_WIDTH - 100, None), method="caption"
    ).set_position(("center", VIDEO_HEIGHT - 300)).set_duration(IMAGE_DURATION)

    final = CompositeVideoClip([base_clip, title_clip, desc_clip])
    return final


# -------------------------------------------------------
# CREATE VIDEO
# -------------------------------------------------------
def create_video(frames):
    print("[BOT] Creating final video...")

    final_clip = concatenate_videoclips(frames, method="compose")

    # Add background music (optional)
    if MUSIC_URL:
        try:
            print("[BOT] Adding background music...")
            music_file = "music.mp3"
            with open(music_file, "wb") as f:
                f.write(requests.get(MUSIC_URL).content)

            audio = AudioFileClip(music_file).volumex(0.2)
            final_clip = final_clip.set_audio(audio)
        except:
            print("[BOT] Failed to load music. Continuing without it.")

    final_clip.write_videofile("news_video.mp4", fps=30, codec="libx264", audio_codec="aac")
    print("[BOT] Video saved as news_video.mp4")


# -------------------------------------------------------
# MAIN
# -------------------------------------------------------
def main():
    title, desc, img_url = get_news()

    frames = []
    images = [download_image(img_url) for _ in range(NUM_IMAGES)]

    for img in images:
        frames.append(create_frame(img, title, desc))

    create_video(frames)


if __name__ == "__main__":
    main()
