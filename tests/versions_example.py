#!/usr/bin/env python3
"""End-to-end example: two versions of one image.

What this exercises: image versioning. Building the same image twice
should produce two distinct registry-tagged versions (v1 and v2),
both listed under the image resource's `versions[]`, with
`next_version` incremented accordingly.

Usage:
    export RUNPOD_API_KEY=rpa_...
    python3 versions_example.py
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
REPO = f"versions-{STAMP}"
DOCKERFILE = Path(__file__).parent.parent / "testdata" / "Dockerfile.versions"


def submit_and_wait(c: httpx.Client, label: str, dockerfile: str) -> dict:
    print(f"\n[{label}] submitting build...")
    resp = c.post(
        f"{API_URL}/v2/repositories/{REPO}/images/app/builds",
        json={"source": {"type": "inline", "dockerfile_content": dockerfile}},
    )
    resp.raise_for_status()
    build = resp.json()
    print(f"        build_id = {build['id']}")
    print(f"        version  = {build.get('version')}")
    print(f"        image    = {build['image']}")

    print(f"\n[{label}] streaming logs...\n")
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
        sys.exit(f"        {label} failed: {final.get('failure_reason')!r}")
    return final


def main() -> None:
    c = httpx.Client(timeout=30.0, headers={"Authorization": f"Bearer {KEY}"})

    print(f"API_URL = {API_URL}")
    print(f"repo    = {REPO}")

    c.post(f"{API_URL}/v2/repositories", json={"name": REPO}).raise_for_status()
    c.post(
        f"{API_URL}/v2/repositories/{REPO}/images",
        json={"name": "app"},
    ).raise_for_status()

    dockerfile = DOCKERFILE.read_text()
    v1 = submit_and_wait(c, "v1", dockerfile)
    v2 = submit_and_wait(c, "v2", dockerfile)

    print("\n[check] confirming the image resource lists both versions...")
    img = c.get(f"{API_URL}/v2/repositories/{REPO}/images/app").json()
    print(f"        next_version = {img['next_version']}")
    print(f"        versions     = {img['versions']}")

    if v1["image"] not in img["versions"]:
        sys.exit(f"        v1 URL {v1['image']!r} missing from versions[]")
    if v2["image"] not in img["versions"]:
        sys.exit(f"        v2 URL {v2['image']!r} missing from versions[]")
    if img["next_version"] < 3:
        sys.exit(f"        next_version should be >=3 after two builds, got {img['next_version']}")

    print("\n[verify] pulling both versions and running each...")
    subprocess.run(
        ["docker", "login", REGISTRY, "-u", "_token", "--password-stdin"],
        input=KEY,
        text=True,
        check=True,
    )
    for label, b in (("v1", v1), ("v2", v2)):
        subprocess.run(["docker", "pull", b["image"]], check=True)
        out = subprocess.run(
            ["docker", "run", "--rm", b["image"]],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        print(f"        {label}: {out!r}")

    print("\n*** SUCCESS — both versions exist, are listed in versions[], and pull-runnable ***")

    print("\n[cleanup] deleting repository + local images...")
    c.delete(
        f"{API_URL}/v2/repositories/{REPO}",
        params={"purge": "true"},
    ).raise_for_status()
    for b in (v1, v2):
        subprocess.run(
            ["docker", "image", "rm", b["image"]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    c.close()


if __name__ == "__main__":
    main()
