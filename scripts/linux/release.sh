#!/bin/sh
# Builds VIKI Agent's Linux .deb for a release (the linux job of .github/workflows/release.yml)
# from the repository, bind-mounted at /src. It builds the Dockerfile's `compile` image
# (manylinux_2_28, so the compiled server needs only glibc 2.28) and runs in it, as that image's
# root, run.sh's compile sequence (`compile_setup`, `compile_compile`) and then the installer
# build (`npm --prefix desktop ci`, `npm --prefix desktop run dist`), which leaves the .deb,
# `latest-linux.yml` and the release files in dist/desktop/. The two node_modules folders are
# anonymous volumes and the Python environment lives under the container's /tmp, so a host
# checkout keeps its own; every other output lands in the checkout. Needs Docker.
# Run from anywhere: sh scripts/linux/release.sh
set -eu

# A folder as the docker CLI takes it: Git Bash's `pwd -W` gives C:/... on Windows; elsewhere
# `pwd -W` fails and plain `pwd` answers.
host_path() {
  (cd "$1" && { pwd -W 2>/dev/null || pwd; })
}

here="$(host_path "$(dirname "$0")")"
repo="$(host_path "$(dirname "$0")/../..")"
# Git Bash would otherwise rewrite the container paths below (/src, /tmp/venv) as Windows ones.
export MSYS_NO_PATHCONV=1

DOCKER_BUILDKIT=1 docker build --target compile -t viki-agent-release:compile "$here"
docker run --rm --init \
  --mount "type=bind,src=$repo,dst=/src" \
  --mount type=volume,dst=/src/frontend/node_modules \
  --mount type=volume,dst=/src/desktop/node_modules \
  --env PYTHONUTF8=1 --env UV_PROJECT_ENVIRONMENT=/tmp/venv \
  --workdir /src viki-agent-release:compile sh -euc '
    uv sync --locked --no-dev --no-editable --group build
    npm --prefix frontend ci --no-audit --no-fund
    npm --prefix frontend run build
    uv run --no-sync python scripts/prepare_magpie.py
    uv run --no-sync python scripts/compile.py
    npm --prefix desktop ci --no-audit --no-fund
    npm --prefix desktop run dist
  '
