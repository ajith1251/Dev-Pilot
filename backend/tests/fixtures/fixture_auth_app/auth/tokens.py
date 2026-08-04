"""Token management for password reset and authentication."""

from datetime import datetime, timedelta
from typing import Optional


class TokenManager:
    """Manages password reset tokens with expiration."""

    def __init__(self, reset_token_expiry_minutes: int = 30):
        self.reset_token_expiry_minutes = reset_token_expiry_minutes
        self._reset_tokens: dict = {}

    def create_reset_token(self, email: str) -> str:
        """Create a password reset token for the given email."""
        token = f"reset_{email}_{datetime.utcnow().timestamp()}"
        self._reset_tokens[token] = {
            "email": email,
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(minutes=self.reset_token_expiry_minutes),
            "used": False,
        }
        return token

    def validate_reset_token(self, token: str) -> Optional[dict]:
        """Validate a password reset token.

        Returns token data if valid, None if token is missing, expired, or already used.
        """
        token_data = self._reset_tokens.get(token)
        if not token_data:
            return None
        if token_data.get("used"):
            return None
        if datetime.utcnow() > token_data["expires_at"]:
            return None
        return token_data

    def mark_token_used(self, token: str) -> bool:
        """Mark a reset token as used."""
        if token in self._reset_tokens:
            self._reset_tokens[token]["used"] = True
            return True
        return False

    def cleanup_expired_tokens(self) -> int:
        """Remove expired tokens and return the count removed."""
        now = datetime.utcnow()
        expired = [t for t, d in self._reset_tokens.items() if now > d["expires_at"]]
        for t in expired:
            del self._reset_tokens[t]
        return len(expired)
