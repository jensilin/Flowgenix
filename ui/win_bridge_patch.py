"""Windows-safe Cursor SDK bridge discovery.

cursor_sdk._bridge._read_discovery uses selectors/select on the subprocess
stderr pipe. On Windows, select() only works on sockets (WinError 10038).
Replace discovery with a threaded stderr reader when on win32.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Mapping
from typing import Any

import subprocess


def apply() -> None:
    if sys.platform != "win32":
        return

    from cursor_sdk._bridge import CursorSDKError, parse_discovery_line
    import cursor_sdk._bridge as bridge_mod

    def _read_discovery_windows(
        process: subprocess.Popen[str], timeout: float
    ) -> Mapping[str, Any]:
        if process.stderr is None:
            raise CursorSDKError("Bridge process stderr is unavailable")

        discovery_box: dict[str, Any] = {"value": None}
        lines: list[str] = []
        done = threading.Event()

        def _reader() -> None:
            try:
                assert process.stderr is not None
                for line in process.stderr:
                    lines.append(line)
                    parsed = parse_discovery_line(line)
                    if parsed is not None:
                        discovery_box["value"] = parsed
                        return
            finally:
                done.set()

        thread = threading.Thread(target=_reader, name="cursor-bridge-discovery", daemon=True)
        thread.start()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if discovery_box["value"] is not None:
                return discovery_box["value"]
            exit_code = process.poll()
            if exit_code is not None:
                # Give the reader a moment to flush remaining lines.
                done.wait(timeout=0.5)
                if discovery_box["value"] is not None:
                    return discovery_box["value"]
                raise CursorSDKError(
                    f"Bridge exited before discovery with status {exit_code}: "
                    + "".join(lines)
                )
            time.sleep(0.05)

        raise CursorSDKError("Timed out waiting for bridge discovery")

    bridge_mod._read_discovery = _read_discovery_windows

    # Async bridge shares the same select-based reader in some versions.
    try:
        import cursor_sdk._async_bridge as async_bridge_mod

        if hasattr(async_bridge_mod, "_read_discovery"):
            async_bridge_mod._read_discovery = _read_discovery_windows
    except Exception:
        pass
