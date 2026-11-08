"""Keep the Mac awake while Tilia runs: `caffeinate -i -w <our pid>` on AC power, and on
battery only if the person chose that. Never `-d`: the screen is allowed to sleep."""
from __future__ import annotations

import os
import subprocess
import sys


def on_ac(run=subprocess.run) -> bool:
    """True if `pmset -g batt` says the Mac is drawing from AC power. Unknown → False,
    so the person's battery choice is respected."""
    try:
        r = run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=10)
        return "'AC Power'" in (r.stdout or "")
    except Exception as e:
        print(f"tilia: pmset failed: {type(e).__name__}", file=sys.stderr)
        return False


class Power:
    def __init__(self, battery_awake: bool, popen=subprocess.Popen, pid: int | None = None):
        self.battery_awake = battery_awake
        self._popen = popen
        self._pid = pid if pid is not None else os.getpid()
        self._proc = None

    def _held(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def update(self, on_ac: bool) -> None:
        want = on_ac or self.battery_awake
        if want and not self._held():
            # -i: no idle sleep. -w: exits by itself when this process dies.
            self._proc = self._popen(["caffeinate", "-i", "-w", str(self._pid)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif not want and self._held():
            self.release()

    def release(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except Exception:
                pass
        self._proc = None
