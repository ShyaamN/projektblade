# ProjektBLADE

Selects which sqlglot tests to run for a unified diff against commit `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, and splits them across up to eight workers.

The image reads `/data/tests.json` and `/data/commits/<id>.patch`. It writes one JSON object per commit on stdout:

```json
{"commit":"k7q2","workers":[[12,845,901],[3,77]]}
```

`workers: []` runs nothing. Logs go to stderr.

## Layout

- `selector.py` reads the pinned coverage and import maps and prints the schedule.
- The Docker image uses `maps/impact.json`, `maps/coverage.json.gz`, and `maps/pinpoint.marshal.gz` from the pinned checkout.
- `Dockerfile` runs the selector as `nobody`.
- `score.py` checks a local stdout file. The arithmetic is in `SCORING.md`.

## Submission

Use the public repository URL `https://github.com/ShyaamN/projektblade` in the repository field and the full 40-character Git SHA in the Commit field. Push the revision to GitHub first. A blank Commit field selects the latest commit on the repository's default branch. The scorer builds the Dockerfile at the repository root and supplies its own `/data` at runtime.

## Local check

With Python 3.13 or newer, run from this directory:

```sh
python tools/run_score.py
```

With Docker, build from the repository root and benchmark under the contest limits:

```sh
docker build --platform linux/amd64 --tag projektblade .
python tools/bench_docker.py projektblade
```

`sample/answers.json` is for that local check only. The selector never opens it.
