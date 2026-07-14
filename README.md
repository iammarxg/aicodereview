# aicr — AI Code Review Assistant

Local-first AI code review that reviews your **staged git diff before you commit**.
It reads `git diff --cached`, sends only the changed lines to an LLM (via
[OpenRouter](https://openrouter.ai)), and prints inline, line-mapped comments —
bugs, security risks, readability issues, and style suggestions — right in the
terminal where you ran `git commit`.

- **Local-first** — runs on your machine as a git pre-commit hook.
- **Warn-only** — flags issues but *never blocks* your commit (v1).
- **Line-mapped** — every comment is tied to a real added/modified line.
- **Provider-agnostic** — OpenRouter today; the adapter interface is built so
  OpenAI, Anthropic, or a local model can be added later without touching the
  review pipeline (see [ARCHITECTURE.md](ARCHITECTURE.md)).

> ⚠️ **Data flow:** this tool sends your staged code diffs to a third-party API
> (OpenRouter). Understand and accept that before using it on private/work code.

---

## 60-second quickstart

```bash
# 1. Install straight from GitHub (installs the `aicr` command)
pip install "git+https://github.com/iammarxg/aicodereview.git"
# or, from a local checkout:  pip install .

# 2. Set your OpenRouter API key (never stored in a committed file)
cp .env.example .env                      # then edit .env, or:
export OPENROUTER_API_KEY="sk-or-..."     # https://openrouter.ai/keys

# 3. Enable the git hook in your repo
cd /path/to/your/repo
aicr enable

# 4. Just commit — the review runs automatically
git add .
git commit -m "your change"
```

The default model is `openrouter/free` — a virtual router that load-balances
across OpenRouter's free models, so a fresh key works out of the box with far
fewer rate-limit errors. Override it per-repo in `.aicr.yaml` or per-run with
`aicr review --model anthropic/claude-3.5-sonnet`.

You can also run a review manually without committing:

```bash
git add .
aicr review                 # colored terminal report
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
```

## Commands

| Command | Description |
|---|---|
| `aicr review [--staged] [--format cli\|json] [--category ...] [--model ...] [--include ...]` | Review staged changes. Always exits 0 (warn-only). |
| `aicr enable [--force]` | Install `.git/hooks/pre-commit` in the current repo. |
| `aicr disable` | Remove the aicr pre-commit hook. |
| `aicr config` | Show the resolved configuration (never prints the API key). |

`install-hook` / `uninstall-hook` remain as hidden aliases for `enable` /
`disable`. Bypass a single commit: `AICR_SKIP=1 git commit -m "..."`.

### Reviewing a normally-excluded file

`.aicr.yaml`'s `exclude_paths` is the single source of truth for what gets
skipped. To review one excluded file just this once, force-include it:

```bash
aicr review --include docker-compose.yml
# multiple, space- or comma-separated:
aicr review --include "docker-compose.yml *.md"
```

## Configuration — `.aicr.yaml`

Committable, human-editable, per-repo settings. The API key is **never** stored
here — only read from `OPENROUTER_API_KEY` (env or `.env`).

```yaml
provider: openrouter
model: openrouter/free        # auto-routes across free models; any OpenRouter model works
categories: [bugs, security, readability, style]
languages: []                 # empty = auto-detect per file by extension
exclude_paths: ["*.lock", "dist/**", "node_modules/**", "*.md"]
max_diff_lines_per_file: 800  # skip files with more added lines than this
concurrency: 5                # max simultaneous provider requests
severity_display_threshold: info
```

## Using the `pre-commit` framework instead

If your repo already uses [pre-commit](https://pre-commit.com), add to your
`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/iammarxg/aicodereview
    rev: v0.1.0
    hooks:
      - id: aicr
```

## Requirements

- Python 3.11+
- An OpenRouter API key
- `git` on your PATH

## License

MIT — see [LICENSE](LICENSE).
