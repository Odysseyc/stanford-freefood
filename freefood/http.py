from __future__ import annotations

import time

import requests

from . import config

_session = requests.Session()
_session.headers["User-Agent"] = config.USER_AGENT

_last_call = 0.0


def get(url: str, **kw) -> requests.Response:
    """Rate-limited GET. Serialises all outbound traffic to one call per
    REQUEST_DELAY seconds so we never look like a hammering bot."""
    global _last_call
    wait = config.REQUEST_DELAY - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    kw.setdefault("timeout", config.HTTP_TIMEOUT)
    resp = _session.get(url, **kw)
    _last_call = time.time()
    return resp
