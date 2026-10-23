"""Mots de passe (PBKDF2) et limitation des tentatives de connexion."""
import hashlib
import hmac
import secrets
import threading
import time

ITERATIONS = 310_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(candidate, digest)


class LoginLimiter:
    """N échecs par IP dans la fenêtre → connexion bloquée jusqu'à expiration."""

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window = window_seconds
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def _recent(self, ip: str) -> list[float]:
        now = time.time()
        recent = [t for t in self.failures.get(ip, []) if now - t < self.window]
        self.failures[ip] = recent
        return recent

    def blocked_for(self, ip: str) -> int:
        """Secondes restantes de blocage (0 si l'IP peut essayer)."""
        with self.lock:
            recent = self._recent(ip)
            if self.max_attempts <= 0 or len(recent) < self.max_attempts:
                return 0
            return int(self.window - (time.time() - recent[0])) + 1

    def fail(self, ip: str) -> None:
        with self.lock:
            self._recent(ip).append(time.time())

    def reset(self, ip: str) -> None:
        with self.lock:
            self.failures.pop(ip, None)
