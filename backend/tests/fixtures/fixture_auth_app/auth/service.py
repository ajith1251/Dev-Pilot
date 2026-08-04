"""Authentication service for handling user auth and tokens."""

from datetime import datetime, timedelta
from typing import Optional


class AuthService:
    """Handles user authentication and authorization."""

    def __init__(self, token_expiry_hours: int = 24):
        self.token_expiry_hours = token_expiry_hours
        self._tokens: dict = {}

    def create_token(self, user_id: str) -> str:
        """Create a new authentication token for a user."""
        token = f"tok_{user_id}_{datetime.utcnow().timestamp()}"
        self._tokens[token] = {
            "user_id": user_id,
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(hours=self.token_expiry_hours),
        }
        return token

    def validate_token(self, token: str) -> Optional[dict]:
        """Validate a token and return its data if valid."""
        token_data = self._tokens.get(token)
        if not token_data:
            return None
        if datetime.utcnow() > token_data["expires_at"]:
            return None
        return token_data

    def revoke_token(self, token: str) -> bool:
        """Revoke a token."""
        if token in self._tokens:
            del self._tokens[token]
            return True
        return False
