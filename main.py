import os
import re
import requests
from pathlib import Path
from gtts import gTTS
from PIL import Image
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# -------- CONFIG --------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
JSON2VIDEO_API_KEY = os.getenv("JSON2VIDEO_API_KEY")
WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

def log(*a): print("[BOT]", *a)

# ------------------- Utilities --------------------
def short_title(topic):
    words = topic.split()
    clean = " ".join(words[:3])
    return f"{clean} 🔥"

def sanitize(t):
    return re.sub(r"[^\x00-\x7F]+", "", t)[:90]

# ------------------- 1. Get Trending Topic --------------------
def get_topic():
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    r = requests.get(url)
    r.raise_for_status()
    article = r.json()["articles"][0]
    return article["title"]

# ------------------- 2. Make Lines --------------------
def build_lines(topic):
    return [
        f"Fans are reacting to: {topic}",
        "Social media is buzzing right now.",
        "Rumors are spreading fast.",
        "Nothing confirmed yet — just online chatter.",
        "What do YOU think?"
    ]

# ------------------- 3. Download Images --------------------
def get_images(topic):
    keywords = topic.split(" ")[:3]
    images = []

    headers = {"Authorization": PEXELS_API_KEY}

    for kw in keywords:
        url = f"https://api.pexels.com/v1/search?query={kw}&per_page=3"
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            data = r.json()
            for p in data.get("photos", []):
                img_url = p["src"].get("portrait")
                if img_url:
                    pth = WORKDIR / f"img_{len(images)}.jpg"
                    img = requests.get(img_url).content
                    with open(pth, "wb") as f: f.write(img)
                    images.append(str(pth))
        if len(images) >= 5:
            break

    # fallback
    if not images:
        for kw in keywords:
            for _ in range(3):
                url = f"https://source.unsplash.com/1080x1920/?{kw}"
                pth = WORKDIR / f"img_fallback_{len(images)}.jpg"
                img = requests.get(url).content
                with open(pth, "wb") as f: f.write(img)
                images.append(str(pth))
                if len(images) >= 5:
                    break

    return images[:5]

# ------------------- 4. Create TTS --------------------
def create_tts(lines):
    audio_files = []
    durations = []

    for i, line in enumerate(lines):
        path = WORKDIR / f"audio_{i}.mp3"
        tts = gTTS(line, lang="en")
        tts.save(str(path))

        # get duration
        import mutagen
        duration = mutagen.File(str(path)).info.length
        durations.append(duration)
        audio_files.append(str(path))

    return audio_files, durations

# ------------------- 5. Build JSON2Video Job --------------------
def build_json2video(images, lines, audios, durations):
    slides = []
    start_t = 0

    for i in range(len(lines)):
        slides.append({
            "type": "scene",
            "duration": durations[i],
            "background": images[i],
            "elements": [
                {
                    "type": "text",
                    "text": lines[i],
                    "position": "bottom",
                    "style": {
                        "fontSize": 56,
                        "color": "white",
                        "background": "rgba(0,0,0,0.6)"
                    }
                },
                {
                    "type": "audio",
                    "src": audios[i],
                    "start": 0
                }
            ]
        })
        start_t += durations[i]

    payload = {
        "output": {
            "format": "mp4",
            "resolution": "1080x1920"
        },
        "timeline": slides
    }

    headers = {
        "X-API-KEY": JSON2VIDEO_API_KEY,
        "Content-Type": "application/json"
    }

    log("Submitting JSON2Video job...")
    r = requests.post("https://api.json2video.com/v2/render", json=payload, headers=headers)
    job_id = r.json()["id"]

    log("Job queued. Waiting for completion...")
    while True:
        s = requests.get(f"https://api.json2video.com/v2/render/{job_id}", headers=headers).json()
        if s["status"] == "completed":
            output_url = s["output"]["url"]
            vid_path = WORKDIR / "final.mp4"
            with open(vid_path, "wb") as f:
                f.write(requests.get(output_url).content)
            log("Downloaded final video.")
            return str(vid_path)
        elif s["status"] == "failed":
            raise RuntimeError("JSON2VIDEO FAILED")
        time.sleep(3)

# ------------------- 6. Upload to YouTube --------------------
def yt_service():
    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)

def upload(video_path, title):
    yt = yt_service()

    body = {
        "snippet": {
            "title": sanitize(title),
            "description": "Unconfirmed online reactions & trending rumors.\n#celebrity #shorts",
            "tags": ["shorts", "celebrity", "gossip"]
        },
        "status": {
            "privacyStatus": "unlisted"
        }
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    resp = None
    while resp is None:
        status, resp = req.next_chunk()
    log("Uploaded:", resp["id"])
    return resp["id"]

# ------------------- MAIN --------------------
def main():
    topic = get_topic()
    lines = build_lines(topic)
    images = get_images(topic)
    audios, durations = create_tts(lines)

    video = build_json2video(images, lines, audios, durations)

    title = short_title(topic)
    upload(video, title)

    log("All done.")

if __name__ == "__main__":
    main()
