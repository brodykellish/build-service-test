# RunPod Build Service — End-to-End Examples

Two self-contained Python scripts that exercise the RunPod build
service end-to-end. If either exits 0, that flow works.

| Script                | What it exercises                                                  |
| --------------------- | ------------------------------------------------------------------ |
| `manual_e2e.py`       | The basic flow: submit a build, stream logs, pull + run the image. |
| `chained_example.py`  | Build A → build B where B's `FROM` is A's pushed image.            |

What `manual_e2e.py` does, in order:

1. Create a repository
2. Create an image inside that repository
3. Submit a build whose source is the local `Dockerfile`
4. Stream the build's logs as they happen
5. Wait for terminal status (`success` / `failed` / `cancelled`)
6. `docker pull` the resulting image
7. Run the image locally and confirm its output matches the expected marker

What `chained_example.py` adds on top:

1. Build A from `Dockerfile.base` (a simple alpine layer)
2. Build B from `Dockerfile.child`, where its `FROM` line is replaced with
   A's just-pushed image URL — this triggers a registry pull from inside
   the builder, which goes through registry-auth on the pull side
3. `docker pull` + run B, verify the output shows both A's and B's markers
   (proves the layers came through correctly)

End-to-end, both scripts hit every public surface of the build service
plus both directions of the registry auth flow (push as the build, pull
as your laptop). `chained_example.py` additionally exercises the
builder-side *pull* path (registry-auth grants a per-build pull token
when one build's image is used as another build's base).

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
python3 manual_e2e.py             # ~30–60 seconds
python3 chained_example.py        # ~60–90 seconds (two builds back-to-back)
```

You should see build logs scroll by in real time and a
`*** SUCCESS … ***` line at the end of each.

## Configuration

The script reads two optional environment variables:

| Variable                | Default                              | Purpose               |
| ----------------------- | ------------------------------------ | --------------------- |
| `BUILD_SERVICE_API_URL` | `https://v2-rest-brody.runpod.dev`   | rphttp2 base URL      |
| `REGISTRY_HOST`         | `registry-brody.runpod.dev`          | OCI registry hostname |

The defaults point at Brody's named development stage. If you've been
given a different stage, override both — they need to match.

## Customizing the Dockerfiles

`./Dockerfile` (used by `manual_e2e.py`) is sent verbatim to the build
service. Edit it freely; if you change the `HELLO_FROM_RUNPOD_BUILD_SERVICE`
marker, also change the `GREETING` constant near the top of `manual_e2e.py`
so the content-verification step still passes.

`./Dockerfile.base` and `./Dockerfile.child` (used by `chained_example.py`)
are sent the same way, with one twist: `Dockerfile.child` contains the
placeholder string `__BASE_IMAGE__`, which the script replaces with the
pushed URL of build A's image before sending to the build service. If you
edit `Dockerfile.child`, keep the `FROM __BASE_IMAGE__` line intact (or
update the substitution in `chained_example.py`).

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
