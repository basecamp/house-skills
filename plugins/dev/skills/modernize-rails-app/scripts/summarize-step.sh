#!/usr/bin/env bash
# Usage: summarize-step.sh OUT [SED_EXPR]  (OUT from compare.sh, both sides Rails 3+).
# Normalizes the port, timings and attribute order into OUT/norm, prints status changes, and
# lists the pages that differ. SED_EXPR (sed -E) is applied to both sides too,
# to set aside a change already accepted, such as 's/ alt="[^"]*"//g'.
# Pages where both sides return 4xx or 5xx are listed apart, with each side's
# first error heading: in development their bodies are Rails' error template,
# which changes between versions.
set -u
scripts=$(cd "$(dirname "$0")" && pwd)
out=$1 ignore=${2:-}
[ -e "$out/norm" ] && { echo "$out/norm exists; remove it first"; exit 1; }
mkdir "$out/norm"
cp -r "$out/base" "$out/new" "$out/norm/"
cd "$out/norm"
sed -i 's/3102/3101/g' new/*
sed -i -E 's/[0-9.]+ms/Nms/g' new/* base/*
ruby "$scripts/sort-attrs.rb" base new
[ -n "$ignore" ] && sed -i -E "$ignore" base/* new/*
diff -r -w -B base new > diff.txt
echo "normalized diff lines: $(wc -l < diff.txt)"
for f in base/*; do n=new/${f#base/}; cmp -s <(head -1 "$f") <(head -1 "$n") || printf "STATUS %-30s %s | %s\n" "${f#base/}" "$(head -1 "$f")" "$(head -1 "$n")"; done
# Rails 7.1's template has no h1 or h2; its heading is the first line of the body.
heading() {
  rg -U -o '<h[12][^>]*>[^<]*</h[12]>' "$1" | tr -s ' \n' ' ' | sed -E 's/<\/h[12]>.*//; s/<h[12][^>]*> ?//; s/ $//' | rg . ||
    sed -n '/<body>/,$p' "$1" | sed '1d' | rg -m1 -v '^\s*$' | sed -E 's/^\s+//'
}
rg -l . base | sort | while read f; do
  n=new/${f#base/}
  cmp -s "$f" "$n" && continue
  if head -1 "$f" | rg -q '^[45]' && head -1 "$n" | rg -q '^[45]'; then
    a=$(heading "$f") b=$(heading "$n")
    if [ "$a" = "$b" ]; then
      echo "DIFFERS (error page, same error) ${f#base/}: $a"
    else
      echo "DIFFERS (error page) ${f#base/}: $a | $b"
    fi
  else
    echo "DIFFERS ${f#base/}"
  fi
done
