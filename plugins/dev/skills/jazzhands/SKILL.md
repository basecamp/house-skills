---
name: jazzhands
description: |
  Use when heavy, parallel or long-running work would load this machine: a slow test
  suite, a build or eval loop run many times, a benchmark that needs a clean box, or a
  job that needs another OS (Linux or macOS) — "my laptop is too slow", "run it
  somewhere with more cores", "on a clean Linux box". Borrows a disposable guest from a
  Jazzhands lease controller over ssh (`ssh lease@<controller> pools`), runs the work
  there, copies results back and releases it. Linux leases first, Macs second.
---

# jazzhands

Jazzhands hands out short leases on disposable VMs ("guests") on spare build machines.
A lease is a fresh guest you reach over ssh, keep for minutes to hours, and throw away.
The hands usually sit idle while workstations choke: when a job is heavy, borrow one
instead of piling it onto the machine you're on. Nobody has to ask you to.

## When to borrow, and when not

Borrow when the job is:
- **heavy or parallel**: a test suite, build or loop that would saturate local cores for
  more than a few minutes;
- **long**: runs that outlast your patience or your laptop's battery;
- **clean-room**: "on a fresh box", a benchmark that local load would skew, an install
  script to prove from zero;
- **another OS**: Linux from a Mac, or macOS from Linux (Mac pools are smaller: try Linux
  first unless the job needs macOS).

Stay local when:
- the job takes under a couple of minutes here (lease and sync overhead is about half a
  minute, more with toolchain setup);
- it needs secrets, credentials or customer data: **nothing secret goes into a guest.**
  No `ssh -A`, no tokens, no `.env`, no private-registry credentials. If the build needs
  private dependencies, vendor or fetch them locally and sync the result, or stay local;
- it needs this machine's hardware, display or local services;
- it's bound by something more cores don't speed up: an API's latency or rate limit, a
  remote service, the network.

When only part of a job needs a secret (a few tests that hit a payment sandbox), split
it: run the rest in the guest, exclude that part there, and run it locally after.

## Find the controller

Use `$JAZZHANDS_CONTROLLER` if set. Otherwise ask the user for the controller's host
name; don't guess. Below, `<controller>` is that name. If `lease-run` is on `PATH`,
prefer it: it automates this loop. Everything below works without it.

```sh
C=${JAZZHANDS_CONTROLLER:?ask the user which Jazzhands controller to use}
ssh lease@$C pools            # human-readable: every pool, its limits, free slots now
ssh lease@$C pools --json     # one JSON object per line, one line per pool
ssh lease@$C help             # the verbs (exits 64, as usage does)
```

`pools` shows each pool's guest OS, vCPUs and memory, the longest lease and renewal, the
snapshot policy and **how many slots are free now**. Sizes, limits and pools change;
read them each time, never hard-code them. In `--json`: `.pool`, `.guests[].os`,
`.guests[].cpu`, `.guests[].memoryMiB`, `.maxMinutes`, `.renewMinutes`,
`.snapshots` (null when none), `.now.free`.

Pick a pool by guest OS first, then by free slots; among equals, leave the pool with the
longest leases for jobs that need them. Different pools can share the same
machines, so one pool's lease can take another's free slot.

## The loop

**Release on every path**, failure included. Run it as one script with a `trap`, or step
by step. Step by step, mind that an agent's shell usually doesn't survive between tool
calls: keep the state in files (`$W/env` below), source it at each step, and never set
a `trap ... EXIT` there, or the lease is released when that one call's shell exits.

### 1. A key for this task alone

```sh
C=${JAZZHANDS_CONTROLLER:?or the controller the user named}
W=$(mktemp -d); echo "$W"            # task dir: key, ssh config, state
ssh-keygen -q -t ed25519 -N '' -C "lease-$(basename "$PWD")" -f "$W/key"
printf 'C=%s\nW=%s\n' "$C" "$W" > "$W/env"   # every later step starts with: . <W>/env
```

Step by step, start each later call with `W=<the path it printed>; . "$W/env"`.

Never request with your main key (`~/.ssh/id_ed25519.pub`): a long-running tool that
leases with the same key can adopt leases made with it. A key made for the task is
yours alone, and is thrown away with `$W`.

### 2. Request

```sh
. "$W/env"
ssh lease@$C request --pool <pool> --minutes 60 --wait 5m < "$W/key.pub" > "$W/lease"
echo "exit $?"; cat "$W/lease"
```

It prints `<lease> on <hand> until <time> (Nm left)`, then an ssh line with the login
(`root@<lease>` on Linux, `admin@<lease>` on a Mac). Ask for the time the job needs
plus margin, not the maximum. Unsure how long it will run there? Scale the local time by
local cores over guest cores, add setup (toolchain, services: often minutes), and renew
if it runs long.

Exit codes are sysexits: **75** no slot now (temporary: wait, retry later, or run
locally), **64** usage (wrong flag, minutes beyond the pool's limit), **69** unavailable.
`--wait 5m` waits for a slot before giving up with 75.

### 3. Reach it

The printed ssh line doesn't name your key. Write a config once per task (used only
with `-F`, so `Host *` touches nothing else):

```sh
. "$W/env"
L=$(awk 'NR==1{print $1}' "$W/lease")
cat > "$W/ssh_config" <<EOF
Host *
  ProxyCommand ssh lease@$C connect %n
  IdentityFile $W/key
  IdentitiesOnly yes
  UserKnownHostsFile $W/known_hosts
  StrictHostKeyChecking accept-new
  BatchMode yes
  ConnectTimeout 10
  ControlMaster auto
  ControlPath ${XDG_RUNTIME_DIR:-$HOME/.ssh}/lease-%C
  ControlPersist 10m
EOF
printf 'L=%s\nG="ssh -F %s/ssh_config root@%s"\nexport RSYNC_RSH="ssh -F %s/ssh_config"\n' \
  "$L" "$W" "$L" "$W" >> "$W/env"     # root@ on Linux; admin@ for a Mac lease
. "$W/env"
for i in $(seq 30); do $G true 2>/dev/null && break; sleep 2; done
```

As one script, also release on exit (step 8):

```sh
trap '$G "systemctl poweroff" || true; ssh -F "$W/ssh_config" -O exit "root@$L" 2>/dev/null || true' EXIT
```

A fresh guest refuses ssh for seconds to tens of seconds after `request` returns: retry, as above.
Each new proxied connection costs a few seconds; `ControlMaster` makes the rest free.
`ControlPath` must stay short (a Unix socket path, under about 100 bytes), hence `%C`.

### 4. Look before you assume

Guests are minimal and differ by pool. Check, don't assume:

```sh
$G '. /etc/os-release; echo "$NAME $VERSION_ID"; nproc; free -g | sed -n 2p   # Linux
    for t in rsync git make gcc python3 go node docker nix apt-get brew; do
      printf "%s=%s\n" "$t" "$(command -v "$t" || echo -)"; done
    curl -sS -m 10 -o /dev/null -w "net %{http_code}\n" https://github.com || echo "no net yet"'
$G 'sw_vers; sysctl -n hw.ncpu hw.memsize; xcode-select -p; command -v brew git'   # Mac
```

Networking can lag ssh by a few seconds on a fresh guest; retry before concluding there
is none. Then bring what's missing:

- **Ubuntu/Debian guests:** `DEBIAN_FRONTEND=noninteractive apt-get install -y -qq <pkg>`
  (run `apt-get update -qq` first). Distro versions lag; when the project pins a
  version, fetch that release's tarball instead.
- **NixOS guests:** `nix --extra-experimental-features 'nix-command flakes' shell
  nixpkgs#go nixpkgs#gnumake -c <cmd>`, or the project's own flake/devshell.
- **macOS guests:** Xcode command-line tools may or may not be there; check `xcode-select
  -p` and `brew` before planning around them.
- **Your exact local toolchain:** for a project managed by mise/asdf, install that
  manager in the guest, then the pinned versions from the project's version file,
  rather than using whatever the guest has.
- **Services the tests need** (a database, Redis, a browser): install and start them in
  the guest (`apt-get install mysql-server redis-server; systemctl start …`), and point
  the test config at them the way the project's CI does. Its CI config is the best
  recipe for a clean box.
- **No network at all:** copy static binaries or tarballs in with the sync below.
- **Or skip the guest's toolchain:** a compiled project can often cross-build locally
  (`GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build`, `cargo build --target …`) and sync
  just the binary and its inputs. Building is usually the cheap part; running is not.

If one setup will serve many leases and the pool keeps snapshots, set up once, then
`ssh lease@$C snapshot --label <label> <lease>`, power the guest off, and later
`request --from <label>` starts from it. Snapshots are per person and expire; list and
drop yours with `snapshots` / `drop-snapshot`.

### 5. Sync the work in

Into `work/` under the login's home (`/root/work`, or the admin's home on a Mac, whose
root filesystem is read-only). Send tracked files only: it skips build output, and `.env` and other untracked secrets.

```sh
. "$W/env"                            # sets RSYNC_RSH to the task's ssh config
git ls-files -z --recurse-submodules | rsync -a --from0 --files-from=- ./ "root@$L:work/"
# no rsync in the guest: tar over ssh; --no-recursion so a listed directory (a
# submodule) can't pull in its untracked or ignored files
git ls-files -z --recurse-submodules | tar --null --no-recursion -T - -cf - |
  $G 'mkdir -p work && tar -xf - -C work'
```

Uncommitted edits to tracked files go with it; new files need `git add` (or `git ls-files
-co --exclude-standard` for untracked-but-not-ignored). Check that nothing secret is
tracked before you send it.

### 6. Run, sized to the guest

Set parallelism from the guest's `nproc`, not your machine's:
`make -j"$(nproc)"` (`sysctl -n hw.ncpu` on a Mac), `xargs -P "$(nproc)"`, `PARALLEL_WORKERS=$(nproc)`, `go test -p`,
`pytest -n`, `--jobs`. A benchmark is the exception: run it serially (below). If each run is itself
multi-threaded, divide: time one run first, then set the fan-out so runs × threads ≈
cores.

A job longer than a few minutes must not hang on one ssh session or one tool call.
Detach it in the guest, then poll:

```sh
$G 'cd work && nohup sh -c "<cmd>; echo \$? > exit.status" > job.log 2>&1 < /dev/null &'
$G 'cd work && { cat exit.status 2>/dev/null || tail -n 3 job.log; }'   # repeat every minute or so
```

### 7. Copy results back

```sh
. "$W/env"; rsync -a "root@$L:work/out/" ./results/          # or: $G 'tar -cf - -C work/out .' | tar -xf - -C ./results
```

Bring back the log and exit status too, into a directory outside the repo's tracked
files (or where the user asks). Report the guest's OS, cores and the timings
alongside the results, so a reader knows where they came from.

### 8. Release

`. "$W/env"; $G 'systemctl poweroff'` in a Linux guest, `sudo shutdown -h now` in a Mac one; a
dropped connection or non-zero exit from poweroff is expected. Step by step, run it
yourself when the job ends **or fails**, before reporting either. Then confirm:
`ssh lease@$C list --pool <pool>` (it needs `--pool`) no longer shows your lease; give it
ten seconds or so after poweroff.
Expiry would release it eventually; don't leave it to expiry.

## Etiquette

- Request what the job needs; `renew` rather than over-asking.
  `ssh lease@$C renew --minutes N <lease>` sets the lease to end N minutes from now, up
  to the pool's renewal limit, so it extends only when N exceeds what's left.
- Release the moment you're done; one lease per job, not one held for the session.
- `list` shows everyone's leases. **Touch only leases you created.**
- On 75, wait (`--wait`) or run locally. Don't loop `request` in a tight retry.
- Parallelise inside one big guest before taking several.

## Benchmarks

A guest is a VM on a shared machine: fresh, but not quiet. For a benchmark:
take a fresh lease (that's the clean box), pick the distro the app targets, install
only what the app needs, and say what "cold" means: a fresh process each run, and, if
the page cache should be cold too, `sync; echo 3 > /proc/sys/vm/drop_caches` before each
(root can, in a Linux guest). Then N runs **serially**, recording each; report median and spread, the
guest's OS/vCPU/memory, and that it ran in a VM. Don't snapshot a benchmark box: a
snapshot is a prepared box, not a clean one. Never compare those numbers with ones
from a different pool or machine as if they were the same environment.

## Mac leases

Same loop, with `admin@<lease>`, `sudo shutdown -h now` to release, and fewer slots, so
keep them short. Screen Sharing: add `-L 5901:127.0.0.1:5900` to the ssh command, then
open `vnc://localhost:5901`.

## References

- `references/evals.md`: the eval prompts this skill is tested against, what a good
  answer does for each, and the failure modes found so far.
