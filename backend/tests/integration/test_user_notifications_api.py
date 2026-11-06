"""Integration tests for User Notifications API endpoints.

Tests the full request/response cycle for /api/v1/user-notifications/ endpoints.
"""

import pytest
from httpx import AsyncClient


class TestUserNotificationsAPI:
    """Integration tests for /api/v1/user-notifications/ endpoints."""

    # ========================================================================
    # GET /preferences — no auth
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_preferences_returns_defaults_when_no_auth(self, async_client: AsyncClient):
        """Without auth, GET should return all-enabled defaults."""
        response = await async_client.get("/api/v1/user-notifications/preferences")

        assert response.status_code == 200
        data = response.json()
        assert data["notify_print_start"] is True
        assert data["notify_print_complete"] is True
        assert data["notify_print_failed"] is True
        assert data["notify_print_stopped"] is True

    # ========================================================================
    # PUT /preferences — no auth
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_preferences_fails_without_auth(self, async_client: AsyncClient):
        """Without auth enabled, PUT should return 400 (no user context)."""
        data = {
            "notify_print_start": False,
            "notify_print_complete": True,
            "notify_print_failed": True,
            "notify_print_stopped": False,
        }

        response = await async_client.put("/api/v1/user-notifications/preferences", json=data)

        assert response.status_code == 400
        assert "Authentication must be enabled" in response.json()["detail"]

    # ========================================================================
    # Schema validation
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_preferences_rejects_missing_fields(self, async_client: AsyncClient):
        """PUT should reject requests missing required boolean fields."""
        data = {
            "notify_print_start": True,
            # missing other fields
        }

        response = await async_client.put("/api/v1/user-notifications/preferences", json=data)

        assert response.status_code == 422  # Pydantic validation error

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_preferences_rejects_invalid_type(self, async_client: AsyncClient):
        """PUT should reject values that cannot be coerced to boolean."""
        data = {
            "notify_print_start": [1, 2, 3],
            "notify_print_complete": True,
            "notify_print_failed": True,
            "notify_print_stopped": True,
        }

        response = await async_client.put("/api/v1/user-notifications/preferences", json=data)

        assert response.status_code == 422

    # ========================================================================
    # Admin-only restriction on disabling plate clear notifications
    # ========================================================================

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_only_admin_can_disable_plate_clear_notifications(self, async_client: AsyncClient):
        """Non-admin gets 403 when trying to disable plate clear notifications; admin succeeds."""
        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "plateadmin",
                "admin_password": "AdminPass1!",
            },
        )
        admin_login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "plateadmin", "password": "AdminPass1!"},
        )
        admin_token = admin_login.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Create regular operator user (Operators group has notifications:user_email)
        groups_resp = await async_client.get("/api/v1/groups/", headers=admin_headers)
        operators_group_id = next(g["id"] for g in groups_resp.json() if g["name"] == "Operators")

        create_resp = await async_client.post(
            "/api/v1/users/",
            headers=admin_headers,
            json={
                "username": "plateuser",
                "password": "UserPass1!",
                "email": "plateuser@example.com",
                "role": "user",
                "group_ids": [operators_group_id],
            },
        )
        assert create_resp.status_code in (200, 201), create_resp.text
        user_id = create_resp.json()["id"]

        user_login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "plateuser", "password": "UserPass1!"},
        )
        user_token = user_login.json()["access_token"]
        user_headers = {"Authorization": f"Bearer {user_token}"}

        # Non-admin can toggle print progress and queue notifications
        ok_resp = await async_client.put(
            "/api/v1/user-notifications/preferences",
            headers=user_headers,
            json={
                "notify_print_start": True,
                "notify_print_complete": True,
                "notify_print_failed": True,
                "notify_print_stopped": True,
                "notify_print_progress": False,
                "notify_queue_waiting": False,
                "notify_plate_clear_required": True,
                "notify_plate_not_empty": True,
            },
        )
        assert ok_resp.status_code == 200
        assert ok_resp.json()["notify_print_progress"] is False
        assert ok_resp.json()["notify_queue_waiting"] is False

        # Non-admin CANNOT disable notify_plate_clear_required
        forbidden_resp = await async_client.put(
            "/api/v1/user-notifications/preferences",
            headers=user_headers,
            json={
                "notify_print_start": True,
                "notify_print_complete": True,
                "notify_print_failed": True,
                "notify_print_stopped": True,
                "notify_plate_clear_required": False,
            },
        )
        assert forbidden_resp.status_code == 403
        assert "only administrators" in forbidden_resp.json()["detail"].lower()

        # Admin CAN disable notify_plate_clear_required for that user via admin endpoint
        admin_update_resp = await async_client.put(
            f"/api/v1/user-notifications/preferences/{user_id}",
            headers=admin_headers,
            json={
                "notify_print_start": True,
                "notify_print_complete": True,
                "notify_print_failed": True,
                "notify_print_stopped": True,
                "notify_plate_clear_required": False,
                "notify_plate_not_empty": False,
            },
        )
        assert admin_update_resp.status_code == 200
        assert admin_update_resp.json()["notify_plate_clear_required"] is False
        assert admin_update_resp.json()["notify_plate_not_empty"] is False

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_forgot_password_works_with_resend_gateway_without_advanced_auth(
        self, async_client: AsyncClient
    ):
        """Password reset works when Resend gateway is configured even if advanced_auth is disabled."""
        from unittest.mock import patch

        await async_client.post(
            "/api/v1/auth/setup",
            json={
                "auth_enabled": True,
                "admin_username": "gwadmin",
                "admin_password": "AdminPass1!",
            },
        )
        admin_login = await async_client.post(
            "/api/v1/auth/login",
            json={"username": "gwadmin", "password": "AdminPass1!"},
        )
        admin_token = admin_login.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Save Resend email gateway without enabling advanced auth
        save_resp = await async_client.post(
            "/api/v1/auth/smtp",
            headers=admin_headers,
            json={
                "email_provider": "resend",
                "smtp_from_email": "noreply@example.com",
                "smtp_from_name": "BamBuddy",
                "resend_api_key": "re_test_123",
            },
        )
        assert save_resp.status_code == 200

        status_resp = await async_client.get("/api/v1/auth/advanced-auth/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["smtp_configured"] is True
        assert status_resp.json()["email_provider"] == "resend"
        assert status_resp.json()["advanced_auth_enabled"] is False

        # Create user with email
        await async_client.post(
            "/api/v1/users/",
            headers=admin_headers,
            json={
                "username": "gwuser",
                "password": "UserPass1!",
                "email": "gwuser@example.com",
                "role": "user",
            },
        )

        with patch("backend.app.api.routes.auth.send_email") as mock_send:
            resp = await async_client.post(
                "/api/v1/auth/forgot-password",
                json={"email": "gwuser@example.com"},
            )
        assert resp.status_code == 200
        mock_send.assert_called_once()
