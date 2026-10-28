"""Bearer-token authentication for protected routes.

A single shared token is the v1 authentication scheme: the server
compares the ``Authorization: Bearer <token>`` header against the
``MUSINGS_TOKEN`` environment variable using a constant-time compare.
Per-client and scoped tokens are roadmap items, not v1 scope.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings_get

bearer_scheme = HTTPBearer(auto_error=False)


def token_verify(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Verify the request bearer token against the configured token.

    Args:
        credentials: Parsed ``Authorization`` header, if present.

    Returns:
        The verified token string.

    Raises:
        HTTPException: 401 when the header is missing, is not a bearer
            token, or does not match the configured token.
    """
    settings = settings_get()
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
        )
    if not secrets.compare_digest(credentials.credentials, settings.musingsToken):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token.",
        )
    return credentials.credentials
