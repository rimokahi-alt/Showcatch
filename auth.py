import json
import re
import bcrypt
import jwt
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
USERS_FILE = DATA_DIR / "users.json"
JWT_SECRET = "movies-downloader-secret-change-in-production"
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24


def _load_users() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_users(users: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")


def validate_username(username: str) -> tuple[bool, str]:
    if not username or len(username) < 3:
        return False, "Username must be at least 3 characters"
    if len(username) > 20:
        return False, "Username must be under 20 characters"
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return False, "Username can only contain letters, numbers, and underscores"
    return True, ""


def validate_password(password: str) -> tuple[bool, str]:
    if not password or len(password) < 6:
        return False, "Password must be at least 6 characters"
    if len(password) > 100:
        return False, "Password too long"
    return True, ""


def register_user(username: str, password: str) -> tuple[bool, str]:
    ok, msg = validate_username(username)
    if not ok:
        return False, msg
    ok, msg = validate_password(password)
    if not ok:
        return False, msg

    users = _load_users()
    if username.lower() in users:
        return False, "Username already exists"

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    users[username.lower()] = {
        "username": username,
        "password_hash": hashed,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_users(users)
    return True, "Account created successfully"


def authenticate_user(username: str, password: str) -> tuple[bool, str]:
    if not username or not password:
        return False, "Username and password required"
    users = _load_users()
    user = users.get(username.lower())
    if not user:
        return False, "Invalid username or password"
    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return False, "Invalid username or password"
    return True, user["username"]


def create_token(username: str) -> str:
    payload = {
        "sub": username,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
        "jti": str(uuid4()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
