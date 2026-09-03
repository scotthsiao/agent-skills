#!/usr/bin/env python3
"""
Template for a no-agent cron script.

Contract:
  - stdout is delivered verbatim as the message
  - print nothing  -> scheduler suppresses the delivery (use for "no news")
  - exit 0         -> success (delivered, or intentionally quiet)
  - exit non-zero  -> failure the scheduler should record

Copy this per job. Keep it self-contained: it receives no conversation context.
"""
import os
import sys

# --- run under the app's bundled interpreter (has the deps) -------------------
_VENV = os.environ.get("APP_VENV_PYTHON", "")
if _VENV and os.path.exists(_VENV) and \
        os.path.realpath(sys.executable) != os.path.realpath(_VENV):
    os.execv(_VENV, [_VENV, os.path.abspath(__file__)] + sys.argv[1:])

# --- config: read from env / a file, never from arguments you won't have ------
FAIL_LOUD = os.environ.get("FAIL_LOUD", "1") == "1"   # page on failure vs. skip quietly


def gather() -> str | None:
    """Do the work. Return the message text, or None to stay silent."""
    # Example: hit an API, compute something, format a short report.
    #
    #   import urllib.request, json
    #   with urllib.request.urlopen("https://api.example.com/status", timeout=20) as r:
    #       data = json.load(r)
    #   if data["incidents"] == 0:
    #       return None                      # nothing to report
    #   return f"{data['incidents']} open incident(s): " + ", ".join(data["titles"])
    raise NotImplementedError("fill in gather()")


def main() -> int:
    try:
        msg = gather()
    except Exception as e:  # noqa: BLE001
        if FAIL_LOUD:
            print(f"[{os.path.basename(__file__)}] FAILED: {type(e).__name__}: {e}")
            return 1
        # nice-to-have job with a flaky source: skip quietly
        print(f"skipped: {e}", file=sys.stderr)
        return 0

    if msg:
        print(msg)          # delivered
    # else: silent, no delivery
    return 0


if __name__ == "__main__":
    sys.exit(main())
