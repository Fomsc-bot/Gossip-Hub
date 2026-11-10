"""Create a Blogger draft from a fresh entertainment news story.

This publisher is intentionally separate from the video workflows and creates
drafts for editorial review. It uses only Blogger-related credentials plus
optional news and Gemini API keys supplied through environment variables.
"""

from __future__ import annotations

import html
import json
import os
import random
import re
from typing import Any
from urllib.parse import urlparse

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


BLOGGER_SCOPE = "https://www.googleapis.com/auth/blogger"
USER_AGENT = "GossipHubBloggerPublisher/1.0 (editorial automation)"


def clean(value: Any) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)).strip()


def get_news() -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    news_key = os.getenv("NEWS_API_KEY")
    gnews_key = os.getenv("GNEWS_API_KEY")
    if news_key:
        response = requests.get(
            "https://newsapi.org/v2/top-headlines",
            params={"category": "entertainment", "pageSize": 20, "apiKey": news_key},
            timeout=25,
        )
        response.raise_for_status()
        articles.extend(response.json().get("articles", []))
    if gnews_key:
        response = requests.get(
            "https://gnews.io/api/v4/top-headlines",
            params={"topic": "entertainment", "lang": "en", "max": 20, "token": gnews_key},
            timeout=25,
        )
        response.raise_for_status()
        articles.extend(response.json().get("articles", []))
    if not articles:
        raise RuntimeError("Set NEWS_API_KEY and/or GNEWS_API_KEY in repository Actions secrets.")
    random.shuffle(articles)
    for item in articles:
        title = clean(item.get("title"))
        url = clean(item.get("url") or item.get("link"))
        description = clean(item.get("description") or item.get("content"))
        if title and url.startswith(("https://", "http://")) and description:
            return [{"title": title, "url": url, "description": description,
                     "source": clean((item.get("source") or {}).get("name")),
                     "image": clean(item.get("urlToImage") or item.get("image"))}]
    raise RuntimeError("The news feeds returned no usable articles with source links and descriptions.")


def generate_article(story: dict[str, str]) -> tuple[str, str, list[str]]:
    """Return an article title and body HTML, requiring Gemini to add original value."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required; refusing to publish a thin auto-rewrite.")
    prompt = f"""Write an original, useful entertainment-news blog article based only on the source details below.
Do not invent quotes, facts, dates, motives, or confirmation. Attribute unconfirmed claims as unconfirmed.
Paraphrase; do not reproduce source wording. Explain what is known, what remains unclear, and why readers may care.
Return only valid JSON with keys title, dek, paragraphs (array of 5-7 substantive paragraphs), and labels (array of 3-5 short labels).
Avoid sensational/clickbait claims. Do not claim exclusive reporting.

Source headline: {story['title']}
Source summary: {story['description']}
Publisher: {story['source'] or urlparse(story['url']).netloc}
Source URL: {story['url']}
"""
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json", "temperature": 0.5}},
        timeout=90,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        # Keep the API key (sent in the query string) out of the Actions log.
        detail = " ".join(response.text.split())[:1000]
        raise RuntimeError(
            f"Gemini request failed (HTTP {response.status_code}) for model {model}: {detail}"
        ) from exc
    raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    data = json.loads(raw)
    title = clean(data.get("title"))
    paragraphs = [clean(p) for p in data.get("paragraphs", []) if clean(p)]
    dek = clean(data.get("dek"))
    if not title or len(paragraphs) < 5:
        raise RuntimeError("Gemini returned an incomplete article; no Blogger draft was created.")

    body = []
    if dek:
        body.append(f"<p><em>{html.escape(dek)}</em></p>")
    body.extend(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)
    image = story.get("image")
    if image.startswith("https://"):
        body.insert(0, f'<p><img src="{html.escape(image, quote=True)}" alt="" /></p>')
    publisher = story["source"] or urlparse(story["url"]).netloc
    body.append(
        '<p><strong>Source:</strong> '
        f'<a href="{html.escape(story["url"], quote=True)}" rel="nofollow noopener">'
        f"{html.escape(publisher)}</a></p>"
    )
    labels = [clean(label)[:40] for label in data.get("labels", []) if clean(label)]
    return title, "\n".join(body), labels


def blogger_service():
    required = ("BLOGGER_CLIENT_ID", "BLOGGER_CLIENT_SECRET", "BLOGGER_REFRESH_TOKEN")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing GitHub Actions secrets: " + ", ".join(missing))
    blog_id = os.getenv("BLOGGER_BLOG_ID")
    if not blog_id:
        raise RuntimeError("Set BLOGGER_BLOG_ID as a repository Actions secret.")
    credentials = Credentials(
        token=None,
        refresh_token=os.environ["BLOGGER_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["BLOGGER_CLIENT_ID"],
        client_secret=os.environ["BLOGGER_CLIENT_SECRET"],
        scopes=[BLOGGER_SCOPE],
    )
    credentials.refresh(Request())
    return build("blogger", "v3", credentials=credentials, cache_discovery=False), blog_id


def already_posted(service, blog_id: str, candidate: str) -> bool:
    """Skip near-identical titles already present in published or draft posts."""
    normalized = re.sub(r"[^a-z0-9]+", " ", candidate.lower()).strip()
    token_set = {word for word in normalized.split() if len(word) > 3}
    page_token = None
    while True:
        result = service.posts().list(
            blogId=blog_id,
            status="live,draft",
            fetchBodies=False,
            maxResults=100,
            pageToken=page_token,
        ).execute()
        for post in result.get("items", []):
            existing = re.sub(r"[^a-z0-9]+", " ", clean(post.get("title")).lower()).strip()
            existing_tokens = {word for word in existing.split() if len(word) > 3}
            if normalized == existing or (token_set and len(token_set & existing_tokens) / len(token_set) >= 0.8):
                print("A post with a matching title already exists; skipping this story.")
                return True
        page_token = result.get("nextPageToken")
        if not page_token:
            return False


def main() -> None:
    story = get_news()[0]
    print(f"Selected story: {story['title']}")
    title, content, labels = generate_article(story)
    service, blog_id = blogger_service()
    if already_posted(service, blog_id, title):
        return
    result = service.posts().insert(
        blogId=blog_id,
        isDraft=True,
        body={"kind": "blogger#post", "title": title, "content": content, "labels": labels},
    ).execute()
    print("Blogger draft created.")
    print(f"Draft title: {title}")
    print(f"Draft ID: {result.get('id')}")
    print("Open Blogger > Posts > Draft to review and publish it manually.")


if __name__ == "__main__":
    main()
