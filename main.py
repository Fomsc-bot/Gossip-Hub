# main.py
import os
import re
import time
import json
import random
import requests
from pathlib import Path
from gtts import gTTS
from mutagen import File as MutagenFile
from PIL import Image
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
MUSIC_URL = os.getenv("MUSIC_URL")   # optional direct mp3 URL

VIDEO_W, VIDEO_H = 1080, 1920
WORKDIR = Path("work")
WORKDIR.mkdir(exist_ok=True)

# Transfer.sh base (no key required). If transfer.sh isn't allowed in your runner/network,
# you'll need another public hosting (S3, your own web host, or a CDN).
TRANSFER_BASE = "https://transfer.sh"

def log(*args, **kwargs):
    print("[BOT]", *args, **kwargs)

# ---------------- Helpers ----------------
def sanitize_title_three_words(topic: str) -> str:
    words = re.findall(r"\w+", topic)
    if not words:
        return "Shorts 🔥"
    core = " ".join(words[:3])
    # pick a relevant emoji if it contains keywords (very small heuristic)
    emoji = "🔥"
    if any(w.lower() in ("love","romance") for w in words[:3]):
        emoji = "💘"
    elif any(w.lower() in ("fight","battle","wwe","boxing","brawl") for w in words[:3]):
        emoji = "🥊"
    elif any(w.lower() in ("scandal","rumor","gossip") for w in words[:3]):
        emoji = "🗣️"
    return f"{core} {emoji}"

def sanitize_description(topic: str) -> str:
    t = re.sub(r"\s+", " ", topic).strip()
    return (
        f"This short covers fan reactions & trending chatter about: {t}\n\n"
        "Content is commentary based on public social media & news. Not confirmed information.\n\n"
        "#shorts #celebrity #gossip"
    )

# ---------------- 1) Get trending topic ----------------
def get_trending_topic():
    log("Fetching trending entertainment topic...")
    url = f"https://newsapi.org/v2/top-headlines?category=entertainment&pageSize=5&apiKey={NEWS_API_KEY}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    articles = data.get("articles") or []
    if not articles:
        raise RuntimeError("No articles returned from NewsAPI.")
    title = articles[0].get("title") or articles[0].get("description") or "Trending Entertainment"
    log("Topic:", title)
    return title

# ---------------- 2) Create lines ----------------
def create_lines(topic):
    return [
        f"Fans are reacting to: {topic}",
        "Social media is buzzing right now.",
        "Rumors are spreading fast.",
        "Nothing confirmed yet — just online chatter.",
        "What do YOU think?"
    ]

# ---------------- 3) Get images (Pexels primary, Unsplash fallback) ----------------
def pexels_search_photo_urls(query, per_page=8):
    if not PEXELS_API_KEY:
        return []
    headers = {"Authorization": PEXELS_API_KEY}
    try:
        r = requests.get("https://api.pexels.com/v1/search", params={"query": query, "per_page": per_page}, headers=headers, timeout=15)
        if r.status_code != 200:
            log("Pexels returned", r.status_code, "->", r.text[:200])
            return []
        data = r.json()
        urls = []
        for p in data.get("photos", []):
            src = p.get("src", {})
            # pick vertical-friendly sizes
            url = src.get("portrait") or src.get("large2x") or src.get("large") or src.get("medium")
            if url:
                urls.append(url)
        return urls
    except Exception as e:
        log("Pexels search error:", e)
        return []

def unsplash_source_urls(query, count=4):
    urls = []
    for _ in range(count):
        urls.append(f"https://source.unsplash.com/{VIDEO_W}x{VIDEO_H}/?{query}&sig={random.randint(1,999999)}")
    return urls

def get_image_urls_for_topic(topic, required=5):
    keywords = []
    # simple keyword extraction: use capitalized sequences first, then words
    caps = re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*", topic)
    if caps:
        for c in caps:
            keywords.append(c)
    words = re.findall(r"\w+", topic)
    if words:
        keywords += [" ".join(words[:3]), words[0]]
    # keep unique and not too long
    seen = set()
    final_kw = []
    for k in keywords:
        kk = re.sub(r"[^\w\s]", "", k).strip()
        if kk and kk.lower() not in seen:
            final_kw.append(kk)
            seen.add(kk.lower())
    if not final_kw:
        final_kw = [topic[:30]]

    image_urls = []
    for kw in final_kw:
        # try pexels
        urls = pexels_search_photo_urls(kw, per_page=6)
        for u in urls:
            if len(image_urls) >= required: break
            image_urls.append(u)
        if len(image_urls) >= required: break

    # fallback to unsplash for any missing ones
    if len(image_urls) < required:
        for kw in final_kw:
            for u in unsplash_source_urls(kw, count=3):
                if len(image_urls) >= required: break
                image_urls.append(u)
            if len(image_urls) >= required: break

    # final fallback: single dark placeholder (we can keep many copies)
    while len(image_urls) < required:
        # create placeholder and host locally (we will use local file in JSON2Video? better to provide transfer.sh)
        placeholder = create_placeholder_image()
        url = upload_file_to_transfer(placeholder)
        image_urls.append(url)

    log("Using image URLs:", image_urls[:required])
    return image_urls[:required]

# helper to create placeholder (returns path)
def create_placeholder_image():
    p = WORKDIR / "placeholder.jpg"
    if p.exists():
        return str(p)
    img = Image.new("RGB", (VIDEO_W, VIDEO_H), (22,22,22))
    img.save(p, "JPEG", quality=85)
    return str(p)

# ---------------- 4) TTS per-line + upload to transfer.sh ----------------
def create_tts_and_upload(lines, lang="en"):
    audio_urls = []
    durations = []
    for idx, line in enumerate(lines):
        safe = re.sub(r"\s+", " ", line).strip()
        out = WORKDIR / f"tts_line_{idx}.mp3"
        log("Generating TTS for line", idx, "len:", len(safe))
        tts = gTTS(safe, lang=lang, slow=False)
        tts.save(str(out))
        # measure duration using mutagen
        try:
            m = MutagenFile(str(out))
            dur = float(m.info.length)
        except Exception:
            # fallback: estimate 0.45s per word
            dur = max(1.0, 0.45 * len(safe.split()))
            log("Mutagen failed; estimated duration:", dur)
        durations.append(dur)
        # upload MP3 to transfer.sh so JSON2Video can fetch it
        url = upload_file_to_transfer(out)
        if not url:
            raise RuntimeError("Failed to upload TTS mp3 to transfer.sh")
        audio_urls.append(url)
        log("Uploaded TTS", idx, "->", url, "duration:", dur)
    return audio_urls, durations

def upload_file_to_transfer(path: Path) -> str:
    """
    Upload file to transfer.sh using PUT. Returns public URL on success.
    transfer.sh typically returns plain-text URL in response body.
    """
    fn = path.name
    url = f"{TRANSFER_BASE}/{fn}"
    try:
        with open(path, "rb") as fh:
            r = requests.put(url, data=fh, timeout=60)
        if r.status_code in (200, 201):
            resp_text = r.text.strip()
            # sometimes transfer.sh returns plain URL, sometimes JSON with 'url'
            if resp_text.startswith("http"):
                return resp_text
            try:
                j = r.json()
                # try common fields
                return j.get("url") or j.get("raw_url") or j.get("download_url") or resp_text
            except Exception:
                return resp_text
        else:
            log("transfer.sh upload failed", r.status_code, r.text[:300])
            return None
    except Exception as e:
        log("transfer.sh error:", e)
        return None

# ---------------- 5) Build JSON2Video job & submit ----------------
def build_and_submit_json2video(image_urls, lines, audio_urls, durations, music_url=None):
    """
    Builds a timeline payload for JSON2Video and submits it.
    Uses per-scene durations and background image/audio links.
    Returns local path to downloaded final MP4.
    """
    # Build scenes: one scene per line
    timeline = []
    for i, line in enumerate(lines):
        scene = {
            "type": "scene",
            "duration": max(1.2, float(durations[i])),
            "background": {"type": "image", "src": image_urls[i % len(image_urls)]},
            "elements": [
                {
                    "type": "text",
                    "text": line,
                    "position": "bottom",
                    "style": {
                        "fontSize": 56,
                        "color": "#FFFFFF",
                        "background": "rgba(0,0,0,0.6)",
                        "padding": 18,
                        "textAlign": "center"
                    }
                },
                {
                    "type": "audio",
                    "src": audio_urls[i],
                    "start": 0
                }
            ]
        }
        timeline.append(scene)

    # optional background music as separate global audio track (looped)
    output_obj = {"format": "mp4", "resolution": "1080x1920"}
    payload = {"output": output_obj, "timeline": timeline}
    if music_url:
        # attach as music at root level, JSON2Video may support it depending on API. We'll also add it as first element fallback.
        payload["music"] = {"src": music_url, "volume": 0.2, "loop": True}

    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": JSON2VIDEO_API_KEY
    }

    submit_url = "https://api.json2video.com/v2/render"
    log("Submitting JSON2Video payload (size roughly):", len(json.dumps(payload)) , "bytes")
    r = requests.post(submit_url, json=payload, headers=headers, timeout=60)
    if r.status_code not in (200, 201):
        # log full response for debugging
        log("JSON2Video submit failed:", r.status_code, r.text[:2000])
        raise RuntimeError("JSON2Video submit failed: " + r.text[:4000])

    data = None
    try:
        data = r.json()
    except Exception:
        log("JSON2Video returned non-json:", r.text[:1000])
        raise RuntimeError("JSON2Video submit returned non-JSON response")

    # JSON could contain job id in several fields; try common ones
    job_id = data.get("id") or data.get("jobId") or data.get("job_id") or data.get("renderId")
    if not job_id:
        # Sometimes API returns object with "data": {"id": ...}
        if isinstance(data.get("data"), dict):
            job_id = data["data"].get("id") or data["data"].get("jobId")
    if not job_id:
        log("Unexpected JSON2Video response:", json.dumps(data)[:1500])
        raise RuntimeError("JSON2Video response missing job id")

    log("JSON2Video job id:", job_id)

    # Poll the job endpoint
    status_url = f"https://api.json2video.com/v2/render/{job_id}"
    log("Polling JSON2Video job at", status_url)
    final_url = None
    for attempt in range(120):  # up to ~10 minutes with sleep(5)
        try:
            s = requests.get(status_url, headers=headers, timeout=30)
            if s.status_code != 200:
                log("Status request returned", s.status_code, s.text[:400])
            j = s.json()
        except Exception as e:
            log("Status request error:", e)
            j = {}
        st = j.get("status") or j.get("state") or j.get("jobStatus")
        log("Job status:", st)
        if st and str(st).lower() in ("completed", "success", "done"):
            # find output link
            out = j.get("output") or j.get("result") or j.get("data")
            # some APIs provide out["url"] or out["videoUrl"]
            if isinstance(out, dict):
                final_url = out.get("url") or out.get("videoUrl") or out.get("downloadUrl") or out.get("output")
            elif isinstance(out, str):
                final_url = out
            # also check top-level fields
            final_url = final_url or j.get("url") or j.get("outputUrl") or j.get("resultUrl")
            if final_url:
                break
            # otherwise sometimes job has list of assets:
            assets = j.get("assets") or j.get("files") or j.get("outputs")
            if isinstance(assets, list) and assets:
                for a in assets:
                    if isinstance(a, dict):
                        u = a.get("url") or a.get("src")
                        if u and u.endswith(".mp4"):
                            final_url = u
                            break
                if final_url:
                    break
            # if no URL yet, keep polling
        elif st and str(st).lower() in ("failed","error"):
            log("JSON2Video job failed:", j.get("error") or j)
            raise RuntimeError("JSON2Video job failed: " + str(j.get("error") or j))
        time.sleep(5)

    if not final_url:
        log("JSON2Video job completed but no final URL found. Full response snippet:", json.dumps(j)[:2000])
        raise RuntimeError("Final video URL not found after polling")

    # Download final mp4
    log("Downloading final video from", final_url)
    outpath = WORKDIR / "final.json2video.mp4"
    r2 = requests.get(final_url, stream=True, timeout=60)
    if r2.status_code != 200:
        log("Failed to download final mp4:", r2.status_code, r2.text[:400])
        raise RuntimeError("Failed to download final mp4")
    with open(outpath, "wb") as fh:
        for chunk in r2.iter_content(chunk_size=32768):
            if chunk:
                fh.write(chunk)
    log("Saved final video to", outpath)
    return str(outpath)

# ---------------- 6) YouTube upload (unlisted) ----------------
def get_youtube_service():
    if not (YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise RuntimeError("YouTube credentials missing")
    creds = Credentials(
        token=None,
        refresh_token=YT_REFRESH_TOKEN,
        client_id=YT_CLIENT_ID,
        client_secret=YT_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)

def upload_to_youtube(video_path, title, description, tags=None):
    yt = get_youtube_service()
    if tags is None:
        tags = ["shorts", "celebrity", "gossip"]
    body = {
        "snippet": {"title": sanitize_title(title) if (lambda t: (t[:1] and True))(title) else title,
                    "description": description,
                    "tags": tags},
        "status": {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/*")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            log(f"Upload progress: {int(status.progress() * 100)}%")
    log("YouTube upload complete. id:", resp.get("id"))
    return resp.get("id")

def sanitize_title(t: str) -> str:
    # ensure ASCII, trim, and limit length
    s = re.sub(r"[^\x00-\x7F]+", "", t)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:100]

# ------------------ MAIN ------------------
def main():
    log("Starting pipeline...")
    topic = get_trending_topic()
    lines = create_lines(topic)
    # image URLs (Pexels primary)
    image_urls = get_image_urls_for_topic(topic, required=len(lines))
    # tts + upload to transfer.sh => public audio URLs + durations
    audio_urls, durations = create_tts_and_upload(lines)
    # optionally music_url (already a public URL if provided)
    music = MUSIC_URL if MUSIC_URL else None

    # Build JSON2Video job and get final mp4
    final_mp4 = build_and_submit_json2video(image_urls, lines, audio_urls, durations, music_url=music)

    # Upload to YouTube as unlisted; use 3-word emoji title
    title = sanitize_title(sanitize_title_three_words(topic))
    description = sanitize_description(topic)
    vid_id = upload_to_youtube(final_mp4, title, description)
    log("Done! YouTube id:", vid_id)

if __name__ == "__main__":
    main()
