import os
import time
import re
import requests
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ------------------------------------------------------
#                      CONFIG
# ------------------------------------------------------

NEWS_API_KEY = os.getenv("NEWS_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
JSON2VIDEO_API_KEY = os.getenv("JSON2VIDEO_API_KEY")
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN")

WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

def log(*a): print("[BOT]", *a)

# ------------------------------------------------------
#                 UTILITIES
# ------------------------------------------------------
def sanitize(txt):
    return re.sub(r"[^\x00-\x7F]+", "", txt).strip()[:95]

def short_title(topic):
    topic = sanitize(topic)
    parts = topic.split()[:4]
    return " ".join(parts) + " 🔥 #shorts"


# ------------------------------------------------------
#     1. GET TRENDING ENTERTAINMENT TOPIC
# ------------------------------------------------------
def get_topic():
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    r = requests.get(url)
    r.raise_for_status()
    return r.json()["articles"][0]["title"]


# ------------------------------------------------------
#               2. WRITE VIDEO TEXT
# ------------------------------------------------------
def build_lines(topic):
    topic = sanitize(topic)
    return [
        f"Fans Reacting To: {topic}",
        "Social Media Is Exploding...",
        "People Are Shocked!",
        "Everyone Is Talking About It!",
        "What Do YOU Think?"
    ]


# ------------------------------------------------------
#          3. FETCH IMAGES FROM PEXELS/UNSPLASH
# ------------------------------------------------------
def get_images(topic):
    words = sanitize(topic).split()[:3]
    headers = {"Authorization": PEXELS_API_KEY}
    images = []

    for w in words:
        try:
            url = f"https://api.pexels.com/v1/search?query={w}&per_page=5"
            r = requests.get(url, headers=headers)
            if r.status_code == 200:
                for p in r.json().get("photos", []):
                    images.append(p["src"]["portrait"])
        except:
            pass

        if len(images) >= 5:
            break

    # fallback – Unsplash
    if len(images) < 5:
        for w in words:
            for i in range(3):
                images.append(f"https://source.unsplash.com/1080x1920/?{w}&sig={i}")

    return images[:5]


# ------------------------------------------------------
#             4. BUILD JSON2VIDEO MOVIE
# ------------------------------------------------------
def build_json2video(images, lines):
    scenes = []

    for i in range(len(lines)):
        scenes.append({
            "duration": 3.3,
            "background": {
                "image": images[i],
                "kenburns": True
            },
            "elements": [
                {
                    "type": "text",
                    "text": lines[i],
                    "x": "50%",
                    "y": "80%",
                    "width": "90%",
                    "style": {
                        "fontSize": 60,
                        "fontFamily": "Montserrat",
                        "fontWeight": "700",
                        "color": "white",
                        "textAlign": "center",
                        "background": "rgba(0,0,0,0.55)",
                        "padding": 40,
                        "borderRadius": 30
                    },
                    "animation": {
                        "type": "slide-up",
                        "duration": 0.7
                    }
                }
            ]
        })

    payload = {
        "output": {
            "resolution": "1080x1920",
            "format": "mp4",
            "fps": 30
        },
        "scenes": scenes
    }

    headers = {
        "X-API-KEY": JSON2VIDEO_API_KEY,
        "Content-Type": "application/json"
    }

    log("Submitting JSON2Video movie job...")

    r = requests.post("https://api.json2video.com/v2/movies", json=payload, headers=headers)

    if "id" not in r.json():
        log("JSON2Video ERROR:", r.text)
        raise RuntimeError("JSON2Video did not return a movie ID")

    movie_id = r.json()["id"]

    log("Movie queued. Waiting...")

    # poll job until done
    while True:
        status = requests.get(
            f"https://api.json2video.com/v2/movies/{movie_id}",
            headers=headers
        ).json()

        if status["status"] == "completed":
            url = status["output"]["url"]
            vid_path = WORKDIR / "final.mp4"

            with open(vid_path, "wb") as f:
                f.write(requests.get(url).content)

            log("Video ready and downloaded!")
            return str(vid_path)

        if status["status"] == "failed":
            raise RuntimeError("JSON2VIDEO FAILED")

        time.sleep(3)


# ------------------------------------------------------
#                 5. UPLOAD TO YOUTUBE
# ------------------------------------------------------
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
            "description": "Trending Entertainment Reactions • #shorts",
            "tags": ["shorts", "trending", "celebrity"]
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

    log("Uploaded to YouTube:", resp["id"])
    return resp["id"]


# ------------------------------------------------------
#                    MAIN
# ------------------------------------------------------
def main():
    log("Starting pipeline...")

    topic = get_topic()
    log("Topic:", topic)

    lines = build_lines(topic)
    images = get_images(topic)
    log("Images:", images)

    video = build_json2video(images, lines)

    title = short_title(topic)
    upload(video, title)

    log("DONE!")


if __name__ == "__main__":
    main()
