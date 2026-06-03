"""Reddit watcher — capture new posts from weather/prediction-market sources
into a local inbox for later review.

Reddit now 403s unauthenticated `.json` from non-browser clients, but the
Atom `.rss` feeds are still open. We poll those, dedupe against a seen-set,
and write each new post as a markdown file into research/reddit_inbox/. A
companion notify-queue (JSONL) records new posts so a notifier can alert.

Sources:
  - r/Prilo_WeatherEdge       (all posts)
  - u/Prilo-WeatherEdge        (all submissions)
  - r/PredictionsMarkets       (weather-related only — keyword filtered)

Run every ~30 min via systemd timer. Manual:
    python -m weather_bot.jobs.reddit_watch
    python -m weather_bot.jobs.reddit_watch --dry-run   # don't write, just report
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_UA = "weatherbot-research/0.1 (personal research; contact /u/wallyworley)"

INBOX = Path(__file__).resolve().parent.parent / "research" / "reddit_inbox"
SEEN_PATH = INBOX / ".seen.json"
NOTIFY_QUEUE = INBOX / "_notify_queue.jsonl"

# (name, rss_url, weather_filter?) — weather_filter restricts to weather posts.
FEEDS = [
    ("Prilo_WeatherEdge", "https://www.reddit.com/r/Prilo_WeatherEdge/new/.rss", False),
    ("Prilo-WeatherEdge_user", "https://www.reddit.com/user/Prilo-WeatherEdge/submitted.rss", False),
    ("PredictionsMarkets", "https://www.reddit.com/r/PredictionsMarkets/new/.rss", True),
]

# Keywords that mark a PredictionsMarkets post as weather-relevant. Lowercased
# substring match against title + body. Deliberately STRONG signals only — on a
# general prediction-market sub, bare "weather"/"forecast"/"rain" produce false
# positives (e.g. a sports post mentioning "weather APIs"). We require an
# explicit temperature-market signal or a station ticker.
_WEATHER_KEYWORDS = (
    "temperature", "high temp", "daily high", "low temp", "°f", "°c",
    "nws ", "metar", "kxhigh", "kalshi temp", "marine layer", "santa ana",
    "lake breeze", "heat index", "degrees",
    "kmia", "klax", "knyc", "kmdw", "khou",
    "kphx", "kden", "kbos", "ksea", "katl", "kaus", "ksat", "kdfw",
)


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())


@dataclass
class Post:
    feed: str
    post_id: str
    title: str
    author: str
    link: str
    updated: str
    body: str


def _strip_html(t: str | None) -> str:
    t = html.unescape(t or "")
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _fetch(url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        log.warning("fetch failed for %s: %s", url, exc)
        return None


def _parse(feed_name: str, xml_text: str) -> list[Post]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("parse failed for %s: %s", feed_name, exc)
        return []
    posts: list[Post] = []
    for e in root.findall("a:entry", _ATOM):
        pid = (e.findtext("a:id", default="", namespaces=_ATOM) or "").strip()
        link_el = e.find("a:link", _ATOM)
        link = link_el.get("href") if link_el is not None else ""
        posts.append(Post(
            feed=feed_name,
            post_id=pid,
            title=_strip_html(e.findtext("a:title", default="", namespaces=_ATOM)),
            author=(e.findtext("a:author/a:name", default="?", namespaces=_ATOM) or "?").strip(),
            link=link,
            updated=(e.findtext("a:updated", default="", namespaces=_ATOM) or "")[:19],
            body=_strip_html(e.findtext("a:content", default="", namespaces=_ATOM)),
        ))
    return posts


def _is_weather(p: Post) -> bool:
    hay = (p.title + " " + p.body).lower()
    return any(k in hay for k in _WEATHER_KEYWORDS)


def _load_seen() -> set[str]:
    if SEEN_PATH.exists():
        try:
            return set(json.loads(SEEN_PATH.read_text()))
        except Exception:
            return set()
    return set()


def _save_seen(seen: set[str]) -> None:
    SEEN_PATH.write_text(json.dumps(sorted(seen), indent=0))


def _slug(title: str, n: int = 50) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()
    return (s[:n] or "post").rstrip("-")


def _write_post(p: Post) -> Path:
    short_id = p.post_id.rsplit("/", 1)[-1] or p.post_id[-12:]
    date = (p.updated[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    path = INBOX / f"{date}_{p.feed}_{_slug(p.title)}_{short_id}.md"
    path.write_text(
        f"---\n"
        f"feed: {p.feed}\n"
        f"author: {p.author}\n"
        f"posted: {p.updated}\n"
        f"link: {p.link}\n"
        f"captured: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"reviewed: false\n"
        f"---\n\n"
        f"# {p.title}\n\n"
        f"{p.body}\n"
    )
    return path


def run(dry_run: bool = False) -> dict:
    INBOX.mkdir(parents=True, exist_ok=True)
    seen = _load_seen()
    new_posts: list[Post] = []

    # FEEDS are processed in order; the Prilo sub is first, so when the same
    # post is cross-posted (sub + user feed + PredictionsMarkets, each with a
    # distinct id) the canonical sub copy wins and the others are collapsed by
    # the title marker added below.
    for name, url, weather_only in FEEDS:
        xml_text = _fetch(url)
        if xml_text is None:
            continue
        for p in _parse(name, xml_text):
            title_key = "t::" + _norm_title(p.title)
            if not p.post_id or p.post_id in seen or title_key in seen:
                continue
            if weather_only and not _is_weather(p):
                seen.add(p.post_id)   # mark non-weather as seen so we skip it next time
                continue
            seen.add(title_key)       # collapse cross-feed duplicate titles
            new_posts.append(p)

    summary = {"checked_feeds": len(FEEDS), "new": len(new_posts), "files": []}
    for p in new_posts:
        if dry_run:
            log.info("[dry-run] would capture: [%s] %s — %s", p.feed, p.author, p.title)
            continue
        path = _write_post(p)
        seen.add(p.post_id)
        summary["files"].append(str(path.name))
        with NOTIFY_QUEUE.open("a") as q:
            q.write(json.dumps({
                "feed": p.feed, "author": p.author, "title": p.title,
                "link": p.link, "posted": p.updated, "file": path.name,
            }) + "\n")
        log.info("captured: [%s] %s — %s", p.feed, p.author, p.title)

    if not dry_run:
        _save_seen(seen)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report new posts without writing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = run(dry_run=args.dry_run)
    log.info("reddit_watch: checked %d feeds, %d new post(s)%s",
             s["checked_feeds"], s["new"], "" if not args.dry_run else " (dry-run)")


if __name__ == "__main__":
    main()
