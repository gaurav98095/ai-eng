"""Cognito ID-token verification with an explicit local-development fallback."""

from __future__ import annotations

from functools import lru_cache

import jwt
from fastapi import HTTPException, Request, status
from jwt import PyJWKClient


@lru_cache(maxsize=4)
def _jwks(url: str) -> PyJWKClient:
    return PyJWKClient(url)


def current_user(request: Request) -> str:
    settings = request.app.state.settings
    auth = request.headers.get("Authorization", "")
    if settings.environment in {"local", "test"} and not auth:
        return "local-dev"
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    token = auth.removeprefix("Bearer ").strip()
    if not settings.cognito_user_pool_id or not settings.cognito_client_id:
        if settings.environment in {"local", "test"}:
            return "local-dev"
        raise HTTPException(status_code=503, detail="identity provider is not configured")
    issuer = f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/{settings.cognito_user_pool_id}"
    try:
        key = _jwks(f"{issuer}/.well-known/jwks.json").get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=settings.cognito_client_id,
                           issuer=issuer, options={"require": ["sub", "iss", "aud", "exp"]})
        if claims.get("token_use") != "id":
            raise ValueError("not an ID token")
        return str(claims["sub"])
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid identity token") from exc


def owns(resource_owner_id: str | None, user_id: str) -> bool:
    if resource_owner_id is None:
        return user_id == "local-dev"
    return resource_owner_id == user_id
