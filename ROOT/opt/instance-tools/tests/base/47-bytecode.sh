#!/bin/bash
# Test: the bytecode compiled at build is still valid where it is used (ADR 0057).
#
# Read-only. compile-bytecode records each tree it compiled and the interpreter that imports
# it in /etc/vast-bytecode.tsv. Bytecode is only worth shipping if Python accepts it at
# runtime: timestamp-mode .pyc files are discarded the moment a source's mtime or size no
# longer matches the header. Venv trees are checked in place; an app tree is checked in the
# copy boot stage 36 made into $WORKSPACE, which must keep the sources' timestamps. Modules
# the app imported may have been rewritten since, but most of a tree is never imported, so a
# copy that lost its timestamps still shows here as mostly invalid bytecode.
source "$(dirname "$0")/../lib.sh"

marker=/etc/vast-bytecode.tsv
[[ -f "$marker" ]] || test_skip "no $marker: this image was built before compile-bytecode (ADR 0057)"
min="${BYTECODE_MIN_VALID:-0.95}"

while IFS=$'\t' read -r py tree; do
    [[ -n "$py" ]] || continue
    where="$tree"
    if [[ "$tree" == /opt/workspace-internal/* ]]; then
        copy="${WORKSPACE:-/workspace}/${tree#/opt/workspace-internal/}"
        [[ -d "$copy" ]] && where="$copy"
    fi
    [[ -x "$py" ]] || { fail_later "$tree" "interpreter $py is missing"; continue; }
    [[ -d "$where" ]] || { fail_later "$tree" "$where is missing"; continue; }
    read -r total valid < <("$py" - "$where" <<'PY'
import importlib.util, os, struct, sys
total = valid = 0
for r, ds, fs in os.walk(sys.argv[1]):
    for f in fs:
        if not f.endswith(".py"):
            continue
        src = os.path.join(r, f)
        total += 1
        try:
            with open(importlib.util.cache_from_source(src), "rb") as fh:
                head = fh.read(16)
            st = os.stat(src)
        except OSError:
            continue
        if len(head) == 16 and head[:4] == importlib.util.MAGIC_NUMBER:
            flags, mtime, size = struct.unpack("<III", head[4:16])
            valid += (flags == 0 and mtime == int(st.st_mtime) & 0xFFFFFFFF
                      and size == st.st_size & 0xFFFFFFFF)
print(total, valid)
PY
    )
    share=$(awk -v v="$valid" -v t="$total" 'BEGIN { printf "%.3f", (t ? v / t : 1) }')
    echo "  $where: $valid/$total sources have valid bytecode ($share)"
    if awk -v s="$share" -v m="$min" 'BEGIN { exit !(s < m) }'; then
        fail_later "$where" "only $share of sources have valid bytecode (< $min)"
    fi
done < "$marker"

report_failures
test_pass "shipped bytecode is valid where it is imported"
