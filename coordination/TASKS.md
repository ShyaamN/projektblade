# ProjektBLADE — TASKS

## Done

- [x] Fetch official kit + SCORING.md; unpack sample (30 commits)
- [x] Baseline local score: **1210.470 s**, 0 misses
- [x] Clone sqlglot @ `eb11aec`
- [x] `tools/build_maps.py` + `maps/impact.json` (schema 2, ID-keyed, provenance)
- [x] Selector with per-commit optimal k∈[1,8] scheduling
- [x] Apply REVIEW correctness fixes (comment skip removed; unknown→ALL; fixture ALL; ID validation)
- [x] Sample score Impact v2: **532.500 s**, 0 misses (local simulated)
- [x] Coordination STATUS / ARCHITECTURE / TASKS; REVIEW owned by ChatGPT

## In progress

- [ ] Docker Desktop daemon usable; `make sample` / CPU measurement (3 runs)
- [ ] LICENSE (MIT) + submission README disclosures

## Next (priority order from REVIEW)

1. **P0 close-out:** Docker-validated score + CPU in STATUS.md
2. **P1:** Function/fixture coverage collector on pinned base; size/time gate (<20 MB repo, build ≤10 min if rebuilt in image)
3. Compare static-only vs coverage hybrid on sample + held-out change families; adopt narrowing only if 0 new misses and better total
4. Cost drivers: serde all-dialects envelope; base `dialect.py` all-dialects — propose safe shrink experiments
5. Public GitHub repo + first practice submission

## Explicitly not doing

- Loading `answers.json` in the image
- Comment-only → empty selection
- Copying competitor submissions
