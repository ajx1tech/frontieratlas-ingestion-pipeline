import asyncio
import logging
from typing import List
import feedparser
import httpx

from src.config import HTTP_TIMEOUT
from src.freshness.date_engine import FreshnessEngine
from src.llm.chunker import clean_html_text
from src.llm.orchestrator import MultiTierLLMOrchestrator
from src.resolution.resolver import EntityResolver
from src.schemas import NewsContent, NewsRecord, SourceInfo

logger = logging.getLogger(__name__)

NEWS_FEEDS = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("Ars Technica AI", "https://arstechnica.com/ai/feed/"),
    ("MIT Technology Review AI", "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
    ("MarkTechPost", "https://www.marktechpost.com/feed/"),
]


async def fetch_feed_and_articles(
    client: httpx.AsyncClient,
    source_name: str,
    feed_url: str,
    freshness: FreshnessEngine,
    resolver: EntityResolver,
    orch: MultiTierLLMOrchestrator,
) -> List[dict]:
    results: List[dict] = []
    try:
        resp = await client.get(feed_url, follow_redirects=True)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.text)
    except Exception as exc:
        logger.warning("News feed error [%s]: %s", source_name, exc)
        return results

    for entry in parsed.entries[:25]:
        link = (entry.get("link") or "").strip()
        title = (entry.get("title") or "").strip()
        raw_date = entry.get("published") or entry.get("updated") or None
        if not link or not title:
            continue

        is_fresh, iso_date, _ = freshness.check_freshness(link, raw_date, title, max_hours=24.0)
        if not is_fresh:
            continue

        # Fetch full article HTML asynchronously
        full_text = clean_html_text(entry.get("summary") or title)
        try:
            art_resp = await client.get(link, follow_redirects=True, timeout=12.0)
            if art_resp.status_code == 200 and len(art_resp.text) > 500:
                extracted = clean_html_text(art_resp.text)
                if len(extracted) > len(full_text):
                    full_text = extracted
        except Exception:
            pass

        llm_out = await orch.extract_json(
            'Extract AI company/organization names mentioned in this news article. Return JSON: {"entities": ["Name1", "Name2"]}',
            f"{title}\n\n{full_text}",
            fallback_default={"entities": []},
        )
        raw_entities = llm_out.get("entities") if isinstance(llm_out.get("entities"), list) else []
        canonical_entities = []
        for ent in raw_entities[:8]:
            if isinstance(ent, str) and ent.strip():
                canon, _, _ = resolver.resolve(ent.strip(), source_tab="News", source_url=link)
                if canon not in canonical_entities:
                    canonical_entities.append(canon)

        try:
            rec = NewsRecord(
                source=SourceInfo(name=source_name, url=link),
                content=NewsContent(
                    title=title,
                    published_date=iso_date,
                    full_text_excerpt=full_text[:600],
                    entities_mentioned=canonical_entities,
                ),
            )
            results.append(rec.to_flat_dict())
        except Exception:
            continue

    logger.info("News [%s]: %d articles within 24h", source_name, len(results))
    return results


async def scrape_fresh_news(
    freshness: FreshnessEngine,
    resolver: EntityResolver,
    orch: MultiTierLLMOrchestrator,
) -> List[dict]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FrontierAtlasBot/1.0"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers) as client:
        batches = await asyncio.gather(
            *(fetch_feed_and_articles(client, name, url, freshness, resolver, orch) for name, url in NEWS_FEEDS)
        )
    seen = set()
    rows = []
    for b in batches:
        for row in b:
            if row["source.url"] not in seen:
                seen.add(row["source.url"])
                rows.append(row)
    logger.info("Total <24h AI News rows: %d", len(rows))
    return rows