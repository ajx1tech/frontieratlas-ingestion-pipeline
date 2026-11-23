import asyncio
import logging
from typing import List
import httpx

from src.config import HTTP_TIMEOUT
from src.freshness.date_engine import FreshnessEngine
from src.llm.chunker import clean_html_text
from src.llm.orchestrator import MultiTierLLMOrchestrator
from src.resolution.resolver import EntityResolver
from src.schemas import JobContent, JobRecord, SourceInfo

logger = logging.getLogger(__name__)

AI_KEYWORDS = ("ai", "ml", "machine learning", "llm", "deep learning", "data", "research", "engineer", "model")


def _heuristic_job_meta(title: str, desc: str) -> dict:
    blob = f"{title} {desc}".lower()
    is_remote = any(k in blob for k in ("remote", "anywhere", "work from home", "distributed"))
    if any(k in title.lower() for k in ("research", "scientist", "phd")):
        role = "Research"
    elif any(k in title.lower() for k in ("product", "pm", "design")):
        role = "Product"
    elif any(k in title.lower() for k in ("sales", "account", "marketing", "growth")):
        role = "GTM"
    else:
        role = "Engineering"
    return {"is_remote": is_remote, "role_family": role}


async def _fetch_greenhouse(client: httpx.AsyncClient) -> List[dict]:
    items = []
    for token, comp in [("anthropic", "Anthropic"), ("scaleai", "Scale AI"), ("databricks", "Databricks"), ("runwayml", "Runway")]:
        try:
            r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
            if r.status_code == 200:
                for j in r.json().get("jobs", []):
                    items.append(
                        {
                            "source_name": "Greenhouse",
                            "url": j.get("absolute_url"),
                            "company": comp,
                            "title": j.get("title"),
                            "raw_date": j.get("updated_at"),
                            "desc": f"{j.get('location', {}).get('name', '')} {j.get('content', '')}",
                        }
                    )
        except Exception:
            continue
    return items


async def _fetch_lever(client: httpx.AsyncClient) -> List[dict]:
    items = []
    for token, comp in [("mistral", "Mistral AI"), ("cohere", "Cohere"), ("palantir", "Palantir")]:
        try:
            r = await client.get(f"https://api.lever.co/v0/postings/{token}?mode=json")
            if r.status_code == 200:
                for j in r.json():
                    items.append(
                        {
                            "source_name": "Lever",
                            "url": j.get("hostedUrl"),
                            "company": comp,
                            "title": j.get("text"),
                            "raw_date": j.get("createdAt"),
                            "desc": f"{j.get('workplaceType', '')} {j.get('descriptionPlain', '')}",
                        }
                    )
        except Exception:
            continue
    return items


async def _fetch_himalayas(client: httpx.AsyncClient) -> List[dict]:
    items = []
    try:
        r = await client.get("https://himalayas.app/jobs/api?limit=50")
        if r.status_code == 200:
            for j in r.json().get("jobs", []):
                title = j.get("title") or ""
                if any(k in title.lower() for k in AI_KEYWORDS):
                    items.append(
                        {
                            "source_name": "Himalayas",
                            "url": j.get("applicationLink") or f"https://himalayas.app/jobs/{j.get('slug', '')}",
                            "company": j.get("companyName") or "Unknown",
                            "title": title,
                            "raw_date": j.get("pubDate"),
                            "desc": f"Remote {j.get('description', '')}",
                        }
                    )
    except Exception:
        pass
    return items


async def _fetch_remoteok(client: httpx.AsyncClient) -> List[dict]:
    items = []
    try:
        r = await client.get("https://remoteok.com/api", headers={"User-Agent": "FrontierAtlasBot/1.0"})
        if r.status_code == 200:
            for j in r.json()[1:80]:
                title = j.get("position") or ""
                tags = " ".join(j.get("tags") or [])
                if any(k in f"{title} {tags}".lower() for k in AI_KEYWORDS):
                    items.append(
                        {
                            "source_name": "RemoteOK",
                            "url": j.get("url"),
                            "company": j.get("company") or "Unknown",
                            "title": title,
                            "raw_date": j.get("date") or j.get("epoch"),
                            "desc": f"Remote {j.get('description', '')}",
                        }
                    )
    except Exception:
        pass
    return items


async def _fetch_arbeitnow(client: httpx.AsyncClient) -> List[dict]:
    items = []
    try:
        r = await client.get("https://www.arbeitnow.com/api/job-board-api")
        if r.status_code == 200:
            for j in r.json().get("data", []):
                title = j.get("title") or ""
                if any(k in title.lower() for k in AI_KEYWORDS):
                    items.append(
                        {
                            "source_name": "Arbeitnow",
                            "url": j.get("url"),
                            "company": j.get("company_name") or "Unknown",
                            "title": title,
                            "raw_date": j.get("created_at"),
                            "desc": f"{'Remote' if j.get('remote') else 'Onsite'} {j.get('description', '')}",
                        }
                    )
    except Exception:
        pass
    return items


async def scrape_fresh_jobs(
    freshness: FreshnessEngine,
    resolver: EntityResolver,
    orch: MultiTierLLMOrchestrator,
) -> List[dict]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        batches = await asyncio.gather(
            _fetch_greenhouse(client),
            _fetch_lever(client),
            _fetch_himalayas(client),
            _fetch_remoteok(client),
            _fetch_arbeitnow(client),
        )

    raw_jobs = [item for b in batches for item in b]
    seen_urls = set()
    rows: List[dict] = []
    llm_calls = 0

    for j in raw_jobs:
        url = (j.get("url") or "").strip()
        title = (j.get("title") or "").strip()
        if not url or not title or url in seen_urls:
            continue

        is_fresh, iso_date, _ = freshness.check_freshness(url, j.get("raw_date"), title, max_hours=24.0)
        if not is_fresh:
            continue

        canonical_comp, _, _ = resolver.resolve(j.get("company") or "Unknown", source_tab="Jobs", source_url=url)
        clean_desc = clean_html_text(j.get("desc") or title)
        fallback_meta = _heuristic_job_meta(title, clean_desc)

        if llm_calls < 30:
            meta = await orch.extract_json(
                'Extract job metadata. Return JSON: {"is_remote": true/false, "role_family": "Engineering"|"Research"|"Product"|"GTM"|"Operations"}',
                f"Title: {title}\nCompany: {canonical_comp}\nDescription: {clean_desc}",
                fallback_default=fallback_meta,
            )
            llm_calls += 1
        else:
            meta = fallback_meta

        try:
            rec = JobRecord(
                source=SourceInfo(name=j["source_name"], url=url),
                content=JobContent(
                    company=canonical_comp,
                    title=title,
                    date=iso_date,
                    is_remote=bool(meta.get("is_remote", fallback_meta["is_remote"])),
                    role_family=str(meta.get("role_family") or fallback_meta["role_family"]),
                ),
            )
            seen_urls.add(url)
            rows.append(rec.to_flat_dict())
        except Exception:
            continue

    logger.info("Total <24h AI Jobs rows across 5 boards: %d", len(rows))
    return rows