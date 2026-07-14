#!/usr/bin/env bash
# Installed by `aicr install-hook`. Runs an AI review of staged changes before
# each commit. WARN-ONLY: this hook never blocks a commit (always exits 0),
# per the aicr design. Set AICR_SKIP=1 to bypass for a single commit.
#
#   AICR_SKIP=1 git commit -m "hotfix"

if [ "${AICR_SKIP:-0}" = "1" ]; then
    exit 0
fi

if ! command -v aicr >/dev/null 2>&1; then
    echo "aicr: not found on PATH — skipping AI review (pip install ai-code-review)." >&2
    exit 0
fi

# Review runs but must never fail the commit.
aicr review --staged || true
exit 0
