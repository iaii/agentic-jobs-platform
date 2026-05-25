from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from typing import TYPE_CHECKING

from agentic_jobs.config import settings
from agentic_jobs.db.models import CompanyCache
from agentic_jobs.services.research.scraper import ScrapedPage

if TYPE_CHECKING:
    from agentic_jobs.services.agents.schemas import CompanyIntelligence


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
    # Intelligence notes
    # ------------------------------------------------------------------

    def write_intelligence_to_vault(
        self,
        company_name: str,
        domain: str,
        intelligence: "CompanyIntelligence",
    ) -> None:
        """
        Append a Company Intelligence section to the existing vault markdown
        for this company. Called after the researcher pipeline completes.
        Only writes if vault_path is configured.
        """
        if not settings.vault_path:
            return

        vault_root = Path(settings.vault_path)
        research_dir = vault_root / settings.company_research_vault_subdir
        safe_name = _safe_filename(company_name)
        target = research_dir / f"{safe_name}.md"

        lines: list[str] = [
            "",
            "## Company Intelligence",
            "> Auto-extracted from JD and scraped pages. For candidate reference only.",
            "",
        ]

        if intelligence.stage_signals:
            lines.append("**Stage signals:**")
            for signal in intelligence.stage_signals:
                lines.append(f"- {signal}")
            lines.append("")

        if intelligence.employee_scale:
            lines.append(f"**Employee scale:** {intelligence.employee_scale}")
            lines.append("")

        if intelligence.equity_type and intelligence.equity_type != "unclear":
            lines.append(f"**Equity type:** {intelligence.equity_type}")
            lines.append("")

        if intelligence.notable_facts:
            lines.append("**Notable facts:**")
            for fact in intelligence.notable_facts:
                lines.append(f"- {fact}")
            lines.append("")

        try:
            if target.exists():
                existing = target.read_text(encoding="utf-8")
                # Replace previous intelligence section if present
                marker = "\n## Company Intelligence"
                if marker in existing:
                    existing = existing[:existing.index(marker)]
                target.write_text(existing + "\n".join(lines), encoding="utf-8")
            else:
                # File doesn't exist yet — write a minimal stub with just the intel
                stub = [
                    f"# {company_name} — Company Research",
                    "",
                    f"> Domain: `{domain}`.",
                    "",
                ] + lines
                target.write_text("\n".join(stub), encoding="utf-8")
            LOGGER.debug("Vault: wrote company intelligence to %s", target)
        except OSError as exc:
            LOGGER.warning("Could not write intelligence to vault for %s: %s", company_name, exc)

    def write_no_domain_note(self, company_name: str) -> None:
        """
        Write a minimal vault note for companies where no website could be
        resolved, so there is at least a record that research was attempted.
        Only writes if the file does not already exist (avoids overwriting
        richer notes from a later successful scrape).
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

        if target.exists():
            return

        lines = [
            f"# {company_name} — Company Research",
            "",
            f"> **No website resolved** — agentic-jobs-platform could not determine "
            f"a scrapable company domain for this employer on "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}. "
            f"The job was sourced from a third-party ATS with no embedded company URL.",
            "",
            "_No scraped content available. Add the company website to the job record "
            "and re-run the pipeline to populate this note._",
        ]
        try:
            target.write_text("\n".join(lines), encoding="utf-8")
            LOGGER.debug("Vault: wrote no-domain stub for %s", company_name)
        except OSError as exc:
            LOGGER.warning("Could not write no-domain vault stub for %s: %s", company_name, exc)

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
