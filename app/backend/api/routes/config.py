"""Where the three model services are.

Unchanged in purpose from version 1: they live on a Colab runtime behind a
tunnel whose address changes every restart, so the addresses are set at runtime
from the app's first screen rather than baked into a config file.

What changed is where the answer is kept. Version 1 wrote a JSON file next to
its database, which worked because there was one process. With many API tasks
and many workers, the addresses have to live somewhere all of them can see —
so they are in Redis, and a worker picking up a message reads the same value
the browser just set.
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from ...shared import clients, services
from ...shared.config import get_settings
from ...shared.schemas import ServiceConfigOut, ServiceUrls

router = APIRouter(prefix="/config", tags=["config"])
log = logging.getLogger(__name__)
settings = get_settings()

async def _probe(urls: dict[str, str]) -> dict[str, dict | None]:
    """Ask all three at once. Sequentially this takes thirty seconds when down."""
    results = await asyncio.gather(*[
        run_in_threadpool(clients.health, url) for url in urls.values()
    ])
    return dict(zip(urls.keys(), results))


@router.get("/services", response_model=ServiceConfigOut)
async def read_services() -> ServiceConfigOut:
    urls = await run_in_threadpool(services.read_urls)
    probed = await _probe(urls)
    healthy = {name: body is not None for name, body in probed.items()}
    return ServiceConfigOut(urls=urls, healthy=healthy, ready=all(healthy.values()))


@router.put("/services", response_model=ServiceConfigOut)
async def write_services(body: ServiceUrls) -> ServiceConfigOut:
    """Point the system at three new addresses.

    Checks that each one answers *and* that it is the service it is supposed to
    be, because all three reply to /health and three addresses pasted in the
    wrong order otherwise look perfectly healthy until an upload fails later.
    """
    candidate = {
        "embedding": body.embedding.strip().rstrip("/"),
        "stt": body.stt.strip().rstrip("/"),
        "llm": body.llm.strip().rstrip("/"),
    }

    for name, url in candidate.items():
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400,
                                detail=f"{name}: address must start with http:// or https://")

    probed = await _probe(candidate)

    unreachable = [n for n, body_ in probed.items() if body_ is None]
    if unreachable:
        raise HTTPException(
            status_code=400,
            detail=f"could not reach: {', '.join(unreachable)}. "
                   "Check the tunnel is still open and the URL was copied whole.",
        )

    swapped = [
        f"{name} address is actually the {body_.get('service') or 'unknown'} service"
        for name, body_ in probed.items()
        if body_.get("service") and body_["service"] != name
    ]
    if swapped:
        raise HTTPException(status_code=400,
                            detail="; ".join(swapped) + ". Check the order you pasted them in.")

    await run_in_threadpool(services.write_urls, candidate)
    healthy = {name: True for name in services.NAMES}
    return ServiceConfigOut(urls=candidate, healthy=healthy, ready=True)
