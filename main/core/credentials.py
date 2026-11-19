"""Task-owned, memory-only credentials, separate from persisted model config."""

from contextlib import contextmanager
from contextvars import ContextVar
import logging


class TemporaryCredentials:
    def __init__(self, values=None):
        self._values = dict(values or {})

    def __repr__(self):
        return "TemporaryCredentials([REDACTED])"

    def get(self, role):
        return self._values.get(role)

    def values(self):
        return tuple(value for value in self._values.values() if value)

    def copy(self):
        return TemporaryCredentials(self._values)

    def clear(self):
        self._values.clear()


_current = ContextVar("task_temporary_credentials", default=None)


def current_credentials():
    return _current.get()


@contextmanager
def credential_scope(credentials):
    token = _current.set(credentials)
    try:
        yield
    finally:
        _current.reset(token)


def scrub_temporary(text, credentials=None):
    credentials = credentials if credentials is not None else current_credentials()
    if credentials:
        for value in sorted(set(credentials.values()), key=len, reverse=True):
            text = text.replace(value, "[REDACTED]")
    return text


class SafeOutput:
    """Line-buffered redaction also handles a key written in multiple chunks."""
    def __init__(self, stream, credentials=None):
        self.stream = stream
        self.credentials = credentials
        self.pending = ""

    def write(self, text):
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            self.stream.write(scrub_temporary(line, self.credentials) + "\n")
        return len(text)

    def flush(self):
        # Keep incomplete lines buffered until close, rather than exposing a
        # partial key across write/flush boundaries.
        self.stream.flush()

    def finish(self):
        if self.pending:
            self.stream.write(scrub_temporary(self.pending, self.credentials))
            self.pending = ""
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


@contextmanager
def protect_existing_loggers(credentials):
    """Protect handlers created before Worker stream redirection, including threads.

    Applied only inside the isolated Worker. Formatting first also scrubs
    exception tracebacks; a message-only filter would miss those.
    """
    class SafeFormatter(logging.Formatter):
        def __init__(self, original):
            super().__init__()
            self.original = original or logging.Formatter()

        def format(self, record):
            return scrub_temporary(self.original.format(record), credentials)

    loggers = [logging.getLogger()] + [
        logger for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]
    handlers = {handler for logger in loggers for handler in logger.handlers}
    originals = [(handler, handler.formatter) for handler in handlers]
    try:
        for handler, formatter in originals:
            handler.setFormatter(SafeFormatter(formatter))
        yield
    finally:
        for handler, formatter in originals:
            handler.setFormatter(formatter)
