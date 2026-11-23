import asyncio
import csv
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import List
import httpx

from src.config import EXPORTS_DIR
from src.freshness.date_engine import FreshnessEngine
from src.llm.orchestrator import MultiTierLLMOrchestrator
from src.resolution.resolver import EntityResolver
from src.scrapers.bulk_papers import scrape_papers
from src.scrapers.bulk_products import scrape_products
from src.scrapers.bulk_startups import scrape_startups
from src.scrapers.fresh_jobs import scrape_fresh_jobs
from src.scrapers.fresh_news import scrape_fresh_news

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("frontieratlas.pipeline")


def write_csv(rows: List[dict], path: Path) -> None:
    if not rows:
        logger.warning("No rows to write for %s", path.name)
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Exported %d rows -> %s", len(rows), path)


async def spot_check_urls(urls: List[str], sample_size: int = 20) -> None:
    sample = random.sample(urls, min(sample_size, len(urls)))
    ok = 0
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        for u in sample:
            try:
                r = await client.head(u)
                if r.status_code < 400:
                    ok += 1
                else:
                    r2 = await client.get(u)
                    if r2.status_code < 400:
                        ok += 1
            except Exception:
                pass
    logger.info("GROUNDING AUDIT: %d / %d sampled URLs verified live (HTTP < 400)", ok, len(sample))


async def main() -> None:
    resolver = EntityResolver()
    orch = MultiTierLLMOrchestrator()
    freshness = FreshnessEngine()

    # Phase I: Bulk extraction (1,000+ rows per vertical)
    startups, products, papers = await asyncio.gather(
        scrape_startups(resolver, target_count=1100),
        scrape_products(resolver, orch, target_count=1100, llm_enrich_limit=20),
        scrape_papers(target_count=1100),
    )

    # Phase II: Strict 24-hour fresh signals
    news, jobs = await asyncio.gather(
        scrape_fresh_news(freshness, resolver, orch),
        scrape_fresh_jobs(freshness, resolver, orch),
    )

    # Export all 6 tabs
    write_csv(startups, EXPORTS_DIR / "startups.csv")
    write_csv(products, EXPORTS_DIR / "products.csv")
    write_csv(papers, EXPORTS_DIR / "research_papers.csv")
    write_csv(jobs, EXPORTS_DIR / "jobs.csv")
    write_csv(news, EXPORTS_DIR / "news.csv")
    log_path = resolver.export_log(EXPORTS_DIR / "entity_mapping_log.csv")
    logger.info("Exported entity mapping audit log -> %s", log_path)

    # Final Verification Summary
    all_urls = (
        [r["source.url"] for r in startups]
        + [r["source.url"] for r in products]
        + [r["content.paper_url"] for r in papers]
        + [r["source.url"] for r in jobs]
        + [r["source.url"] for r in news]
    )
    null_urls = sum(1 for u in all_urls if not u or not str(u).startswith("http"))

    now = datetime.now(timezone.utc)
    max_job_age = max(
        ((now - datetime.fromisoformat(r["content.date"])).total_seconds() / 3600.0 for r in jobs),
        default=0.0,
    )
    max_news_age = max(
        ((now - datetime.fromisoformat(r["content.published_date"])).total_seconds() / 3600.0 for r in news),
        default=0.0,
    )

    print("\n" + "=" * 60)
    print("FRONTIERATLAS PIPELINE AUDIT SUMMARY")
    print("=" * 60)
    print(f"1. Startups Tab        : {len(startups)} rows (target >= 1000)")
    print(f"2. Products Tab        : {len(products)} rows (target >= 1000)")
    print(f"3. Research Papers Tab : {len(papers)} rows (target >= 1000)")
    print(f"4. Jobs Tab (<24h)     : {len(jobs)} rows (max age: {max_job_age:.2f}h)")
    print(f"5. News Tab (<24h)     : {len(news)} rows (max age: {max_news_age:.2f}h)")
    print(f"6. Entity Mapping Log  : {len(resolver.logs)} resolution events logged")
    print(f"Null / Invalid URLs    : {null_urls} (MUST BE 0)")
    print("=" * 60 + "\n")

    await spot_check_urls(all_urls, sample_size=20)


if __name__ == "__main__":
    asyncio.run(main())