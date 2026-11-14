#!/bin/bash
# Install fix-ledger for one repository.
#   ./install.sh REPO [--dest DIR] [--no-enable]
# Copies the tool to ~/.local/lib/fix-ledger and writes fix-ledger-<name>.{service,timer,path}
# (default DIR: ~/.config/systemd/user), watching the ledger and TODO files the repo's
# .fix-ledger.json names. --no-enable skips systemctl; a --dest other than the default
# implies it. Units already written for a different repository are never overwritten.
set -eu
[ $# -ge 1 ] || { echo "usage: $0 REPO [--dest DIR] [--no-enable]" >&2; exit 2; }
repo=$(realpath "$1"); shift
[ -d "$repo" ] || { echo "not a directory: $repo" >&2; exit 2; }
# These characters would need systemd escaping in the units; spaces are fine (quoted).
case "$repo$HOME" in *%*|*'"'*|*\\*|*$'\n'*) echo "unsupported character (% \" \\ or newline) in $repo or HOME" >&2; exit 2 ;; esac
default_dest="$HOME/.config/systemd/user"
dest=$default_dest; enable=1
while [ $# -gt 0 ]; do
    case "$1" in --dest) dest=${2:?--dest needs a directory}; shift 2 ;; --no-enable) enable=0; shift ;; *) echo "unknown $1" >&2; exit 2 ;; esac
done
[ "$(realpath -m "$dest")" = "$(realpath -m "$default_dest")" ] || enable=0
here=$(cd -- "$(dirname -- "$0")" && pwd)
lib="$HOME/.local/lib/fix-ledger"
name="fix-ledger-$(basename "$repo" | tr -c 'A-Za-z0-9_.\n-' '_')"
# Refuse to replace units that watch another repository with the same folder name.
if [ -f "$dest/$name.service" ] && ! grep -qxF "# fix-ledger repo: $repo" "$dest/$name.service"; then
    echo "$dest/$name.service exists for another repository; remove it first or rename this one" >&2
    exit 2
fi
install -d -m 755 "$lib" "$dest"
install -m 755 "$here/check.py" "$here/guard.py" "$here/fix-ledger-run" "$lib/"
paths=$(python3 -I -c 'import json,sys,pathlib; sys.path.insert(0,sys.argv[2]); import guard; l=guard.configure(sys.argv[1]); r=pathlib.Path(sys.argv[1]); print("\n".join("PathChanged="+str(r/p) for p in [l["ledger"],*l["todos"].values()]))' "$repo" "$here")
for kind in service timer path; do
    python3 -I -c 'import sys; t=open(sys.argv[1]).read(); print(t.replace("@REPO@",sys.argv[2]).replace("@LIB@",sys.argv[3]).replace("@NAME@",sys.argv[4]).replace("@PATHS@",sys.argv[5]), end="")' \
        "$here/fix-ledger.$kind.in" "$repo" "$lib" "$name" "$paths" > "$dest/$name.$kind"
done
echo "wrote $dest/$name.{service,timer,path}"
[ "$enable" = 1 ] || exit 0
systemctl --user daemon-reload
systemctl --user enable --now "$name.path" "$name.timer"
