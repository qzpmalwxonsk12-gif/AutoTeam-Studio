"""Cooperative absolute budgets shared by facade I/O and contained processes."""
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
import time

from .types import ControllerError

_deadline = ContextVar("autoteam_operation_deadline", default=None)

def remaining(maximum=None):
    deadline = _deadline.get()
    left = deadline - time.monotonic() if deadline is not None else None
    if left is not None and left <= 0:
        raise ControllerError("operation_timeout", "Operation exceeded its absolute deadline.")
    return maximum if left is None else left if maximum is None else min(left, maximum)

def bounded(seconds):
    def decorate(method):
        @wraps(method)
        def call(*args, **kwargs):
            current = _deadline.get()
            deadline = time.monotonic() + seconds
            token = _deadline.set(min(current, deadline) if current is not None else deadline)
            try:
                remaining()
                result = method(*args, **kwargs)
                remaining()
                return result
            finally:
                _deadline.reset(token)
        return call
    return decorate

@contextmanager
def cleanup_budget(seconds=5):
    """A bounded cleanup allowance after timeout; never launch further work."""
    token = _deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)
