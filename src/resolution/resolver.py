import csv
import re
from pathlib import Path
from typing import Dict, List, Tuple
from rapidfuzz import fuzz, process

from src.config import EXPORTS_DIR
from src.resolution.seed_entities import SEED_ENTITIES

SUFFIX_PATTERN = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|pbc|gmbh|sas|bv|labs|technologies|co)\b\.?",
    flags=re.I,
)


def normalize_name(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"[^\w\s&]", " ", s)
    s = SUFFIX_PATTERN.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


class EntityResolver:
    def __init__(self, fuzzy_threshold: int = 88):
        self.fuzzy_threshold = fuzzy_threshold
        self.canonical_names: List[str] = list(SEED_ENTITIES.keys())
        self.alias_map: Dict[str, str] = {}
        self.norm_to_canonical: Dict[str, str] = {}
        self.logs: List[dict] = []

        for canonical, aliases in SEED_ENTITIES.items():
            c_norm = normalize_name(canonical)
            self.alias_map[c_norm] = canonical
            self.alias_map[c_norm.replace(" ", "")] = canonical
            self.norm_to_canonical[c_norm] = canonical
            for alias in aliases:
                a_norm = normalize_name(alias)
                self.alias_map[a_norm] = canonical
                self.alias_map[a_norm.replace(" ", "")] = canonical

    def resolve(self, raw_name: str, source_tab: str = "Unknown", source_url: str = "") -> Tuple[str, str, float]:
        raw_clean = (raw_name or "").strip()
        if not raw_clean:
            return "Unknown", "empty", 0.0

        norm = normalize_name(raw_clean)
        compact = norm.replace(" ", "")

        # Stage 1 & 2: Exact normalized / alias lookup
        if norm in self.alias_map:
            canonical = self.alias_map[norm]
            self._log(raw_clean, canonical, "exact_seed", 100.0, source_tab, source_url)
            return canonical, "exact_seed", 100.0

        if compact in self.alias_map:
            canonical = self.alias_map[compact]
            self._log(raw_clean, canonical, "exact_compact_alias", 100.0, source_tab, source_url)
            return canonical, "exact_compact_alias", 100.0

        # Stage 3: RapidFuzz deterministic fuzzy match
        match = process.extractOne(norm, list(self.norm_to_canonical.keys()), scorer=fuzz.token_sort_ratio)
        if match and match[1] >= self.fuzzy_threshold:
            canonical = self.norm_to_canonical[match[0]]
            score = round(float(match[1]), 2)
            self._log(raw_clean, canonical, "fuzzy_rapidfuzz", score, source_tab, source_url)
            return canonical, "fuzzy_rapidfuzz", score

        # Passthrough for non-seed entities
        cleaned_display = raw_clean.strip()
        self._log(raw_clean, cleaned_display, "passthrough", 100.0, source_tab, source_url)
        return cleaned_display, "passthrough", 100.0

    def _log(self, raw: str, canonical: str, method: str, score: float, tab: str, url: str) -> None:
        self.logs.append(
            {
                "raw_name": raw,
                "canonical_name": canonical,
                "match_method": method,
                "confidence_score": score,
                "source_tab": tab,
                "source_url": url,
            }
        )

    def export_log(self, path: Path = EXPORTS_DIR / "entity_mapping_log.csv") -> Path:
        fieldnames = ["raw_name", "canonical_name", "match_method", "confidence_score", "source_tab", "source_url"]
        # Prioritize interesting canonicalized mappings at the top of the sheet
        ordered = sorted(self.logs, key=lambda r: (r["match_method"] == "passthrough", r["raw_name"].lower()))
        seen = set()
        deduped = []
        for row in ordered:
            key = (row["raw_name"], row["canonical_name"], row["source_tab"])
            if key not in seen:
                seen.add(key)
                deduped.append(row)

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(deduped)
        return path