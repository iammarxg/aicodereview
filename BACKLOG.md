# Backlog

Ideas and designs planned for future versions of aicr. This file is the durable
record: an idea documented here should stay actionable even if it's picked up
many versions later. Newest thinking goes at the top of each entry — don't
rewrite history; append updates with their own UTC timestamp.

**Per-entry metadata**
- **ID** — stable identifier (never reused).
- **Status** — `idea` (rough) · `designed` (ready to build) · `in-progress` ·
  `done` (moved to CHANGELOG) · `dropped` (with reason).
- **Suggested (UTC)** — when the idea was first raised.
- **Suggested at** — aicr version + short commit the idea was raised against.
- **Target version** — planned release, or `TBA` (to be announced) if uncertain.
- **Updated (UTC)** — last time the entry changed (optional; append, don't erase).

All times are UTC.

---

## B1 — `aicr scan`: full-repository review (not just the diff)
- **Status:** designed
- **Suggested (UTC):** 2026-07-14T20:20Z
- **Suggested at:** v0.3.0 (commit ca2c95a)
- **Target version:** TBA (leading candidate for the next feature release)

### Problem
`aicr review` only looks at the staged/unstaged/range diff. Users want a one-shot
"review my whole codebase" to find existing issues, and to gauge cost/value before
wiring the hook into their workflow. This was first raised as "scan the whole repo
during `aicr init`"; it graduated into its own command.

### Design
- New command `aicr scan [PATH]` reviewing all tracked, reviewable files (reuse
  `aicr.analyze` for discovery + exclude/binary filtering).
- Reuse the engine: synthesize a `DiffFile` per file where every line is treated
  as "added" so the provider reviews full content. Keep the existing
  `max_files_per_review` / `concurrency` / cache machinery.
- **Cost guardrails (required):** before running, print the `aicr.analyze` report
  (files / lines / chars / ~tokens) and a timed estimate, then confirm. Support
  `--yes` to skip the prompt and `--max-files` / `--max-tokens` budget caps.
- **Timed estimate — measured, not guessed:** the estimate should be driven by
  volume, not file count. Current `analyze.estimate_scan_seconds` already uses
  `tokens ≈ chars / 4` and divides by throughput × concurrency. Refinement: run
  ONE real file first to measure the user's actual tokens/sec, then extrapolate
  across the remaining tokens. (This replaces the conservative default constant
  the `init` estimator uses today.) Rationale for tokens-over-lines: model latency
  tracks tokens, and tokens ≈ chars/4, so chars/lines both feed the same estimate
  — chars are the more stable input, which is why the estimator keys off chars.
- Output: existing CLI/JSON renderers + a per-file summary and grand total. Pairs
  naturally with SARIF (B2) for CI.

### Open questions
- Large-file chunking (beyond `max_diff_lines_per_file`): split by section with
  overlap and dedupe comments, vs. hard truncate. Leaning: split + dedupe.
- Incremental re-scan: whole-repo cache keyed by file-content hash so a re-scan
  only pays for changed files (the hunk cache already covers most of this).

### Why deferred
Needs the cost-guardrail UX and the measured estimator to be trustworthy before we
point it at large repos; not worth shipping half-done.

---

## B2 — `--fix`: model-suggested code fixes
- **Status:** idea
- **Suggested (UTC):** 2026-07-14T20:20Z
- **Suggested at:** v0.3.0 (commit ca2c95a)
- **Target version:** TBA

### Problem
Reviews point out issues; users then fix them by hand. A `--fix` mode would have a
model propose concrete patches for findings.

### Design constraints (from discussion)
- **Model quality gate:** only enable for models actually good at writing/repairing
  code. Maintain an allowlist (or a capability probe) rather than letting any model
  attempt fixes.
- **Separate fixer model:** allow a different model for fixing than for reviewing
  (e.g. review on a cheap/free model, fix on a stronger one) to control cost —
  add a `fix_model` config key.
- **Excellent, dedicated prompt:** the fix prompt must be strong and separate from
  the review prompt; produce a unified diff / patch, not prose, so it can be applied
  and shown for confirmation.
- Never auto-apply silently: show the proposed patch and require confirmation
  (mirrors the warn-only philosophy).

### Why deferred
Depends on a curated model allowlist and a high-quality fix prompt; premature
without both.

---

## B3 — SARIF output format
- **Status:** idea
- **Suggested (UTC):** 2026-07-14T20:20Z
- **Suggested at:** v0.3.0 (commit ca2c95a)
- **Target version:** TBA

### Problem
CI systems and code-scanning dashboards (e.g. GitHub code scanning) ingest SARIF.
`--format sarif` would let `aicr review` / `aicr scan` post findings as first-class
PR annotations.

### Design sketch
- Add `aicr/report/sarif_renderer.py` producing SARIF 2.1.0: one `run`, tool driver
  = `aicr` + version, `results[]` mapping each `ReviewComment` (path, line,
  severity → level, category → rule id).
- Small stable rule catalog (one rule per category) so results group cleanly.
  Severity map: critical→error, warning→warning, info→note.
- Wire `--format sarif` into the CLI beside `cli`/`json`; keep stdout clean.
- Tests: schema-shape assertions + severity/level mapping.

---

## B4 — Per-file / per-path prompt overrides
- **Status:** idea
- **Suggested (UTC):** 2026-07-14T20:20Z
- **Suggested at:** v0.3.0 (commit ca2c95a)
- **Target version:** TBA

### Problem
Different parts of a repo want different review emphasis (e.g. stricter security on
`auth/**`, style-only on `scripts/**`). Today categories/languages are global.

### Design sketch
- Optional `overrides:` section in `.aicr.yaml`: a list of `{ path-glob, categories?,
  languages?, severity_block_threshold?, extra_prompt? }` rules, first-match-wins.
- Thread the resolved per-file settings through the engine (it already reviews
  file-by-file, so this is mostly config resolution + plumbing).
- Keep it out of the core `.aicr.yaml` surface for now to avoid overcomplicating the
  config — this belongs in an "advanced/overrides" area, revisited later.

### Why deferred
Don't want to bloat the main config file yet; needs a clean home and a resolution
model that's easy to reason about.

---

## B5 — More diff modes / sources
- **Status:** idea
- **Suggested (UTC):** 2026-07-14T20:20Z
- **Suggested at:** v0.3.0 (commit ca2c95a)
- **Target version:** TBA

### Candidates
- `--since <ref>`: everything changed since a branch point (sugar over range).
- `--commit <sha>`: review a specific commit, including merge commits.
- `--stdin`: read a unified diff from stdin so other tools can pipe in.
- Remote PR sources (GitHub/GitLab) behind the existing `DiffSource` interface —
  `LocalGitSource` already anticipates this; moves aicr into team review flows.

---

## Shipped from this backlog
- **Gemini provider** (was "idea #5") — shipped in v0.4.0. Gemini's generous free
  tier + advanced models made it the right first addition after OpenRouter/Ollama.
  See CHANGELOG.
