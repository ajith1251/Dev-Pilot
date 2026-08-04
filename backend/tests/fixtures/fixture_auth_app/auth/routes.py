"""API routes for authentication and password reset."""

from typing import Optional

from auth.service import AuthService
from auth.tokens import TokenManager


class AuthRoutes:
    """API route handlers for auth endpoints."""

    def __init__(self, auth_service: AuthService, token_manager: TokenManager):
        self.auth_service = auth_service
        self.token_manager = token_manager

    def handle_login(self, username: str, password: str) -> dict:
        """Handle user login."""
        # Validate credentials (simplified)
        if not username or not password:
            return {"error": "Missing credentials", "status": 400}
        user_id = f"user_{username}"
        token = self.auth_service.create_token(user_id)
        return {"token": token, "user_id": user_id, "status": 200}

    def handle_password_reset_request(self, email: str) -> dict:
        """Handle password reset request."""
        if not email:
            return {"error": "Email required", "status": 400}
        token = self.token_manager.create_reset_token(email)
        # In real app, send email with reset link
        return {"message": "Reset link sent", "reset_token": token, "status": 200}

    def handle_password_reset(self, reset_token: str, new_password: str) -> dict:
        """Handle password reset with token validation."""
        # Validate the reset token
        token_data = self.token_manager.validate_reset_token(reset_token)
        if not token_data:
            return {"error": "Invalid or expired reset token", "status": 401}

        # Validate password
        if len(new_password) < 8:
            return {"error": "Password too short", "status": 400}

        # Mark token as used
        self.token_manager.mark_token_used(reset_token)

        return {"message": "Password reset successful", "status": 200}
