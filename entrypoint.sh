#!/bin/sh
set -e

# Host's ~/.ssh is bind-mounted read-only at /host-ssh (see docker-compose.yml).
# ssh refuses identity/known_hosts files not owned by the user running it, so
# they can't be used directly from a foreign-uid read-only mount — copy the
# two files the `git` action's push actually needs into a root-owned,
# container-local ~/.ssh instead of the whole host directory.
if [ -d /host-ssh ]; then
    mkdir -p /root/.ssh
    cp /host-ssh/id_ed25519 /root/.ssh/id_ed25519 2>/dev/null || true
    cp /host-ssh/known_hosts /root/.ssh/known_hosts 2>/dev/null || true
    chmod 700 /root/.ssh
    chmod 600 /root/.ssh/id_ed25519 2>/dev/null || true
    chmod 644 /root/.ssh/known_hosts 2>/dev/null || true
fi

exec "$@"
