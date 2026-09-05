#!/usr/bin/env bash
# Bot-hosting entrypoint — must NEVER abort before launching the bot.
# Panel: START_BASH_FILE=start.sh  |  Auto Update: OFF (this script pulls)
#
# Host logs showed: Exit code 1, Out of memory: false
# Root cause on main: Deno download under `set -e` killed the process when
# GitHub/Cloudflare failed. Music is SoundCloud-only — Deno is optional.
set +e
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "[DreamBot] entrypoint $(date -u +%Y-%m-%dT%H:%M:%SZ) pwd=$ROOT"

# --- git pull (best-effort; never block boot) ---------------------------------
if [[ "${DREAMBOT_SKIP_PULL:-}" != "1" && -d .git ]]; then
  _git_token="${ACCESS_TOKEN:-${GIT_ACCESS_TOKEN:-}}"
  _git_user="${USERNAME:-${GIT_USERNAME:-x-access-token}}"
  _git_branch="${GIT_BRANCH:-${BRANCH:-main}}"
  if [[ -n "${_git_token}" ]]; then
    echo "[DreamBot] Fetching ${_git_branch} as ${_git_user}"
    _git_url="$(git remote get-url origin 2>/dev/null | sed -E 's#https://[^@]+@#https://#; s#git@github.com:#https://github.com/#')"
    _git_url="${_git_url#https://}"
    _auth_url="https://${_git_user}:${_git_token}@${_git_url}"
    if git fetch --force "${_auth_url}" "${_git_branch}"; then
      git reset --hard FETCH_HEAD
      echo "[DreamBot] Repo reset to $(git log -1 --oneline 2>/dev/null || echo unknown)"
      unset _git_token _git_user _git_branch _git_url _auth_url
      export DREAMBOT_SKIP_PULL=1
      exec bash "$ROOT/start.sh"
    else
      echo "[DreamBot] git fetch failed — continuing with local files (token needs Contents: Read)"
    fi
    unset _git_url _auth_url
  else
    echo "[DreamBot] No ACCESS_TOKEN — skipping pull (fill Git Access Token in Variables)"
  fi
  unset _git_token _git_user _git_branch
fi

if command -v git >/dev/null 2>&1 && [[ -d .git ]]; then
  echo "[DreamBot] Running $(git log -1 --oneline 2>/dev/null || echo 'unknown commit')"
fi

# --- Deno (optional; never required to start) --------------------------------
BIN_DIR="$ROOT/.local/bin"
DENO_BIN="$BIN_DIR/deno"
mkdir -p "$BIN_DIR"

if [[ "${DREAMBOT_INSTALL_DENO:-}" == "1" ]]; then
  if [[ ! -x "$DENO_BIN" ]]; then
    echo "[DreamBot] DREAMBOT_INSTALL_DENO=1 — trying Deno download (failures are ignored)"
    ARCH="$(uname -m)"
    case "$ARCH" in
      x86_64|amd64) TARGET="x86_64-unknown-linux-gnu" ;;
      aarch64|arm64) TARGET="aarch64-unknown-linux-gnu" ;;
      *) TARGET="" ;;
    esac
    if [[ -n "$TARGET" ]]; then
      VER="v2.4.5"
      URL="https://github.com/denoland/deno/releases/download/${VER}/deno-${TARGET}.zip"
      TMP="$(mktemp -d)"
      if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$URL" -o "$TMP/deno.zip"
      else
        wget -qO "$TMP/deno.zip" "$URL"
      fi
      if [[ -f "$TMP/deno.zip" ]]; then
        python3 - "$TMP" <<'PY' && mv "$TMP/deno" "$DENO_BIN" && chmod +x "$DENO_BIN"
import sys, zipfile
from pathlib import Path
tmp = Path(sys.argv[1])
with zipfile.ZipFile(tmp / "deno.zip") as zf:
    zf.extractall(tmp)
PY
        echo "[DreamBot] Deno install attempt finished (present=$([[ -x $DENO_BIN ]] && echo yes || echo no))"
      else
        echo "[DreamBot] Deno download failed — continuing without it"
      fi
      rm -rf "$TMP"
    fi
  fi
else
  # Free disk + avoid accidental use; SoundCloud does not need Deno
  if [[ -e "$DENO_BIN" || -d "$ROOT/.local/deno-cache" ]]; then
    echo "[DreamBot] Removing unused Deno (set DREAMBOT_INSTALL_DENO=1 to keep)"
    rm -f "$DENO_BIN"
    rm -rf "$ROOT/.local/deno-cache"
  else
    echo "[DreamBot] Skipping Deno (SoundCloud-only)"
  fi
fi

export PATH="$BIN_DIR:$PATH"
export DENO_DIR="${DENO_DIR:-$ROOT/.local/deno-cache}"
export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"
# Reduce Python allocator fragmentation on small plans
export PYTHONMALLOC="${PYTHONMALLOC:-malloc}"

# --- deps (best-effort; egg may already have installed requirements.txt) ------
PY_BIN="${PYTHON_BIN:-/usr/local/bin/python}"
if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="$(command -v python3 || command -v python || true)"
fi
if [[ -z "${PY_BIN}" ]]; then
  echo "[DreamBot] FATAL: no python binary found" >&2
  exit 1
fi

echo "[DreamBot] Python: $("$PY_BIN" -V 2>&1) at $PY_BIN"

# Optional voice-recv (DAVE decrypt). Must NOT live in requirements.txt — the host
# egg runs pip install -r requirements.txt before start.sh, and a failed git+https
# fetch (Cloudflare/GitHub) aborts the whole server with exit code 1.
VOICE_RECV_SPEC="discord-ext-voice-recv @ git+https://github.com/porgeeratad/discord-ext-voice-recv.git@dave-decrypt"
if ! "$PY_BIN" -c "from discord.ext import voice_recv" 2>/dev/null; then
  echo "[DreamBot] Installing optional voice-recv (failures ignored — /join listen may degrade)"
  "$PY_BIN" -m pip install --user -q "$VOICE_RECV_SPEC" \
    || "$PY_BIN" -m pip install -q "$VOICE_RECV_SPEC" \
    || echo "[DreamBot] voice-recv install skipped"
fi

# Quick import probe — print the real error instead of a silent egg crash
if ! "$PY_BIN" - <<'PY'
import sys
print("[DreamBot] probing imports…", flush=True)
missing = []
for name in ("discord", "aiohttp", "dotenv", "PIL"):
    try:
        __import__(name if name != "dotenv" else "dotenv")
    except Exception as exc:
        missing.append(f"{name}: {exc}")
if missing:
    print("[DreamBot] missing deps:", "; ".join(missing), flush=True)
    sys.exit(2)
print("[DreamBot] core imports ok", flush=True)
PY
then
  echo "[DreamBot] Core imports failed — trying pip install -r requirements.txt"
  "$PY_BIN" -m pip install --user -q -r "$ROOT/requirements.txt" \
    || "$PY_BIN" -m pip install -q -r "$ROOT/requirements.txt" \
    || echo "[DreamBot] pip install failed — will still try to launch"
fi

# Memory snapshot before launch
"$PY_BIN" - <<'PY' 2>/dev/null || true
import resource
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(f"[DreamBot] pre-launch RSS ~{rss:.0f} MB", flush=True)
PY

BOT_FILE="${BOT_PY_FILE:-bot.py}"
if [[ ! -f "$ROOT/$BOT_FILE" ]]; then
  echo "[DreamBot] FATAL: $ROOT/$BOT_FILE not found" >&2
  ls -la "$ROOT" >&2
  exit 1
fi

echo "[DreamBot] exec $PY_BIN $ROOT/$BOT_FILE"
# shellcheck disable=SC2086
exec "$PY_BIN" ${PY_START_FLAGS:-} "$ROOT/$BOT_FILE"
