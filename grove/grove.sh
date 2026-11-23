#!/usr/bin/env bash
# Start Grove. Any arguments are passed through (try --help).
cd "$(dirname "$(readlink -f "$0")")" && exec python3 -m grove "$@"
