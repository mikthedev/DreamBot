#!/usr/bin/env bash
# Bot-hosting entrypoint: ensure Deno exists for yt-dlp YouTube challenges, then run the bot.
# Panel setting: START_BASH_FILE=start.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Host egg env names: USERNAME + ACCESS_TOKEN (labels say Git Username / Git Access Token).
# The egg's own `git pull` never uses the token — turn Auto Update OFF and let this script pull.
_git_token="${ACCESS_TOKEN:-${GIT_ACCESS_TOKEN:-}}"
_git_user="${USERNAME:-${GIT_USERNAME:-x-access-token}}"
_git_branch="${GIT_BRANCH:-${BRANCH:-main}}"
if [[ -d .git ]]; then
  if [[ -n "${_git_token}" ]]; then
    echo "[DreamBot] Pulling ${_git_branch} as ${_git_user} (token present)"
    _pull_ok=0
    if git -c "http.extraHeader=AUTHORIZATION: Bearer ${_git_token}" pull --ff-only origin "${_git_branch}"; then
      _pull_ok=1
    else
      echo "[DreamBot] Bearer pull failed, retrying with username + token URL..."
      _git_url="$(git remote get-url origin | sed -E 's#https://[^@]+@#https://#; s#git@github.com:#https://github.com/#')"
      _git_url="${_git_url#https://}"
      if git pull --ff-only "https://${_git_user}:${_git_token}@${_git_url}" "${_git_branch}"; then
        _pull_ok=1
      fi
    fi
    if [[ "${_pull_ok}" == "1" ]]; then
      echo "[DreamBot] Repo is up to date with origin/${_git_branch}"
    else
      echo "[DreamBot] git pull failed — token needs Contents: Read on mikthedev/DreamBot"
    fi
    unset _git_url _pull_ok
  else
    echo "[DreamBot] No ACCESS_TOKEN in the environment — fill Git Access Token in Variables"
  fi
fi
unset _git_token _git_user _git_branch

if command -v git >/dev/null 2>&1 && [[ -d .git ]]; then
  echo "[DreamBot] Running $(git log -1 --oneline 2>/dev/null || echo 'unknown commit')"
fi

BIN_DIR="$ROOT/.local/bin"
DENO_BIN="$BIN_DIR/deno"
mkdir -p "$BIN_DIR"

if [[ ! -x "$DENO_BIN" ]]; then
  echo "[DreamBot] Downloading Deno (needed by yt-dlp for YouTube JS challenges)..."
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64|amd64) TARGET="x86_64-unknown-linux-gnu" ;;
    aarch64|arm64) TARGET="aarch64-unknown-linux-gnu" ;;
    *)
      echo "[DreamBot] Unsupported architecture: $ARCH (YouTube may fail without Deno)"
      TARGET=""
      ;;
  esac

  if [[ -n "$TARGET" ]]; then
    # Deno >= 2.3 required by yt-dlp
    VER="v2.4.5"
    URL="https://github.com/denoland/deno/releases/download/${VER}/deno-${TARGET}.zip"
    TMP="$(mktemp -d)"
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL "$URL" -o "$TMP/deno.zip"
    else
      wget -qO "$TMP/deno.zip" "$URL"
    fi
    python3 - "$TMP" <<'PY'
import sys, zipfile
from pathlib import Path
tmp = Path(sys.argv[1])
with zipfile.ZipFile(tmp / "deno.zip") as zf:
    zf.extractall(tmp)
PY
    mv "$TMP/deno" "$DENO_BIN"
    chmod +x "$DENO_BIN"
    rm -rf "$TMP"
    echo "[DreamBot] Deno installed at $DENO_BIN"
  fi
fi

export PATH="$BIN_DIR:$PATH"
export DENO_DIR="${DENO_DIR:-$ROOT/.local/deno-cache}"

if [[ -x "$DENO_BIN" ]]; then
  echo "[DreamBot] $($DENO_BIN --version 2>&1 | head -1)"
else
  echo "[DreamBot] WARNING: Deno not available — YouTube playback may fail"
fi

PY_BIN="${PYTHON_BIN:-/usr/local/bin/python}"
if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="$(command -v python3 || command -v python)"
fi

BOT_FILE="${BOT_PY_FILE:-bot.py}"
# shellcheck disable=SC2086
exec "$PY_BIN" ${PY_START_FLAGS:-} "$ROOT/$BOT_FILE"
