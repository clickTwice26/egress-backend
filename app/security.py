import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Argon2id with the argon2-cffi defaults, which track the RFC 9106
# low-memory profile. Argon2id is the current OWASP recommendation for
# password storage — it resists both GPU and side-channel attacks.
_hasher = PasswordHasher()

# 32 bytes = 256 bits of entropy per token.
_TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than the current policy."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return False


def generate_token() -> str:
    """A URL-safe, cryptographically random session token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 digest used as the database lookup key for a session token.

    A fast hash is correct here: the token is 256 bits of CSPRNG output, so
    there is no dictionary to attack. Hashing means a database leak does not
    hand an attacker usable bearer tokens.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a, b)


# A valid Argon2id hash of a throwaway value. Verifying against this on the
# "user not found" path keeps login timing roughly constant, so response time
# does not reveal whether an email is registered.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalisation")


def dummy_verify() -> None:
    try:
        _hasher.verify(_DUMMY_HASH, "not-the-password")
    except Exception:
        pass
