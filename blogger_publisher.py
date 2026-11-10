"""Publish an entertainment news article to Blogger.

This publisher is intentionally separate from the video workflows and creates
drafts for editorial review. It uses Blogger and news credentials plus a
Groq API key from environment variables for article generation.
"""

from __future__ import annotations

import html
import json
import os
import random
import re
import time
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
        print("Fetching entertainment headlines from NewsAPI...", flush=True)
        response = requests.get(
            "https://newsapi.org/v2/top-headlines",
            params={"category": "entertainment", "pageSize": 20, "apiKey": news_key},
            timeout=25,
        )
        response.raise_for_status()
        articles.extend(response.json().get("articles", []))
    if gnews_key:
        print("Fetching entertainment headlines from GNews...", flush=True)
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


def generate_article(story: dict[str, str]) -> tuple[str, str, list[str], str]:
    """Return article title, body HTML, and labels using Groq."""
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        raise RuntimeError("GROQ_API_KEY is missing from GitHub Actions secrets.")
    prompt = f"""Write a useful, reader-first entertainment news analysis using only the source details below.
Do not invent quotes, facts, dates, motives, reactions, legal outcomes, or confirmation. Attribute reported claims to the named publisher and clearly state what the short source summary does not establish.
Do not reproduce the source headline or copy its wording. Add context by explaining implications and unanswered questions without inventing details. Avoid sensationalism, keyword stuffing, unsupported claims, and generic filler.
Return only valid JSON with these keys: title, dek, meta_description, sections, labels.
title: descriptive, accurate, concise (about 50-65 characters, never misleading).
dek: one short introductory summary.
meta_description: accurate search snippet, 140-160 characters.
sections: array of 3 objects, each with a descriptive heading and 2 substantive paragraphs. Headings should reflect this specific story, not generic SEO headings.
labels: array of 3-5 concise Blogger labels.

Source headline: {story['title']}
Source summary: {story['description']}
Publisher: {story['source'] or urlparse(story['url']).netloc}
Source URL: {story['url']}
"""
    retryable_statuses = {408, 429, 500, 502, 503, 504}
    groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
    last_error = "unknown Groq API error"
    data = None
    for attempt, delay in enumerate((0, 4, 10), start=1):
        if delay:
            print(f"Groq retry {attempt}/3 after {delay}s.", flush=True)
            time.sleep(delay)
        try:
            print(f"Requesting article generation from Groq model {groq_model} (attempt {attempt}/3)...", flush=True)
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json={
                    "model": groq_model,
                    "messages": [
                        {"role": "system", "content": "Return a valid JSON object only. Follow the user's instructions and do not invent facts."},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.5,
                    "max_completion_tokens": 3000,
                },
                timeout=60,
            )
            if response.status_code in retryable_statuses:
                last_error = f"Groq HTTP {response.status_code}: {' '.join(response.text.split())[:500]}"
                continue
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            data = json.loads(raw)
            print(f"Groq article generation succeeded with {groq_model}.", flush=True)
            break
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = f"Groq temporary network error: {type(exc).__name__}"
        except (requests.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            if isinstance(exc, requests.HTTPError):
                code = exc.response.status_code if exc.response is not None else "unknown"
                detail = " ".join(exc.response.text.split())[:500] if exc.response is not None else str(exc)
                raise RuntimeError(f"Groq request failed (HTTP {code}) for {groq_model}: {detail}") from exc
            last_error = f"invalid Groq response: {type(exc).__name__}"
            break
    if data is None:
        raise RuntimeError(
            f"Groq article generation failed after retries for {groq_model}. "
            f"Last error: {last_error}. No Blogger post was created; check the Groq key, model access, and rate limits."
        )
    title = clean(data.get("title") or data.get("headline") or story["title"])[:100]
    sections = data.get("sections", [])
    dek = clean(data.get("dek") or data.get("intro"))
    meta_description = clean(data.get("meta_description") or data.get("description") or dek)[:160]
    valid_sections = []
    for section in sections:
        if isinstance(section, dict):
            heading = clean(section.get("heading") or section.get("title") or section.get("h2"))
            paragraph_data = section.get("paragraphs") or section.get("body") or section.get("content") or section.get("text") or []
        else:
            heading, paragraph_data = "", section
        if isinstance(paragraph_data, str):
            paragraphs = [clean(p) for p in re.split(r"\n\s*\n", paragraph_data) if clean(p)]
        elif isinstance(paragraph_data, list):
            paragraphs = [clean(p) for p in paragraph_data if clean(p)]
        else:
            paragraphs = []
        if paragraphs:
            valid_sections.append((heading, paragraphs[:4]))

    # Groq models sometimes return the requested prose as a flat paragraph list.
    # Regroup that valid prose into three article sections instead of rejecting it.
    if len(valid_sections) < 3:
        flat_paragraphs = data.get("paragraphs") or data.get("body") or []
        if isinstance(flat_paragraphs, str):
            flat_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", flat_paragraphs) if p.strip()]
        if isinstance(flat_paragraphs, list):
            flat_paragraphs = [clean(p) for p in flat_paragraphs if clean(p)]
            if len(flat_paragraphs) >= 3:
                headings = data.get("section_headings") or []
                fallback_headings = ["What the report says", "Why this story matters", "What remains unclear"]
                valid_sections = []
                groups = [flat_paragraphs[0::3], flat_paragraphs[1::3], flat_paragraphs[2::3]]
                for index, paragraphs in enumerate(groups):
                    heading = clean(headings[index]) if isinstance(headings, list) and index < len(headings) else ""
                    valid_sections.append((heading or fallback_headings[index], paragraphs))

    paragraph_count = sum(len(paragraphs) for _, paragraphs in valid_sections)
    article_word_count = sum(len(paragraph.split()) for _, paragraphs in valid_sections for paragraph in paragraphs)
    fallback_headings = ["What happened", "What the report says", "What happens next"]
    valid_sections = [
        (heading or fallback_headings[index % len(fallback_headings)], paragraphs)
        for index, (heading, paragraphs) in enumerate(valid_sections)
    ]
    if not title or len(valid_sections) < 3 or paragraph_count < 3 or article_word_count < 220:
        raise RuntimeError(
            f"Groq returned an article that is too incomplete to publish "
            f"({len(valid_sections)} sections, {paragraph_count} paragraphs, {article_word_count} words). "
            "Nothing was published; retry the workflow."
        )

    body = []
    if dek:
        body.append(f'<p class="article-dek"><strong>{html.escape(dek)}</strong></p>')
    if meta_description:
        body.append(f'<p class="article-summary"><strong>Summary:</strong> {html.escape(meta_description)}</p>')
    image = story.get("image")
    if image.startswith("https://"):
        alt_text = f"Related image accompanying the report: {title}"
        publisher = story["source"] or urlparse(story["url"]).netloc
        caption = f"Image accompanying the report from {publisher}."
        body.append(
            '<figure style="width:100%;max-width:900px;margin:20px auto;text-align:center;">'
            '<div style="width:100%;aspect-ratio:16/9;overflow:hidden;background:#f2f2f2;">'
            f'<img src="{html.escape(image, quote=True)}" '
            f'alt="{html.escape(alt_text, quote=True)}" '
            'loading="lazy" referrerpolicy="no-referrer" '
            'style="display:block;width:100%;height:100%;object-fit:contain;margin:0 auto;" />'
            '</div>'
            + (f'<figcaption style="font-size:0.9em;color:#555;margin-top:6px;">{html.escape(caption)}</figcaption>' if caption else '')
            + '</figure>'
        )
    for heading, paragraphs in valid_sections:
        body.append(f"<h2>{html.escape(heading)}</h2>")
        body.extend(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)
    publisher = story["source"] or urlparse(story["url"]).netloc
    body.append(
        '<p><strong>Source:</strong> '
        f'<a href="{html.escape(story["url"], quote=True)}" rel="nofollow noopener">'
        f"{html.escape(publisher)}</a></p>"
    )
    labels = [clean(label)[:40] for label in data.get("labels", []) if clean(label)]
    return title, "\n".join(body), labels, meta_description


def blogger_service():
    required = ("BLOGGER_CLIENT_ID", "BLOGGER_CLIENT_SECRET", "BLOGGER_REFRESH_TOKEN")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing GitHub Actions secrets: " + ", ".join(missing))
    blog_id = os.getenv("BLOGGER_BLOG_ID")
    if not blog_id:
        raise RuntimeError(
            "BLOGGER_BLOG_ID is empty. Add your numeric Blogger blog ID as a repository "
            "Actions secret named BLOGGER_BLOG_ID, then rerun this workflow."
        )
    print("Refreshing Blogger authorization...", flush=True)
    credentials = Credentials(
        token=None,
        refresh_token=os.environ["BLOGGER_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["BLOGGER_CLIENT_ID"],
        client_secret=os.environ["BLOGGER_CLIENT_SECRET"],
        scopes=[BLOGGER_SCOPE],
    )
    credentials.refresh(Request())
    print("Blogger authorization ready.", flush=True)
    return build("blogger", "v3", credentials=credentials, cache_discovery=False), blog_id


def already_posted(service, blog_id: str, candidate: str, source_url: str = "") -> bool:
    """Skip stories whose source URL or title already appears in a live post or draft."""
    normalized = re.sub(r"[^a-z0-9]+", " ", candidate.lower()).strip()
    token_set = {word for word in normalized.split() if len(word) > 3}
    normalized_url = html.unescape(source_url).rstrip("/")
    for status in ("LIVE", "DRAFT", "SCHEDULED"):
        page_token = None
        while True:
            result = service.posts().list(
                blogId=blog_id,
                status=status,
                fetchBodies=bool(normalized_url),
                maxResults=100,
                pageToken=page_token,
            ).execute()
            for post in result.get("items", []):
                existing_content = html.unescape(post.get("content") or "")
                if normalized_url and normalized_url in existing_content:
                    print("This source URL already appears in a Blogger post; skipping duplicate story.", flush=True)
                    return True
                existing = re.sub(r"[^a-z0-9]+", " ", clean(post.get("title")).lower()).strip()
                existing_tokens = {word for word in existing.split() if len(word) > 3}
                if normalized == existing or (token_set and len(token_set & existing_tokens) / len(token_set) >= 0.8):
                    print("A post with a matching title already exists; skipping this story.", flush=True)
                    return True
            page_token = result.get("nextPageToken")
            if not page_token:
                break


def main() -> None:
    print("Checking Blogger configuration before contacting news or Groq...", flush=True)
    service, blog_id = blogger_service()
    print("Blogger settings validated. Starting news lookup.", flush=True)
    story = get_news()[0]
    print(f"Selected story: {story['title']}", flush=True)
    if already_posted(service, blog_id, story["title"], story["url"]):
        return
    title, content, labels, meta_description = generate_article(story)
    print("Publishing Blogger post...", flush=True)
    result = service.posts().insert(
        blogId=blog_id,
        isDraft=False,
        body={"kind": "blogger#post", "title": title, "content": content, "labels": labels},
    ).execute()
    print("Blogger post published.", flush=True)
    print(f"Published title: {title}", flush=True)
    print(f"Post URL: {result.get('url', '(URL not returned)')}", flush=True)
    if meta_description:
        print(f"Suggested Blogger search description (add in post Settings): {meta_description}", flush=True)


if __name__ == "__main__":
    main()
