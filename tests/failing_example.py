#!/usr/bin/env python3
"""End-to-end example: a build that fails on purpose.

What this exercises: the failure path of the pipeline. The Dockerfile
contains `RUN ... exit 1`, so buildkit will abort the build mid-run.
The build service must:

  - flip the build's status to "failed" (NOT leave it stuck in "running")
  - surface a non-empty `failure_reason` diagnostic
  - close the SSE log stream cleanly when it hits terminal status

Usage:
    export RUNPOD_API_KEY=rpa_...
    python3 failing_example.py
"""

from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

import httpx

API_URL = os.environ.get("BUILD_SERVICE_API_URL", "https://v2-rest-brody.runpod.dev")
KEY = os.environ.get("RUNPOD_API_KEY")
if not KEY:
    sys.exit("error: RUNPOD_API_KEY is not set")

STAMP = f"{int(time.time())}-{random.randint(0, 0xFFFF):04x}"
REPO = f"failing-{STAMP}"
DOCKERFILE = Path(__file__).parent.parent / "testdata" / "Dockerfile.failing"


def main() -> None:
    c = httpx.Client(timeout=30.0, headers={"Authorization": f"Bearer {KEY}"})

    print(f"API_URL = {API_URL}")
    print(f"repo    = {REPO}")

    c.post(f"{API_URL}/v2/repositories", json={"name": REPO}).raise_for_status()
    c.post(
        f"{API_URL}/v2/repositories/{REPO}/images",
        json={"name": "doomed"},
    ).raise_for_status()

    print("\n[submit] sending a Dockerfile with `RUN ... exit 1`...")
    resp = c.post(
        f"{API_URL}/v2/repositories/{REPO}/images/doomed/builds",
        json={"source": {"type": "inline", "dockerfile_content": DOCKERFILE.read_text()}},
    )
    resp.raise_for_status()
    build = resp.json()
    print(f"        build_id = {build['id']}")

    print("\n[stream] watching the build fail in real time...\n")
    with c.stream(
        "GET",
        f"{API_URL}/v2/builds/{build['id']}/logs?follow=true",
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data: "):
                print("        " + line[len("data: "):])

    print("\n[check] confirming the build correctly transitioned to 'failed'...")
    final = c.get(f"{API_URL}/v2/builds/{build['id']}").json()
    print(f"        status         = {final['status']}")
    print(f"        failure_reason = {final.get('failure_reason')!r}")
    if final["status"] != "failed":
        sys.exit(f"        UNEXPECTED status — wanted 'failed', got {final['status']!r}")
    if not final.get("failure_reason"):
        sys.exit("        MISSING failure_reason — should surface a diagnostic")

    print("\n*** SUCCESS — build correctly marked 'failed' with a diagnostic ***")

    print("\n[cleanup] deleting repository...")
    c.delete(
        f"{API_URL}/v2/repositories/{REPO}",
        params={"purge": "true"},
    ).raise_for_status()
    c.close()


if __name__ == "__main__":
    main()
