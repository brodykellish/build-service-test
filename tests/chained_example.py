#!/usr/bin/env python3
"""End-to-end example: chained builds (B's FROM = A's pushed image).

What this exercises:

    Build A (Dockerfile.base):
      FROM alpine:3.20  ->  push to registry as <repo>/base:v1

    Build B (Dockerfile.child, with __BASE_IMAGE__ replaced by A's URL):
      FROM <A's registry URL>  ->  push to registry as <repo>/child:v1

    docker pull + run child  ->  output shows BOTH "FROM_A" and "FROM_B"

Why this is interesting: build B's FROM line goes through registry-auth
to mint a *pull* token for the user's namespace. That hits a different
code path from build A's *push* — both must work for the chained flow
to succeed.

Usage:
    export RUNPOD_API_KEY=rpa_...
    python3 chained_example.py

Optional env overrides (same as manual_e2e.py):
    BUILD_SERVICE_API_URL   default: https://v2-rest-brody.runpod.dev
    REGISTRY_HOST           default: registry-brody.runpod.dev
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
REPO = f"chained-{STAMP}"

TESTDATA = Path(__file__).parent.parent / "testdata"
DF_BASE = TESTDATA / "Dockerfile.base"
DF_CHILD = TESTDATA / "Dockerfile.child"


def stream_logs(c: httpx.Client, build_id: str) -> None:
    """Stream a build's logs to stdout until the server closes the connection
    (which happens at terminal status)."""
    with c.stream(
        "GET",
        f"{API_URL}/v2/builds/{build_id}/logs?follow=true",
        timeout=httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0),
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data: "):
                print("      " + line[len("data: "):])


def submit_and_wait(c: httpx.Client, label: str, image_name: str, dockerfile: str) -> dict:
    """Submit a build, stream its logs, return the final build dict.
    Exits the script on a non-success terminal status."""
    print(f"\n[{label}] submitting build for image={image_name}...")
    resp = c.post(
        f"{API_URL}/v2/repositories/{REPO}/images/{image_name}/builds",
        json={"source": {"type": "inline", "dockerfile_content": dockerfile}},
    )
    resp.raise_for_status()
    build = resp.json()
    print(f"        build_id  = {build['id']}")
    print(f"        image_url = {build['image']}")

    print(f"\n[{label}] streaming logs...")
    stream_logs(c, build["id"])

    print(f"\n[{label}] confirming terminal status...")
    final = c.get(f"{API_URL}/v2/builds/{build['id']}").json()
    print(f"        status = {final['status']}")
    if final["status"] != "success":
        sys.exit(f"        failure_reason = {final.get('failure_reason')!r}")
    return final


def main() -> None:
    c = httpx.Client(timeout=30.0, headers={"Authorization": f"Bearer {KEY}"})

    print(f"API_URL  = {API_URL}")
    print(f"REGISTRY = {REGISTRY}")
    print(f"repo     = {REPO}")

    # One repo holds both images.
    print("\n[setup] creating repository + 'base' and 'child' images...")
    c.post(f"{API_URL}/v2/repositories", json={"name": REPO}).raise_for_status()
    for img in ("base", "child"):
        c.post(
            f"{API_URL}/v2/repositories/{REPO}/images",
            json={"name": img},
        ).raise_for_status()

    # --- Build A: produce the base image.
    a_final = submit_and_wait(c, "A", "base", DF_BASE.read_text())
    base_image = a_final["image"]
    print(f"\n[A] base image is at: {base_image}")

    # --- Render Dockerfile.child by substituting A's pushed image URL.
    child_dockerfile = DF_CHILD.read_text().replace("__BASE_IMAGE__", base_image)
    print(f"\n[render] child Dockerfile rendered, first FROM line:")
    for line in child_dockerfile.splitlines():
        if line.startswith("FROM "):
            print(f"        {line}")
            break

    # --- Build B: FROM the image A produced.
    b_final = submit_and_wait(c, "B", "child", child_dockerfile)
    child_image = b_final["image"]
    print(f"\n[B] child image is at: {child_image}")

    # --- Pull the child image and run it. Expect output with BOTH markers.
    print("\n[verify] docker login + pull child image...")
    subprocess.run(
        ["docker", "login", REGISTRY, "-u", "_token", "--password-stdin"],
        input=KEY,
        text=True,
        check=True,
    )
    subprocess.run(["docker", "pull", child_image], check=True)

    print("\n[verify] running child container — expecting BOTH lineage markers...")
    out = subprocess.run(
        ["docker", "run", "--rm", child_image],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    print("        --- container output ---")
    for line in out.splitlines():
        print(f"        {line}")
    print("        ------------------------")
    if "FROM_A" not in out or "FROM_B" not in out:
        sys.exit(f"        MISMATCH: expected both FROM_A and FROM_B in output")
    print("\n*** SUCCESS — child image was built on top of the previously-built base ***")

    # --- Cleanup.
    print("\n[cleanup] deleting repository (cascades to both images + builds)...")
    c.delete(
        f"{API_URL}/v2/repositories/{REPO}",
        params={"purge": "true"},
    ).raise_for_status()
    for img in (base_image, child_image):
        subprocess.run(
            ["docker", "image", "rm", img],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    c.close()


if __name__ == "__main__":
    main()
