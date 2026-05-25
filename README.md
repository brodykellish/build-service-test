# RunPod Build Service — Examples

End-to-end test scripts for the RunPod container build service. Each
script exercises a different feature; if it exits 0, that flow works.

| Script                            | What it does                                                              |
| --------------------------------- | ------------------------------------------------------------------------- |
| `tests/basic_example.py`          | Submit a build, stream logs, pull and run the image.                      |
| `tests/chained_example.py`        | Build A; build B `FROM` A's image; verify B inherits A's layers.          |
| `tests/failing_example.py`        | Submit a deliberately-broken Dockerfile; verify the failure is surfaced.  |
| `tests/multistage_example.py`     | Build a multistage Dockerfile; verify only the final stage's content ships. |
| `tests/versions_example.py`       | Build the same image twice; verify both versions are tagged and listed.   |
| `tests/isolation_example.py`      | Two accounts; verify neither can see, read, or pull the other's resources. |

## Setup

Requires Python 3.10+ and Docker.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment

You need API keys for **two separate accounts**. Set both before running anything:

```bash
export RUNPOD_API_KEY=rpa_...      # account #1 — used by every script
export RUNPOD_API_KEY_2=rpa_...    # account #2 — used only by isolation_example.py
```

`RUNPOD_API_KEY_2` MUST resolve to a different account than `RUNPOD_API_KEY`
— `isolation_example.py` exits with an error if the two keys are equal.
The other five scripts ignore `RUNPOD_API_KEY_2`.

| Variable                | Default                              | Used by                              |
| ----------------------- | ------------------------------------ | ------------------------------------ |
| `RUNPOD_API_KEY`        | required                             | all six scripts                      |
| `RUNPOD_API_KEY_2`      | required for isolation               | `isolation_example.py` only          |
| `BUILD_SERVICE_API_URL` | `https://v2-rest-brody.runpod.dev`   | all six scripts                      |
| `REGISTRY_HOST`         | `registry-brody.runpod.dev`          | all six scripts                      |

## Run

```bash
python3 tests/basic_example.py
python3 tests/chained_example.py
python3 tests/failing_example.py
python3 tests/multistage_example.py
python3 tests/versions_example.py
python3 tests/isolation_example.py
```

Each script prints `*** SUCCESS … ***` and exits 0 when its flow works.

## Layout

```
tests/        runnable example scripts
testdata/     Dockerfiles the scripts submit to the build service
```

The `testdata/Dockerfile.*` files are sent verbatim. Edit freely — but
if you change a marker string a script greps for, update the matching
constant at the top of that script. `testdata/Dockerfile.child` uses a
`__BASE_IMAGE__` placeholder that `chained_example.py` substitutes at
submission time.

## Cleanup

Each script deletes the repository it created and removes the local
image on success. To inspect artifacts after a run, comment out the
`[cleanup]` block at the bottom of `main()`.
