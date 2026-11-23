import asyncio
import json
import logging
import random
import re
from typing import Any, Dict, Optional
import httpx
from google import genai
from google.genai import types
from groq import AsyncGroq

from src.config import (
    GEMINI_API_KEY,
    GEMINI_CONCURRENCY,
    GEMINI_MODEL,
    GROQ_API_KEY,
    GROQ_CONCURRENCY,
    GROQ_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
)
from src.llm.chunker import prepare_llm_payload

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            return json.loads(match.group(0))
        raise


class MultiTierLLMOrchestrator:
    def __init__(self, base_delay: float = 1.2, max_retries: int = 2):
        self.base_delay = base_delay
        self.max_retries = max_retries
        self.gemini_sem = asyncio.Semaphore(GEMINI_CONCURRENCY)
        self.groq_sem = asyncio.Semaphore(GROQ_CONCURRENCY)
        self.gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
        self.groq_client = AsyncGroq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

    async def _call_gemini(self, prompt: str) -> Dict[str, Any]:
        if not self.gemini_client:
            raise RuntimeError("GEMINI_API_KEY not configured")
        async with self.gemini_sem:
            resp = await self.gemini_client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            return _extract_json(resp.text or "{}")

    async def _call_groq(self, prompt: str) -> Dict[str, Any]:
        if not self.groq_client:
            raise RuntimeError("GROQ_API_KEY not configured")
        async with self.groq_sem:
            resp = await self.groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "Return strictly valid JSON only. Never invent URLs or facts."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            return _extract_json(resp.choices[0].message.content or "{}")

    async def _call_openrouter(self, prompt: str) -> Dict[str, Any]:
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY not configured")
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": [
                        {"role": "system", "content": "Return strictly valid JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return _extract_json(content)

    async def extract_json(
        self,
        instruction: str,
        raw_text: str,
        fallback_default: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        cleaned, _ = prepare_llm_payload(raw_text)
        prompt = f"{instruction}\n\nSOURCE TEXT:\n{cleaned}"

        tiers = [
            ("Tier1-Gemini", self._call_gemini),
            ("Tier2-Groq", self._call_groq),
            ("Tier3-OpenRouter", self._call_openrouter),
        ]

        for tier_name, fn in tiers:
            for attempt in range(self.max_retries + 1):
                try:
                    result = await fn(prompt)
                    logger.info("LLM success via %s (attempt %d)", tier_name, attempt + 1)
                    return result
                except Exception as exc:
                    err_str = str(exc)
                    is_rate_limit = "429" in err_str or "rate" in err_str.lower() or "503" in err_str
                    if is_rate_limit and attempt < self.max_retries:
                        delay = min(15.0, self.base_delay * (2 ** attempt)) + random.uniform(0, 0.5)
                        logger.warning(
                            "%s hit 429/503 -> exponential backoff %.2fs (attempt %d/%d)",
                            tier_name,
                            delay,
                            attempt + 1,
                            self.max_retries,
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.warning("%s failed (%s) -> failing over to next tier", tier_name, err_str[:120])
                    break

        logger.warning("All LLM tiers exhausted -> returning deterministic fallback")
        return fallback_default if fallback_default is not None else {}