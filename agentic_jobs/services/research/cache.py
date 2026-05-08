from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentic_jobs.config import settings
from agentic_jobs.db.models import CompanyCache
from agentic_jobs.services.research.scraper import ScrapedPage


LOGGER = logging.getLogger(__name__)


class CompanyResearchCache:
    """
    Two-layer cache for company research data:
      1. PostgreSQL `company_cache` table — runtime source of truth
      2. Obsidian vault — human-readable markdown copy for browsing

    Cache entries expire after `ttl_hours` (default 168h = 7 days).
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # DB cache
    # ------------------------------------------------------------------

    def get(self, domain: str) -> dict | None:
        """Return cached scraped_data if present and not stale, else None."""
        row = self._get_row(domain)
        if row is None:
            return None
        if self._is_stale(row):
            return None
        return row.scraped_data

    def put(self, domain: str, company_name: str, pages: list[ScrapedPage]) -> None:
        """Persist scraped pages to DB and write a markdown copy to Obsidian."""
        data = self._pages_to_dict(pages)
        row = self._get_row(domain)
        if row:
            row.company_name = company_name
            row.scraped_data = data
            row.scraped_at = datetime.now(timezone.utc)
            row.ttl_hours = settings.company_cache_ttl_hours
        else:
            row = CompanyCache(
                domain=domain,
                company_name=company_name,
                scraped_data=data,
                ttl_hours=settings.company_cache_ttl_hours,
            )
            self.session.add(row)
        self.session.commit()

        # Mirror to Obsidian vault for human browsing
        self._write_to_vault(company_name, domain, data)

    # ------------------------------------------------------------------
    # Obsidian vault mirror
    # ------------------------------------------------------------------

    def _write_to_vault(self, company_name: str, domain: str, data: dict) -> None:
        """
        Write a markdown summary of the company research to the Obsidian vault
        under Company Research/{company_name}.md.

        This file is machine-generated and clearly marked as such.
        The vault remains the human's primary knowledge source — this is
        a read-friendly copy of DB data, not authoritative.
        """
        if not settings.vault_path:
            return

        vault_root = Path(settings.vault_path)
        research_dir = vault_root / settings.company_research_vault_subdir
        try:
            research_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            LOGGER.warning("Could not create Company Research dir in vault: %s", exc)
            return

        safe_name = _safe_filename(company_name)
        target = research_dir / f"{safe_name}.md"

        lines: list[str] = [
            f"# {company_name} — Company Research",
            "",
            f"> **Auto-generated** by agentic-jobs-platform on "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}. "
            f"Domain: `{domain}`.",
            "",
        ]

        pages: list[dict] = data.get("pages", [])
        if not pages:
            lines.append("_No content scraped._")
        else:
            for page in pages:
                url = page.get("url", "")
                title = page.get("title", "").strip()
                text = page.get("text", "").strip()
                if not text:
                    continue
                heading = title if title else url
                lines += [f"## {heading}", "", f"Source: {url}", "", text, ""]

        try:
            target.write_text("\n".join(lines), encoding="utf-8")
            LOGGER.debug("Vault: wrote company research to %s", target)
        except OSError as exc:
            LOGGER.warning("Could not write vault research file for %s: %s", company_name, exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_row(self, domain: str) -> CompanyCache | None:
        return self.session.execute(
            select(CompanyCache).where(CompanyCache.domain == domain)
        ).scalar_one_or_none()

    @staticmethod
    def _is_stale(row: CompanyCache) -> bool:
        from datetime import timedelta
        ttl = timedelta(hours=row.ttl_hours)
        age = datetime.now(timezone.utc) - row.scraped_at.replace(tzinfo=timezone.utc)
        return age > ttl

    @staticmethod
    def _pages_to_dict(pages: list[ScrapedPage]) -> dict:
        return {
            "pages": [
                {
                    "url": p.url,
                    "title": p.title,
                    "text": p.text,
                    "status_code": p.status_code,
                }
                for p in pages
                if p.text  # only store pages that yielded content
            ]
        }


def _safe_filename(name: str) -> str:
    """Convert a company name to a safe filename."""
    import re
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    return cleaned.strip()[:80] or "unknown"
