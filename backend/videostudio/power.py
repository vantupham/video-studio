"""Keep the computer awake while rendering/publishing (Windows); no-op elsewhere.

``SetThreadExecutionState`` is per thread: the request lasts until the same thread
clears it or exits, so :func:`keep_awake` must be entered and left on one thread.
Optionally a Windows Task Scheduler task with "wake the computer to run this task" is
created for the next scheduled publication."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040

_local = threading.local()
WAKE_TASK_NAME = "VideoStudio\\WakeForPublish"


def _set_state(flags: int) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes

    try:
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        log.debug("SetThreadExecutionState failed", exc_info=True)
        return False


@contextmanager
def keep_awake(reason: str = "") -> Iterator[bool]:
    """Prevent system sleep (the display may still turn off). Re-entrant per thread."""
    depth = getattr(_local, "depth", 0)
    ok = True
    if depth == 0:
        ok = _set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        if ok:
            log.info("Keeping the system awake: %s", reason)
    _local.depth = depth + 1
    try:
        yield ok
    finally:
        _local.depth -= 1
        if _local.depth == 0:
            _set_state(ES_CONTINUOUS)


class AwakeHold:
    """Hold/release from one long-lived thread (the scheduler)."""

    def __init__(self) -> None:
        self.held = False

    def set(self, want: bool, reason: str = "") -> None:
        if want and not self.held:
            self.held = _set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) or sys.platform != "win32"
            log.info("Awake hold on (%s)", reason)
        elif not want and self.held:
            _set_state(ES_CONTINUOUS)
            self.held = False


def wake_task_xml(when: datetime, exe: str = "cmd.exe", args: str = "/c exit 0") -> str:
    """Task Scheduler XML that only wakes the PC at ``when``. The running app (in the tray)
    then holds the system awake until the due publication is done; launching a second
    app instance from the task would start a second backend."""
    start = when.astimezone().replace(tzinfo=None, microsecond=0).isoformat()
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Video Studio: đánh thức máy để đăng video đã hẹn giờ
  </Description></RegistrationInfo>
  <Triggers><TimeTrigger><StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>
  </TimeTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType>
  <RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
  <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
  <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><WakeToRun>true</WakeToRun>
  <StartWhenAvailable>true</StartWhenAvailable><Enabled>true</Enabled>
  <ExecutionTimeLimit>PT0S</ExecutionTimeLimit></Settings>
  <Actions Context="Author"><Exec><Command>{escape(str(exe))}</Command>
  <Arguments>{escape(args)}</Arguments></Exec></Actions>
</Task>
"""


def schedule_wake(when: datetime | None, tmp_dir: Path) -> str:
    """Create/replace (or delete when ``when`` is None) the wake task. Returns a status
    string; never raises."""
    if sys.platform != "win32":
        return "unsupported"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if when is None:
            subprocess.run(["schtasks", "/Delete", "/TN", WAKE_TASK_NAME, "/F"],
                           capture_output=True, timeout=20, creationflags=flags, check=False)
            return "deleted"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        xml_path = tmp_dir / "wake-task.xml"
        xml_path.write_text(wake_task_xml(when), encoding="utf-16")
        proc = subprocess.run(["schtasks", "/Create", "/TN", WAKE_TASK_NAME, "/XML",
                               str(xml_path), "/F"], capture_output=True, timeout=20,
                              creationflags=flags, check=False)
        if proc.returncode != 0:
            log.warning("schtasks failed: %s", proc.stderr.decode(errors="replace")[-300:])
            return "error"
        return "scheduled"
    except Exception:  # noqa: BLE001
        log.warning("Creating the wake task failed", exc_info=True)
        return "error"
