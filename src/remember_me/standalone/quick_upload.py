# SPDX-License-Identifier: CPAL-1.0
"""Bounded single-user upload rate limiter, independent of spoofable IP headers."""

from collections import deque
import math
import threading
import time


class UploadRateLimiter:
    def __init__(self, limit, now=None):
        self.limit = limit
        self.now = now or time.monotonic
        self.requests = deque()
        self.lock = threading.Lock()

    def check(self):
        with self.lock:
            now = self.now()
            while self.requests and self.requests[0] <= now - 60:
                self.requests.popleft()
            if len(self.requests) >= self.limit:
                return max(1, math.ceil(60 - (now - self.requests[0])))
            self.requests.append(now)
            return None
