import os
import time
import re
import requests
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ---------------- CONFIG ----------------
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
JSON2VIDEO_API_KEY = os.getenv("JSON2VIDEO_API_KEY")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

def log(*a): print("[BOT]", *a)

# ---------------- UTIL ----------------
def sanitize(x):
    return re.sub(r"[^\x00-\x7F]+", "", x).strip()[:95]

def short_title(topic):
    words = topic.split()
    clean = " ".join(words[:4])
    return sanitize(clean + " 🔥 #shorts")

# ---------------- 1. GET TRENDING TOPIC ----------------
def get_topic():
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    r = requests.get(url)
    r.raise_for_status()
    article = r.json()["articles"][0]
    return article["title"]

# ---------------- 2. GENERATE LINES ----------------
def build_lines(topic):
    topic = sanitize(topic)
    return [
        f"Fans Reacting to: {topic}",
        "Social media is exploding...",
        "People are shocked!",
        "Everyone is talking about it!",
        "What do YOU think?"
    ]

# ---------------- 3. GET IMAGES ----------------
def get_images(topic):
    words = topic.split()[:3]
    headers = {"Authorization": PEXELS_API_KEY}
    images = []

    for w in words:
        url = f"https://api.pexels.com/v1/search?query={w}&per_page=5"
        r = requests.get(url, headers=headers)

        if r.status_code == 200:
            for p in r.json().get("photos", []):
                images.append(p["src"]["portrait"])

        if len(images) >= 5:
            break

    # fallback → Unsplash
    if len(images) < 5:
        for w in words:
            for i in range(3):
                images.append(f"https://source.unsplash.com/1080x1920/?{w}&sig={i}")

    return images[:5]

# ---------------- 4. JSON2VIDEO RENDER ----------------
def build_json2video(images, lines):
    slides = []

    for i in range(len(lines)):
        slides.append({
            "type": "scene",
            "duration": 3.5,
            "background": images[i],
            "elements": [
                {
                    "type": "text",
                    "text": lines[i],
                    "position": "center",
                    "animation": "slide_up",
                    "style": {
                        "fontSize": 64,
                        "fontFamily": "Montserrat",
                        "fontWeight": "700",
                        "color": "white",
                        "background": "rgba(0,0,0,0.55)",
                        "padding": 40,
                        "borderRadius": 30
                    }
                }
            ]
        })

    payload = {
        "output": {
            "format": "mp4",
            "resolution": "1080x1920",
            "fps": 30
        },
        "timeline": slides
    }

    headers = {
        "X-API-KEY": JSON2VIDEO_API_KEY,
        "Content-Type": "application/json"
    }

    log("Submitting JSON2Video job...")
    r = requests.post("https://api.json2video.com/v2/render", json=payload, headers=headers)
    if "id" not in r.json():
        log("JSON2Video error response:", r.text)
        raise RuntimeError("JSON2Video did not return job ID")

    job_id = r.json()["id"]

    log("Job queued. Waiting...")
    while True:
        status = requests.get(f"https://api.json2video.com/v2/render/{job_id}", headers=headers).json()

        if status["status"] == "completed":
            url = status["output"]["url"]
            vid_path = WORKDIR / "final.mp4"
            with open(vid_path, "wb") as f:
                f.write(requests.get(url).content)
            log("Video downloaded.")
            return str(vid_path)

        if status["status"] == "failed":
            raise RuntimeError("JSON2Video FAILED")

        time.sleep(3)

# ---------------- 5. UPLOAD TO YOUTUBE ----------------
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
            "description": "Trending reactions • Breaking Entertainment Buzz\n#shorts",
            "tags": ["shorts", "trending", "celebrity"]
        },
        "status": {
            "privacyStatus": "unlisted"
        }
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()

    log("Uploaded video:", response["id"])
    return response["id"]

# ---------------- MAIN ----------------
def main():
    log("Starting pipeline...")
    topic = get_topic()
    log("Topic:", topic)

    lines = build_lines(topic)
    images = get_images(topic)
    log("Using images:", images)

    video = build_json2video(images, lines)

    title = short_title(topic)
    upload(video, title)

    log("DONE.")

if __name__ == "__main__":
    main()
