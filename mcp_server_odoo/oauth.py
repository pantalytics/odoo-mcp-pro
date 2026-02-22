"""OAuth 2.1 token verification via Zitadel introspection.

This module provides a TokenVerifier implementation that validates
Bearer tokens by calling Zitadel's RFC 7662 token introspection endpoint.

Used when the MCP server runs in HTTP transport mode with OAuth enabled.
Not used for stdio transport (local Claude Desktop).
"""

import base64
import logging
from typing import Optional

import httpx

from mcp.server.auth.provider import AccessToken, TokenVerifier

logger = logging.getLogger(__name__)


class ZitadelTokenVerifier(TokenVerifier):
    """Validates Bearer tokens via Zitadel's introspection endpoint (RFC 7662).

    The MCP server acts as a Resource Server (RS). Zitadel is the
    Authorization Server (AS). Token validation uses the introspection
    endpoint with Basic Auth (client_id:client_secret).
    """

    def __init__(
        self,
        introspection_url: str,
        client_id: str,
        client_secret: str,
        timeout: int = 10,
    ):
        self.introspection_url = introspection_url
        self._auth_header = "Basic " + base64.b64encode(
            f"{client_id}:{client_secret}".encode()
        ).decode()
        self.timeout = timeout

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        """Verify a Bearer token via Zitadel introspection.

        Args:
            token: The Bearer token from the Authorization header.

        Returns:
            AccessToken if valid, None if invalid/expired.
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

            # Extract scopes (space-separated string → list)
            scopes = data.get("scope", "").split() if data.get("scope") else []

            # Extract client_id from token data
            client_id = data.get("client_id", "unknown")

            # Extract expiry
            expires_at = data.get("exp")

            return AccessToken(
                token=token,
                client_id=client_id,
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
