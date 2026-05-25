# RunPod Build Service — End-to-End Example

A self-contained Python script that exercises the full container-build
pipeline against the RunPod build service. If `python3 manual_e2e.py`
exits 0, the pipeline works.

What the script does, in order:

1. Create a repository
2. Create an image inside that repository
3. Submit a build whose source is the local `Dockerfile`
4. Stream the build's logs as they happen
5. Wait for terminal status (`success` / `failed` / `cancelled`)
6. `docker pull` the resulting image
7. Run the image locally and confirm its output matches the expected marker

End-to-end, this hits every public surface of the build service plus
both directions of the registry auth flow (push as the build, pull as
your laptop).

## Requirements

- Python 3.10 or newer
- Docker (the daemon running, and `docker` on your `PATH`)
- A RunPod API key, provided separately

## Setup

```bash
git clone <this-repo>
cd build-service-example
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
export RUNPOD_API_KEY=rpa_...     # the key Brody sent you
python3 manual_e2e.py
```

Expected runtime: ~30–60 seconds. You should see the build logs scroll
by in real time, then a `*** SUCCESS — full pipeline works ***` line.

## Configuration

The script reads two optional environment variables:

| Variable                | Default                              | Purpose               |
| ----------------------- | ------------------------------------ | --------------------- |
| `BUILD_SERVICE_API_URL` | `https://v2-rest-brody.runpod.dev`   | rphttp2 base URL      |
| `REGISTRY_HOST`         | `registry-brody.runpod.dev`          | OCI registry hostname |

The defaults point at Brody's named development stage. If you've been
given a different stage, override both — they need to match.

## Customizing the Dockerfile

`./Dockerfile` is sent verbatim to the build service. Edit it freely.
If you change the `HELLO_FROM_RUNPOD_BUILD_SERVICE` marker, also change
the `GREETING` constant near the top of `manual_e2e.py`, otherwise the
content-verification step in (7) will fail.

## Cleanup

By default the script deletes the repository it created (which cascades
to the image and build) and removes the local image. If you'd like to
keep the artifacts for inspection, comment out the `[cleanup]` block at
the bottom of `main()` in `manual_e2e.py`.

## Troubleshooting

**`error: RUNPOD_API_KEY is not set`**
Export it in the shell where you'll run the script.

**`docker login` fails with `unauthorized`**
The key is for a different stage than `REGISTRY_HOST`. Make sure your
key matches the registry hostname.

**`final status=failed` with a `failure_reason`**
Usually a Dockerfile syntax error or a registry-permission issue. The
streamed logs above (step 4) show the buildkit output that produced it.

**HTTP 504 on the log stream**
The gateway in front of `rphttp2` timed the SSE connection out because
no bytes flowed for ~60 seconds. The build was probably queued without
producing log output. Re-run; if it keeps happening, the build service
itself may be stalled — let Brody know.

**`MISMATCH: got '...', want 'HELLO_FROM_RUNPOD_BUILD_SERVICE'`**
You edited the Dockerfile's greeting but didn't update `GREETING` in
`manual_e2e.py`. Change them together.
