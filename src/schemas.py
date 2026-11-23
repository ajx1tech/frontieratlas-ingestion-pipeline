from datetime import datetime, timezone
from typing import List, Literal, Optional
from urllib.parse import urlparse
from pydantic import BaseModel, Field, field_validator

BLOCKED_DOMAINS = {"example.com", "test.com", "localhost", "127.0.0.1", "fake.com"}


def validate_real_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = value.strip()
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid URL (must be http/https with valid domain): {v}")
    host = parsed.netloc.lower().split(":")[0]
    if any(host == d or host.endswith(f".{d}") for d in BLOCKED_DOMAINS):
        raise ValueError(f"Placeholder/blocked domain rejected by Grounding Gate: {v}")
    return v


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SourceInfo(BaseModel):
    name: str
    url: str

    @field_validator("url")
    @classmethod
    def check_url(cls, v: str) -> str:
        return validate_real_url(v)  # type: ignore


class StartupData(BaseModel):
    employeeCount: Optional[int] = None


class StartupContent(BaseModel):
    entityName: str
    data: StartupData = Field(default_factory=StartupData)


class StartupRecord(BaseModel):
    schemaVersion: str = "1.0"
    recordType: Literal["STARTUP"] = "STARTUP"
    source: SourceInfo
    content: StartupContent
    collectedAt: str = Field(default_factory=utc_now_iso)

    def to_flat_dict(self) -> dict:
        return {
            "schemaVersion": self.schemaVersion,
            "recordType": self.recordType,
            "source.name": self.source.name,
            "source.url": self.source.url,
            "content.entityName": self.content.entityName,
            "content.data.employeeCount": self.content.data.employeeCount,
            "collectedAt": self.collectedAt,
        }


class ProductContent(BaseModel):
    startupName: str
    productName: str = ""
    pricingModel: Literal["FREE", "FREEMIUM", "PAID", "ENTERPRISE"] = "FREE"


class ProductRecord(BaseModel):
    schemaVersion: str = "1.0"
    recordType: Literal["PRODUCT"] = "PRODUCT"
    source: SourceInfo
    content: ProductContent
    collectedAt: str = Field(default_factory=utc_now_iso)

    def to_flat_dict(self) -> dict:
        return {
            "schemaVersion": self.schemaVersion,
            "recordType": self.recordType,
            "source.name": self.source.name,
            "source.url": self.source.url,
            "content.startupName": self.content.startupName,
            "content.productName": self.content.productName,
            "content.pricingModel": self.content.pricingModel,
            "collectedAt": self.collectedAt,
        }


class ResearchPaperContent(BaseModel):
    title: str
    authors: List[str] = Field(default_factory=list)
    paper_url: str
    github_url: Optional[str] = None
    github_stars: Optional[int] = None
    published_date: str

    @field_validator("paper_url")
    @classmethod
    def check_paper_url(cls, v: str) -> str:
        return validate_real_url(v)  # type: ignore

    @field_validator("github_url")
    @classmethod
    def check_github_url(cls, v: Optional[str]) -> Optional[str]:
        return validate_real_url(v)


class ResearchPaperRecord(BaseModel):
    schemaVersion: str = "1.0"
    recordType: Literal["RESEARCH_PAPER"] = "RESEARCH_PAPER"
    content: ResearchPaperContent
    collectedAt: str = Field(default_factory=utc_now_iso)

    def to_flat_dict(self) -> dict:
        return {
            "schemaVersion": self.schemaVersion,
            "recordType": self.recordType,
            "content.title": self.content.title,
            "content.authors": ", ".join(self.content.authors[:10]),
            "content.paper_url": self.content.paper_url,
            "content.github_url": self.content.github_url or "",
            "content.github_stars": self.content.github_stars if self.content.github_stars is not None else "",
            "content.published_date": self.content.published_date,
            "collectedAt": self.collectedAt,
        }


class JobContent(BaseModel):
    company: str
    title: str
    date: str
    is_remote: bool = False
    role_family: str = "Engineering"


class JobRecord(BaseModel):
    schemaVersion: str = "1.0"
    recordType: Literal["JOB"] = "JOB"
    source: SourceInfo
    content: JobContent
    collectedAt: str = Field(default_factory=utc_now_iso)

    def to_flat_dict(self) -> dict:
        return {
            "schemaVersion": self.schemaVersion,
            "recordType": self.recordType,
            "source.name": self.source.name,
            "source.url": self.source.url,
            "content.company": self.content.company,
            "content.title": self.content.title,
            "content.date": self.content.date,
            "content.is_remote": self.content.is_remote,
            "content.role_family": self.content.role_family,
            "collectedAt": self.collectedAt,
        }


class NewsContent(BaseModel):
    title: str
    published_date: str
    full_text_excerpt: str
    entities_mentioned: List[str] = Field(default_factory=list)


class NewsRecord(BaseModel):
    schemaVersion: str = "1.0"
    recordType: Literal["NEWS"] = "NEWS"
    source: SourceInfo
    content: NewsContent
    collectedAt: str = Field(default_factory=utc_now_iso)

    def to_flat_dict(self) -> dict:
        return {
            "schemaVersion": self.schemaVersion,
            "recordType": self.recordType,
            "source.name": self.source.name,
            "source.url": self.source.url,
            "content.title": self.content.title,
            "content.published_date": self.content.published_date,
            "content.full_text_excerpt": self.content.full_text_excerpt,
            "content.entities_mentioned": ", ".join(self.content.entities_mentioned),
            "collectedAt": self.collectedAt,
        }