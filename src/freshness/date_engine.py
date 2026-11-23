import hashlib
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple
import dateparser

from src.config import DB_PATH

URL_DATE_REGEX = re.compile(r"/(202\d)/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/")


class FreshnessEngine:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_urls (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    first_seen_utc TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def parse_to_utc(self, raw_date: Optional[str | int | float], url: str = "") -> Optional[datetime]:
        if raw_date is not None and raw_date != "":
            if isinstance(raw_date, (int, float)):
                ts = raw_date / 1000.0 if raw_date > 1e12 else float(raw_date)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            raw_str = str(raw_date).strip()
            if raw_str.isdigit():
                val = int(raw_str)
                ts = val / 1000.0 if val > 1e12 else float(val)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            parsed = dateparser.parse(
                raw_str,
                settings={
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "TIMEZONE": "UTC",
                    "TO_TIMEZONE": "UTC",
                    "RELATIVE_BASE": datetime.now(timezone.utc).replace(tzinfo=None),
                },
            )
            if parsed:
                return parsed.astimezone(timezone.utc)

        # Fallback: extract date from URL path e.g. /2026/11/23/
        match = URL_DATE_REGEX.search(url or "")
        if match:
            y, m, d = map(int, match.groups())
            return datetime(y, m, d, 12, 0, 0, tzinfo=timezone.utc)
        return None

    def check_freshness(
        self,
        url: str,
        raw_date: Optional[str | int | float],
        content_text: str = "",
        max_hours: float = 24.0,
    ) -> Tuple[bool, str, str]:
        """Returns (is_fresh, normalized_iso_utc, method)."""
        now = datetime.now(timezone.utc)
        parsed_dt = self.parse_to_utc(raw_date, url=url)

        if parsed_dt is not None:
            age_hours = (now - parsed_dt).total_seconds() / 3600.0
            is_fresh = -2.0 <= age_hours <= max_hours
            return is_fresh, parsed_dt.isoformat(), "explicit_or_relative_date"

        # Heuristic for dateless sources: SQLite first_seen_utc ledger
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        content_hash = hashlib.sha256((content_text or url).encode("utf-8")).hexdigest()

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT first_seen_utc FROM seen_urls WHERE url_hash = ?", (url_hash,)
            ).fetchone()
            if row is None:
                now_iso = now.isoformat()
                conn.execute(
                    "INSERT INTO seen_urls (url_hash, url, content_sha256, first_seen_utc) VALUES (?, ?, ?, ?)",
                    (url_hash, url, content_hash, now_iso),
                )
                conn.commit()
                return True, now_iso, "sqlite_first_seen_new"
            first_seen = datetime.fromisoformat(row[0]).astimezone(timezone.utc)
            age_hours = (now - first_seen).total_seconds() / 3600.0
            return (age_hours <= max_hours), first_seen.isoformat(), "sqlite_first_seen_existing"