#!/usr/bin/env bash
# Cross-platform smoke check: forbid non-portable APIs in web/.
# Fails if any forbidden pattern (os.fork, signal.SIGWINCH, /proc/, fcntl.)
# is found in the listed directories.
set -e
PATTERNS='os\.fork|signal\.SIGWINCH|/proc/|fcntl\.'
DIRS="web/"
fail=0
for d in $DIRS; do
  [ -d "$d" ] || continue
  if grep -rE "$PATTERNS" "$d" 2>/dev/null; then
    echo "FAIL: non-portable API found in $d" >&2
    fail=1
  fi
done
[ $fail -eq 0 ] && echo "OK: no non-portable APIs in $DIRS"
exit $fail
