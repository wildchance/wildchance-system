"""Member passwords — stdlib PBKDF2-SHA256 (no extra dependency), constant-time check,
and a small in-memory attempt limiter (5 failures / 15 min per email and per IP)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Dict, List

ITERATIONS = 310_000            # OWASP 2023 guidance for PBKDF2-HMAC-SHA256
MIN_LEN = 10
WINDOW_S, MAX_FAILS = 15 * 60, 5
_fails: Dict[str, List[float]] = {}


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
    return f"pbkdf2${iterations}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = (stored or "").split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, TypeError):
        return False


def strong_enough(password: str) -> bool:
    return isinstance(password, str) and len(password) >= MIN_LEN and not password.isdigit()


def locked(*keys: str) -> bool:
    now = time.time()
    for k in keys:
        _fails[k] = [t for t in _fails.get(k, []) if now - t < WINDOW_S]
        if len(_fails[k]) >= MAX_FAILS:
            return True
    return False


def record_fail(*keys: str) -> None:
    for k in keys:
        _fails.setdefault(k, []).append(time.time())


def clear(*keys: str) -> None:
    for k in keys:
        _fails.pop(k, None)
