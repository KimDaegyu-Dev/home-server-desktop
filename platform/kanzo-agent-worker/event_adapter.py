#!/usr/bin/env python3
"""Bridge Kanzo's in-process change stream to the worker's local wake endpoint."""

from __future__ import annotations

import logging
import os
import time
import urllib.error
import urllib.request


LOG = logging.getLogger("kanzo-event-adapter")
EVENTS_URL = os.environ.get(
    "KANZO_EVENTS_URL", "http://kanzo.kanzo.svc.cluster.local:4242/api/events"
)
WAKE_URL = os.environ.get("KANZO_WAKE_URL", "http://127.0.0.1:9105/wake")


def wake_worker() -> None:
    request = urllib.request.Request(WAKE_URL, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 202:
            raise RuntimeError(f"worker wake endpoint returned HTTP {response.status}")


def listen_once() -> None:
    request = urllib.request.Request(
        EVENTS_URL,
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        if response.status != 200:
            raise RuntimeError(f"Kanzo event stream returned HTTP {response.status}")
        LOG.info("Connected to Kanzo event stream")
        # Reconcile after every (re)connect so changes during an outage are not lost.
        wake_worker()
        event_name = ""
        while True:
            line = response.readline()
            if not line:
                raise ConnectionError("Kanzo event stream closed")
            line = line.decode("utf-8", errors="replace").strip()
            if line.startswith("event:"):
                event_name = line.partition(":")[2].strip()
            elif not line:
                if event_name == "kanzo.changed":
                    wake_worker()
                event_name = ""


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    delay = 1
    while True:
        try:
            listen_once()
            delay = 1
        except Exception:
            LOG.exception("Kanzo event connection failed; retrying in %s seconds", delay)
            time.sleep(delay)
            delay = min(delay * 2, 30)


if __name__ == "__main__":
    main()
