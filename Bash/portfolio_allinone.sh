#!/usr/bin/env bash
set -euo pipefail

if [[ "${TRACE-0}" == "1" ]]; then
    set -o xtrace
fi

if [[ "${1-}" =~ ^-*h(elp)?$ ]]; then
    echo "Usage $0

Set up a fresh clone of my portfolio. Install dependencies. And run all tests across projects.

"
    exit 0
fi

require_cmd(){
    if ! command -v "$1" &>/dev/null; then
        echo "Error: $1 not found in PATH" >&2
        exit 1
    fi
}

main(){
    if [[ $# -gt 0 ]]; then
        echo "Error: $0 takes no arguments" >&2
        exit 1
    fi

    require_cmd git
    require_cmd uv

    local my_repo="https://github.com/Chen-ZZ-ZZzz/technical-portfolio"
    local home_dir=technical-portfolio

    if [[ -e "$home_dir" ]]; then
        echo "Error: $home_dir exists" >&2
        exit 1
    fi

    echo "Cloning a fresh portfolio..."
    git clone "$my_repo"
    cd "$home_dir"

    local project
    for project in health-checker LSST-Alert-QA; do
        echo "=== $project ==="
        (cd "$project"
         uv sync
         uv run pytest tests/)
    done

    echo "All tests passed."
}

main "$@"
