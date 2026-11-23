import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError

from src.schemas import StartupRecord, SourceInfo, StartupContent
from src.llm.chunker import prepare_llm_payload
from src.llm.orchestrator import MultiTierLLMOrchestrator
from src.resolution.resolver import EntityResolver
from src.freshness.date_engine import FreshnessEngine

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")


def test_grounding_gate_blocks_fake_url():
    with pytest.raises(ValidationError):
        StartupRecord(
            source=SourceInfo(name="Fake", url="http://example.com/fake"),
            content=StartupContent(entityName="OpenAI"),
        )
    valid = StartupRecord(
        source=SourceInfo(name="Y Combinator", url="https://www.ycombinator.com/companies/openai"),
        content=StartupContent(entityName="OpenAI"),
    )
    assert valid.source.url == "https://www.ycombinator.com/companies/openai"


def test_413_chunker_truncates_large_html():
    huge_html = "<html><body><script>evil()</script><p>" + ("AI research news. " * 3000) + "</p></body></html>"
    cleaned, meta = prepare_llm_payload(huge_html, max_chars=4000)
    assert meta["truncated"] is True
    assert meta["original_chars"] > 50000
    assert meta["final_chars"] <= 4000
    assert "evil()" not in cleaned


def test_429_backoff_and_tier_failover():
    orch = MultiTierLLMOrchestrator(base_delay=0.05, max_retries=2)
    orch._call_gemini = AsyncMock(side_effect=Exception("429 Too Many Requests: quota exceeded"))
    orch._call_groq = AsyncMock(return_value={"is_remote": True, "role_family": "Engineering"})

    result = asyncio.run(orch.extract_json("Extract job fields", "Remote Senior AI Engineer"))
    assert orch._call_gemini.call_count == 3  # 1 initial + 2 retries with backoff
    assert orch._call_groq.call_count == 1
    assert result == {"is_remote": True, "role_family": "Engineering"}


def test_entity_resolution_canonicalization():
    resolver = EntityResolver()
    for messy in ["OpenAI, Inc.", "Open AI", "open-ai", "OpenAI LP"]:
        canonical, method, score = resolver.resolve(messy, source_tab="Test")
        assert canonical == "OpenAI", f"Failed for {messy}: got {canonical}"
        assert score >= 88.0
    c2, _, _ = resolver.resolve("Anthropic PBC", source_tab="Test")
    assert c2 == "Anthropic"


def test_freshness_date_parsing_and_sqlite_heuristic(tmp_path: Path):
    engine = FreshnessEngine(db_path=tmp_path / "test_state.db")

    fresh, iso_fresh, _ = engine.check_freshness("https://techcrunch.com/ai-1", "2 hours ago")
    assert fresh is True
    assert "T" in iso_fresh

    stale, _, _ = engine.check_freshness("https://techcrunch.com/ai-old", "3 days ago")
    assert stale is False

    # Dateless source uses SQLite first-seen heuristic
    is_new, _, method = engine.check_freshness("https://nodate.ai/post-1", None, "New AI release")
    assert is_new is True
    assert method == "sqlite_first_seen_new"