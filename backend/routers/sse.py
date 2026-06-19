import asyncio
import json

from fastapi import APIRouter, Request, Query, Depends
from fastapi.responses import StreamingResponse

from event_bus import subscribe, unsubscribe
from routers.public import get_active_tenant

sse_router = APIRouter()


async def _event_generator(topics: list[str], tenant_id: int | None = None):
    queues = {t: subscribe(t) for t in topics}
    get_tasks = {}
    try:
        for topic, q in queues.items():
            task = asyncio.ensure_future(q.get())
            get_tasks[task] = topic

        while True:
            done, _ = await asyncio.wait(get_tasks.keys(), timeout=30, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                yield ":ping\n\n"
                continue
            for task in done:
                topic = get_tasks[task]
                data = task.result()
                if tenant_id is not None and data.get("tenant_id") is not None and data["tenant_id"] != tenant_id:
                    del get_tasks[task]
                    new_task = asyncio.ensure_future(queues[topic].get())
                    get_tasks[new_task] = topic
                    continue
                yield f"event: {data['event']}\ndata: {json.dumps(data)}\n\n"
                del get_tasks[task]
                new_task = asyncio.ensure_future(queues[topic].get())
                get_tasks[new_task] = topic
    except (asyncio.CancelledError, GeneratorExit):
        for task in get_tasks:
            task.cancel()
    finally:
        for topic, q in queues.items():
            unsubscribe(topic, q)


@sse_router.get("/{slug}/api/events")
async def public_events(
    request: Request,
    slug: str,
    tenant: dict = Depends(get_active_tenant),
    token: str = Query(""),
):
    from auth import decode_token

    tenant_id = tenant["id"]
    topics = ["status_changed", "announcement_changed"]

    if token:
        payload = decode_token(token)
        if payload and payload.get("type") == "user" and payload.get("tenant_id") == tenant_id:
            topics.extend(["tweet_updated", "user_status_changed"])

    return StreamingResponse(
        _event_generator(topics, tenant_id=tenant_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@sse_router.get("/{slug}/panel/api/events")
async def admin_events(
    request: Request,
    slug: str,
    tenant: dict = Depends(get_active_tenant),
    token: str = Query(""),
):
    from auth import decode_token

    tenant_id = tenant["id"]
    payload = decode_token(token) if token else None
    if payload is None or payload.get("type") != "admin":
        return StreamingResponse(iter([]), status_code=401)

    admin_tenant_id = payload.get("tenant_id")
    if admin_tenant_id is not None and admin_tenant_id != tenant_id:
        return StreamingResponse(iter([]), status_code=403)

    topics = [
        "new_tweet",
        "tweet_updated",
        "status_changed",
        "announcement_changed",
        "user_status_changed",
        "sync_progress",
        "task_progress",
        "keyword_updated",
        "admin_updated",
    ]
    return StreamingResponse(
        _event_generator(topics, tenant_id=tenant_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
