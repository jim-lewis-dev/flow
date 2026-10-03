#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
python_bin="${FLOW_PYTHON:-python3}"
if ! "$python_bin" -c 'import tkinter; import matplotlib' >/dev/null 2>&1; then
    echo 'Flow needs Tkinter and Matplotlib.' >&2
    echo 'On Ubuntu 22.04/24.04 run: sudo apt install python3-tk python3-matplotlib' >&2
    echo 'Then run this launcher again.' >&2
    exit 1
fi
exec "$python_bin" -m flowledger "$@"
