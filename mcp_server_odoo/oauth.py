"""OAuth 2.1 token verification via Zitadel introspection.

This module provides a TokenVerifier implementation that validates
Bearer tokens by calling Zitadel's RFC 7662 token introspection endpoint.

Security model:
- Claude.ai acts as a public client (PKCE, no client_secret) — this is
  correct per OAuth 2.1 for browser/CLI clients that cannot keep secrets.
- The MCP server acts as a Resource Server and validates tokens via
  introspection using its own client_id:client_secret (confidential).
- Audience validation ensures tokens are intended for this resource server.

Used when the MCP server runs in HTTP transport mode with OAuth enabled.
Not used for stdio transport (local Claude Desktop).
"""

import base64
import logging
from typing import List, Optional

import httpx

from mcp.server.auth.provider import AccessToken, TokenVerifier

logger = logging.getLogger(__name__)


class ZitadelTokenVerifier(TokenVerifier):
    """Validates Bearer tokens via Zitadel's introspection endpoint (RFC 7662).

    The MCP server acts as a Resource Server (RS). Zitadel is the
    Authorization Server (AS). Token validation uses the introspection
    endpoint with Basic Auth (client_id:client_secret).

    Security checks performed:
    1. Token is active (not revoked/expired) per Zitadel
    2. Audience matches this resource server (if configured)
    3. Required scopes are present (if configured)
    4. Token expiry is validated by the MCP middleware
    """

    def __init__(
        self,
        introspection_url: str,
        client_id: str,
        client_secret: str,
        expected_audience: Optional[str] = None,
        required_scopes: Optional[List[str]] = None,
        timeout: int = 10,
    ):
        self.introspection_url = introspection_url
        self._auth_header = "Basic " + base64.b64encode(
            f"{client_id}:{client_secret}".encode()
        ).decode()
        self._expected_audience = expected_audience
        self._required_scopes = set(required_scopes) if required_scopes else set()
        self.timeout = timeout

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        """Verify a Bearer token via Zitadel introspection.

        Validates token activity, audience, and scopes before returning
        an AccessToken that the MCP middleware uses for authorization.

        Args:
            token: The Bearer token from the Authorization header.

        Returns:
            AccessToken if valid, None if invalid/expired/wrong audience.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.introspection_url,
                    headers={
                        "Authorization": self._auth_header,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data={"token": token},
                )

            if response.status_code != 200:
                logger.warning(
                    f"Introspection endpoint returned {response.status_code}"
                )
                return None

            data = response.json()

            if not data.get("active"):
                logger.debug("Token is not active")
                return None

            # Audience validation (RFC 7662 §2.2)
            # Ensures the token was issued for this resource server
            if self._expected_audience:
                token_aud = data.get("aud", [])
                if isinstance(token_aud, str):
                    token_aud = [token_aud]
                if self._expected_audience not in token_aud:
                    logger.warning(
                        f"Token audience {token_aud} does not include "
                        f"expected audience {self._expected_audience}"
                    )
                    return None

            # Extract scopes (space-separated string → list)
            scopes = data.get("scope", "").split() if data.get("scope") else []

            # Scope validation at introspection level
            if self._required_scopes and not self._required_scopes.issubset(set(scopes)):
                missing = self._required_scopes - set(scopes)
                logger.warning(f"Token missing required scopes: {missing}")
                return None

            # Extract client_id from token data
            token_client_id = data.get("client_id", "unknown")

            # Extract expiry
            expires_at = data.get("exp")

            return AccessToken(
                token=token,
                client_id=token_client_id,
                scopes=scopes,
                expires_at=expires_at,
            )

        except httpx.TimeoutException:
            logger.error(
                f"Token introspection timeout after {self.timeout}s"
            )
            return None
        except Exception as e:
            logger.error(f"Token introspection failed: {e}")
            return None
