#!/bin/bash
# amd-proxy rebuild dry-run: throwaway container, read-only tarball, count only.
set -u
TARBALL="/home/ubuntu/ai-team/state/amd-proxy-config-latest.tar.gz"
SHA=$(sha256sum "$TARBALL" | awk '{print $1}')
echo "tarball_sha256=$SHA"
echo "tarball_bytes=$(stat -c%s "$TARBALL")"
docker run --rm -v "$TARBALL:/tmp/amd-proxy-config.tar.gz:ro" ubuntu:22.04 bash -c 'cd /tmp && mkdir -p amd-dryrun && tar tzf amd-proxy-config.tar.gz | wc -l && tar tzf amd-proxy-config.tar.gz | grep -c fanout || true'
