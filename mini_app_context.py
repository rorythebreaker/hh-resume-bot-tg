"""Route UI prompts/menus to a Mini App, while background reports still reach chat."""
import contextvars
import threading
import functools
from contextlib import contextmanager

_CURRENT = contextvars.ContextVar('mini_app_capture', default=None)
OPERATION_LOCK = threading.RLock()

def current():
    return _CURRENT.get()

@contextmanager
def capture(session):
    token = _CURRENT.set((session, threading.get_ident()))
    try:
        yield session
    finally:
        _CURRENT.reset(token)

def text(message):
    context = current()
    if context is None: return False
    session, request_thread = context
    session.add_text(message)
    return threading.get_ident() == request_thread

def menu(value, profile=None):
    context = current()
    if context is None: return False
    context[0].set_menu(value, profile)
    return True

def captcha(png):
    context = current()
    if context is not None:
        context[0].set_captcha(png)

def make_thread(target, args=(), kwargs=None, **options):
    context = contextvars.copy_context()
    return threading.Thread(target=lambda: context.run(target, *args, **(kwargs or {})), **options)

def serialized(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with OPERATION_LOCK:
            return function(*args, **kwargs)
    return wrapped
