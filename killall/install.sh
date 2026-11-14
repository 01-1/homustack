#!/usr/bin/env bash
# Links agents-killall and ka into ~/.local/bin. Stops and starts nothing.
# Refuses without a valid ~/.config/agents-killall/config.toml: there are no built-in
# targets, and replacing a working stop with one that has no config would leave ka
# stopping nothing.
#   install.sh [BIN_DIR]   (default ~/.local/bin)
set -euo pipefail
here=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")
# The same path agents-killall uses: the home directory from the password database.
config="$(getent passwd "$(id -u)" | cut -d: -f6)/.config/agents-killall/config.toml"
bin=${1:-$HOME/.local/bin}
if [[ ! -f $config ]]; then
    printf 'No %s. Copy config.example.toml there and edit it for this machine first.\n' "$config" >&2
    exit 2
fi
# Loads and validates the config only; inspects and signals nothing.
/usr/bin/python3 -I "$here/agents-killall" --check-config
mkdir -p -- "$bin"
stamp=$(date +%Y%m%dT%H%M%S%N)
for name in agents-killall ka; do
    if [[ -e $bin/$name || -L $bin/$name ]]; then
        mv -- "$bin/$name" "$bin/$name.bak-$stamp"   # an existing copy is kept, not overwritten
    fi
    ln -s -- "$here/$name" "$bin/$name"
done
printf 'Linked agents-killall (dry run by default) and ka (REAL stop) into %s.\n' "$bin"
printf 'Check the dry run before relying on ka: %s/agents-killall\n' "$bin"
