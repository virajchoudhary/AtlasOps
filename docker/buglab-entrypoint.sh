#!/bin/sh
# Start the bug lab, resuming an existing working copy when there is one.
#
# demo/bug_lab/workspace.py deliberately refuses two things: a non-empty
# --workdir without --resume, and --resume without a valid workspace marker.
# Both are correct on their own, but together they mean a single fixed command
# cannot both start a container and restart it:
#
#   without --resume : first start works, `docker restart` exits 1 with
#                      "Bug Lab workdir must be new or empty"
#   with --resume    : first start exits 1 with
#                      "Bug Lab resume requires a valid workspace marker"
#
# So the flag has to be chosen at start time from what /work actually holds.
# The marker name is the _MARKER_NAME constant in demo/bug_lab/workspace.py.
set -eu

WORKDIR="${BUG_LAB_WORKDIR:-/work}"
MARKER="$WORKDIR/.bug-lab-workspace.json"

if [ -f "$MARKER" ]; then
    echo "bug lab: resuming the existing working copy at $WORKDIR"
    set -- --resume "$@"
else
    echo "bug lab: starting a new working copy at $WORKDIR"
fi

exec python -m demo.bug_lab \
    --host "${BUG_LAB_HOST:-0.0.0.0}" \
    --allow-remote \
    --workdir "$WORKDIR" \
    "$@"
