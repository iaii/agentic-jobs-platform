import asyncio

import httpx

from agentic_jobs.services.discovery.universal.detector import ParserDetector


def test_detector_infers_lever_from_url():
    async def _run():
        async with httpx.AsyncClient() as client:
            detector = ParserDetector(client)
            result = detector._infer_from_url("https://jobs.lever.co/meta")
            assert result is not None
            assert result.parser == "lever"
            assert result.options["company"] == "meta"

    asyncio.run(_run())


def test_detector_parses_workday_from_body():
    sample_html = """
        <html>
        <script>
        const api = "https://jobs.apple.com/wday/cxs/apple/en-us/jobs";
        </script>
        </html>
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=sample_html)

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            detector = ParserDetector(client)
            result = await detector.detect("https://example.com")
            assert result.parser == "workday"
            assert result.options["tenant"] == "apple"
            assert result.options["site"] == "en-us"

    asyncio.run(_run())
