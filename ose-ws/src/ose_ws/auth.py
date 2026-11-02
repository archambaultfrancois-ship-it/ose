"""Cookie sessions, CSRF, explicit proxy trust and scoped web permissions."""
from dataclasses import dataclass
import fnmatch
import hmac
import os
from pathlib import Path
import secrets
import stat
import time
from ose_ws.util import APIError, require, timestamp


@dataclass
class Session:
    id: str
    actor: str
    csrf: str
    created: float
    touched: float
    permissions: dict

    def allows(self, permission, scope="*"):
        patterns = self.permissions.get(permission, [])
        if self.permissions.get("admin") == ["*"]:
            return True
        # Parameter rights are exact paths. Wildcard '*' is an explicit administrative selection.
        if permission.startswith("parameters."):
            return scope in patterns or "*" in patterns
        return any(fnmatch.fnmatchcase(scope, pattern) for pattern in patterns)

    def check(self, permission, scope="*"):
        require(self.allows(permission, scope), "Permission denied.", 403, "FORBIDDEN")

    def public(self):
        return {"actor": self.actor, "csrf": self.csrf, "permissions": self.permissions,
                "expires_at": timestamp(min(self.created + 28800, self.touched + 1800))}


class Auth:
    cookie = "ose_ws_session"

    def __init__(self, config):
        self.config = config["auth"]
        self.sessions = {}
        self.attempts = {}
        self.token = None

    def load(self):
        if self.config["mode"] != "local_token":
            return
        fd = os.open(self.config["token_file"], os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise ValueError("The access token must be a regular owner-only file.")
            self.token = stream.read(1024).strip()
        if len(self.token) < 43:
            raise ValueError("The access token must contain at least 256 bits of randomness.")

    def proxy_actor(self, request):
        host = request.client.host if request.client else ""
        require(host in self.config["trusted_proxies"], "Untrusted proxy connection.", 403)
        require(request.headers.get("x-forwarded-proto") == "https", "Proxy access requires HTTPS.", 403)
        actor = request.headers.get("x-ose-user", "")
        require(actor in self.config["users"], "Unknown proxy identity.", 403)
        return actor

    def same_origin(self, request):
        origin = request.headers.get("origin")
        if origin:
            scheme = "https" if self.config["mode"] == "proxy" else {"ws": "http", "wss": "https"}.get(request.url.scheme, request.url.scheme)
            require(origin == f"{scheme}://{request.headers.get('host')}", "Origin rejected.", 403, "ORIGIN_REJECTED")

    def cleanup(self):
        now = time.time()
        self.sessions = {key: value for key, value in self.sessions.items()
                         if now - value.touched < 1800 and now - value.created < 28800}
        self.attempts = {key: value for key, value in self.attempts.items() if value[0] > now - 60}

    def login(self, request, body):
        self.cleanup()
        self.same_origin(request)
        peer = request.client.host if request.client else "unknown"
        if self.config["mode"] == "proxy":
            actor = self.proxy_actor(request)
            permissions = self.config["users"][actor]["permissions"]
        else:
            started, attempts = self.attempts.get(peer, (time.time(), 0))
            require(attempts < 20, "Too many login attempts; wait one minute.", 429, "LOGIN_THROTTLED")
            require(len(self.attempts) < 1000 or peer in self.attempts, "Login capacity reached.", 429)
            self.attempts[peer] = (started, attempts + 1)
            token = body.get("token", "")
            require(isinstance(token, str) and hmac.compare_digest(token.encode(), self.token.encode()),
                    "Invalid access token.", 401, "INVALID_TOKEN")
            actor, permissions = "local", {"admin": ["*"]}
        existing = request.cookies.get(self.cookie)
        if existing in self.sessions:
            del self.sessions[existing]
        require(len(self.sessions) < self.config["max_sessions"], "Browser session limit reached.", 429)
        now = time.time()
        session = Session(secrets.token_urlsafe(32), actor, secrets.token_urlsafe(32), now, now, permissions)
        self.sessions[session.id] = session
        return session

    def get(self, request, *, mutation=False, touch=False):
        self.cleanup()
        session = self.sessions.get(request.cookies.get(self.cookie))
        require(session is not None, "Sign in to continue.", 401, "SESSION_REQUIRED")
        if self.config["mode"] == "proxy":
            require(self.proxy_actor(request) == session.actor, "Proxy identity changed; sign in again.", 401)
            session.permissions = self.config["users"][session.actor]["permissions"]
        if mutation:
            self.same_origin(request)
            supplied = request.headers.get("x-csrf-token", "")
            require(hmac.compare_digest(supplied, session.csrf), "Invalid CSRF token.", 403, "CSRF_REJECTED")
            touch = True
        if touch:
            session.touched = time.time()
        return session
