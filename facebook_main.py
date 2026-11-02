"""Render a short with the shared pipeline and publish it only to a Facebook Page."""

import os
from pathlib import Path

import requests

from main3 import (
    WORKDIR,
    build_video,
    cleanup,
    create_tts,
    fetch_and_prepare_bg,
    generate_script,
    get_news_article,
    log,
    save_uploaded,
)


def _response_json(response: requests.Response, action: str) -> dict:
    try:
        result = response.json()
    except ValueError:
        result = {}
    if not response.ok:
        error = result.get("error", {}) if isinstance(result, dict) else {}
        message = error.get("message", "") if isinstance(error, dict) else ""
        code = error.get("code", "") if isinstance(error, dict) else ""
        # Do not print request URLs; Meta access tokens are sent as query parameters.
        raise RuntimeError(
            f"Facebook {action} failed (HTTP {response.status_code}, code {code}): "
            f"{message or 'no error details returned'}"
        )
    return result


def upload_facebook_reel(video_path: Path, title: str, description: str) -> None:
    page_id = os.environ.get("FB_PAGE_ID")
    page_token = os.environ.get("FB_PAGE_ACCESS_TOKEN")
    api_version = os.environ.get("FB_GRAPH_API_VERSION", "v26.0")
    if not page_id or not page_token:
        raise RuntimeError("FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN must be configured as GitHub Actions secrets")

    api_root = f"https://graph.facebook.com/{api_version}"

    # Phase 1: initialize the Reel upload and use Meta's returned upload URL.
    start = _response_json(
        requests.post(
            f"{api_root}/{page_id}/video_reels",
            params={"upload_phase": "start", "access_token": page_token},
            timeout=60,
        ),
        "upload initialization",
    )
    video_id = start.get("video_id")
    upload_url = start.get("upload_url")
    if not video_id or not upload_url:
        raise RuntimeError("Facebook did not return a video ID and upload URL")

    # Phase 2: upload this workflow's locally rendered MP4.
    with video_path.open("rb") as video_file:
        uploaded = requests.post(
            upload_url,
            headers={
                "Authorization": f"OAuth {page_token}",
                "offset": "0",
                "file_size": str(video_path.stat().st_size),
                "Content-Type": "application/octet-stream",
            },
            data=video_file,
            timeout=(60, 900),
        )
    _response_json(uploaded, "video upload")

    # Phase 3: publish the uploaded video as a Page Reel.
    published = _response_json(
        requests.post(
            f"{api_root}/{page_id}/video_reels",
            params={
                "access_token": page_token,
                "video_id": video_id,
                "upload_phase": "finish",
                "video_state": "PUBLISHED",
                "title": title[:255],
                "description": description,
            },
            timeout=60,
        ),
        "Reel publishing",
    )
    if published.get("success") is False:
        raise RuntimeError("Facebook did not confirm Reel publishing")
    log(f"Facebook Reel published successfully! Video ID: {video_id}")


def main() -> None:
    if not os.environ.get("FB_PAGE_ID") or not os.environ.get("FB_PAGE_ACCESS_TOKEN"):
        raise RuntimeError("FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN must be configured before rendering")

    log("Starting Facebook-only short pipeline...")
    try:
        article_title, description, image_url, _article_url, lead = get_news_article()
        post_title, lines = generate_script(article_title, description, lead)
        background_path = fetch_and_prepare_bg(image_url)
        tts_paths, durations = create_tts(lines)

        video_path = WORKDIR / "facebook_reel.mp4"
        build_video(background_path, lines, tts_paths, durations, video_path)
        upload_facebook_reel(video_path, post_title, description or lead)
        save_uploaded(article_title)
        log("Facebook-only pipeline completed successfully.")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
