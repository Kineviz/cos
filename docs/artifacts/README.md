# Artifacts

Published pages, mirrored here so the repo holds the same thing the link shows.

The files are page bodies, not whole documents — the publisher wraps them in a
`<!doctype html><head>…</head><body>` skeleton with a CSS reset. Opening one
straight from disk therefore looks close to, but not exactly like, the
published version. The published URL is the reference copy.

| File | What it is |
|---|---|
| `cos-architecture.html` | The whole system in five diagrams — architecture, data flow, agent tasks, the self-improvement loop, and on-demand panels. Written for readers new to AI. |
| `benchmark-report.html` | The benchmark report — every run, what each change did, where the time goes, and the seventeen eval questions. Rewritten after each new run. |
| `retrieval-architecture.html` | How the assistant finds answers — four ways of finding a page, why they are fused by rank, and the five measured faults in context construction. |

People and company names in the example questions and answers are the
repository's fictional cast (Northwind, Morgan, Falcon…); the measurements
are real.

## Keeping them in step

Editing the file here does not republish it, and republishing does not commit
it. When one changes, do both — the copy in the repo is what survives the
link, and the link is what Wei actually reads.

The prose source for the benchmark report is `../OVERNIGHT-2026-08-08.md`; the
numbers behind both come from `~/.cos/bench/*.json` via `cos bench-report`.
