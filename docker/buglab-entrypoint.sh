#!/bin/sh
set -eu

if [ "$#" -gt 0 ]; then
    case "$1" in
        -*)
            ;;
        *)
            exec "$@"
            ;;
    esac
fi

DEFAULT_WORKDIR="${BUG_LAB_WORKDIR:-/work}"
select_workspace_arguments() {
    WORKDIR=$DEFAULT_WORKDIR
    EXPLICIT_RESUME=no
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --resume)
                EXPLICIT_RESUME=yes
                ;;
            --workdir)
                if [ "$#" -gt 1 ]; then
                    shift
                    WORKDIR=$1
                fi
                ;;
            --workdir=*)
                WORKDIR=${1#--workdir=}
                ;;
        esac
        shift
    done
}

select_workspace_arguments "$@"
if [ -z "$WORKDIR" ]; then
    WORKDIR=.
fi

if [ -f "$WORKDIR/.bug-lab-workspace.json" ] && [ "$EXPLICIT_RESUME" = no ]; then
    set -- --resume "$@"
fi

exec python -m demo.bug_lab \
    --host "${BUG_LAB_HOST:-0.0.0.0}" \
    --allow-remote \
    --workdir "$WORKDIR" \
    "$@"
