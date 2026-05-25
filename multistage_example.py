#!/usr/bin/env python3
"""End-to-end example: multistage Dockerfile.

What this exercises: buildkit's multistage feature. Dockerfile.multistage
has a 'builder' stage that produces both a wanted artifact and an
unwanted internal file, followed by a minimal final stage that COPYs
only the artifact. The example asserts the final image contains the
artifact but NOT the builder stage's internal file — i.e., multistage
isolation works.

Usage:
    export RUNPOD_API_KEY=rpa_...
    python3 multistage_example.py
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
import time
from pathlib import Path

import httpx

API_URL = os.environ.get("BUILD_SERVICE_API_URL", "https://v2-rest-brody.runpod.dev")
REGISTRY = os.environ.get("REGISTRY_HOST", "registry-brody.runpod.dev")
KEY = os.environ.get("RUNPOD_API_KEY")
if not KEY:
    sys.exit("error: RUNPOD_API_KEY is not set")

STAMP = f"{int(time.time())}-{random.randint(0, 0xFFFF):04x}"
REPO = f"multistage-{STAMP}"
DOCKERFILE = Path(__file__).parent / "Dockerfile.multistage"


def main() -> None:
    c = httpx.Client(timeout=30.0, headers={"Authorization": f"Bearer {KEY}"})

    print(f"API_URL = {API_URL}")
    print(f"repo    = {REPO}")

    c.post(f"{API_URL}/v2/repositories", json={"name": REPO}).raise_for_status()
    c.post(
        f"{API_URL}/v2/repositories/{REPO}/images",
        json={"name": "demo"},
    ).raise_for_status()

    print("\n[submit] sending multistage Dockerfile...")
    resp = c.post(
        f"{API_URL}/v2/repositories/{REPO}/images/demo/builds",
        json={"source": {"type": "inline", "dockerfile_content": DOCKERFILE.read_text()}},
    )
    resp.raise_for_status()
    build = resp.json()
    image_url = build["image"]
    print(f"        build_id  = {build['id']}")
    print(f"        image_url = {image_url}")

    print("\n[stream] streaming build logs (you'll see both stages run)...\n")
    with c.stream(
        "GET",
        f"{API_URL}/v2/builds/{build['id']}/logs?follow=true",
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data: "):
                print("        " + line[len("data: "):])

    final = c.get(f"{API_URL}/v2/builds/{build['id']}").json()
    if final["status"] != "success":
        sys.exit(f"\n        build failed: {final.get('failure_reason')!r}")

    print("\n[verify] docker login + pull + run final image...")
    subprocess.run(
        ["docker", "login", REGISTRY, "-u", "_token", "--password-stdin"],
        input=KEY,
        text=True,
        check=True,
    )
    subprocess.run(["docker", "pull", image_url], check=True)
    out = subprocess.run(
        ["docker", "run", "--rm", image_url],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    print("        --- container output ---")
    for line in out.splitlines():
        print(f"        {line}")
    print("        ------------------------")

    if "ARTIFACT_FROM_BUILDER_STAGE" not in out:
        sys.exit("        MISSING the artifact — multistage COPY did not work")
    if "BUILDER_STAGE_INTERNAL_FILE" in out:
        sys.exit("        LEAK: builder-stage internal file appeared in final image")
    if "internal-file-absent" not in out:
        sys.exit("        unexpected output — internal file should have been absent")

    print("\n*** SUCCESS — final image has the artifact, NOT the builder's internal file ***")

    print("\n[cleanup] deleting repository + local image...")
    c.delete(
        f"{API_URL}/v2/repositories/{REPO}",
        params={"purge": "true"},
    ).raise_for_status()
    subprocess.run(
        ["docker", "image", "rm", image_url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    c.close()


if __name__ == "__main__":
    main()
