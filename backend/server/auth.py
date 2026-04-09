import os
from functools import wraps
from typing import Any, Dict

from flask import jsonify, request
import jwt


COGNITO_REGION = os.getenv("COGNITO_REGION", "")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")
COGNITO_APP_CLIENT_ID = os.getenv("COGNITO_APP_CLIENT_ID", "")

_jwks_client = None


def _issuer() -> str:
    return f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"


def _ensure_auth_config() -> None:
    if not COGNITO_REGION or not COGNITO_USER_POOL_ID or not COGNITO_APP_CLIENT_ID:
        raise RuntimeError(
            "Missing required Cognito env vars: COGNITO_REGION, COGNITO_USER_POOL_ID, COGNITO_APP_CLIENT_ID."
        )


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(f"{_issuer()}/.well-known/jwks.json")
    return _jwks_client


def decode_cognito_jwt(token: str) -> Dict[str, Any]:
    _ensure_auth_config()
    signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
    decoded = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=_issuer(),
        options={"verify_aud": False},
    )

    token_use = decoded.get("token_use")
    if token_use == "id":
        if decoded.get("aud") != COGNITO_APP_CLIENT_ID:
            raise jwt.InvalidTokenError("Invalid audience for id token.")
    elif token_use == "access":
        if decoded.get("client_id") != COGNITO_APP_CLIENT_ID:
            raise jwt.InvalidTokenError("Invalid client_id for access token.")
    else:
        raise jwt.InvalidTokenError("Invalid token_use claim.")

    return decoded


def require_auth(handler):
    @wraps(handler)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header."}), 401

        token = auth_header.removeprefix("Bearer ").strip()
        if not token:
            return jsonify({"error": "Missing bearer token."}), 401

        try:
            request.cognito_claims = decode_cognito_jwt(token)
        except Exception:
            return jsonify({"error": "Invalid or expired token."}), 401

        return handler(*args, **kwargs)

    return wrapper
