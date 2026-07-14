# aicr — AI Code Review Assistant

Local-first AI code review that reviews your **staged git diff before you commit**.
It reads `git diff --cached`, sends only the changed lines to an LLM (via
[OpenRouter](https://openrouter.ai), [Google Gemini](https://ai.google.dev), or a
local [Ollama](https://ollama.com) model), and prints inline, line-mapped comments
— bugs, security risks, readability issues, and style suggestions — right in the
terminal where you ran `git commit`.

- **Local-first** — runs on your machine as a git pre-commit hook.
- **Warn-only** — flags issues but *never blocks* your commit (unless you opt in).
- **Line-mapped** — every comment is tied to a real added/modified line.
- **Provider-agnostic** — OpenRouter (cloud), Gemini (cloud), and Ollama (local)
  today; the adapter interface lets you add OpenAI, Anthropic, etc. without
  touching the review pipeline (see [ARCHITECTURE.md](ARCHITECTURE.md)).


> ⚠️ **Data flow:** with a cloud provider this tool sends your staged code diffs
> to a third-party API (OpenRouter or Gemini). Prefer **`provider: ollama`** for
> fully local review where nothing leaves your machine.


---

## 60-second quickstart

```bash
# 1. Install straight from GitHub (installs the `aicr` command)
pip install "git+https://github.com/iammarxg/aicodereview.git"
# or, from a local checkout:  pip install .

# 2. Interactive setup — pick a provider, store your key, install the hook
cd /path/to/your/repo
aicr init

# 3. Just commit — the review runs automatically
git add .
git commit -m "your change"
```

`aicr init` is an rclone-style wizard: it asks which provider you want, saves your
API key to a gitignored `.env` (never to `.aicr.yaml`), lets you pick categories
and blocking behavior, optionally walks you through advanced options, and offers to
install the pre-commit hook. At the end it can **analyze your repo** (fast, no API
calls) — reporting file/line/character counts, detected languages, and recommended
excludes/limits tuned to its size — and apply those recommendations to `.aicr.yaml`
(for a measured full-repo review time, run `aicr scan`). The generated file lists every
option — the ones you didn't set are written commented-out at their defaults, so
the whole config surface is discoverable in one place. Prefer to do it by hand? Set
the provider's key env var, write a `.aicr.yaml`, and run `aicr enable`.

Changed your mind? `aicr reset` removes everything aicr added to the repo — the
pre-commit hook, `.aicr.yaml`, and the `.aicr/` cache — and offers to strip the API
key line from `.env` (leaving any other variables untouched).



You can also run a review manually, without committing:

```bash
aicr review                 # staged changes, colored report
aicr review --unstaged      # working-tree changes not yet staged
aicr review --range main..HEAD   # a whole branch before you push
aicr review --format json   # machine-readable (for CI/editors later)
```

## Example output

```
AI Code Review  (openrouter:openrouter/free)

calc.py
  [CRITICAL] L5 (bug) Division by zero is not handled when b == 0.
    ↳ Guard with `if b == 0: raise ValueError(...)` before dividing.
  [INFO] L4 (readability) Consider a docstring describing the return value.

1 file(s) reviewed · 2 comment(s) · 1.3s
~1240 tokens (980 in / 260 out)
API usage: [████░░░░░░] 42% · 0.42/1.00 credits ($)
```

The token line and **"% API usage" bar** appear whenever the provider reports
usage (OpenRouter does; local Ollama has no billing).

## Providers

| Provider | Where it runs | API key | Select with |
|---|---|---|---|
| `openrouter` | Cloud, hundreds of models | `OPENROUTER_API_KEY` | `provider: openrouter` (default) |
| `gemini` | Cloud, Google Gemini (generous free tier) | `GEMINI_API_KEY` | `provider: gemini` + `model: gemini-2.0-flash` |
| `ollama` | **Local**, private | none | `provider: ollama` + `model: llama3.1` |

Each cloud provider reads its own key env var (shown above), from your environment
or a gitignored `.env`. Get a Gemini key at
[aistudio.google.com/apikey](https://aistudio.google.com/apikey).

For Ollama: install it, `ollama pull llama3.1`, then set `provider: ollama` in
`.aicr.yaml` (optionally `base_url` if not on the default `localhost:11434`).


## Commands

| Command | Description |
|---|---|
| `aicr init` | Interactive setup wizard (provider, key, categories, hook). |
| `aicr review [--unstaged\|--range A..B] [--strict] [--no-cache] [--format cli\|json] [--category ...] [--model ...] [--include ...]` | Review changes. Warn-only (exit 0) unless `--strict`. |
| `aicr scan [--max-files N] [--yes] [--format cli\|json] [--category ...] [--model ...]` | Review the whole repo's existing code (not just a diff). Shows a cost/time estimate and confirms first. |
| `aicr enable [--force]` | Install `.git/hooks/pre-commit` in the current repo. |
| `aicr disable` | Remove the aicr pre-commit hook. |
| `aicr reset [--yes]` | Remove everything aicr added: hook, `.aicr.yaml`, and the `.aicr/` cache (offers to strip the key from `.env`). |
| `aicr config` | Show the resolved configuration (never prints the API key). |



`install-hook` / `uninstall-hook` remain as hidden aliases for `enable` /
`disable`. Bypass a single commit: `AICR_SKIP=1 git commit -m "..."`.

### Blocking mode (opt-in)

By default aicr never blocks a commit. To turn it into a gate, pass `--strict`
(blocks on `critical` findings) or set a threshold in config:

```yaml
severity_block_threshold: critical   # or "warning"
```

When a finding meets the threshold, `aicr review` exits non-zero, which aborts
the commit from the pre-commit hook. `AICR_SKIP=1` still bypasses it.

### Faster re-runs — the hunk cache

Files whose changed lines haven't moved since a previous run are served from a
local cache (`.aicr/cache/`, gitignored) instead of re-calling the provider —
handy for the "commit, fix, recommit" loop. Disable with `--no-cache` or
`cache_enabled: false`.

### Reviewing a normally-excluded file

`.aicr.yaml`'s `exclude_paths` is the single source of truth for what gets
skipped. To review one excluded file just this once, force-include it:

```bash
aicr review --include docker-compose.yml
# multiple, space- or comma-separated:
aicr review --include "docker-compose.yml *.md"
```

### Scanning the whole repo

`aicr review` only looks at a diff. To review your **existing** code — for a first
pass on a repo, or to gauge value before wiring up the hook — use `aicr scan`:

```bash
aicr scan                 # estimate cost/time, confirm, then review every file
aicr scan --max-files 20  # cap the run (and the spend)
aicr scan --yes           # skip the confirmation prompt
aicr scan --format json   # machine-readable, for CI
```

It sends full file contents (not just changed lines) to the provider, so it can
find issues anywhere — not only in code you just touched. Because that can be many
API calls, it reviews one representative file first to **measure** your provider's
real speed, prints a size + time estimate from that, and asks before scanning the
rest (the sample is cached, so it isn't reviewed twice).


## Configuration — `.aicr.yaml`

Committable, human-editable, per-repo settings. The API key is **never** stored
here — only read from the provider's key env var (e.g. `OPENROUTER_API_KEY` or
`GEMINI_API_KEY`), from your environment or `.env`.


```yaml
provider: openrouter          # or: gemini, ollama
model: openrouter/free        # an OpenRouter/Gemini model, or a pulled Ollama model

# base_url: http://localhost:11434/v1   # override provider endpoint (e.g. Ollama)
categories: [bugs, security, readability, style]
languages: []                 # empty = auto-detect per file by extension
exclude_paths: ["*.lock", "dist/**", "node_modules/**", "*.md"]
max_diff_lines_per_file: 800  # skip files with more added lines than this
concurrency: 5                # max simultaneous provider requests
cache_enabled: true           # reuse results for unchanged files across runs
severity_display_threshold: info
# severity_block_threshold: critical    # uncomment to block commits (opt-in)
```

## Using the `pre-commit` framework instead

If your repo already uses [pre-commit](https://pre-commit.com), add to your
`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/iammarxg/aicodereview
    rev: v0.2.0
    hooks:
      - id: aicr
```


## Requirements

- Python 3.11+
- An OpenRouter or Gemini API key (or a local Ollama install)
- `git` on your PATH


## License

MIT — see [LICENSE](LICENSE).
