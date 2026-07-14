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
5. Add tests. Use `httpx.MockTransport` for HTTP behavior (see
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

## Reporting bugs

Open an issue with a minimal repro (a small sample diff is ideal). Never paste
private code or API keys.
