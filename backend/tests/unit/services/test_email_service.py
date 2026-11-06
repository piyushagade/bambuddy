"""Unit tests for email service.

These tests verify email template rendering, HTML formatting,
password generation, and SMTP settings persistence.
"""

import string
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.notification_template import NotificationTemplate
from backend.app.services.email_service import (
    create_password_reset_email_from_template,
    create_welcome_email_from_template,
    generate_secure_password,
    render_template,
)


class TestEmailTemplateFormatting:
    """Tests for email template formatting."""

    @pytest.mark.asyncio
    async def test_welcome_email_newlines_converted_to_br(self):
        """Verify that newlines in welcome email body are converted to <br> tags."""
        # Mock database session
        db = AsyncMock(spec=AsyncSession)

        # Mock template with newlines
        template = NotificationTemplate(
            event_type="user_created",
            name="Welcome Email",
            title_template="Welcome to {app_name}",
            body_template="Hello {username}!\n\nYour password is: {password}\n\nPlease login at: {login_url}",
            is_default=True,
        )

        # Patch get_notification_template to return our template
        with patch("backend.app.services.email_service.get_notification_template", return_value=template):
            # Generate email
            subject, text_body, html_body = await create_welcome_email_from_template(
                db=db,
                username="testuser",
                password="testpass123",
                login_url="http://example.com/login",
                app_name="TestApp",
            )

        # Verify subject
        assert subject == "Welcome to TestApp"

        # Verify text body has newlines
        assert "\n\n" in text_body
        assert "Hello testuser!" in text_body
        assert "Your password is: testpass123" in text_body

        # Verify HTML body has <br> tags instead of relying on CSS
        assert "<br>" in html_body
        # Should not use white-space: pre-wrap
        assert "white-space: pre-wrap" not in html_body
        # Should have proper structure
        assert "<!DOCTYPE html>" in html_body
        assert '<div style="font-size: 16px;">' in html_body

        # Verify that escaped content is present (XSS protection)
        assert "Hello testuser!<br>" in html_body
        assert "Your password is: testpass123<br>" in html_body

    @pytest.mark.asyncio
    async def test_password_reset_email_newlines_converted_to_br(self):
        """Verify that newlines in password reset email body are converted to <br> tags."""
        # Mock database session
        db = AsyncMock(spec=AsyncSession)

        # Mock template with newlines
        template = NotificationTemplate(
            event_type="password_reset",
            name="Password Reset",
            title_template="{app_name} - Password Reset",
            body_template="Hello {username},\n\nYour password has been reset.\nNew password: {password}\n\nLogin at: {login_url}",
            is_default=True,
        )

        # Patch get_notification_template to return our template
        with patch("backend.app.services.email_service.get_notification_template", return_value=template):
            # Generate email
            subject, text_body, html_body = await create_password_reset_email_from_template(
                db=db,
                username="testuser",
                password="newpass456",
                login_url="http://example.com/login",
                app_name="TestApp",
            )

        # Verify subject
        assert subject == "TestApp - Password Reset"

        # Verify text body has newlines
        assert "\n\n" in text_body
        assert "Hello testuser," in text_body

        # Verify HTML body has <br> tags
        assert "<br>" in html_body
        # Should not use white-space: pre-wrap
        assert "white-space: pre-wrap" not in html_body
        # Should have security alert
        assert "Security Alert" in html_body

    @pytest.mark.asyncio
    async def test_email_header_padding(self):
        """Verify that email header has proper padding to prevent cutoff."""
        # Mock database session
        db = AsyncMock(spec=AsyncSession)

        # Mock template
        template = NotificationTemplate(
            event_type="user_created",
            name="Welcome Email",
            title_template="Welcome",
            body_template="Test body",
            is_default=True,
        )

        # Patch get_notification_template to return our template
        with patch("backend.app.services.email_service.get_notification_template", return_value=template):
            # Generate email
            subject, text_body, html_body = await create_welcome_email_from_template(
                db=db,
                username="testuser",
                password="testpass123",
                login_url="http://example.com/login",
            )

        # Verify header has 30px padding (not 20px which was cutting off)
        assert "padding: 30px; border-radius: 8px 8px 0 0;" in html_body

    @pytest.mark.asyncio
    async def test_email_xss_protection(self):
        """Verify that HTML escaping is applied to prevent XSS attacks."""
        # Mock database session
        db = AsyncMock(spec=AsyncSession)

        # Mock template with potential XSS content
        template = NotificationTemplate(
            event_type="user_created",
            name="Welcome Email",
            title_template="Welcome <script>alert('xss')</script>",
            body_template="Hello <script>alert('xss')</script>\nTest",
            is_default=True,
        )

        # Patch get_notification_template to return our template
        with patch("backend.app.services.email_service.get_notification_template", return_value=template):
            # Generate email
            subject, text_body, html_body = await create_welcome_email_from_template(
                db=db,
                username="testuser",
                password="testpass123",
                login_url="http://example.com/login",
            )

        # Verify that script tags are escaped
        assert "&lt;script&gt;" in html_body
        # Verify no unescaped script tags
        assert "<script>" not in html_body


class TestGenerateSecurePassword:
    """Tests for generate_secure_password()."""

    def test_password_default_length(self):
        """Default password is 16 characters."""
        password = generate_secure_password()
        assert len(password) == 16

    def test_password_custom_length(self):
        """Custom length is respected."""
        password = generate_secure_password(24)
        assert len(password) == 24

    def test_password_has_required_char_types(self):
        """Password contains uppercase, lowercase, digit, and special character."""
        # Run multiple times to reduce flakiness from random shuffling
        for _ in range(5):
            password = generate_secure_password()
            assert any(c in string.ascii_uppercase for c in password), "Missing uppercase"
            assert any(c in string.ascii_lowercase for c in password), "Missing lowercase"
            assert any(c in string.digits for c in password), "Missing digit"
            assert any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password), "Missing special"


class TestRenderTemplate:
    """Tests for render_template()."""

    def test_render_template_basic(self):
        """Placeholders are replaced correctly."""
        result = render_template("Hello {name}, welcome to {app}!", {"name": "Alice", "app": "BamBuddy"})
        assert result == "Hello Alice, welcome to BamBuddy!"

    def test_render_template_removes_unreplaced(self):
        """Unreplaced placeholders are removed."""
        result = render_template("Hello {name}, your code is {code}", {"name": "Bob"})
        assert result == "Hello Bob, your code is "


class TestSMTPSettingsPersistence:
    """Tests for save_smtp_settings() and get_smtp_settings() round-trip."""

    @pytest.mark.asyncio
    async def test_save_and_retrieve_smtp_settings(self, db_session):
        """Save SMTP settings, then retrieve them and verify values match."""
        from backend.app.schemas.auth import SMTPSettings
        from backend.app.services.email_service import get_smtp_settings, save_smtp_settings

        settings = SMTPSettings(
            smtp_host="mail.example.com",
            smtp_port=465,
            smtp_username="user@example.com",
            smtp_password="secret",
            smtp_security="ssl",
            smtp_auth_enabled=True,
            smtp_from_email="noreply@example.com",
        )
        await save_smtp_settings(db_session, settings)
        await db_session.commit()

        retrieved = await get_smtp_settings(db_session)
        assert retrieved is not None
        assert retrieved.smtp_host == "mail.example.com"
        assert retrieved.smtp_port == 465
        assert retrieved.smtp_username == "user@example.com"
        assert retrieved.smtp_password == "secret"
        assert retrieved.smtp_security == "ssl"
        assert retrieved.smtp_auth_enabled is True
        assert retrieved.smtp_from_email == "noreply@example.com"

    @pytest.mark.asyncio
    async def test_get_smtp_settings_returns_none_when_unconfigured(self, db_session):
        """Empty DB returns None for SMTP settings."""
        from backend.app.services.email_service import get_smtp_settings

        result = await get_smtp_settings(db_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_save_and_retrieve_resend_gateway_settings(self, db_session):
        """Save Resend gateway settings and preserve API key on subsequent update."""
        from backend.app.schemas.auth import SMTPSettings
        from backend.app.services.email_service import get_smtp_settings, save_smtp_settings

        settings = SMTPSettings(
            email_provider="resend",
            smtp_from_email="prints@example.com",
            smtp_from_name="BamBuddy Prints",
            smtp_reply_to="help@example.com",
            resend_api_key="re_test_key_123",
        )
        await save_smtp_settings(db_session, settings)
        await db_session.commit()

        retrieved = await get_smtp_settings(db_session)
        assert retrieved is not None
        assert retrieved.email_provider == "resend"
        assert retrieved.resend_api_key == "re_test_key_123"
        assert retrieved.resend_api_key_configured is True
        assert retrieved.smtp_from_email == "prints@example.com"
        assert retrieved.smtp_reply_to == "help@example.com"

        # Update without re-sending API key — existing key must be preserved
        updated = SMTPSettings(
            email_provider="resend",
            smtp_from_email="updated@example.com",
            smtp_from_name="BamBuddy Prints",
        )
        await save_smtp_settings(db_session, updated)
        await db_session.commit()

        retrieved2 = await get_smtp_settings(db_session)
        assert retrieved2 is not None
        assert retrieved2.smtp_from_email == "updated@example.com"
        assert retrieved2.resend_api_key == "re_test_key_123"

    @pytest.mark.asyncio
    async def test_save_and_retrieve_mailgun_gateway_settings(self, db_session):
        """Save Mailgun gateway settings (EU region) and retrieve them."""
        from backend.app.schemas.auth import SMTPSettings
        from backend.app.services.email_service import get_smtp_settings, save_smtp_settings

        settings = SMTPSettings(
            email_provider="mailgun",
            smtp_from_email="noreply@mg.example.com",
            smtp_from_name="BamBuddy",
            mailgun_api_key="key-mailgun-secret",
            mailgun_domain="mg.example.com",
            mailgun_region="eu",
        )
        await save_smtp_settings(db_session, settings)
        await db_session.commit()

        retrieved = await get_smtp_settings(db_session)
        assert retrieved is not None
        assert retrieved.email_provider == "mailgun"
        assert retrieved.mailgun_api_key == "key-mailgun-secret"
        assert retrieved.mailgun_api_key_configured is True
        assert retrieved.mailgun_domain == "mg.example.com"
        assert retrieved.mailgun_region == "eu"


class TestResendAndMailgunDelivery:
    """Tests for Resend and Mailgun HTTP API email delivery."""

    def test_send_email_via_resend_with_inline_image(self):
        from unittest.mock import MagicMock

        from backend.app.schemas.auth import SMTPSettings
        from backend.app.services.email_service import send_email

        settings = SMTPSettings(
            email_provider="resend",
            smtp_from_email="noreply@example.com",
            smtp_from_name="BamBuddy",
            smtp_reply_to="reply@example.com",
            resend_api_key="re_123",
        )
        mock_resp = MagicMock(status_code=200, text='{"id":"123"}')
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_resp
        with patch("backend.app.services.email_service.httpx.Client", return_value=mock_client):
            send_email(
                settings,
                "user@example.com",
                "Print Complete",
                "Plain body",
                '<img src="cid:snap1">',
                image_data=b"\xff\xd8jpeg",
                image_cid="snap1",
            )

        mock_client.post.assert_called_once()
        assert mock_client.post.call_args.args[0] == "https://api.resend.com/emails"
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["from"] == "BamBuddy <noreply@example.com>"
        assert payload["to"] == ["user@example.com"]
        assert payload["reply_to"] == "reply@example.com"
        assert payload["attachments"][0]["content_id"] == "snap1"

    def test_send_email_via_mailgun_eu_region(self):
        from unittest.mock import MagicMock

        from backend.app.schemas.auth import SMTPSettings
        from backend.app.services.email_service import send_email

        settings = SMTPSettings(
            email_provider="mailgun",
            smtp_from_email="noreply@mg.example.com",
            smtp_from_name="BamBuddy",
            mailgun_api_key="key-xyz",
            mailgun_domain="mg.example.com",
            mailgun_region="eu",
        )
        mock_resp = MagicMock(status_code=200, text='{"message":"Queued"}')
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_resp
        with patch("backend.app.services.email_service.httpx.Client", return_value=mock_client):
            send_email(
                settings,
                "user@example.com",
                "Plate Clear Required",
                "Please clear the build plate",
                "<p>Please clear the build plate</p>",
            )

        mock_client.post.assert_called_once()
        assert mock_client.post.call_args.args[0] == "https://api.eu.mailgun.net/v3/mg.example.com/messages"
        assert mock_client.post.call_args.kwargs["auth"] == ("api", "key-xyz")
        assert mock_client.post.call_args.kwargs["data"]["to"] == ["user@example.com"]

    @pytest.mark.asyncio
    async def test_notification_service_resend_and_mailgun_providers(self):
        from unittest.mock import MagicMock

        from backend.app.services.notification_service import NotificationService

        service = NotificationService()
        mock_resp = MagicMock(status_code=200, text="ok")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.object(service, "_get_client", new_callable=AsyncMock, return_value=mock_client):
            ok_r, msg_r = await service._send_resend(
                {
                    "api_key": "re_abc",
                    "from_email": "from@example.com",
                    "to_email": "to@example.com",
                },
                "Test Title",
                "Test Body",
                image_data=b"\xff\xd8jpeg",
            )
            assert ok_r is True
            assert "Resend" in msg_r

            ok_m, msg_m = await service._send_mailgun(
                {
                    "api_key": "key-abc",
                    "domain": "mg.example.com",
                    "region": "us",
                    "from_email": "from@mg.example.com",
                    "to_email": "to@example.com",
                },
                "Test Title",
                "Test Body",
                image_data=b"\xff\xd8jpeg",
            )
            assert ok_m is True
            assert "Mailgun" in msg_m
            assert mock_client.post.call_count == 2
