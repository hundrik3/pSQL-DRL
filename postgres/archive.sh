#!/bin/sh
# Publish only complete, durable segments; never overwrite a different segment.
set -eu
umask 077
source_path=$1
segment=$2
case "$segment" in *[!A-Za-z0-9.]*|'') exit 2 ;; esac
destination=/repository/wal/$segment
if [ -f "$destination" ]; then
    cmp -s "$source_path" "$destination" || exit 1
    sync -f "$destination"
    sync -f /repository/wal
    exit 0
fi
temporary=$destination.tmp.$$
trap 'rm -f "$temporary"' EXIT HUP INT TERM
cp "$source_path" "$temporary"
sync -f "$temporary"
# Hard-link publication is atomic and refuses to replace an existing name.
if ! ln "$temporary" "$destination"; then
    cmp -s "$source_path" "$destination" || exit 1
fi
sync -f /repository/wal
