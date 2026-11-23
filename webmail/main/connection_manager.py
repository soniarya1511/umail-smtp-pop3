"""Process-wide registry of open mail connections, keyed by email address.

The Django session only stores the email address; the sockets live here.
This is why the webmail runs as a single process (gunicorn --workers 1).
"""

from __future__ import annotations

import threading

from .mailclient import MailServers, UserConnection


class ConnectionManager:
    _connections: dict[str, UserConnection] = {}
    _lock = threading.Lock()

    @classmethod
    def open(cls, email: str, password: str) -> UserConnection:
        """Discover the user's servers, log in, and replace any older connection."""
        conn = UserConnection(email, MailServers.discover(email))
        conn.login(password)  # raises MailError
        with cls._lock:
            old = cls._connections.pop(email, None)
            cls._connections[email] = conn
        if old:
            old.close()
        return conn

    @classmethod
    def get(cls, email: str) -> UserConnection | None:
        with cls._lock:
            return cls._connections.get(email)

    @classmethod
    def close(cls, email: str) -> None:
        with cls._lock:
            conn = cls._connections.pop(email, None)
        if conn:
            conn.close()
