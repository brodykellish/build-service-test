#!/usr/bin/env python3
"""End-to-end example: user isolation between two accounts.

What this exercises: two independent users cannot read, list, or pull
each other's resources. RUNPOD_API_KEY is user A; RUNPOD_API_KEY_2 is
user B. The script asserts:

  - User B's repository listing does NOT include user A's repo
  - User B GET'ing A's image returns 404 (not 403 — we don't leak existence)
  - User B GET'ing A's build returns 404
  - User B docker-logged-in cannot `docker pull` A's image URL

Usage:
    export RUNPOD_API_KEY=rpa_...    # user A
    export RUNPOD_API_KEY_2=rpa_...  # user B (different account)
    python3 tests/isolation_example.py
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
KEY_A = os.environ.get("RUNPOD_API_KEY")
KEY_B = os.environ.get("RUNPOD_API_KEY_2")
if not KEY_A:
    sys.exit("error: RUNPOD_API_KEY is not set (user A)")
if not KEY_B:
    sys.exit("error: RUNPOD_API_KEY_2 is not set (user B — must be a different account)")
if KEY_A == KEY_B:
    sys.exit("error: RUNPOD_API_KEY and RUNPOD_API_KEY_2 are the same — isolation needs two accounts")

STAMP = f"{int(time.time())}-{random.randint(0, 0xFFFF):04x}"
REPO = f"isolation-{STAMP}"
DOCKERFILE = Path(__file__).parent.parent / "testdata" / "Dockerfile.basic"


def client(key: str) -> httpx.Client:
    return httpx.Client(timeout=30.0, headers={"Authorization": f"Bearer {key}"})


def stream_logs(c: httpx.Client, build_id: str) -> None:
    with c.stream(
        "GET",
        f"{API_URL}/v2/builds/{build_id}/logs?follow=true",
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data: "):
                print("        " + line[len("data: "):])


def main() -> None:
    a = client(KEY_A)
    b = client(KEY_B)

    print(f"API_URL = {API_URL}")
    print(f"repo    = {REPO}  (owned by user A)")

    # --- User A: set up a real repo + image + build.
    print("\n[A] creating repository + image...")
    a.post(f"{API_URL}/v2/repositories", json={"name": REPO}).raise_for_status()
    a.post(
        f"{API_URL}/v2/repositories/{REPO}/images",
        json={"name": "demo"},
    ).raise_for_status()

    print("[A] submitting build...")
    resp = a.post(
        f"{API_URL}/v2/repositories/{REPO}/images/demo/builds",
        json={"source": {"type": "inline", "dockerfile_content": DOCKERFILE.read_text()}},
    )
    resp.raise_for_status()
    build = resp.json()
    print(f"      build_id  = {build['id']}")
    print(f"      image_url = {build['image']}")

    print("\n[A] streaming logs...\n")
    stream_logs(a, build["id"])

    final = a.get(f"{API_URL}/v2/builds/{build['id']}").json()
    if final["status"] != "success":
        sys.exit(f"\n[A] setup build failed: {final.get('failure_reason')!r}")
    image_url = final["image"]

    # --- User B: attempt to see, read, or pull A's resources.
    print("\n[B] listing repositories — A's repo must NOT appear...")
    b_repos = b.get(f"{API_URL}/v2/repositories").json().get("repositories", [])
    if REPO in {r["name"] for r in b_repos}:
        sys.exit(f"        LEAK: user B can see A's repo {REPO!r} in their list")
    print(f"        ok — user B sees {len(b_repos)} of their own repos; A's not present")

    print("\n[B] GET A's image by exact path — expect 404...")
    r = b.get(f"{API_URL}/v2/repositories/{REPO}/images/demo")
    print(f"        HTTP {r.status_code}")
    if r.status_code != 404:
        sys.exit(f"        LEAK: user B got HTTP {r.status_code} on A's image; expected 404")

    print("\n[B] GET A's build by id — expect 404...")
    r = b.get(f"{API_URL}/v2/builds/{build['id']}")
    print(f"        HTTP {r.status_code}")
    if r.status_code != 404:
        sys.exit(f"        LEAK: user B got HTTP {r.status_code} on A's build; expected 404")

    print("\n[B] docker login as user B + try to pull A's image — expect failure...")
    subprocess.run(
        ["docker", "login", REGISTRY, "-u", "_token", "--password-stdin"],
        input=KEY_B,
        text=True,
        check=True,
    )
    p = subprocess.run(
        ["docker", "pull", image_url],
        capture_output=True,
        text=True,
    )
    print(f"        docker pull exit={p.returncode}")
    if p.returncode == 0:
        sys.exit(f"        LEAK: user B successfully pulled A's image:\n{p.stdout}")
    print("        ok — pull rejected. tail of stderr:")
    for line in (p.stderr or p.stdout).strip().splitlines()[-3:]:
        print(f"          {line}")

    print("\n*** SUCCESS — user B cannot list, read, or pull anything in user A's namespace ***")

    # --- Cleanup (as user A; user B has nothing to clean up).
    print("\n[cleanup] user A deletes their repository; both clients log out of docker...")
    a.delete(
        f"{API_URL}/v2/repositories/{REPO}",
        params={"purge": "true"},
    ).raise_for_status()
    subprocess.run(
        ["docker", "logout", REGISTRY],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    a.close()
    b.close()


if __name__ == "__main__":
    main()
