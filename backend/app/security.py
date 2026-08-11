import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.fernet import Fernet

from .config import Settings


def hash_secret(value: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


def verify_secret(value: str, expected_hash: str, pepper: str) -> bool:
    return hmac.compare_digest(hash_secret(value, pepper), expected_hash)


def new_device_secret() -> str:
    return secrets.token_urlsafe(32)


def new_claim_code() -> str:
    return secrets.token_urlsafe(24)


def create_access_token(user_id: str, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + timedelta(hours=12)},
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_access_token(token: str, settings: Settings) -> str:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    return str(payload["sub"])


def create_device_session_token(serial_number: str, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": serial_number,
            "typ": "device-session",
            "iat": now,
            "exp": now + timedelta(minutes=15),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def verify_device_session_token(token: str, serial_number: str, settings: Settings) -> bool:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    return payload.get("typ") == "device-session" and payload.get("sub") == serial_number


def _memory_fernet(settings: Settings) -> Fernet:
    digest = hashlib.sha256(settings.memory_master_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_memory(value: str, settings: Settings) -> str:
    return _memory_fernet(settings).encrypt(value.encode()).decode()


def decrypt_memory(value: str, settings: Settings) -> str:
    return _memory_fernet(settings).decrypt(value.encode()).decode()
