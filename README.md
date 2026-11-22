# ProjektBLADE

Selects which sqlglot tests to run for a unified diff against commit `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`, and splits them across up to eight workers.

The image reads `/data/tests.json` and `/data/commits/<id>.patch`. It writes one JSON object per commit on stdout:

```json
{"commit":"k7q2","workers":[[12,845,901],[3,77]]}
```

`workers: []` runs nothing. Logs go to stderr.

## Layout

- `selector.py` reads the pinned coverage and import maps and prints the schedule.
- `maps/impact.json` and `maps/coverage.json.gz` are built from that pinned checkout.
- `Dockerfile` runs the selector as `nobody`.
- `score.py` checks a local stdout file. The arithmetic is in `SCORING.md`.

## Local check

Python 3.9 or newer. From this directory:

```sh
python tools/run_score.py
```

Docker, when you want the same limits as a container run:

```sh
docker build --platform linux/amd64 --tag projektblade .
```

`sample/answers.json` is for that local check only. The selector never opens it.
