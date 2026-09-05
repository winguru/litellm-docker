#!/bin/sh
# serena/init-projects.sh
#
# One-shot job that runs before the serena-mcp service:
#   1. seeds the serena config volume from serena/serena_config.yml
#      (only on first run; runtime changes are preserved afterwards)
#   2. clones / updates the git repositories listed in SERENA_GIT_REPOS
#      into the shared /workspaces/projects volume
#
# This is the recommended setup when the LiteLLM stack runs on a different
# machine than your code: instead of bind-mounting a local directory, the
# code is fetched with git inside the Docker network.
#
# Env vars:
#   SERENA_GIT_REPOS  space-separated git URLs, optionally "<url>|<branch>"
#                     e.g. "https://github.com/org/repo.git https://github.com/org/other.git|develop"
#   SERENA_GIT_TOKEN  optional token (e.g. GitHub PAT) injected into https
#                     GitHub URLs to clone private repositories
#   SERENA_GIT_RESET  "true" to discard local edits in the projects volume
#                     on every run (default: keep them)
set -eu

PROJECTS_DIR="/workspaces/projects"
CONFIG_DIR="/workspaces/serena/config"
CONFIG_SRC="/opt/serena_config.yml"

# --- 1. Seed serena config on first run -------------------------------------
if [ -f "$CONFIG_SRC" ]; then
  if [ -f "$CONFIG_DIR/serena_config.yml" ]; then
    echo "[init] serena_config.yml already present, keeping existing file"
  else
    mkdir -p "$CONFIG_DIR"
    cp "$CONFIG_SRC" "$CONFIG_DIR/serena_config.yml"
    echo "[init] seeded serena_config.yml into config volume"
  fi
fi

# --- 2. Clone / update projects ----------------------------------------------
if [ -z "${SERENA_GIT_REPOS:-}" ]; then
  echo "[init] SERENA_GIT_REPOS is empty - nothing to clone"
  exit 0
fi

RESET="${SERENA_GIT_RESET:-false}"
TOKEN="${SERENA_GIT_TOKEN:-}"

mkdir -p "$PROJECTS_DIR"

for spec in $SERENA_GIT_REPOS; do
  url="${spec%%|*}"
  branch="${spec#*|}"
  [ "$branch" = "$spec" ] && branch=""

  # destination directory name: last path segment of the URL minus ".git"
  name="${url##*/}"
  name="${name%.git}"
  dest="$PROJECTS_DIR/$name"

  clone_url="$url"
  if [ -n "$TOKEN" ]; then
    case "$url" in
      https://github.com/*)
        clone_url="https://x-access-token:${TOKEN}@${url#https://}"
        ;;
    esac
  fi

  if [ ! -d "$dest/.git" ]; then
    echo "[init] cloning $url -> $dest"
    if [ -n "$branch" ]; then
      git clone --branch "$branch" "$clone_url" "$dest"
    else
      git clone "$clone_url" "$dest"
    fi
  else
    echo "[init] updating $dest"
    git -C "$dest" fetch --all --prune
    if [ "$RESET" = "true" ]; then
      echo "[init] SERENA_GIT_RESET=true: discarding local changes in $dest"
      git -C "$dest" reset --hard
      git -C "$dest" clean -fd
    fi
    if [ -n "$branch" ]; then
      git -C "$dest" checkout "$branch" 2>/dev/null \
        || echo "[init] warn: could not checkout branch '$branch' in $dest" >&2
      git -C "$dest" pull --ff-only origin "$branch" 2>/dev/null \
        || echo "[init] warn: could not fast-forward $dest (local changes?)" >&2
    else
      git -C "$dest" pull --ff-only 2>/dev/null \
        || echo "[init] warn: could not fast-forward $dest (local changes?)" >&2
    fi
  fi
done

echo "[init] serena projects are ready"
