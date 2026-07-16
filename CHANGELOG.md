# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **SARIF output (`--format sarif`).** `aicr review` and `aicr scan` can now emit
  SARIF 2.1.0, the format GitHub code scanning and other CI dashboards ingest to
  show findings as first-class PR annotations. The mapping is small and stable:
  one rule per review category (so results group cleanly), each comment becomes a
  result with its file/line and a severity→level mapping (critical→error,
  warning→warning, info→note). Emitted via a new `report/sarif_renderer.py`, a
  pure consumer of `ReviewResult` — nothing upstream changes (roadmap B3).
- **Large files are now chunked instead of skipped (`chunk_large_files`, on by
  default).** Previously a file with more added lines than
  `max_diff_lines_per_file` was skipped entirely, so the biggest — often most
  important — files got no review. aicr now splits such a file into overlapping
  windows that each carry their real new-file line numbers, reviews each, and
  dedupes findings that land in the overlap so a split file reads like one review.
  It still counts as a single file in the summary. Set `chunk_large_files: false`
  to restore the old skip-if-too-large behavior (roadmap B1).

## [0.4.2] — 2026-07-15


### Changed

- **Reviewer is far less likely to hallucinate.** The system prompt was rewritten
  around a strict *grounding* contract: the model may only report issues it can
  prove from the code visible in the diff, and must **not** make claims about
  things it can't see — whether a library/SDK function exists or behaves a certain
  way, whether a model/API name or version is valid, or what unseen inputs contain.
  A new always-included grounding block spells this out, and each category template
  now demands diff-grounded evidence. This targets false positives like "that model
  name is wrong" (it wasn't) or "this SDK call can raise" (unprovable from the diff).
- The prompt now asks the model to **only report findings it's ≥90% confident are
  real** and to stay silent otherwise — a missed nit is cheaper than a false alarm.

### Added
- **Confidence backstop.** `ReviewComment` gained a `confidence` field (0–1) that
  the model self-rates per finding; `parse_comments` drops anything below the
  threshold, so overconfident noise is filtered out even if a model ignores the
  prompt. Confidence is internal (used only for filtering) and not shown in output.
  Comments without the field default to kept, so simpler providers aren't penalized.

### Fixed
- **`aicr init` and `aicr scan` reported different file counts.** `init`'s analysis
  ignored the `.aicr.yaml` `exclude_paths`, so it counted more files than `scan`
  would review (e.g. 12 vs 8). `init` now honors the effective excludes and shows a
  "N tracked · M excluded by your config" breakdown that agrees with the scan.
- **The pre-scan token number didn't match the files being scanned.** `aicr scan`
  showed the whole-repo token estimate even after excludes/`--max-files` capping;
  it's now computed from the exact files that will be sent.
- **Token estimates were ~40% low.** Both the `init` analysis and the `scan`
  confirmation counted only file content (~chars/4), ignoring the per-file prompt
  overhead (system + grounding + category templates) and model output that every
  call incurs. Estimates now include that overhead — e.g. a repo that read ~12k
  tokens now estimates ~19k, matching real usage of ~20k.

## [0.4.1] — 2026-07-15


### Added
- **`aicr scan`** — review the whole repository's existing code, not just a diff.
  It synthesizes a full-content "diff" per tracked, reviewable file and runs the
  same engine/provider/renderer pipeline, so it finds issues in code you didn't
  just touch. Guarded by a **measured** cost/time estimate + confirmation prompt
  (skip with `--yes`), with `--max-files` to cap spend and `--format json` for CI.
  Reuses `analyze.py` for discovery so it reviews exactly what the analysis reports.

### Fixed
- **`aicr init` repo analysis gave wrong excludes and a nonsensical time
  estimate.** Three fixes:
  - The scan-time estimate now models the **one-API-call-per-file** cost (fixed
    per-call overhead + tokens), instead of treating the repo as a single token
    stream — so a many-small-files repo no longer estimates as "~0.0 min".
  - `aicr scan` now shows a **measured** estimate: it times one representative-sized
    file review and extrapolates (the sample is cached and reused by the full run).
    `aicr init` no longer prints a guessed time — it points you to `aicr scan` for a
    real one, since `init` makes no API calls.
  - Exclude recommendations are now **repo-aware**: they reflect the non-source
    file types actually present (docs/config/assets, heavy dirs, lockfiles) instead
    of a static `*.min.js`/`*.min.css` guess. Docs/config text (`.md`, `.yaml`, …)
    is no longer counted as reviewable code, so the token estimate isn't inflated.


## [0.4.0] — 2026-07-15


### Added

- **Google Gemini provider** — `provider: gemini` reviews via Gemini's
  OpenAI-compatible endpoint (default model `gemini-2.0-flash`), reading its key
  from `GEMINI_API_KEY`. Great free tier for local-first users who still want a
  capable cloud model. `aicr init` now offers OpenRouter / Gemini / Ollama, each
  with sensible default models and a key sign-up URL.
- **Per-provider API-key env vars** — each cloud provider now reads its own key
  variable (`OPENROUTER_API_KEY`, `GEMINI_API_KEY`); `aicr config` and the
  missing-key error name the right one for the configured provider.
- **`aicr reset`** — one command to remove everything aicr added to a repo: the
  pre-commit hook, `.aicr.yaml`, and the `.aicr/` cache. Confirm-gated (with
  `--yes` to skip), and it offers to strip the API-key line from `.env` while
  leaving any other variables intact.
- **Repo analysis in `aicr init`** — an optional, fast, no-API-call scan that
  reports file / line / character counts and estimated tokens, detects languages
  and heavy directories, and recommends excludes and limits tuned to the repo's
  size. It also prints a rough full-repo scan-time estimate and can apply the
  recommendations straight into `.aicr.yaml`.

### Added (docs)
- **BACKLOG.md** — durable, timestamped record of planned work: the full
  `aicr scan` design, a `--fix` mode, SARIF output, per-path prompt overrides, and
  more diff sources.

## [0.3.0] — 2026-07-14


### Added
- **Live review progress** — `aicr review` now prints a real-time
  `Reviewing (n/total) <file> …` status to stderr as each file actually starts
  going to the LLM. It clears itself before the report, so results still appear
  all at once and `--format json` stays a clean pipe.
- **`aicr init` writes every option** — the generated `.aicr.yaml` now lists the
  full configuration surface; options you didn't set are written commented-out at
  their defaults. A new optional "advanced options" step in the wizard (same
  prompt style) lets you set languages, excludes, limits, concurrency, display
  threshold, and cache without hand-editing YAML.
- **`scripts/release.py`** — one-command release: bump (major/minor/patch or an
  exact `--version`), roll the CHANGELOG, commit, create an annotated `vX.Y.Z`
  tag, and push branch + tag. Policy: features → minor, fixes → patch.

## [0.2.0]


### Added
- **`aicr init`** — an interactive, rclone-style setup wizard: pick a provider,
  enter/store your key in `.env`, choose categories and blocking, and optionally
  install the hook, all from numbered prompts.

- **Ollama provider** — fully local, private review via a model on `localhost`
  (no API key, no data leaves your machine). A second `LLMProvider`
  implementation proving the adapter pattern; select it with `provider: ollama`.
- **Token & cost usage display** — providers now report `TokenUsage`, aggregated
  per run and shown as a token line plus a mini **"% API usage" progress bar**
  (from OpenRouter's `/key` credit endpoint). Usage is a provider-agnostic
  interface (`_record_usage` + `account_usage`), so any future provider that
  supports it gets the bar for free.
- **Diff-range & unstaged review** — `aicr review --unstaged` reviews
  working-tree changes and `--range main..HEAD` reviews a commit range (e.g. a
  branch before pushing), not just the staged index.
- **Blocking mode (opt-in)** — `--strict` (or `severity_block_threshold` in
  config) makes `review` exit non-zero on findings at/above a severity, turning
  aicr into a real gate. Default stays warn-only (exit 0).
- **Hunk-level cache** — unchanged files are served from `.aicr/cache/` instead
  of re-calling the provider across amended/re-staged commits; disable per run
  with `--no-cache` or via `cache_enabled: false`.

### Fixed
- `review --staged` was a no-op flag that could never be disabled; diff-source
  selection is now explicit (staged / unstaged / range).
- `severity_display_threshold` (and the new `severity_block_threshold`) are now
  validated, so a typo raises a friendly error instead of silently misbehaving.

## [0.1.0] — Initial release


### Added
- Distributed as `aicr-review` on PyPI (the `ai-code-review` name belongs to an
  unrelated project); installs the `aicr` command. Until published, install from
  GitHub: `pip install "git+https://github.com/iammarxg/aicodereview.git"`.
- Local git integration via a pre-commit hook, plus a `.pre-commit-hooks.yaml`
  manifest for the pre-commit framework.
- `aicr enable` / `aicr disable` to install/remove the hook (`install-hook` /
  `uninstall-hook` kept as hidden backward-compatible aliases).
- `aicr review` — reviews the staged diff (`git diff --cached`) and prints
  inline, line-mapped comments; **warn-only**, always exits 0.
- `aicr review --include <patterns>` — force-include file(s) that `.aicr.yaml`
  excludes, for a single run. `exclude_paths` stays the single source of truth;
  force-include just overrides it ad hoc. Accepts space- or comma-separated
  patterns.
- Diff parsing via `unidiff` with correct new-file line-number mapping; filters
  binary, excluded, and oversized files.
- `LLMProvider` adapter interface with an `OpenRouterProvider` implementation and
  a name→class registry. Shared, provider-agnostic response parsing with JSON
  extraction, per-item validation, retry-on-malformed, and a line-mapping safety
  net that drops comments not tied to a changed line.
- `DiffSource` adapter interface with `LocalGitSource` (v1).
- Selectable review categories (bugs, security, readability, style) and
  per-language prompt tuning with auto-detection by file extension.
- Concurrent per-file review (`asyncio` + semaphore) with per-file error
  isolation — one bad response never aborts the run or blocks the commit.
- Config via `.aicr.yaml` + `OPENROUTER_API_KEY` from env/`.env`; friendly
  fail-fast error when the key is missing. API key never read from YAML. Default
  model is `openrouter/free` (a virtual router that load-balances across
  OpenRouter's free models), so a fresh key works with far fewer 429s.
- CLI (`click`): `review`, `enable`, `disable`, `config`, with a rich
  quick-reference `--help` epilog.
- Colored, grouped-by-file terminal renderer and a JSON renderer for future
  CI/editor consumers.
- Test suite with a `FakeProvider` — no network calls required.
  `ruff`, `mypy --strict`, and `pytest` all green.

[0.2.0]: https://github.com/iammarxg/aicodereview/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/iammarxg/aicodereview/releases/tag/v0.1.0
[0.3.0]: https://github.com/iammarxg/aicodereview/releases/tag/v0.3.0
[0.4.0]: https://github.com/iammarxg/aicodereview/releases/tag/v0.4.0
[0.4.1]: https://github.com/iammarxg/aicodereview/releases/tag/v0.4.1
[Unreleased]: https://github.com/iammarxg/aicodereview/compare/v0.4.2...HEAD
[0.4.2]: https://github.com/iammarxg/aicodereview/releases/tag/v0.4.2
