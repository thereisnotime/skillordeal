set shell := ["bash", "-uc"]
set dotenv-load := false

# ANSI color helpers
BOLD := '\033[1m'
DIM := '\033[2m'
RESET := '\033[0m'
RED := '\033[31m'
GREEN := '\033[32m'
YELLOW := '\033[33m'
MAGENTA := '\033[35m'
CYAN := '\033[36m'

cli_version := "2.1.280"
base_image := "docker.io/library/node:24.21.0-trixie-slim@sha256:8ec5d7557396cfe32d21c3f9c13072355ceab22b584578ca4bb28af31120cffe"
image := "localhost/skillordeal-runner:dev"
so := "uv run skillordeal"

# Show the colorized, categorized menu (bare `just`)
default:
    #!/usr/bin/env bash
    set -uo pipefail
    printf "{{BOLD}}{{CYAN}}skillordeal{{RESET}} {{DIM}}reproducible benchmark harness for LLM agent skills{{RESET}}\n\n"
    printf "{{BOLD}}{{MAGENTA}}SETUP{{RESET}}\n"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "setup"            "asdf install + uv sync (venv in .venv)"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "image"            "build the pinned runner image with podman"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "doctor"           "check podman, cgroup v2, image, auth env"
    printf "\n{{BOLD}}{{MAGENTA}}TRIALS{{RESET}}\n"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "validate TRIAL"   "load and check a trial.yaml"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "lock TRIAL ROUND" "pin every input into rounds/ROUND/lock.yaml"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "plan TRIAL ROUND" "list the bouts of a locked round"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "run TRIAL ROUND [j=1]" "run a round (resumable, -j parallel bouts)"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "status TRIAL ROUND" "per-bout status, cost, tokens, RAM"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "score TRIAL ROUND" "findings, ground truth, dedup, summary (no network)"
    printf "\n{{BOLD}}{{MAGENTA}}INSPECT{{RESET}}\n"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "show BOUT_DIR"    "one bout's record summary and findings"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "transcript BOUT_DIR" "decompressed stream-json (pipe to jq)"
    printf "\n{{BOLD}}{{MAGENTA}}DEV{{RESET}}\n"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "smoke"            "lock + run the examples/smoke trial (costs cents)"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "test"             "pytest (unit tests, no podman, no API)"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "test-podman"      "pytest incl. podman integration tests"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "lint"             "ruff check + format check"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "fmt"              "ruff format + autofix"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "secrets-scan"     "gitleaks over the working tree and history"
    printf "  {{GREEN}}%-28s{{RESET}} {{YELLOW}}%s{{RESET}}\n" "verify" "lint + test + secrets-scan, run before you push"
    printf "  {{GREEN}}%-28s{{RESET}} %s\n" "clean"            "remove work/, caches, build output"
    printf "\n{{DIM}}Run {{RESET}}{{BOLD}}just --list{{RESET}}{{DIM}} for the raw recipe list.{{RESET}}\n"

# SETUP: install the pinned toolchain and the venv
setup:
    asdf install || true
    uv sync --all-extras

# SETUP: build the runner image (pinned base digest + pinned CLI)
image:
    podman build -f container/Containerfile \
        --build-arg BASE_IMAGE={{base_image}} \
        --build-arg CLI_VERSION={{cli_version}} \
        -t {{image}} container/
    podman run --rm --network=none {{image}} claude --version

# SETUP: check the host can run bouts
doctor:
    #!/usr/bin/env bash
    set -uo pipefail
    ok() { printf "  {{GREEN}}ok{{RESET}}   %s\n" "$1"; }
    bad() { printf "  {{RED}}fail{{RESET}} %s\n" "$1"; }
    command -v podman >/dev/null && ok "podman $(podman --version | awk '{print $3}')" || bad "podman missing"
    [[ "$(podman info --format '{{{{.Host.CgroupsVersion}}')" == v2 ]] && ok "cgroup v2" || bad "cgroup v2 needed for resource monitoring"
    [[ "$(podman info --format '{{{{.Host.Security.Rootless}}')" == true ]] && ok "rootless" || bad "podman is not rootless"
    podman image exists {{image}} && ok "image {{image}}" || bad "image missing, run: just image"
    command -v uv >/dev/null && ok "uv $(uv --version | awk '{print $2}')" || bad "uv missing"
    [[ -n "${ANTHROPIC_API_KEY:-}" ]] && ok "ANTHROPIC_API_KEY set" || printf "  {{DIM}}--   ANTHROPIC_API_KEY not set{{RESET}}\n"
    [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]] && ok "CLAUDE_CODE_OAUTH_TOKEN set" || printf "  {{DIM}}--   CLAUDE_CODE_OAUTH_TOKEN not set (or use auth.token_env / auth.env_file){{RESET}}\n"

# TRIALS: load and check a trial.yaml
validate trial:
    {{so}} validate {{trial}}

# TRIALS: pin every input into rounds/ROUND/lock.yaml
lock trial round *args:
    {{so}} lock {{trial}} -r {{round}} {{args}}

# TRIALS: list the bouts of a locked round
plan trial round:
    {{so}} plan {{trial}} -r {{round}}

# TRIALS: run a round; j=1 runs one after another, j=N runs N bouts at once
run trial round j="1" *args:
    {{so}} run {{trial}} -r {{round}} -j {{j}} {{args}}

# TRIALS: per-bout status table
status trial round:
    {{so}} status {{trial}} -r {{round}}

# TRIALS: collect findings, match ground truth, cluster duplicates, write scores/
score trial round *args:
    {{so}} score {{trial}} -r {{round}} {{args}}

# INSPECT: summarize one bout
show bout_dir:
    {{so}} show {{bout_dir}}

# INSPECT: print a bout's transcript (pipe into jq)
transcript bout_dir:
    {{so}} transcript {{bout_dir}}

# DEV: lock and run the smoke trial (one baseline + one skill bout on dvpwa)
smoke *args:
    {{so}} lock examples/smoke/trial.yaml -r smoke --force
    {{so}} run examples/smoke/trial.yaml -r smoke {{args}}
    {{so}} status examples/smoke/trial.yaml -r smoke

# DEV: unit tests
test *args:
    uv run pytest {{args}}

# DEV: unit + podman integration tests
test-podman *args:
    uv run pytest -m "not live" {{args}}

# DEV: lint
lint:
    uv run ruff check src tests
    uv run ruff format --check src tests

# DEV: format and autofix
fmt:
    uv run ruff format src tests
    uv run ruff check --fix src tests

# DEV: scan history and staged changes for secrets
secrets-scan:
    gitleaks git --no-banner --redact .
    gitleaks git --no-banner --redact --staged .

# DEV: run before you push
verify: lint test secrets-scan
    @printf "{{GREEN}}verify passed{{RESET}}\n"

# DEV: remove generated stuff
clean:
    rm -rf work .pytest_cache .ruff_cache .mypy_cache dist build
    find . -name __pycache__ -type d -prune -exec rm -rf {} +
