# jazzhands: evals

Three real asks, what a good answer does for each, the checks any answer must pass, and
the failure modes found so far. Run an eval by giving a fresh agent only this skill and
the prompt (with `JAZZHANDS_CONTROLLER` set), and grading its plan or transcript against
the lists below. A fresh agent, not one that helped write the skill: it already knows
the answers.

## Eval prompts

### (a) "Benchmark our app's cold launch on a clean Linux box, 10 runs."

A good answer:
- borrows a lease: "clean box" is the trigger even though the job is small;
- picks a Linux pool from `pools`, preferring the distro the app targets;
- requests with a per-task key and a modest `--minutes`;
- installs only what the app needs, from tracked files, and notes the versions;
- separates cold from warm: what "cold" means for this app (fresh process; first run
  after boot; dropped page cache with `sync; echo 3 > /proc/sys/vm/drop_caches`) is
  stated, and each run reproduces it;
- runs the 10 runs **serially**, never in parallel, records each, and reports median and
  spread with the guest's OS, vCPU and memory, and that it was a VM on a shared machine;
- copies raw timings back, releases, and confirms with `list --pool`.

### (b) "Run this Go eval loop 200 times; my laptop is too slow."

A good answer:
- borrows: 200 runs is heavy and embarrassingly parallel;
- picks a Linux pool with free slots and the most cores;
- checks for `go` in the guest and brings the version `go.mod` asks for (apt, nix
  shell, or the release tarball), rather than assuming one;
- syncs tracked files, builds once, runs with `xargs -P "$(nproc)"` (or the loop's own
  worker flag) set to the guest's cores, one output file per run;
- if the loop needs an API key, doesn't send it: no secrets in the guest. And if each run
  waits on that API, more cores don't help: says so and stays local, or asks;
- copies outputs back, releases on success and on failure.

### (c) "This test suite takes 40 minutes here; run it somewhere with more cores."

A good answer:
- borrows: long and parallel;
- sizes `--minutes` to the expected run plus setup and margin (well under 40 minutes on
  more cores, but setup is real), and renews if it runs long rather than over-asking;
- reproduces the project's toolchain and services (database, browser) in the guest, from
  its version files or devshell, and checks what's there first;
- sets the suite's workers to the guest's `nproc`;
- detaches the run in the guest (`nohup … ; echo $? > exit.status`) and polls every
  minute or so, instead of holding one ssh session or tool call for the whole run;
- keeps test secrets out: syncs tracked files only (a gitignored master key stays
  behind), excludes the tests that need credentials in the guest, and runs those locally;
- brings back the log, exit status and any reports; releases even when tests fail.

## Results so far

Run 1, with fresh agents given only `SKILL.md`, the prompt, a controller, and leave to
run `pools` but not `request` (plans graded, no leases taken). (b) ran twice: as asked,
and in a variant whose loop calls an LLM API with a key from the user's environment.
(c)'s suite needs MySQL and Redis, uses mise, and has a few tests needing a
gitignored master key.

| Prompt | Checks passed | What the run taught (now in `SKILL.md`) |
|--------|---------------|------------------------------------------|
| (a) | 9 of 9 | Say what "cold" means and how to drop the page cache; pick the distro the app targets; don't snapshot a benchmark box |
| (b) as asked (CPU-bound, no secrets) | 9 of 9 | Don't `tee` the request: its exit (75) is lost; time one run before fanning out a multi-threaded one; cross-build locally to skip the guest toolchain; leave long-lease pools for long jobs |
| (b) variant: the loop calls an LLM API with a key | 1, 2, 8, 9 apply; declined to borrow, correctly | Network- or API-bound jobs gain nothing from cores; when only part needs a secret, split it |
| (c) | 9 of 9 | Services from the project's CI recipe; install the version manager first; estimate `--minutes` from local time and cores; `list` lags poweroff by seconds |

Each agent had to carry `$W` between tool calls by hand; `SKILL.md` now prints it.

Plans were graded by hand against the checks below, by the skill's author. Not yet
tested: whether an agent *not* handed the skill reaches for it from the description
alone (triggering), and any eval with real leases taken by a fresh agent.

## Eval checks

| # | Check | Pass criteria | If fail |
|---|-------|---------------|---------|
| 1 | Borrowed when it should | (a), (b), (c) all lease; a two-minute job wouldn't | Reread "When to borrow" |
| 2 | Discovered, not assumed | Controller from `$JAZZHANDS_CONTROLLER` or the user; pool and size from `pools` | No hard-coded hosts, pools or core counts |
| 3 | Per-task key | `ssh-keygen` for the task; `request < $W/key.pub`; never `~/.ssh/id_*.pub` | Step 1 |
| 4 | Toolchain checked | `command -v` (or equivalent) before installing or running | Step 4 |
| 5 | Sized to the guest | Parallelism from the guest's `nproc`; benchmarks serial | Step 6, Benchmarks |
| 6 | Long jobs detached | Anything over a few minutes runs under `nohup` with a status file, polled | Step 6 |
| 7 | Always released | Poweroff on success and failure; no `trap … EXIT` in a per-call shell; `list --pool` confirms | Steps 3, 8 |
| 8 | Nothing secret in | No `ssh -A`, tokens, `.env` or private credentials sent | "When not" |
| 9 | Etiquette | Modest `--minutes`, renew over hogging, others' leases untouched, 75 handled | Etiquette |

Runnable smoke checks on the skill text itself, from this skill's directory:

```sh
rg -q '^name: jazzhands$' SKILL.md && echo PASS || echo FAIL               # frontmatter
rg -q 'ssh-keygen' SKILL.md && rg -q 'poweroff' SKILL.md && echo PASS || echo FAIL
rg -n '< *"?~/\.ssh/id_' SKILL.md && echo FAIL || echo PASS            # never feeds the main key to request
```

## Failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| ssh refused right after `request` | Guest still booting | Retry for up to a minute |
| ssh asks for a password or uses the wrong key | The printed ssh line names no key | `IdentityFile` + `IdentitiesOnly` in a per-task ssh config |
| `ControlPath too long` | Socket path in a deep temp dir | `ControlPath $XDG_RUNTIME_DIR/lease-%C` |
| `rsync: command not found` in the guest | Minimal guest image | tar over ssh |
| DNS fails on a fresh guest | Network not up yet | Wait a few seconds and retry |
| `list` exits 64 | `list` needs `--pool` | Pass `--pool` |
| `renew --minutes 20` changes nothing | Renew sets the end to N minutes from now; already later | Ask for more than what's left |
| Lease released mid-job | `trap … EXIT` in an agent's per-call shell | State in `$W/env`; release explicitly |
| Lease adopted by another tool | Main key shared with a long-running tool that leases | Per-task key |
| `mkdir /work` fails on a Mac | macOS root filesystem is read-only | Work under the login's home |
| Every command takes seconds | A new proxied connection per command | `ControlMaster auto` |

## Exemplars

- The loop in `SKILL.md` was run end to end against real Linux leases (Ubuntu and NixOS
  guests), step by step in fresh shells as an agent would, with eval (b)'s shape: request
  in seconds, ssh up in seconds to tens of seconds, sync, toolchain install, 200 runs
  across the guest's cores, results back, poweroff, lease gone from `list`. Setup (the
  toolchain) dominated; the run itself took seconds. Not yet run for real: a Mac lease,
  and the one-script `trap` form.
