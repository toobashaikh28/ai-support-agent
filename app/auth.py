"""
JWT authentication for the admin panel.

Deliberately minimal for now - one role check (admin), one secret, tokens
expire after 8 hours. This is enough for a single-admin upload panel;
Day 11 in the roadmap ("Auth + PII masking") is where this would be
hardened for multiple roles and customer-facing auth.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import wraps

import bcrypt
import jwt
from flask import g, jsonify, request

JWT_SECRET = os.getenv("JWT_SECRET_KEY", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 8


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode(), hashed.encode())
    except ValueError:
        # hashed wasn't a valid bcrypt hash (e.g. still the seed placeholder)
        return False


def issue_token(user_id: int, email: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Raises jwt.PyJWTError subclasses on invalid/expired tokens."""
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def require_admin(view_func):
    """Route decorator: requires a valid JWT for a user with role='admin'
    in the Authorization: Bearer <token> header. Puts the decoded claims
    on flask.g.current_user for the view to use."""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"detail": "Missing or malformed Authorization header"}), 401

        token = auth_header[len("Bearer "):]
        try:
            claims = decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"detail": "Token expired, please log in again"}), 401
        except jwt.PyJWTError:
            return jsonify({"detail": "Invalid token"}), 401

        if claims.get("role") not in ("admin", "supervisor"):
            return jsonify({"detail": "Admin access required"}), 403

        g.current_user = claims
        return view_func(*args, **kwargs)

    return wrapper


def require_customer(view_func):
    """Route decorator: requires a valid JWT for a customer (role='customer',
    issued by /auth/register or /auth/customer-login). Puts the decoded
    claims on flask.g.current_customer for the view to use."""

    @wraps(view_func)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"detail": "Missing or malformed Authorization header"}), 401

        token = auth_header[len("Bearer "):]
        try:
            claims = decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"detail": "Token expired, please log in again"}), 401
        except jwt.PyJWTError:
            return jsonify({"detail": "Invalid token"}), 401

        if claims.get("role") != "customer":
            return jsonify({"detail": "Customer login required"}), 403

        g.current_customer = claims
        return view_func(*args, **kwargs)

    return wrapper
