#!/usr/bin/env python3
"""Bound and observe Gateway hygiene compression; never continue uncompressed on failure."""

from pathlib import Path
import re

path = Path("/opt/hermes/gateway/run_turn_hygiene.py")
source = path.read_text(encoding="utf-8")

# A slow summary may stream for the configured hold window. Emit a heartbeat every
# 30 seconds, and an explicit completion line when the summary arrives.
wait_pattern = re.compile(
    r"(?P<prefix>        fence = attempt\.commit_fence\n)(?P<loop>        while True:\n"
    r"            if fence\.is_cancelled:\n                raise TimeoutError\n"
    r"            # Charge the idle budget from the LAST PROGRESS event, else silence can approach 2x timeout\.\n"
    r"            _hyg_waited = time\.monotonic\(\) - attempt\.wait_started\n"
    r"            _ceiling = effective_ceiling\(fence, hs\.total_ceiling_seconds, attempt\.wait_started\)\n"
    r"            _slice = min\(max\(hs\.timeout_seconds - fence\.seconds_since_progress\(\), 0\.005\),\n"
    r"                         max\(_ceiling - _hyg_waited, 0\.005\)\)\n)"
)
wait_replacement = (
    "        fence = attempt.commit_fence\n"
    "        _last_progress_log = 0.0\n"
    "        while True:\n"
    "            if fence.is_cancelled:\n                raise TimeoutError\n"
    "            # Charge the idle budget from the LAST PROGRESS event, else silence can approach 2x timeout.\n"
    "            _hyg_waited = time.monotonic() - attempt.wait_started\n"
    "            _ceiling = effective_ceiling(fence, hs.total_ceiling_seconds, attempt.wait_started)\n"
    "            _slice = min(max(hs.timeout_seconds - fence.seconds_since_progress(), 0.005),\n"
    "                         max(_ceiling - _hyg_waited, 0.005))\n"
)
source, wait_count = wait_pattern.subn(wait_replacement, source, count=1)
if wait_count != 1:
    raise SystemExit("pinned Hermes wait loop did not match; refusing an unobserved compression wait")

poll_pattern = """                _idle = fence.seconds_since_progress()
                # Never hold the TURN past the budget even while the summary streams: proceed on the
"""
poll_replacement = """                _idle = fence.seconds_since_progress()
                if _hyg_waited - _last_progress_log >= 30.0:
                    logger.info(
                        "Session hygiene compression progress for session %s: waited %.0fs, "
                        "last token progress %.1fs ago (progress observed=%s)",
                        session_entry.session_id, _hyg_waited, _idle, fence.progress_observed,
                    )
                    _last_progress_log = _hyg_waited
                # Never hold the TURN past the budget even while the summary streams: proceed on the
"""
if source.count(poll_pattern) != 1:
    raise SystemExit("pinned Hermes progress point did not match exactly once")
source = source.replace(poll_pattern, poll_replacement, 1)

success_pattern = """                _compressed, _ = await asyncio.wait_for(asyncio.shield(attempt.future), timeout=_slice)
                return _compressed
"""
success_replacement = """                _compressed, _ = await asyncio.wait_for(asyncio.shield(attempt.future), timeout=_slice)
                logger.info(
                    "Session hygiene compression completed for session %s after %.1fs",
                    session_entry.session_id, time.monotonic() - attempt.wait_started,
                )
                return _compressed
"""
if source.count(success_pattern) != 1:
    raise SystemExit("pinned Hermes completion point did not match exactly once")
source = source.replace(success_pattern, success_replacement, 1)

# The compressor can report a summary failure without raising. In that case the
# Gateway otherwise continues with unchanged history; make that a failed turn too.
abort_pattern = """                await self._hmwa_hygiene_notify(
                    source, attempt.meta, t("gateway.compress.hygiene_failed"), "compression-failure warning",
                )
        # Configured aux model failed, recovered on the main model: only the user can fix that config.
"""
abort_replacement = """                await self._hmwa_hygiene_notify(
                    source, attempt.meta, t("gateway.compress.hygiene_failed"), "compression-failure warning",
                )
            logger.error("Session hygiene summary aborted; refusing to continue this turn uncompressed")
            raise RuntimeError("session_hygiene_compression_failed")
        # Configured aux model failed, recovered on the main model: only the user can fix that config.
"""
if source.count(abort_pattern) != 1:
    raise SystemExit("pinned Hermes summary-abort point did not match exactly once")
source = source.replace(abort_pattern, abort_replacement, 1)

# Pinned v0.23.1 catches both hold expiry and compression errors, then sends the
# bounded uncompressed transcript onward. Re-raise fixed, non-sensitive markers
# so the worker preserves a checkpoint and routes the task to human review.
handler_pattern = re.compile(
    r"        except HygieneTurnHoldExceeded:\n"
    r"(?:            .*\n)*?            pass\n"
    r"        except Exception as e:\n"
    r"            logger\.warning\(\"Session hygiene auto-compress failed: %s\", e\)"
)
handler_replacement = (
    "        except HygieneTurnHoldExceeded as exc:\n"
    "            logger.error(\"Session hygiene checkpoint wait exceeded its turn-hold limit\")\n"
    "            raise RuntimeError(\"session_hygiene_checkpoint_wait_exceeded\") from exc\n"
    "        except Exception as exc:\n"
    "            logger.warning(\"Session hygiene compression failed (%s)\", type(exc).__name__)\n"
    "            raise RuntimeError(\"session_hygiene_compression_failed\") from exc"
)
source, handler_count = handler_pattern.subn(handler_replacement, source, count=1)
if handler_count != 1:
    raise SystemExit("pinned Hermes failure handler did not match; refusing fail-open behavior")

path.write_text(source, encoding="utf-8", newline="\n")
