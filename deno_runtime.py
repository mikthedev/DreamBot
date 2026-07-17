"""Ensure a Deno binary exists for yt-dlp YouTube JS challenges (Python-only hosts)."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import config

log = logging.getLogger("dream_team.deno")

DENO_VERSION = "v2.4.5"
_ensured: Path | None | bool = False  # False=not tried, None=failed, Path=ok


def _deno_target() -> str | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux":
        if machine in ("x86_64", "amd64"):
            return "x86_64-unknown-linux-gnu"
        if machine in ("aarch64", "arm64"):
            return "aarch64-unknown-linux-gnu"
    if system == "darwin":
        if machine in ("x86_64", "amd64"):
            return "x86_64-apple-darwin"
        if machine in ("aarch64", "arm64"):
            return "aarch64-apple-darwin"
    return None


def _export_env(deno_bin: Path) -> None:
    bin_dir = str(deno_bin.parent)
    path = os.environ.get("PATH", "")
    if bin_dir not in path.split(os.pathsep):
        os.environ["PATH"] = bin_dir + os.pathsep + path
    cache = config.BASE_DIR / ".local" / "deno-cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DENO_DIR", str(cache))


def deno_bin_path() -> Path:
    return config.BASE_DIR / ".local" / "bin" / "deno"


def _download_file(url: str, dest: Path) -> None:
    """Download url to dest; prefer curl/wget (more reliable SSL on some hosts)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    curl = shutil.which("curl")
    if curl:
        try:
            subprocess.run(
                [curl, "-fsSL", url, "-o", str(dest)],
                check=True,
                capture_output=True,
                text=True,
            )
            return
        except Exception as exc:
            errors.append(f"curl: {exc}")

    wget = shutil.which("wget")
    if wget:
        try:
            subprocess.run(
                [wget, "-qO", str(dest), url],
                check=True,
                capture_output=True,
                text=True,
            )
            return
        except Exception as exc:
            errors.append(f"wget: {exc}")

    try:
        urllib.request.urlretrieve(url, dest)
        return
    except Exception as exc:
        errors.append(f"urllib: {exc}")

    raise RuntimeError("All download methods failed: " + " | ".join(errors))


def ensure_deno() -> Path | None:
    """Return path to deno, downloading once into .local/bin if needed."""
    global _ensured
    if _ensured is not False:
        return _ensured if isinstance(_ensured, Path) else None

    dest = deno_bin_path()
    if dest.is_file() and os.access(dest, os.X_OK):
        _export_env(dest)
        log.info("Using existing Deno at %s", dest)
        _ensured = dest
        return dest

    target = _deno_target()
    log.info(
        "Deno bootstrap: system=%s machine=%s target=%s dest=%s",
        platform.system(),
        platform.machine(),
        target,
        dest,
    )
    if target is None:
        log.warning(
            "Cannot auto-install Deno on %s/%s — YouTube may fail",
            platform.system(),
            platform.machine(),
        )
        _ensured = None
        return None

    url = (
        f"https://github.com/denoland/deno/releases/download/"
        f"{DENO_VERSION}/deno-{target}.zip"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading Deno %s for YouTube (%s)…", DENO_VERSION, target)

    try:
        # Keep zip + extract on the same filesystem as dest (avoid cross-device rename).
        zip_path = dest.parent / "deno-download.zip"
        try:
            _download_file(url, zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
                log.info("Deno zip entries: %s", names[:5])
                zf.extractall(dest.parent)
            extracted = dest.parent / "deno"
            if not extracted.is_file():
                # Some zips nest the binary
                candidates = list(dest.parent.rglob("deno"))
                extracted = next((p for p in candidates if p.is_file()), None)
            if extracted is None or not extracted.is_file():
                raise FileNotFoundError("deno binary missing from release zip")
            if extracted.resolve() != dest.resolve():
                shutil.copy2(extracted, dest)
                if extracted != dest:
                    try:
                        extracted.unlink(missing_ok=True)
                    except OSError:
                        pass
            dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        finally:
            zip_path.unlink(missing_ok=True)

        # Sanity-check the binary runs
        probe = subprocess.run(
            [str(dest), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if probe.returncode != 0:
            raise RuntimeError(
                f"deno --version failed (code={probe.returncode}): "
                f"{probe.stderr or probe.stdout}"
            )
        log.info("Deno probe OK: %s", (probe.stdout or "").splitlines()[:1])
    except Exception as exc:
        log.exception("Failed to install Deno: %s", exc)
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        _ensured = None
        return None

    _export_env(dest)
    log.info("Deno installed at %s", dest)
    _ensured = dest
    return dest
