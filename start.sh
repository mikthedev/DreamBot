#!/usr/bin/env bash
# Bot-hosting entrypoint: pull latest code, then run the bot on a tight RAM budget.
# Panel setting: START_BASH_FILE=start.sh
# Free/starter hosts are often 256–512 MB — avoid Deno/YouTube bootstrap (music is SoundCloud only).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Host egg env names: USERNAME + ACCESS_TOKEN (labels say Git Username / Git Access Token).
# The egg's own `git pull` never uses the token — turn Auto Update OFF and let this script pull.
# Host file edits (e.g. a pasted start.sh) must be discarded or git merge aborts.
if [[ "${DREAMBOT_SKIP_PULL:-}" != "1" && -d .git ]]; then
  _git_token="${ACCESS_TOKEN:-${GIT_ACCESS_TOKEN:-}}"
  _git_user="${USERNAME:-${GIT_USERNAME:-x-access-token}}"
  _git_branch="${GIT_BRANCH:-${BRANCH:-main}}"
  if [[ -n "${_git_token}" ]]; then
    echo "[DreamBot] Fetching ${_git_branch} as ${_git_user} (token present)"
    _git_url="$(git remote get-url origin | sed -E 's#https://[^@]+@#https://#; s#git@github.com:#https://github.com/#')"
    _git_url="${_git_url#https://}"
    _auth_url="https://${_git_user}:${_git_token}@${_git_url}"
    if git fetch --force "${_auth_url}" "${_git_branch}"; then
      git reset --hard FETCH_HEAD
      echo "[DreamBot] Repo reset to $(git log -1 --oneline)"
      unset _git_token _git_user _git_branch _git_url _auth_url
      export DREAMBOT_SKIP_PULL=1
      exec bash "$ROOT/start.sh"
    else
      echo "[DreamBot] git fetch failed — token needs Contents: Read on mikthedev/DreamBot"
    fi
    unset _git_url _auth_url
  else
    echo "[DreamBot] No ACCESS_TOKEN in the environment — fill Git Access Token in Variables"
  fi
  unset _git_token _git_user _git_branch
fi

if command -v git >/dev/null 2>&1 && [[ -d .git ]]; then
  echo "[DreamBot] Running $(git log -1 --oneline 2>/dev/null || echo 'unknown commit')"
fi

BIN_DIR="$ROOT/.local/bin"
DENO_BIN="$BIN_DIR/deno"
mkdir -p "$BIN_DIR"

# Deno is optional and heavy (~90 MB binary; zip extract spikes RAM). Music is SoundCloud-only.
# Drop any leftover install so free-tier disk (often 512 MB) isn't eaten by an unused runtime.
if [[ "${DREAMBOT_INSTALL_DENO:-}" == "1" ]]; then
  if [[ ! -x "$DENO_BIN" ]]; then
    echo "[DreamBot] DREAMBOT_INSTALL_DENO=1 — downloading Deno..."
    ARCH="$(uname -m)"
    case "$ARCH" in
      x86_64|amd64) TARGET="x86_64-unknown-linux-gnu" ;;
      aarch64|arm64) TARGET="aarch64-unknown-linux-gnu" ;;
      *)
        echo "[DreamBot] Unsupported architecture: $ARCH — skipping Deno"
        TARGET=""
        ;;
    esac

    if [[ -n "$TARGET" ]]; then
      # Deno >= 2.3 required by yt-dlp YouTube challenges (unused for SoundCloud)
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
  else
    echo "[DreamBot] Using existing Deno at $DENO_BIN"
  fi
else
  if [[ -e "$DENO_BIN" || -d "$ROOT/.local/deno-cache" ]]; then
    echo "[DreamBot] Removing unused Deno install (SoundCloud-only; set DREAMBOT_INSTALL_DENO=1 to keep)"
    rm -f "$DENO_BIN"
    rm -rf "$ROOT/.local/deno-cache"
  else
    echo "[DreamBot] Skipping Deno (SoundCloud-only; set DREAMBOT_INSTALL_DENO=1 to install)"
  fi
fi

export PATH="$BIN_DIR:$PATH"
export DENO_DIR="${DENO_DIR:-$ROOT/.local/deno-cache}"
# Fewer glibc malloc arenas → lower RSS on small bot-hosting plans
export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"

if [[ -x "$DENO_BIN" ]]; then
  echo "[DreamBot] $($DENO_BIN --version 2>&1 | head -1)"
fi

PY_BIN="${PYTHON_BIN:-/usr/local/bin/python}"
if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="$(command -v python3 || command -v python)"
fi

BOT_FILE="${BOT_PY_FILE:-bot.py}"
# shellcheck disable=SC2086
exec "$PY_BIN" ${PY_START_FLAGS:-} "$ROOT/$BOT_FILE"
