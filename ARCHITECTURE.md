# Architecture

The single most important design decision in aicr: **the pipeline stages are
fully decoupled and communicate only through two pydantic models** (`DiffFile`
and `ReviewComment`). Nothing downstream knows or cares *where* a diff came from
(local git vs. a future GitHub PR) or *which* LLM produced the review (OpenRouter
vs. a future OpenAI/Anthropic/Ollama adapter).

## Data flow

```
git commit
   │  .git/hooks/pre-commit  →  aicr review --staged
   ▼
config.py       load .aicr.yaml + OPENROUTER_API_KEY (env / .env)
   ▼
diff/source.py  LocalGitSource → `git diff --cached` → raw unified diff
   ▼
diff/parser.py  unidiff → list[DiffFile]  (paths, hunks, new-file line numbers)
                filters binary / excluded / oversized files
   ▼
engine.py       for each DiffFile, concurrently (asyncio.gather + semaphore):
   ▼
prompts/builder.py   system + user prompt for the selected categories/language
   ▼
providers/openrouter.py   HTTP call → raw text → parse_comments() → list[ReviewComment]
   ▼
report/cli_renderer.py    group by file, sort by line, color by severity → stdout
   ▼
exit 0          always — v1 is warn-only, never blocks the commit
```

## The two adapter interfaces

Both "swap this out later" problems have the **same shape**, so both get an
abstract base class now. Adding a new implementation later is purely additive.

### `DiffSource` (`diff/source.py`)

```python
class DiffSource(ABC):
    @abstractmethod
    def get_diff_files(self) -> list[DiffFile]: ...
```

- `LocalGitSource` — v1, runs `git diff --cached`.
- `GitHubPRSource` — v2 (not built), would fetch a PR diff via the GitHub API.
  The parser, prompt builder, provider, and renderer downstream **do not change**.

### `LLMProvider` (`providers/base.py`)

```python
class LLMProvider(ABC):
    @abstractmethod
    async def review(
        self, diff_file: DiffFile, categories: list[Category], languages: list[str]
    ) -> list[ReviewComment]: ...
```

- `OpenRouterProvider` — the only v1 implementation.
- `registry.py` maps the config string `provider: openrouter` → the class, so
  switching providers (once more exist) is a one-line config change.
- `parse_comments()` (in `providers/base.py`) is shared, provider-agnostic
  response handling: it extracts JSON from the raw model text (tolerating
  markdown fences / surrounding prose), validates each item against
  `ReviewComment`, drops individually malformed items, and — critically —
  **drops any comment whose line isn't an actually-changed line** in that file.
  This is the safety net that keeps output line-mapped and on-topic.

## The models are the contract (`models.py`)

- `DiffFile` — path, detected language, `is_binary`, and hunks of typed
  `DiffLine`s. Helpers: `changed_line_numbers()`, `to_prompt_text()`.
- `ReviewComment` — file, line, category, severity, comment, optional suggestion.
- `ReviewResult` — aggregate: comments + counts (reviewed / skipped-binary /
  skipped-too-large / errored) + provider/model/duration.

Because these are the only things crossing stage boundaries, each stage is unit
tested in isolation (see `tests/`), and a `FakeProvider` exercises the whole
pipeline without any network calls.

## Error isolation

`engine.py` reviews files concurrently but isolates failures **per file**: a
provider error, a rate limit exhausting retries, or malformed JSON on one file
is recorded as a skip and never aborts the run or blocks the commit. Infra
failures surface as a yellow warning and exit 0.

## Why CLI-only in v1

Review has to happen at the moment `git commit` runs, in the terminal already
open. A web view would need a running server and a manual browser step for no
benefit at that instant. `report/json_renderer.py` already emits machine-readable
output, so a web dashboard, editor extension, or CI consumer can be bolted on
later as an *additional consumer* of `ReviewResult` — without reworking the
pipeline.
