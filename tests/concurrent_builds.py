#!/usr/bin/env python3
"""Concurrency smoke: submit N builds in quick succession and verify
all reach a terminal status. Validates the scheduler / executor pool
can drain a burst even with a single scheduler ECS task.

What this exercises specifically:
  - POST /v2/builds N times in parallel (build-api concurrent submit)
  - SQS publish under burst (build-api → SQS)
  - Scheduler drain: single ECS task pulling 10 messages per
    ReceiveMessage and dispatching them sequentially via StartBuild
  - CodeBuild concurrent build execution (default account-wide limit
    is 60 in-flight builds per region — bursts under that should run
    fully in parallel rather than queueing)
  - Status writeback path: each build's buildspec PATCHes terminal
    status independently; rows must all transition out of `running`

Usage:
    python3 tests/concurrent_builds.py

Env overrides:
    N_BUILDS              default 5
    BUILD_TIMEOUT_SECS    default 600 (10 min total — covers cold-start)
    POLL_INTERVAL_SECS    default 5
    TARGET_PLATFORM       default auto-detected from host arch
"""

from __future__ import annotations

import concurrent.futures
import os
import platform
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_URL = os.environ.get("BUILD_SERVICE_API_URL", "https://v2-rest-brody.runpod.dev")
KEY = os.environ.get("RUNPOD_API_KEY")
if not KEY:
    sys.exit("error: RUNPOD_API_KEY is not set")

N_BUILDS = int(os.environ.get("N_BUILDS", "5"))
BUILD_TIMEOUT_SECS = int(os.environ.get("BUILD_TIMEOUT_SECS", "600"))
POLL_INTERVAL_SECS = float(os.environ.get("POLL_INTERVAL_SECS", "5"))

DOCKERFILE = Path(__file__).parent.parent / "testdata" / "Dockerfile.basic"
STAMP = f"{int(time.time())}-{random.randint(0, 0xFFFF):04x}"
TERMINAL_STATUSES = {"success", "failed", "cancelled"}


def host_platform() -> str:
    if override := os.environ.get("TARGET_PLATFORM"):
        return override
    machine = platform.machine().lower()
    return "linux/arm64" if machine in ("arm64", "aarch64") else "linux/amd64"


PLATFORM = host_platform()


# ---------------------------------------------------------------------------
# Per-build state
# ---------------------------------------------------------------------------


@dataclass
class Build:
    """Per-build state tracked across the test lifecycle."""

    idx: int
    repo: str
    image: str = "demo"
    build_id: str | None = None
    submitted_at: float | None = None
    terminal_at: float | None = None
    last_status: str = "pending"
    # ordered list of (timestamp, status) so we can report the per-build
    # transition timeline at the end. Useful for spotting scheduler
    # dispatch latency vs. build execution time.
    transitions: list[tuple[float, str]] = field(default_factory=list)

    def note_status(self, status: str) -> bool:
        """Record a status change. Returns True if changed."""
        if status == self.last_status:
            return False
        self.transitions.append((time.time(), status))
        self.last_status = status
        if status in TERMINAL_STATUSES:
            self.terminal_at = time.time()
        return True


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------


def submit_one(build: Build, client: httpx.Client) -> Build:
    """Create repo + image + submit build. Records the time the build_id
    came back so the per-build timeline starts from when our request
    completed (not when the build was dispatched)."""
    client.post(
        f"{API_URL}/v2/repositories", json={"name": build.repo}
    ).raise_for_status()
    client.post(
        f"{API_URL}/v2/repositories/{build.repo}/images",
        json={"name": build.image},
    ).raise_for_status()
    resp = client.post(
        f"{API_URL}/v2/repositories/{build.repo}/images/{build.image}/builds",
        json={
            "source": {
                "type": "inline",
                "dockerfile_content": DOCKERFILE.read_text(),
            },
            "platforms": [PLATFORM],
        },
    )
    resp.raise_for_status()
    body = resp.json()
    build.build_id = body["id"]
    build.submitted_at = time.time()
    build.note_status(body["status"])
    return build


def poll_one(build: Build, client: httpx.Client) -> str | None:
    """Fetch the build's current status. Returns None on 404 (so the
    caller can treat a deleted build as terminal-cancelled-ish)."""
    resp = client.get(f"{API_URL}/v2/builds/{build.build_id}")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()["status"]


def cleanup(build: Build, client: httpx.Client) -> None:
    """Best-effort repo + cascade delete. A cleanup failure doesn't fail
    the test — the resources expire from the bucket lifecycle eventually."""
    try:
        client.delete(
            f"{API_URL}/v2/repositories/{build.repo}",
            params={"purge": "true"},
        ).raise_for_status()
    except Exception as e:
        print(f"  cleanup warning [{build.idx}]: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"API_URL  = {API_URL}")
    print(f"N_BUILDS = {N_BUILDS}")
    print(f"PLATFORM = {PLATFORM}")
    print(f"TIMEOUT  = {BUILD_TIMEOUT_SECS}s")
    print()

    builds = [Build(idx=i, repo=f"concurrent-{STAMP}-{i}") for i in range(N_BUILDS)]
    client = httpx.Client(
        headers={"Authorization": f"Bearer {KEY}"},
        timeout=httpx.Timeout(30.0),
    )

    # --- Phase 1: concurrent submit -----------------------------------------
    submit_start = time.time()
    print(f"[1] submitting {N_BUILDS} builds concurrently...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=N_BUILDS) as pool:
        # list() forces propagation of any exception raised in submit_one
        list(pool.map(lambda b: submit_one(b, client), builds))
    submit_elapsed = time.time() - submit_start
    print(f"      all {N_BUILDS} accepted in {submit_elapsed:.1f}s\n")
    for b in builds:
        print(f"      [{b.idx}] build_id={b.build_id}  initial_status={b.last_status}")

    # --- Phase 2: poll until all terminal or timeout ------------------------
    print(f"\n[2] polling every {POLL_INTERVAL_SECS:g}s until all terminal or timeout...\n")
    deadline = time.time() + BUILD_TIMEOUT_SECS
    phase_start = time.time()
    poll_n = 0
    while time.time() < deadline:
        in_flight = [b for b in builds if b.last_status not in TERMINAL_STATUSES]
        if not in_flight:
            break
        # Poll every in-flight build; record + print transitions inline.
        for b in in_flight:
            cur = poll_one(b, client)
            if cur is None:
                cur = "cancelled"  # deleted out from under us
            if b.note_status(cur):
                elapsed = time.time() - b.submitted_at
                print(f"      [{b.idx}] → {cur:10s}  (+{elapsed:.1f}s)")
        # Heartbeat: one summary line per poll so the test always shows
        # liveness, even when nothing transitioned this cycle. Shows
        # bucket counts so you can spot uneven progress (e.g. one build
        # stuck while the rest finish).
        poll_n += 1
        buckets: dict[str, int] = {}
        for b in builds:
            buckets[b.last_status] = buckets.get(b.last_status, 0) + 1
        summary = " ".join(f"{k}={v}" for k, v in sorted(buckets.items()))
        elapsed_phase = time.time() - phase_start
        print(f"      [poll #{poll_n:>2} +{elapsed_phase:5.1f}s]  {summary}")
        time.sleep(POLL_INTERVAL_SECS)

    # --- Phase 3: report ---------------------------------------------------
    print("\n[3] final state:")
    failed = []
    for b in builds:
        if b.terminal_at and b.submitted_at:
            elapsed = f"{b.terminal_at - b.submitted_at:6.1f}s"
        else:
            elapsed = "  TIMEOUT"
        print(f"      [{b.idx}] {b.last_status:10s} {elapsed}  build_id={b.build_id}")
        if b.last_status != "success":
            failed.append(b)

    # --- Phase 4: cleanup (best-effort) -------------------------------------
    print(f"\n[4] cleanup: deleting {N_BUILDS} repositories...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=N_BUILDS) as pool:
        list(pool.map(lambda b: cleanup(b, client), builds))
    client.close()

    # --- Verdict ------------------------------------------------------------
    if failed:
        sys.exit(
            f"\nFAIL: {len(failed)}/{N_BUILDS} builds did not reach 'success' "
            f"within {BUILD_TIMEOUT_SECS}s"
        )
    print(f"\n*** SUCCESS — {N_BUILDS}/{N_BUILDS} builds reached terminal=success ***")


if __name__ == "__main__":
    main()
