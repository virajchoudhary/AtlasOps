# Restart persistence: the container could not restart at all, now it can

PR #240 made the bug lab's workspace durable (owned-workspace resume, durable
incident history, interruption records). The container image never enabled it,
so `docker restart` exited 1 on both the old and the merged image.

## Reproduced three times

| Image | Built from | `docker restart` | Result |
|---|---|---|---|
| `atlasops-buglab:v2` | 9ab930a | exit 1 | `ValueError: Bug Lab workdir must be new or empty` at `workspace.py:48` |
| `atlasops-buglab:v3-fafe1eb` | fafe1eb (merged main) | exit 1 | same error at `workspace.py:201`, now reached through `Workspace(config.workdir, resume=config.resume)` with `resume=False` |
| `atlasops-buglab:v4-resume` | fafe1eb + entrypoint | **HTTP 200** | `bug lab: resuming the existing working copy at /work` |

This is how it was found: the Colima resize stopped the lab container, and it
would not come back. On a demo machine that is a dead control room after any
host restart, Docker restart or crash.

## Why a fixed command cannot work

`demo/bug_lab/workspace.py` refuses two things, each correct on its own:

```
resume=False and the workdir is not empty -> "Bug Lab workdir must be new or empty"
resume=True  and there is no marker file  -> "Bug Lab resume requires a valid workspace marker"
```

`Dockerfile.buglab` had a fixed `CMD` with no `--resume`, so the first start
worked and every restart failed. Adding `--resume` to that CMD would invert the
problem: restarts would work and first starts would fail. The flag has to be
chosen at start time from what `/work` actually holds.

## Fix (Mac runtime, this branch)

`docker/buglab-entrypoint.sh` checks for the marker that
`demo/bug_lab/workspace.py` writes (`.bug-lab-workspace.json`) and adds
`--resume` only when it is present. `Dockerfile.buglab` now uses it as
`ENTRYPOINT`, with `CMD ["--agents", "llm"]` so the agent mode stays overridable.

No application code was changed. The resume logic itself is the Windows agent's
PR #240 work and is correct; it was simply unreachable from the container.

## Verification

```
first start:   bug lab: starting a new working copy at /work
inject truncated-data, P1 approval left to expire -> escalated
               history: 1 | seq: 1 | /work/incidents.jsonl: 1 line

docker restart buglab-resume-test

after restart: bug lab: resuming the existing working copy at /work
               http: 200
               history: 1 | seq: 2 | state: active
               /work/incidents.jsonl: 1 line
               container: Up
```

History and the JSONL evidence survived the restart, and the sequence advanced
to 2 rather than resetting, which is the durable-identity behaviour #240 added.

## Note on an earlier false signal

An intermediate attempt restored a saved `/work` into a new container with
`docker cp` and failed with `WorkspacePersistenceError`. `docker cp` writes as
root while the container runs as uid 10001, so the lock and marker were not
writable by the lab. That was an artifact of the copy, not a defect, and is
recorded here so it is not mistaken for one.
