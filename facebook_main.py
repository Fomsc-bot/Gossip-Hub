"""Render a short with the shared pipeline and publish it only to a Facebook Page."""

import os
import re
from pathlib import Path

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
from fb_playwright_poster import publish_to_facebook

# Facebook-only hashtag rules. Prefer specific story/entity terms, then the
# matching entertainment topic, with a single page tag. Avoid generic #viral/#fyp.
FACEBOOK_TOPIC_TAGS = (
    (re.compile(r"\b(movie|film|cinema|trailer|box office|director|premiere)\b", re.I), ("#MovieNews", "#FilmNews")),
    (re.compile(r"\b(music|singer|song|album|concert|tour|rapper|band)\b", re.I), ("#MusicNews", "#EntertainmentNews")),
    (re.compile(r"\b(award|awards|nomination|nominated|wins|winner|grammy|oscar)\b", re.I), ("#AwardsNews", "#EntertainmentNews")),
    (re.compile(r"\b(television|tv|series|episode|reality show|streaming)\b", re.I), ("#TVNews", "#EntertainmentNews")),
    (re.compile(r"\b(celebrity|actor|actress|star|hollywood|fame|famous)\b", re.I), ("#CelebrityNews", "#EntertainmentNews")),
    (re.compile(r"\b(scandal|controversy|dating|relationship|breakup|wedding|engaged|divorce|gossip)\b", re.I), ("#CelebrityNews", "#EntertainmentNews")),
)
ENTITY_TOKEN = re.compile(r"(?<!\w)(?:[A-Z][a-z]{2,}|[A-Z]{2,})(?!\w)")
ENTITY_STOPWORDS = {
    "Breaking", "Exclusive", "Latest", "Update", "News", "Entertainment",
    "Fans", "Report", "Reports", "Video", "Watch", "What", "Why", "How",
    "This", "That", "After", "Before", "When", "Where", "The", "A", "An",
    "Announces", "Announced", "Reveals", "Revealed", "Says", "Said", "Shares",
    "Shared", "Confirms", "Confirmed", "Posts", "Posted", "Drops", "Dropped",
    "New", "Big", "Major", "First", "Latest", "Here", "Now", "Just",
}


def facebook_content_hashtags(headline: str, description: str, lead: str = "") -> str:
    """Create a compact, story-relevant hashtag set for the Facebook Reel caption."""
    source_text = " ".join(part for part in (headline, description, lead) if part)
    tags = []

    # Proper-name phrases in the headline give the post a searchable story tag.
    tokens = ENTITY_TOKEN.findall(headline)
    entity_groups = []
    current_group = []
    for token in tokens:
        if token in ENTITY_STOPWORDS:
            if current_group:
                entity_groups.append(current_group)
                current_group = []
            continue
        current_group.append(token)
    if current_group:
        entity_groups.append(current_group)

    for group in entity_groups:
        # Prefer a full name/organization phrase; otherwise use a single named entity.
        phrase = group[:3] if len(group) >= 2 else group
        if len(phrase) == 1 and len(phrase[0]) < 4:
            continue
        tag = "#" + "".join(phrase)
        if tag.lower() not in {existing.lower() for existing in tags}:
            tags.append(tag)
        if len(tags) == 2:
            break

    # Match topic tags against the actual headline, description, and article lead.
    topic_tags = []
    for pattern, candidates in FACEBOOK_TOPIC_TAGS:
        if pattern.search(source_text):
            for candidate in candidates:
                if candidate.lower() not in {tag.lower() for tag in topic_tags}:
                    topic_tags.append(candidate)
    if not topic_tags:
        topic_tags.append("#EntertainmentNews")

    for tag in topic_tags:
        if tag.lower() not in {existing.lower() for existing in tags}:
            tags.append(tag)
        if len(tags) == 4:
            break

    if "#GossipHub".lower() not in {tag.lower() for tag in tags}:
        tags.append("#GossipHub")
    return " ".join(tags[:5])


def main() -> None:
    if not os.environ.get("FB_COOKIES_JSON"):
        raise RuntimeError("FB_COOKIES_JSON must be configured as a GitHub Actions secret")

    log("Starting Facebook-only short pipeline...")
    try:
        article_title, description, image_url, _article_url, lead = get_news_article()
        _generated_title, lines = generate_script(article_title, description, lead)
        story_description = description or lead
        hashtags = facebook_content_hashtags(article_title, description, lead)
        facebook_description = f"{story_description.strip()}\n\n{hashtags}".strip()
        background_path = fetch_and_prepare_bg(image_url)
        tts_paths, durations = create_tts(lines)

        video_path = WORKDIR / "facebook_reel.mp4"
        build_video(background_path, lines, tts_paths, durations, video_path)
        log(f"Facebook hashtags: {hashtags}")
        # Reuse the Playwright browser uploader. The existing publisher reads the
        # video path and caption from the environment and uses FB_COOKIES_JSON.
        os.environ["VIDEO_PATH"] = str(video_path)
        os.environ["CAPTION_TEXT"] = f"{article_title[:255]}\n\n{facebook_description}"
        publish_to_facebook()
        save_uploaded(article_title)
        log("Facebook-only pipeline completed successfully.")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
