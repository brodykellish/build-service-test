#!/usr/bin/env python3
"""End-to-end smoke test for the RunPod build service.

Submits a build with the local Dockerfile, streams its logs, then docker
pulls the resulting image and runs it to confirm the bytes roundtripped.
If the script exits 0, the full pipeline works:

    your terminal
      -> rphttp2 (gateway / public REST)
      -> build-api (resources)
      -> scheduler (SQS-fed dispatch)
      -> builder + buildkitd
      -> registry-auth (token minting)
      -> registry (R2-backed OCI registry)

Usage:
    pip install -r requirements.txt
    export RUNPOD_API_KEY=rpa_...
    python3 manual_e2e.py

Optional env overrides:
    BUILD_SERVICE_API_URL   default: https://v2-rest-brody.runpod.dev
    REGISTRY_HOST           default: registry-brody.runpod.dev
"""

from __future__ import annotations

import os
import platform
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

# Must match the marker in testdata/Dockerfile.basic. Change both together.
GREETING = "HELLO_FROM_RUNPOD_BUILD_SERVICE"
DOCKERFILE = Path(__file__).parent.parent / "testdata" / "Dockerfile.basic"

STAMP = f"{int(time.time())}-{random.randint(0, 0xFFFF):04x}"
REPO = f"example-{STAMP}"
IMAGE_NAME = "demo"


def host_platform() -> str:
    """Return the OCI platform string matching the local host architecture.

    We use this to ask the build service to build for the SAME arch we're
    going to docker-pull from — otherwise Apple Silicon Macs (arm64) get
    a "no matching manifest" error when pulling a single-arch amd64 image.
    Override via the TARGET_PLATFORM env var if you want a specific target
    (e.g. TARGET_PLATFORM=linux/amd64 to test on Intel hosts via QEMU).
    """
    if override := os.environ.get("TARGET_PLATFORM"):
        return override
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "linux/arm64"
    return "linux/amd64"


PLATFORM = host_platform()

client = httpx.Client(timeout=30.0, headers={"Authorization": f"Bearer {KEY}"})


def main() -> None:
    print(f"API_URL  = {API_URL}")
    print(f"REGISTRY = {REGISTRY}")
    print(f"repo={REPO}  image={IMAGE_NAME}  stamp={STAMP}")

    print("\n[1+2] creating repository and image...")
    client.post(
        f"{API_URL}/v2/repositories",
        json={"name": REPO},
    ).raise_for_status()
    client.post(
        f"{API_URL}/v2/repositories/{REPO}/images",
        json={"name": IMAGE_NAME},
    ).raise_for_status()

    print(f"\n[3] submitting build (Dockerfile = {DOCKERFILE.name}, platform = {PLATFORM})...")
    resp = client.post(
        f"{API_URL}/v2/repositories/{REPO}/images/{IMAGE_NAME}/builds",
        json={
            "source": {
                "type": "inline",
                "dockerfile_content": DOCKERFILE.read_text(),
            },
            # Build for the host arch so the docker pull/run in [6]/[7]
            # finds a matching manifest. Multi-arch is supported (e.g.
            # ["linux/amd64", "linux/arm64"]) but takes ~2-3x longer
            # because the cross-arch leg runs under QEMU.
            "platforms": [PLATFORM],
        },
    )
    resp.raise_for_status()
    build = resp.json()
    build_id, image_url = build["id"], build["image"]
    print(f"      build_id  = {build_id}")
    print(f"      image_url = {image_url}")

    print("\n[4] streaming build logs (server closes the stream at terminal status)...\n")
    with client.stream(
        "GET",
        f"{API_URL}/v2/builds/{build_id}/logs?follow=true",
        timeout=httpx.Timeout(connect=10.0, read=600.0, write=10.0, pool=10.0),
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data: "):
                print("    " + line[len("data: "):])

    print("\n[5] confirming terminal status...")
    final = client.get(f"{API_URL}/v2/builds/{build_id}").json()
    print(f"      status = {final['status']}")
    if final["status"] != "success":
        print(f"      failure_reason = {final.get('failure_reason')!r}")
        sys.exit(1)

    print("\n[6] docker login + pull...")
    subprocess.run(
        ["docker", "login", REGISTRY, "-u", "_token", "--password-stdin"],
        input=KEY,
        text=True,
        check=True,
    )
    subprocess.run(["docker", "pull", image_url], check=True)

    print("\n[7] running the pulled image to verify content roundtrip...")
    out = subprocess.run(
        ["docker", "run", "--rm", image_url],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    print(f"      container output: {out!r}")
    if out != GREETING:
        sys.exit(f"      MISMATCH: got {out!r}, want {GREETING!r}")
    print("\n*** SUCCESS — full pipeline works ***")

    print("\n[cleanup] deleting repository (cascades to image + build)...")
    client.delete(
        f"{API_URL}/v2/repositories/{REPO}",
        params={"purge": "true"},
    ).raise_for_status()
    subprocess.run(
        ["docker", "image", "rm", image_url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


if __name__ == "__main__":
    try:
        main()
    finally:
        client.close()
