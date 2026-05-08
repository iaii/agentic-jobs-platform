from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence


@dataclass(slots=True)
class AutoWhitelistEntry:
    domain_root: str
    ats_type: str
    company_name: str | None = None


EXACT_HOSTS: dict[str, AutoWhitelistEntry] = {
    "boards.greenhouse.io": AutoWhitelistEntry("boards.greenhouse.io", "greenhouse", "Greenhouse"),
    "job-boards.greenhouse.io": AutoWhitelistEntry("job-boards.greenhouse.io", "greenhouse", "Greenhouse"),
    "jobs.lever.co": AutoWhitelistEntry("jobs.lever.co", "lever", "Lever"),
    "jobs.ashbyhq.com": AutoWhitelistEntry("jobs.ashbyhq.com", "ashby", "Ashby"),
    "jobs.smartrecruiters.com": AutoWhitelistEntry("jobs.smartrecruiters.com", "smartrecruiters", "SmartRecruiters"),
    "amazon.jobs": AutoWhitelistEntry("amazon.jobs", "company", "Amazon"),
    "apply.careers.microsoft.com": AutoWhitelistEntry("apply.careers.microsoft.com", "company", "Microsoft"),
}

SUFFIX_HOSTS: Sequence[tuple[str, AutoWhitelistEntry]] = (
    (".myworkdayjobs.com", AutoWhitelistEntry("", "workday", "Workday")),
    (".myworkdaysite.com", AutoWhitelistEntry("", "workday", "Workday Site")),
    (".wd1.myworkdayjobs.com", AutoWhitelistEntry("", "workday", "Workday")),
    (".wd2.myworkdayjobs.com", AutoWhitelistEntry("", "workday", "Workday")),
    (".wd3.myworkdayjobs.com", AutoWhitelistEntry("", "workday", "Workday")),
    (".wd4.myworkdayjobs.com", AutoWhitelistEntry("", "workday", "Workday")),
    (".wd5.myworkdayjobs.com", AutoWhitelistEntry("", "workday", "Workday")),
    (".wd12.myworkdayjobs.com", AutoWhitelistEntry("", "workday", "Workday")),
    (".wd501.myworkdayjobs.com", AutoWhitelistEntry("", "workday", "Workday")),
    (".icims.com", AutoWhitelistEntry("", "icims", "iCIMS")),
    (".fa.ocs.oraclecloud.com", AutoWhitelistEntry("", "oraclehcm", "Oracle Cloud HCM")),
)

NETFLIX_HOSTS = {
    "jobs.netflix.net": AutoWhitelistEntry("jobs.netflix.net", "company", "Netflix"),
    "explore.jobs.netflix.net": AutoWhitelistEntry("explore.jobs.netflix.net", "company", "Netflix"),
}


def lookup_auto_whitelist(domain_root: str) -> AutoWhitelistEntry | None:
    host = (domain_root or "").lower()
    if not host:
        return None
    if host in EXACT_HOSTS:
        return EXACT_HOSTS[host]
    if host in NETFLIX_HOSTS:
        return NETFLIX_HOSTS[host]
    for suffix, entry in SUFFIX_HOSTS:
        if host.endswith(suffix):
            return AutoWhitelistEntry(
                domain_root=host,
                ats_type=entry.ats_type,
                company_name=entry.company_name,
            )
    return None


def apply_auto_whitelist(session, domain_root: str, company_name: str | None, entry: AutoWhitelistEntry):
    from agentic_jobs.db import models

    if session.get(models.Whitelist, domain_root):
        return
    record = models.Whitelist(
        domain_root=domain_root,
        company_name=company_name or entry.company_name,
        ats_type=entry.ats_type,
        approved_by="auto",
        approved_at=datetime.now(tz=timezone.utc),
    )
    session.merge(record)
