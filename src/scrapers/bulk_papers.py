import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import httpx

from src.config import GITHUB_TOKEN, HTTP_TIMEOUT
from src.schemas import ResearchPaperContent, ResearchPaperRecord

logger = logging.getLogger(__name__)

GITHUB_REPO_RE = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


def extract_github_url(text: str) -> Optional[str]:
    if not text:
        return None
    m = GITHUB_REPO_RE.search(text)
    if not m:
        return None
    owner, repo = m.group(1), m.group(2).rstrip(".,);]")
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"https://github.com/{owner}/{repo}"


async def fetch_hf_day(client: httpx.AsyncClient, sem: asyncio.Semaphore, date_str: str) -> List[dict]:
    url = f"https://huggingface.co/api/daily_papers?date={date_str}"
    async with sem:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("results", [])
        except Exception as exc:
            logger.debug("HF daily papers error for %s: %s", date_str, exc)
    return []


async def fetch_github_stars_graphql(repo_urls: List[str]) -> Dict[str, int]:
    """Batch-fetches live stargazerCount via GitHub GraphQL API (50 repos per POST)."""
    if not GITHUB_TOKEN:
        logger.warning("GITHUB_TOKEN missing — skipping live GraphQL star lookup")
        return {}

    unique_pairs: List[Tuple[str, str, str]] = []
    seen = set()
    for url in repo_urls:
        m = GITHUB_REPO_RE.search(url or "")
        if m:
            owner, repo = m.group(1), m.group(2)
            full_url = f"https://github.com/{owner}/{repo}"
            if full_url not in seen:
                seen.add(full_url)
                unique_pairs.append((full_url, owner, repo))

    stars_map: Dict[str, int] = {}
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for i in range(0, len(unique_pairs), 50):
            chunk = unique_pairs[i : i + 50]
            aliases = []
            for idx, (_, owner, repo) in enumerate(chunk):
                safe_owner = owner.replace('"', "")
                safe_repo = repo.replace('"', "")
                aliases.append(
                    f'r{idx}: repository(owner: "{safe_owner}", name: "{safe_repo}") {{ stargazerCount }}'
                )
            query = "query { " + " ".join(aliases) + " }"
            try:
                resp = await client.post("https://api.github.com/graphql", headers=headers, json={"query": query})
                if resp.status_code == 200:
                    data = resp.json().get("data") or {}
                    for idx, (full_url, _, _) in enumerate(chunk):
                        node = data.get(f"r{idx}")
                        if node and isinstance(node.get("stargazerCount"), int):
                            stars_map[full_url] = node["stargazerCount"]
            except Exception as exc:
                logger.warning("GitHub GraphQL batch error: %s", exc)

    logger.info("GitHub GraphQL resolved live star counts for %d repositories", len(stars_map))
    return stars_map


async def scrape_papers(target_count: int = 1100) -> List[dict]:
    logger.info("Fetching AI research papers from Hugging Face Daily Papers + arXiv...")
    sem = asyncio.Semaphore(10)
    today = datetime.now(timezone.utc).date()
    dates = [(today - timedelta(days=d)).isoformat() for d in range(1, 95)]

    raw_papers: List[dict] = []
    seen_paper_urls = set()

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        batches = await asyncio.gather(*(fetch_hf_day(client, sem, d) for d in dates))
        for items in batches:
            for entry in items:
                paper = entry.get("paper") or entry
                arxiv_id = (paper.get("id") or "").strip()
                title = (paper.get("title") or "").strip().replace("\n", " ")
                if not arxiv_id or not title:
                    continue
                paper_url = f"https://arxiv.org/abs/{arxiv_id}"
                if paper_url in seen_paper_urls:
                    continue

                authors = [
                    a.get("name", "").strip()
                    for a in (paper.get("authors") or [])
                    if isinstance(a, dict) and a.get("name")
                ]
                gh_url = extract_github_url(paper.get("githubRepo") or "") or extract_github_url(
                    paper.get("summary") or ""
                )
                pub_date = (paper.get("publishedAt") or f"{today.isoformat()}T00:00:00Z").strip()

                seen_paper_urls.add(paper_url)
                raw_papers.append(
                    {
                        "title": title,
                        "authors": authors or ["Unknown Author"],
                        "paper_url": paper_url,
                        "github_url": gh_url,
                        "published_date": pub_date,
                    }
                )
                if len(raw_papers) >= target_count:
                    break
            if len(raw_papers) >= target_count:
                break

        # Supplement from arXiv Atom API if below target_count (respecting 3s rate limit)
        if len(raw_papers) < target_count:
            import xml.etree.ElementTree as ET

            arxiv_query = (
                "https://export.arxiv.org/api/query?"
                "search_query=(cat:cs.AI+OR+cat:cs.LG+OR+cat:cs.CL)+AND+abs:github.com"
                "&start=0&max_results=300&sortBy=submittedDate&sortOrder=descending"
            )
            resp = await client.get(arxiv_query)
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                for entry in root.findall("atom:entry", ns):
                    if len(raw_papers) >= target_count:
                        break
                    id_url = (entry.findtext("atom:id", "", ns) or "").strip()
                    arxiv_id = id_url.split("/abs/")[-1].split("v")[0]
                    paper_url = f"https://arxiv.org/abs/{arxiv_id}"
                    if not arxiv_id or paper_url in seen_paper_urls:
                        continue
                    title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
                    summary = entry.findtext("atom:summary", "", ns) or ""
                    pub_date = (entry.findtext("atom:published", "", ns) or "").strip()
                    authors = [
                        (a.findtext("atom:name", "", ns) or "").strip()
                        for a in entry.findall("atom:author", ns)
                    ]
                    gh_url = extract_github_url(summary)
                    seen_paper_urls.add(paper_url)
                    raw_papers.append(
                        {
                            "title": title,
                            "authors": [a for a in authors if a] or ["Unknown Author"],
                            "paper_url": paper_url,
                            "github_url": gh_url,
                            "published_date": pub_date,
                        }
                    )

    repo_urls = [p["github_url"] for p in raw_papers if p["github_url"]]
    stars_map = await fetch_github_stars_graphql(repo_urls)

    rows: List[dict] = []
    for p in raw_papers:
        gh = p["github_url"]
        stars = stars_map.get(gh) if gh else None
        try:
            rec = ResearchPaperRecord(
                content=ResearchPaperContent(
                    title=p["title"],
                    authors=p["authors"],
                    paper_url=p["paper_url"],
                    github_url=gh,
                    github_stars=stars,
                    published_date=p["published_date"],
                )
            )
            rows.append(rec.to_flat_dict())
        except Exception:
            continue

    with_gh = sum(1 for r in rows if r["content.github_url"])
    logger.info("Research Papers extracted & validated: %d total (%d linked to GitHub)", len(rows), with_gh)
    return rows