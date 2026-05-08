from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx

from agentic_jobs.services.discovery.universal.detector import ParserDetectionError, ParserDetector


async def _run(url: str) -> int:
    async with httpx.AsyncClient() as client:
        detector = ParserDetector(client)
        try:
            result = await detector.detect(url)
        except ParserDetectionError as exc:
            print(f"Detection failed: {exc}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "parser": result.parser,
                    "options": result.options,
                },
                indent=2,
            )
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect ATS parser + options for a careers URL.")
    parser.add_argument("url", help="Public careers/job search URL (Lever/Workday supported).")
    args = parser.parse_args()
    return asyncio.run(_run(args.url))


if __name__ == "__main__":
    raise SystemExit(main())
