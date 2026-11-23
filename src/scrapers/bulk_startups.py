import asyncio
import logging
from typing import List
import httpx

from src.config import HTTP_TIMEOUT
from src.resolution.resolver import EntityResolver
from src.schemas import SourceInfo, StartupContent, StartupData, StartupRecord

logger = logging.getLogger(__name__)

YC_AI_URL = "https://yc-oss.github.io/api/tags/ai.json"
YC_ALL_URL = "https://yc-oss.github.io/api/companies/all.json"


async def scrape_startups(resolver: EntityResolver, target_count: int = 1100) -> List[dict]:
    logger.info("Fetching YC OSS startup datasets...")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        ai_resp, all_resp = await asyncio.gather(
            client.get(YC_AI_URL),
            client.get(YC_ALL_URL),
        )
        ai_resp.raise_for_status()
        all_resp.raise_for_status()
        combined = ai_resp.json() + all_resp.json()

    seen_urls = set()
    rows: List[dict] = []

    for item in combined:
        if len(rows) >= target_count:
            break
        raw_name = (item.get("name") or "").strip()
        slug = (item.get("slug") or "").strip()
        if not raw_name or not slug:
            continue

        source_url = f"https://www.ycombinator.com/companies/{slug}"
        if source_url in seen_urls:
            continue

        canonical_name, _, _ = resolver.resolve(raw_name, source_tab="Startups", source_url=source_url)
        team_size = item.get("team_size")
        emp_count = int(team_size) if isinstance(team_size, (int, float)) and team_size > 0 else None

        try:
            rec = StartupRecord(
                source=SourceInfo(name="Y Combinator Directory", url=source_url),
                content=StartupContent(
                    entityName=canonical_name,
                    data=StartupData(employeeCount=emp_count),
                ),
            )
            seen_urls.add(source_url)
            rows.append(rec.to_flat_dict())
        except Exception:
            continue

    logger.info("Startups extracted & validated: %d unique records", len(rows))
    return rows


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    res = EntityResolver()
    data = asyncio.run(scrape_startups(res))
    print(f"Sample startup: {data[0]}")