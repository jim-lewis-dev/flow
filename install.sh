#!/usr/bin/env bash
set -euo pipefail
project_dir="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
python_bin="${FLOW_PYTHON:-python3}"
if ! "$python_bin" -c 'import sys; assert sys.version_info >= (3, 10); import tkinter; import matplotlib' >/dev/null 2>&1; then
    echo 'Flow needs Python 3.10+, Tkinter, and Matplotlib.' >&2
    echo 'On Ubuntu: sudo apt install python3-tk python3-matplotlib' >&2
    echo 'Install the missing dependencies, then run ./install.sh again.' >&2
    exit 1
fi
target="$HOME/.local/bin/flow"
if [[ -e "$target" || -L "$target" ]]; then
    if [[ ! -L "$target" || "$(readlink -f -- "$target")" != "$project_dir/flow" ]]; then
        echo "Cannot install: $target already exists and belongs to something else." >&2
        exit 1
    fi
    echo "Already installed: $target -> $project_dir/flow"
else
    mkdir -p -- "$(dirname -- "$target")"
    ln -s -- "$project_dir/flow" "$target"
    echo "Installed: $target -> $project_dir/flow"
fi
case ":$PATH:" in
    *":$HOME/.local/bin:"*) echo 'Run: flow [name]' ;;
    *) echo 'For this shell, run: export PATH="$HOME/.local/bin:$PATH"'
       echo 'No shell settings were changed.' ;;
esac
