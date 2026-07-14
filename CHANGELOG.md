# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/iammarxg/aicodereview/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/iammarxg/aicodereview/releases/tag/v0.1.0
