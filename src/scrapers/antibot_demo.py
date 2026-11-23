import asyncio
import logging
import time
from curl_cffi.requests import AsyncSession
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)


async def tier_a_tls_impersonation(url: str) -> dict:
    """Tier A: Lightweight Chrome JA3/HTTP2 TLS fingerprint impersonation (~5 MB RAM)."""
    t0 = time.perf_counter()
    async with AsyncSession(impersonate="chrome124") as session:
        resp = await session.get(url, timeout=20)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "tier": "Tier-A curl_cffi (chrome124 TLS)",
            "url": url,
            "status": resp.status_code,
            "bytes": len(resp.text),
            "latency_ms": elapsed_ms,
        }


async def tier_b_playwright_stealth(url: str, sem: asyncio.Semaphore) -> dict:
    """Tier B: Async Playwright Chromium with heavy asset blocking (cuts RAM/bandwidth ~70%)."""
    async with sem:
        t0 = time.perf_counter()
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                locale="en-US",
                timezone_id="America/New_York",
            )
            page = await context.new_page()

            async def block_heavy_assets(route):
                if route.request.resource_type in {"image", "media", "font", "stylesheet"}:
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", block_heavy_assets)
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            title = await page.title()
            await browser.close()
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            return {
                "tier": "Tier-B Playwright Async (Asset-Blocked)",
                "url": url,
                "status": resp.status if resp else 0,
                "title": title.strip()[:80],
                "latency_ms": elapsed_ms,
            }


async def run_antibot_demo() -> None:
    sem = asyncio.Semaphore(2)  # Bounded for 6-core / 16 GB local machine
    res_a, res_b = await asyncio.gather(
        tier_a_tls_impersonation("https://httpbin.org/headers"),
        tier_b_playwright_stealth("https://www.ycombinator.com/companies", sem),
    )
    logger.info("Anti-Bot Result A: %s", res_a)
    logger.info("Anti-Bot Result B: %s", res_b)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    asyncio.run(run_antibot_demo())