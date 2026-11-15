"""Background scheduler: starts due local schedules and retries, polls platform status,
keeps the PC awake before a scheduled publication and (optionally) registers a Windows
wake-up task for the next one."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from videostudio.db import AppSetting, utcnow
from videostudio.power import AwakeHold, schedule_wake

from .service import PublishService

log = logging.getLogger(__name__)

AWAKE_BEFORE = timedelta(minutes=10)
WAKE_LEAD = timedelta(minutes=2)
EXPIRY_CHECK_EVERY = timedelta(hours=12)

DESKTOP_KEY = "desktop"
DEFAULT_DESKTOP = {"close_to_tray": True, "autostart": False, "start_minimized": False,
                   "wake_for_schedule": False, "keep_awake": True, "notifications": True}


def desktop_prefs(sf: sessionmaker[Session]) -> dict[str, Any]:
    with sf() as s:
        row = s.get(AppSetting, DESKTOP_KEY)
        return {**DEFAULT_DESKTOP, **(row.value if row else {})}


def set_desktop_prefs(sf: sessionmaker[Session], patch: dict[str, Any]) -> dict[str, Any]:
    clean = {k: bool(v) for k, v in patch.items() if k in DEFAULT_DESKTOP}
    with sf.begin() as s:
        row = s.get(AppSetting, DESKTOP_KEY)
        value = {**DEFAULT_DESKTOP, **(row.value if row else {}), **clean}
        if row is None:
            s.add(AppSetting(key=DESKTOP_KEY, value=value))
        else:
            row.value = value
    return value


class Scheduler:
    def __init__(self, publish: PublishService, sf: sessionmaker[Session], tmp_dir: Path,
                 interval_s: float = 10.0):
        self.publish = publish
        self.sf = sf
        self.tmp_dir = tmp_dir
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._awake = AwakeHold()
        self._wake_for: datetime | None = None
        self._last_expiry_check: datetime | None = None
        self.last_tick: dict[str, Any] = {}

    def start(self) -> None:
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._main, name="publish-scheduler",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self._thread = None

    def _main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while not self._stop.is_set():
                try:
                    self.last_tick = loop.run_until_complete(self.tick_async())
                except Exception:  # noqa: BLE001 - keep the scheduler alive
                    log.exception("Scheduler tick failed")
                self._stop.wait(self.interval_s)
        finally:
            self._awake.set(False)
            loop.close()

    def tick(self, now: datetime | None = None) -> dict[str, Any]:
        return asyncio.run(self.tick_async(now))

    async def tick_async(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or utcnow()
        prefs = desktop_prefs(self.sf)
        out: dict[str, Any] = {"ts": now.isoformat(), "started": [], "polled": {}}
        paused = self.publish.queue_state()["paused"]
        if not paused:
            out["started"] = self.publish.due(now)
        for pid in self.publish.to_poll(now):
            out["polled"][pid] = await self.publish.poll(pid)
        nxt = self.publish.next_local_schedule()
        want_awake = prefs.get("keep_awake", True) and not paused and (
            self.publish.busy() or (nxt is not None and nxt - now <= AWAKE_BEFORE))
        self._awake.set(bool(want_awake), "scheduled publication")
        out["awake"] = bool(want_awake)
        target = (nxt - WAKE_LEAD) if (nxt and prefs.get("wake_for_schedule")) else None
        if target != self._wake_for:
            status = schedule_wake(target, self.tmp_dir)
            log.info("Wake task %s for %s", status, target)
            self._wake_for = target
        if self._last_expiry_check is None or now - self._last_expiry_check > \
                EXPIRY_CHECK_EVERY:
            self._last_expiry_check = now
            for acc in self.publish.social.expiring_accounts():
                self.publish.notify(level="warning", title=f"Tài khoản {acc['name']}",
                                    message=acc["warning"], link="/settings?tab=social")
        return out
