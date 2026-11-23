import logging
import re
from typing import Tuple
import trafilatura
from src.config import MAX_CHUNK_CHARS

logger = logging.getLogger(__name__)


def clean_html_text(raw_text: str) -> str:
    if not raw_text:
        return ""
    if "<html" in raw_text.lower() or "<div" in raw_text.lower() or "<p" in raw_text.lower():
        extracted = trafilatura.extract(raw_text, include_comments=False, include_tables=True)
        if extracted and len(extracted.strip()) > 40:
            return re.sub(r"\s+", " ", extracted).strip()
        stripped = re.sub(r"<(script|style|nav|footer)[^>]*>.*?</\1>", " ", raw_text, flags=re.S | re.I)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        return re.sub(r"\s+", " ", stripped).strip()
    return re.sub(r"\s+", " ", raw_text).strip()


def prepare_llm_payload(raw_text: str, max_chars: int = MAX_CHUNK_CHARS) -> Tuple[str, dict]:
    original_chars = len(raw_text or "")
    cleaned = clean_html_text(raw_text)
    cleaned_chars = len(cleaned)
    truncated = False

    if cleaned_chars > max_chars:
        truncated = True
        head_len = int(max_chars * 0.70)
        tail_len = max_chars - head_len - 25
        cleaned = f"{cleaned[:head_len]}\n...[TRUNCATED]...\n{cleaned[-tail_len:]}"
        logger.info(
            "413-GUARD Chunked payload: raw=%d -> cleaned=%d -> final=%d chars (truncated=True)",
            original_chars,
            cleaned_chars,
            len(cleaned),
        )

    meta = {
        "original_chars": original_chars,
        "cleaned_chars": cleaned_chars,
        "final_chars": len(cleaned),
        "truncated": truncated,
    }
    return cleaned, meta