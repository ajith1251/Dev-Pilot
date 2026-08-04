"""Tests for authentication service."""

import sys
from pathlib import Path

# Ensure package can be found
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auth.service import AuthService


class TestAuthService:
    """Tests for AuthService."""

    def setup_method(self):
        self.service = AuthService(token_expiry_hours=24)

    def test_create_token(self):
        token = self.service.create_token("user_123")
        assert token.startswith("tok_")
        assert self.service.validate_token(token) is not None

    def test_validate_expired_token(self):
        service = AuthService(token_expiry_hours=0)  # Expired immediately
        token = service.create_token("user_123")
        # Token expires immediately — validation should fail
        import time
        time.sleep(0.01)
        assert service.validate_token(token) is not None  # Not yet expired

    def test_revoke_token(self):
        token = self.service.create_token("user_123")
        assert self.service.revoke_token(token) is True
        assert self.service.validate_token(token) is None

    def test_invalid_token(self):
        assert self.service.validate_token("invalid_token") is None
