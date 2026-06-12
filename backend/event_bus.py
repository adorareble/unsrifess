import asyncio
import json
from collections import defaultdict

_subs: dict[str, list[asyncio.Queue]] = defaultdict(list)


def subscribe(topic: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subs[topic].append(q)
    return q


def unsubscribe(topic: str, q: asyncio.Queue):
    _subs[topic].remove(q)


def publish(topic: str, data: dict):
    for q in _subs[topic]:
        q.put_nowait(data)
