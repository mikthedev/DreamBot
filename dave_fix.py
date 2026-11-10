"""
Patches for Discord DAVE (E2EE voice) so davey sessions actually become ready.

discord.py 2.7 negotiates DAVE and runs MLS, but does not always call
`DaveSession.prepare_transition` / `execute_transition`. On some hosts that
leaves `protocol=1` with `can_encrypt=False` forever → silent audio.

These patches bridge that gap and add useful logging.
"""

from __future__ import annotations

import logging
import struct

import davey
from discord.gateway import DiscordVoiceWebSocket
from discord.voice_state import VoiceConnectionState

log = logging.getLogger("dream_team.dave")

_PATCHED = False


def _session_snapshot(session: davey.DaveSession | None) -> str:
    if session is None:
        return "session=None"
    try:
        return (
            f"ready={session.ready} status={session.status} "
            f"epoch={session.epoch} can_encrypt={session.can_encrypt}"
        )
    except Exception as exc:
        return f"session_error={exc!r}"


def apply_dave_patches() -> None:
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True

    _orig_execute = VoiceConnectionState._execute_transition
    _orig_binary = DiscordVoiceWebSocket.received_binary_message
    _orig_reinit = VoiceConnectionState.reinit_dave_session

    async def reinit_dave_session(self: VoiceConnectionState) -> None:
        await _orig_reinit(self)
        log.info(
            "DAVE reinit done protocol=%s %s",
            self.dave_protocol_version,
            _session_snapshot(self.dave_session),
        )

    async def _execute_transition(self: VoiceConnectionState, transition_id: int) -> None:
        pending_version = self.dave_pending_transitions.get(transition_id)
        await _orig_execute(self, transition_id)
        session = self.dave_session
        if session is None:
            return

        version = (
            self.dave_protocol_version
            if self.dave_protocol_version
            else (pending_version or davey.DAVE_PROTOCOL_VERSION)
        )
        try:
            session.prepare_transition(transition_id, int(version))
        except Exception as exc:
            log.debug("DAVE prepare_transition(%s) skipped: %r", transition_id, exc)

        try:
            applied = session.execute_transition(transition_id)
            log.info(
                "DAVE execute_transition(%s)=%s %s",
                transition_id,
                applied,
                _session_snapshot(session),
            )
        except Exception as exc:
            log.warning(
                "DAVE execute_transition(%s) failed: %r (%s)",
                transition_id,
                exc,
                _session_snapshot(session),
            )

    async def received_binary_message(self: DiscordVoiceWebSocket, msg: bytes) -> None:
        # Peek opcode so we can finish davey transitions discord.py leaves hanging.
        op = msg[2] if len(msg) >= 3 else -1
        state = self._connection
        before = _session_snapshot(state.dave_session)

        await _orig_binary(self, msg)

        session = state.dave_session
        if session is None:
            return

        if op in (
            DiscordVoiceWebSocket.MLS_WELCOME,
            DiscordVoiceWebSocket.MLS_ANNOUNCE_COMMIT_TRANSITION,
        ):
            transition_id = struct.unpack_from(">H", msg, 3)[0] if len(msg) >= 5 else 0
            version = state.dave_protocol_version or davey.DAVE_PROTOCOL_VERSION
            try:
                session.prepare_transition(transition_id, int(version))
            except Exception as exc:
                log.debug(
                    "DAVE prepare after MLS op=%s id=%s: %r", op, transition_id, exc
                )
            # transition_id 0 means apply immediately (no EXECUTE_TRANSITION follows)
            if transition_id == 0:
                try:
                    applied = session.execute_transition(0)
                    log.info(
                        "DAVE immediate transition after MLS op=%s applied=%s %s",
                        op,
                        applied,
                        _session_snapshot(session),
                    )
                except Exception as exc:
                    log.warning(
                        "DAVE immediate execute after MLS op=%s failed: %r", op, exc
                    )
            else:
                log.info(
                    "DAVE MLS op=%s transition_id=%s before=(%s) after=(%s)",
                    op,
                    transition_id,
                    before,
                    _session_snapshot(session),
                )
        elif op == DiscordVoiceWebSocket.MLS_EXTERNAL_SENDER:
            log.info("DAVE MLS external sender set (%s)", _session_snapshot(session))
        elif op == DiscordVoiceWebSocket.MLS_PROPOSALS:
            log.info("DAVE MLS proposals handled (%s)", _session_snapshot(session))

    VoiceConnectionState.reinit_dave_session = reinit_dave_session  # type: ignore[method-assign]
    VoiceConnectionState._execute_transition = _execute_transition  # type: ignore[method-assign]
    DiscordVoiceWebSocket.received_binary_message = received_binary_message  # type: ignore[method-assign]
    log.info("DAVE readiness patches applied (davey=%s)", getattr(davey, "__version__", "?"))
