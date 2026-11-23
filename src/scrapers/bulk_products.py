import asyncio
import logging
from typing import List
import httpx

from src.config import HTTP_TIMEOUT
from src.llm.orchestrator import MultiTierLLMOrchestrator
from src.resolution.resolver import EntityResolver
from src.schemas import ProductContent, ProductRecord, SourceInfo

logger = logging.getLogger(__name__)

HF_ENDPOINTS = [
    "https://huggingface.co/api/spaces?sort=likes&direction=-1&limit=1000&full=true",
    "https://huggingface.co/api/spaces?sort=trendingScore&direction=-1&limit=500&full=true",
]


async def classify_pricing(
    orch: MultiTierLLMOrchestrator,
    space_id: str,
    card_text: str,
) -> str:
    instruction = (
        "Classify the pricing model of this AI product based strictly on the provided metadata. "
        'Return JSON: {"pricingModel": "FREE" | "FREEMIUM" | "PAID" | "ENTERPRISE"}. '
        "If it is an open community demo with no paid tiers mentioned, return FREE."
    )
    res = await orch.extract_json(instruction, card_text, fallback_default={"pricingModel": "FREE"})
    val = str(res.get("pricingModel", "FREE")).upper().strip()
    return val if val in {"FREE", "FREEMIUM", "PAID", "ENTERPRISE"} else "FREE"


async def scrape_products(
    resolver: EntityResolver,
    orch: MultiTierLLMOrchestrator,
    target_count: int = 1100,
    llm_enrich_limit: int = 25,
) -> List[dict]:
    logger.info("Fetching Hugging Face Spaces AI products...")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        responses = await asyncio.gather(*(client.get(url) for url in HF_ENDPOINTS))
        spaces = []
        for r in responses:
            r.raise_for_status()
            spaces.extend(r.json())

    seen_urls = set()
    rows: List[dict] = []
    llm_enriched = 0

    for sp in spaces:
        if len(rows) >= target_count:
            break
        space_id = (sp.get("id") or "").strip()
        if not space_id or "/" not in space_id:
            continue

        source_url = f"https://huggingface.co/spaces/{space_id}"
        if source_url in seen_urls:
            continue

        raw_org, prod_slug = space_id.split("/", 1)
        canonical_org, _, _ = resolver.resolve(raw_org, source_tab="Products", source_url=source_url)
        card = sp.get("cardData") or {}
        prod_name = (card.get("title") or prod_slug).strip()
        card_summary = f"Product: {prod_name} by {canonical_org}. SDK: {sp.get('sdk')}. Tags: {sp.get('tags', [])}"

        pricing = "FREE"
        if llm_enriched < llm_enrich_limit and any(
            kw in card_summary.lower() for kw in ("api", "pro", "enterprise", "cloud", "studio")
        ):
            pricing = await classify_pricing(orch, space_id, card_summary)
            llm_enriched += 1

        try:
            rec = ProductRecord(
                source=SourceInfo(name="Hugging Face Spaces", url=source_url),
                content=ProductContent(
                    startupName=canonical_org,
                    productName=prod_name,
                    pricingModel=pricing,  # type: ignore
                ),
            )
            seen_urls.add(source_url)
            rows.append(rec.to_flat_dict())
        except Exception:
            continue

    logger.info("Products extracted & validated: %d unique records (LLM-classified: %d)", len(rows), llm_enriched)
    return rows