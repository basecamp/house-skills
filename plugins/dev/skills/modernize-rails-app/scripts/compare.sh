#!/usr/bin/env bash
# Usage: SMOKE=path/to/smoke.rb compare.sh BASE_DIR BASE_CMD NEW_DIR NEW_CMD OUT
# Starts both servers on ports 3101 and 3102, saves the pages the smoke client
# fetches from each, and diffs them. OUT must not exist yet. Exits nonzero
# when a port is already in use, a server does not start, a smoke run fails,
# or diff fails; stops only the servers it started.
set -u
: "${SMOKE:?set SMOKE to the smoke client for this app (see smoke.example.rb)}"
base_dir=$1 base_cmd=$2 new_dir=$3 new_cmd=$4 out=$5

port_open() { (echo > /dev/tcp/127.0.0.1/$1) 2>/dev/null; }
for port in 3101 3102; do
  if port_open $port; then echo "port $port is in use; stop what holds it first"; exit 1; fi
done
mkdir -p "$out"

# Each server runs in its own process group, so stopping the group stops
# any workers it forked and nothing else.
start() {
  setsid bash -c 'cd "$1" && eval "$(mise hook-env -s bash)" && exec timeout 600 $2' _ "$1" "$2" > "$out/$3.server.log" 2>&1 &
  echo $!
}
stop() {
  kill -INT -- "-$1" 2>/dev/null
  for i in $(seq 10); do kill -0 -- "-$1" 2>/dev/null || return 0; sleep 1; done
  kill -KILL -- "-$1" 2>/dev/null
}
wait_port() {
  for i in $(seq 90); do port_open $1 && return 0; sleep 1; done; return 1
}

pid1=$(start "$base_dir" "$base_cmd" base)
pid2=$(start "$new_dir" "$new_cmd" new)
trap 'stop "$pid1"; stop "$pid2"' EXIT
failed=""
wait_port 3101 || failed="$failed base"
wait_port 3102 || failed="$failed new"
if [ -n "$failed" ]; then
  echo "server did not start:$failed; see $out/*.server.log"
  exit 1
fi
ruby "$SMOKE" 3101 "$out/base"; base_status=$?
ruby "$SMOKE" 3102 "$out/new"; new_status=$?
if [ $base_status -ne 0 ] || [ $new_status -ne 0 ]; then
  echo "smoke client failed: base exit $base_status, new exit $new_status"
  exit 1
fi
diff -r "$out/base" "$out/new" > "$out/diff.txt"
if [ $? -gt 1 ]; then echo "diff failed; see $out/diff.txt"; exit 1; fi
echo "diff lines: $(wc -l < "$out/diff.txt")"
