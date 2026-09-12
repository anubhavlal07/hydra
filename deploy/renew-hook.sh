#!/usr/bin/env bash
set -euo pipefail

if [[ "${RENEWED_LINEAGE:-}" != */hydra ]]; then
    exit 0
fi

chgrp -R hydra /etc/letsencrypt/live /etc/letsencrypt/archive
chmod g+rx /etc/letsencrypt/live /etc/letsencrypt/archive
chmod -R g+rX /etc/letsencrypt/archive/hydra

systemctl reload hydra-dns
