# Contributing

Thanks for your interest in improving aicr! This is a small, type-safe,
adapter-driven codebase — the conventions below keep it that way.

## Setup

```bash
git clone https://github.com/iammarxg/aicodereview.git
cd aicodereview
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Before you push — the three gates

All three must pass; CI will enforce them:

```bash
ruff check src tests      # lint + import order
mypy src                  # strict type checking
pytest -q                 # tests (no network calls; a FakeProvider is used)
```

`ruff check --fix` auto-fixes most lint issues.

## Code style

- **Type hints on every public function/class** — this matters more than usual
  here, because the whole value of the adapter pattern is other people (and
  future-you) extending it correctly. `mypy --strict` is on.
- Docstrings on public functions/classes.
- Line length 100 (`ruff`).
- Keep the pipeline stages decoupled: `diff/`, `prompts/`, `providers/`, and
  `report/` communicate **only** through the `DiffFile` and `ReviewComment`
  models. Don't leak OpenRouter-specifics into `prompts/` or `report/`, and
  don't let the renderer assume where a diff came from.

## Adding a new `LLMProvider`

1. Create `src/aicr/providers/yourprovider.py` with a class subclassing
   `LLMProvider` and implementing `async def review(...)`. Call `super().__init__()`
   so token accounting is set up. See `openrouter.py` / `ollama.py` as models.
2. Reuse `parse_comments()` from `providers/base.py` for response validation and
   line-mapping — don't reinvent it.
3. Report usage where you can: call `_record_usage(...)` per API call, and
   override `account_usage()` if the provider exposes spend/limit — the renderer
   then shows the "% API usage" bar for your provider automatically.
4. Register it in `providers/registry.py` (name → class), threading `base_url`
   from config if relevant.
5. If it's a cloud provider needing a key, add its env var to
   `config.PROVIDER_API_KEY_ENV` (name → env var). `config.py` then reads and
   reports the right variable, and the missing-key error names it. Local providers
   (no key) skip this.
6. Add tests. Use `httpx.MockTransport` for HTTP behavior (see
   `tests/test_providers_http.py`) — never require real network access in CI.
   Any real-API tests must be **opt-in**, skipped unless the relevant key is set.



## Adding a new `DiffSource`

1. Subclass `DiffSource` and implement `get_diff_files() -> list[DiffFile]`.
2. Return the same `DiffFile` model the parser produces — everything downstream
   works unchanged.

## Commits & PRs

- Commit incrementally per component; write a clear message.
- Include tests for new behavior and keep the three gates green.
- Update `CHANGELOG.md` under "Unreleased".
- Planning something bigger? Record the design in `BACKLOG.md` (each entry has a
  stable ID, status, and UTC timestamps) so it stays actionable across versions.


## Releasing

Use the release helper — it keeps the version, tag, and CHANGELOG in sync:

```bash
python scripts/release.py minor    # new features   → v0.X.0
python scripts/release.py patch    # bug/small fixes → v0.0.X
python scripts/release.py major    # breaking        → vX.0.0
# or an exact version:  python scripts/release.py --version 0.4.2
```

It bumps `src/aicr/__init__.py` and `pyproject.toml`, rolls the CHANGELOG
"Unreleased" section into the new version, commits, creates an annotated
`vX.Y.Z` tag, and pushes the branch + tag. It refuses to run on a dirty tree.


## Reporting bugs

Open an issue with a minimal repro (a small sample diff is ideal). Never paste
private code or API keys.
