# coding=utf-8
"""
    @project: MaxKB
    @file: single_flight.py
    @desc: Bounded Redis single-flight leases with ownership-safe renewal/release.
"""
import logging
import threading

from django_redis import get_redis_connection

logger = logging.getLogger("maxkb")


class CacheLease:
    def __init__(self, key, wait=1, ttl=120):
        self.key, self.wait, self.ttl = key, wait, ttl
        self.acquired = False
        self.stop = threading.Event()
        self.lock = None

    def __enter__(self):
        try:
            self.lock = get_redis_connection("default").lock(
                self.key, timeout=self.ttl, blocking_timeout=self.wait, thread_local=False)
            self.acquired = self.lock.acquire()
            if self.acquired:
                threading.Thread(target=self._renew, daemon=True).start()
        except Exception:
            logger.warning("Cache lease unavailable key=%s", self.key, exc_info=True)
        return self

    def _renew(self):
        while not self.stop.wait(self.ttl / 3):
            try:
                self.lock.extend(self.ttl, replace_ttl=True)
            except Exception:
                logger.warning("Cache lease renewal failed key=%s", self.key, exc_info=True)
                return

    def __exit__(self, *exc):
        self.stop.set()
        if self.acquired:
            self.acquired = False
            try:
                self.lock.release()
            except Exception:
                logger.warning("Cache lease release failed key=%s", self.key, exc_info=True)
