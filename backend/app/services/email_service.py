"""Email service for sending authentication-related emails."""

from __future__ import annotations

import base64
import html
import logging
import re
import secrets
import smtplib
import string
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.notification_template import NotificationTemplate
from backend.app.models.settings import Settings
from backend.app.schemas.auth import SMTPSettings

logger = logging.getLogger(__name__)

_USER_AGENT = "Bambuddy/1.0 (+https://github.com/maziggy/bambuddy)"


def generate_secure_password(length: int = 16) -> str:
    """Generate a secure random password.

    Args:
        length: Length of the password (default: 16)

    Returns:
        A secure random password containing uppercase, lowercase, digits, and special characters
    """
    # Define character sets
    lowercase = string.ascii_lowercase
    uppercase = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%^&*()_+-=[]{}|;:,.<>?"

    # Ensure at least one character from each set
    password_chars = [
        secrets.choice(lowercase),
        secrets.choice(uppercase),
        secrets.choice(digits),
        secrets.choice(special),
    ]

    # Fill the rest with random characters from all sets
    all_chars = lowercase + uppercase + digits + special
    password_chars.extend(secrets.choice(all_chars) for _ in range(length - 4))

    # Shuffle with CSPRNG — random.shuffle() is seeded from time and not cryptographically safe
    secrets.SystemRandom().shuffle(password_chars)

    return "".join(password_chars)


async def get_notification_template(db: AsyncSession, event_type: str) -> NotificationTemplate | None:
    """Get a notification template by event type from database.

    Args:
        db: Database session
        event_type: Type of event (e.g., 'user_created', 'password_reset')

    Returns:
        NotificationTemplate object or None if not found
    """
    result = await db.execute(select(NotificationTemplate).where(NotificationTemplate.event_type == event_type))
    return result.scalar_one_or_none()


def render_template(template_str: str, variables: dict[str, Any]) -> str:
    """Render a template string with variables.

    Args:
        template_str: Template string with {variable} placeholders
        variables: Dictionary of variables to substitute

    Returns:
        Rendered template string
    """
    result = template_str
    for key, value in variables.items():
        result = result.replace("{" + key + "}", str(value) if value is not None else "")
    # Remove any remaining unreplaced placeholders (case-insensitive, alphanumeric + underscore)
    result = re.sub(r"\{[a-zA-Z0-9_]+\}", "", result)
    return result


def _format_from_address(from_name: str | None, from_email: str) -> str:
    """Format a From header from optional display name and email address."""
    cleaned_email = (from_email or "").strip()
    if "<" in cleaned_email and ">" in cleaned_email:
        return cleaned_email
    cleaned_name = (from_name or "").strip()
    if cleaned_name:
        return f"{cleaned_name} <{cleaned_email}>"
    return cleaned_email


def _parse_recipient_emails(to_email: str | None) -> list[str]:
    """Parse colon-, comma-, or semicolon-separated email addresses."""
    if not to_email:
        return []
    return [addr.strip() for addr in re.split(r"[:;,]+", to_email) if addr.strip()]


async def _get_smtp_settings_from_notification_provider(db: AsyncSession) -> SMTPSettings | None:
    """Fall back to an enabled email/Resend/Mailgun NotificationProvider whose To Email is empty."""
    import json

    from backend.app.models.notification import NotificationProvider

    try:
        with db.no_autoflush:
            result = await db.execute(
                select(NotificationProvider)
                .where(
                    NotificationProvider.enabled.is_(True),
                    NotificationProvider.provider_type.in_(["resend", "mailgun", "email"]),
                )
                .order_by(NotificationProvider.id.asc())
            )
        providers = result.scalars().all()
    except Exception:
        return None

    for provider in providers:
        try:
            cfg = json.loads(provider.config) if isinstance(provider.config, str) else (provider.config or {})
        except Exception:
            continue
        if _parse_recipient_emails(cfg.get("to_email")):
            # Provider has explicit fixed recipients; do not use as user-gateway fallback.
            continue
        from_email = (cfg.get("from_email") or "").strip()
        if not from_email:
            continue
        from_name = (cfg.get("from_name") or "BamBuddy").strip() or "BamBuddy"
        reply_to = (cfg.get("reply_to") or "").strip() or None

        if provider.provider_type == "resend":
            api_key = (cfg.get("api_key") or "").strip()
            if api_key:
                return SMTPSettings(
                    email_provider="resend",
                    smtp_from_email=from_email,
                    smtp_from_name=from_name,
                    smtp_reply_to=reply_to,
                    resend_api_key=api_key,
                    resend_api_key_configured=True,
                )
        elif provider.provider_type == "mailgun":
            api_key = (cfg.get("api_key") or "").strip()
            domain = (cfg.get("domain") or "").strip()
            region = (cfg.get("region") or "us").strip().lower()
            if region not in ("us", "eu"):
                region = "us"
            if api_key and domain:
                return SMTPSettings(
                    email_provider="mailgun",
                    smtp_from_email=from_email,
                    smtp_from_name=from_name,
                    smtp_reply_to=reply_to,
                    mailgun_api_key=api_key,
                    mailgun_domain=domain,
                    mailgun_region=region,  # type: ignore[arg-type]
                    mailgun_api_key_configured=True,
                )
        elif provider.provider_type == "email":
            smtp_host = (cfg.get("smtp_server") or "").strip()
            if smtp_host:
                try:
                    smtp_port = int(cfg.get("smtp_port") or 587)
                except (TypeError, ValueError):
                    smtp_port = 587
                security = (cfg.get("security") or "starttls").strip()
                auth_enabled = str(cfg.get("auth_enabled", "true")).lower() == "true"
                username = (cfg.get("username") or "").strip() or None
                password = (cfg.get("password") or "").strip() or None
                return SMTPSettings(
                    email_provider="smtp",
                    smtp_host=smtp_host,
                    smtp_port=smtp_port,
                    smtp_username=username,
                    smtp_password=password,
                    smtp_security=security,
                    smtp_auth_enabled=auth_enabled,
                    smtp_from_email=from_email,
                    smtp_from_name=from_name,
                    smtp_reply_to=reply_to,
                    smtp_password_configured=bool(password),
                )
    return None


async def get_smtp_settings(db: AsyncSession) -> SMTPSettings | None:
    """Get email gateway settings (SMTP, Resend, or Mailgun) from database.

    Args:
        db: Database session

    Returns:
        SMTPSettings object or None if not configured
    """
    with db.no_autoflush:
        result = await db.execute(
            select(Settings).where(
                Settings.key.in_(
                    [
                        "email_provider",
                        "smtp_host",
                        "smtp_port",
                        "smtp_username",
                        "smtp_password",
                        "smtp_use_tls",
                        "smtp_security",
                        "smtp_auth_enabled",
                        "smtp_from_email",
                        "smtp_from_name",
                        "smtp_reply_to",
                        "resend_api_key",
                        "mailgun_api_key",
                        "mailgun_domain",
                        "mailgun_region",
                    ]
                )
            )
        )
    settings_dict = {s.key: s.value for s in result.scalars().all()}

    provider = (settings_dict.get("email_provider") or "smtp").strip().lower()
    if provider not in ("smtp", "resend", "mailgun"):
        provider = "smtp"

    from_email = (settings_dict.get("smtp_from_email") or "").strip()
    if not from_email:
        return await _get_smtp_settings_from_notification_provider(db)

    if provider == "smtp":
        if not settings_dict.get("smtp_host") or not settings_dict.get("smtp_port"):
            return await _get_smtp_settings_from_notification_provider(db)
    elif provider == "resend":
        if not settings_dict.get("resend_api_key"):
            return await _get_smtp_settings_from_notification_provider(db)
    elif provider == "mailgun":
        if not settings_dict.get("mailgun_api_key") or not settings_dict.get("mailgun_domain"):
            return await _get_smtp_settings_from_notification_provider(db)

    # Handle migration: convert old smtp_use_tls to smtp_security if needed
    smtp_security = settings_dict.get("smtp_security")
    if not smtp_security:
        smtp_use_tls = settings_dict.get("smtp_use_tls", "true").lower() == "true"
        smtp_security = "starttls" if smtp_use_tls else "ssl"

    smtp_auth_enabled = settings_dict.get("smtp_auth_enabled", "true").lower() == "true"
    try:
        smtp_port = int(settings_dict.get("smtp_port") or 587)
    except (TypeError, ValueError):
        smtp_port = 587

    mailgun_region = (settings_dict.get("mailgun_region") or "us").strip().lower()
    if mailgun_region not in ("us", "eu"):
        mailgun_region = "us"

    return SMTPSettings(
        email_provider=provider,  # type: ignore[arg-type]
        smtp_host=settings_dict.get("smtp_host", ""),
        smtp_port=smtp_port,
        smtp_username=settings_dict.get("smtp_username"),
        smtp_password=settings_dict.get("smtp_password"),
        smtp_security=smtp_security,
        smtp_auth_enabled=smtp_auth_enabled,
        smtp_from_email=from_email,
        smtp_from_name=settings_dict.get("smtp_from_name", "BamBuddy"),
        smtp_reply_to=settings_dict.get("smtp_reply_to") or None,
        resend_api_key=settings_dict.get("resend_api_key"),
        mailgun_api_key=settings_dict.get("mailgun_api_key"),
        mailgun_domain=settings_dict.get("mailgun_domain"),
        mailgun_region=mailgun_region,  # type: ignore[arg-type]
        smtp_password_configured=bool(settings_dict.get("smtp_password")),
        resend_api_key_configured=bool(settings_dict.get("resend_api_key")),
        mailgun_api_key_configured=bool(settings_dict.get("mailgun_api_key")),
    )


async def save_smtp_settings(db: AsyncSession, smtp_settings: SMTPSettings) -> None:
    """Save email gateway settings (SMTP, Resend, or Mailgun) to database.

    Args:
        db: Database session
        smtp_settings: Email gateway settings to save
    """
    from backend.app.core.db_dialect import upsert_setting

    provider = (getattr(smtp_settings, "email_provider", None) or "smtp").strip().lower()
    if provider not in ("smtp", "resend", "mailgun"):
        provider = "smtp"

    settings_data: dict[str, str] = {
        "email_provider": provider,
        "smtp_host": smtp_settings.smtp_host or "",
        "smtp_port": str(smtp_settings.smtp_port or 587),
        "smtp_security": smtp_settings.smtp_security or "starttls",
        "smtp_auth_enabled": "true" if smtp_settings.smtp_auth_enabled else "false",
        "smtp_from_email": smtp_settings.smtp_from_email,
        "smtp_from_name": smtp_settings.smtp_from_name or "BamBuddy",
        "smtp_reply_to": (getattr(smtp_settings, "smtp_reply_to", None) or "").strip(),
        "mailgun_domain": (getattr(smtp_settings, "mailgun_domain", None) or "").strip(),
        "mailgun_region": (getattr(smtp_settings, "mailgun_region", None) or "us").strip().lower(),
    }

    if smtp_settings.smtp_username is not None:
        settings_data["smtp_username"] = smtp_settings.smtp_username

    # Only overwrite secrets when non-empty values are provided
    if smtp_settings.smtp_password:
        settings_data["smtp_password"] = smtp_settings.smtp_password
    if getattr(smtp_settings, "resend_api_key", None):
        settings_data["resend_api_key"] = smtp_settings.resend_api_key  # type: ignore[assignment]
    if getattr(smtp_settings, "mailgun_api_key", None):
        settings_data["mailgun_api_key"] = smtp_settings.mailgun_api_key  # type: ignore[assignment]

    for key, value in settings_data.items():
        await upsert_setting(db, Settings, key, value)


def send_via_resend_sync(
    *,
    api_key: str,
    from_email: str,
    from_name: str | None,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    reply_to: str | None = None,
    image_data: bytes | None = None,
    image_cid: str = "bambuddy-inline-photo",
) -> None:
    """Send an email via the Resend HTTP API (https://api.resend.com/emails)."""
    if not api_key or not api_key.strip():
        raise ValueError("Resend API key is required")
    recipients = _parse_recipient_emails(to_email)
    if not recipients:
        raise ValueError("Recipient email address is required")

    payload: dict[str, Any] = {
        "from": _format_from_address(from_name, from_email),
        "to": recipients,
        "subject": subject,
        "text": body_text,
    }
    if body_html:
        payload["html"] = body_html
    if reply_to and reply_to.strip():
        payload["reply_to"] = reply_to.strip()
    if image_data and body_html:
        att: dict[str, Any] = {
            "filename": "photo.jpg",
            "content": base64.b64encode(image_data).decode("ascii"),
            "content_type": "image/jpeg",
        }
        if image_cid:
            att["content_id"] = image_cid
        payload["attachments"] = [att]

    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    with httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        response = client.post("https://api.resend.com/emails", json=payload, headers=headers)
    if response.status_code not in (200, 201, 202):
        detail = response.text[:200]
        try:
            body_json = response.json()
            if isinstance(body_json, dict) and body_json.get("message"):
                detail = str(body_json["message"])
        except Exception:
            pass
        raise RuntimeError(f"Resend API error (HTTP {response.status_code}): {detail}")


def send_via_mailgun_sync(
    *,
    api_key: str,
    domain: str,
    region: str = "us",
    from_email: str,
    from_name: str | None,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    reply_to: str | None = None,
    image_data: bytes | None = None,
    image_cid: str = "bambuddy-inline-photo",
) -> None:
    """Send an email via the Mailgun HTTP API."""
    if not api_key or not api_key.strip():
        raise ValueError("Mailgun API key is required")
    if not domain or not domain.strip():
        raise ValueError("Mailgun domain is required")
    recipients = _parse_recipient_emails(to_email)
    if not recipients:
        raise ValueError("Recipient email address is required")

    base_url = "https://api.eu.mailgun.net" if (region or "us").strip().lower() == "eu" else "https://api.mailgun.net"
    url = f"{base_url}/v3/{domain.strip()}/messages"

    data: dict[str, Any] = {
        "from": _format_from_address(from_name, from_email),
        "to": recipients,
        "subject": subject,
        "text": body_text,
    }
    if body_html:
        data["html"] = body_html
    if reply_to and reply_to.strip():
        data["h:Reply-To"] = reply_to.strip()

    files = None
    if image_data and body_html:
        files = [("inline", (image_cid, image_data, "image/jpeg"))]

    with httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0), headers={"User-Agent": _USER_AGENT}) as client:
        response = client.post(url, auth=("api", api_key.strip()), data=data, files=files)
    if response.status_code not in (200, 201, 202):
        detail = response.text[:200]
        try:
            body_json = response.json()
            if isinstance(body_json, dict) and body_json.get("message"):
                detail = str(body_json["message"])
        except Exception:
            pass
        raise RuntimeError(f"Mailgun API error (HTTP {response.status_code}): {detail}")


def send_email(
    smtp_settings: SMTPSettings,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
    image_data: bytes | None = None,
    image_cid: str = "bambuddy-inline-photo",
) -> None:
    """Send an email using the configured email gateway (SMTP, Resend, or Mailgun).

    Args:
        smtp_settings: Email gateway configuration
        to_email: Recipient email address
        subject: Email subject
        body_text: Plain text body
        body_html: Optional HTML body
        image_data: Optional JPEG bytes to embed inline. The caller is
            responsible for putting a matching ``<img src="cid:{image_cid}">``
            in body_html — this just attaches the bytes under that Content-ID.
        image_cid: Content-ID the embedded image is referenced by in body_html.

    Raises:
        Exception: If email sending fails
    """
    provider = (getattr(smtp_settings, "email_provider", None) or "smtp").strip().lower()
    reply_to = getattr(smtp_settings, "smtp_reply_to", None)

    try:
        if provider == "resend":
            send_via_resend_sync(
                api_key=getattr(smtp_settings, "resend_api_key", None) or "",
                from_email=smtp_settings.smtp_from_email,
                from_name=getattr(smtp_settings, "smtp_from_name", "BamBuddy"),
                to_email=to_email,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                reply_to=reply_to,
                image_data=image_data,
                image_cid=image_cid,
            )
            logger.info("Email sent successfully via Resend to %s", to_email)
            return

        if provider == "mailgun":
            send_via_mailgun_sync(
                api_key=getattr(smtp_settings, "mailgun_api_key", None) or "",
                domain=getattr(smtp_settings, "mailgun_domain", None) or "",
                region=getattr(smtp_settings, "mailgun_region", "us") or "us",
                from_email=smtp_settings.smtp_from_email,
                from_name=getattr(smtp_settings, "smtp_from_name", "BamBuddy"),
                to_email=to_email,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
                reply_to=reply_to,
                image_data=image_data,
                image_cid=image_cid,
            )
            logger.info("Email sent successfully via Mailgun to %s", to_email)
            return

        recipients = _parse_recipient_emails(to_email)
        if not recipients:
            raise ValueError("Recipient email address is required")
        to_header = ", ".join(recipients)

        if image_data and body_html:
            # multipart/related wraps multipart/alternative so the inline image
            # travels with the HTML part without becoming a visible attachment.
            msg = MIMEMultipart("related")
            msg["From"] = _format_from_address(smtp_settings.smtp_from_name, smtp_settings.smtp_from_email)
            msg["To"] = to_header
            msg["Subject"] = subject
            if reply_to:
                msg["Reply-To"] = reply_to

            alt = MIMEMultipart("alternative")
            alt.attach(MIMEText(body_text, "plain"))
            alt.attach(MIMEText(body_html, "html"))
            msg.attach(alt)

            img = MIMEImage(image_data, _subtype="jpeg")
            img.add_header("Content-ID", f"<{image_cid}>")
            img.add_header("Content-Disposition", "inline", filename="photo.jpg")
            msg.attach(img)
        else:
            msg = MIMEMultipart("alternative")
            msg["From"] = _format_from_address(smtp_settings.smtp_from_name, smtp_settings.smtp_from_email)
            msg["To"] = to_header
            msg["Subject"] = subject
            if reply_to:
                msg["Reply-To"] = reply_to

            # Attach plain text part
            msg.attach(MIMEText(body_text, "plain"))

            # Attach HTML part if provided
            if body_html:
                msg.attach(MIMEText(body_html, "html"))

        security = smtp_settings.smtp_security
        auth_enabled = smtp_settings.smtp_auth_enabled

        # Validate username is provided when authentication is enabled
        if auth_enabled and smtp_settings.smtp_password:
            if not smtp_settings.smtp_username:
                raise ValueError("SMTP username is required when authentication is enabled")

        if security == "ssl":
            # Direct SSL connection (typically port 465)
            with smtplib.SMTP_SSL(smtp_settings.smtp_host, smtp_settings.smtp_port, timeout=10) as server:
                if auth_enabled and smtp_settings.smtp_password and smtp_settings.smtp_username:
                    server.login(smtp_settings.smtp_username, smtp_settings.smtp_password)
                server.send_message(msg, to_addrs=recipients)
        elif security == "starttls":
            # STARTTLS upgrade (typically port 587)
            with smtplib.SMTP(smtp_settings.smtp_host, smtp_settings.smtp_port, timeout=10) as server:
                server.starttls()
                if auth_enabled and smtp_settings.smtp_password and smtp_settings.smtp_username:
                    server.login(smtp_settings.smtp_username, smtp_settings.smtp_password)
                server.send_message(msg, to_addrs=recipients)
        else:
            # No encryption (typically port 25) - use with caution
            with smtplib.SMTP(smtp_settings.smtp_host, smtp_settings.smtp_port, timeout=10) as server:
                if auth_enabled and smtp_settings.smtp_password and smtp_settings.smtp_username:
                    server.login(smtp_settings.smtp_username, smtp_settings.smtp_password)
                server.send_message(msg, to_addrs=recipients)
        logger.info(f"Email sent successfully to {to_email}")
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        raise


def infer_email_theme(subject: str, event_type: str | None = None) -> tuple[str, str]:
    """Infer the visual theme and status badge label from an email subject or event type.

    Returns:
        Tuple of (theme_name, badge_text) where theme_name is one of:
        'brand' (emerald), 'danger' (rose), 'warning' (amber), or 'security' (indigo).
    """
    ev = (event_type or "").lower()
    sub = (subject or "").lower()

    if any(k in ev or k in sub for k in ("password", "2fa", "verification", "otp", "security", "welcome", "account details")):
        if "welcome" in sub or "user_created" in ev:
            return "security", "ACCOUNT CREATED"
        if "2fa" in sub or "verification" in sub or "otp" in sub:
            return "security", "SECURITY VERIFICATION"
        return "security", "SECURITY & ACCESS"

    if any(k in ev or k in sub for k in ("failed", "error", "fault", "offline", "reject", "sensor")):
        return "danger", "ALERT - ATTENTION NEEDED"

    if any(
        k in ev or k in sub
        for k in (
            "plate",
            "clear",
            "waiting",
            "skipped",
            "stopped",
            "expir",
            "warning",
            "low_spool",
            "confirm",
            "maintenance",
        )
    ):
        return "warning", "ACTION REQUIRED"

    if any(k in ev or k in sub for k in ("complete", "finish", "ready", "success")):
        return "brand", "PRINT COMPLETED"

    if "test" in sub:
        return "brand", "GATEWAY TEST"

    return "brand", "PRINT NOTIFICATION"


def render_modern_email_html(
    title: str,
    body_html: str,
    *,
    theme: str | None = None,
    badge_text: str | None = None,
    event_type: str | None = None,
    details_html: str = "",
    cta_html: str = "",
    callout_html: str = "",
    footer_note: str = "",
) -> str:
    """Render a modern, responsive HTML email card compatible with major email clients.

    Preserves `<div style="font-size: 16px;">` around `body_html` and
    `padding: 30px; border-radius: 8px 8px 0 0;` on the header banner for backwards
    compatibility with existing template formatting tests.
    """
    inferred_theme, inferred_badge = infer_email_theme(title, event_type)
    resolved_theme = theme or inferred_theme
    resolved_badge = badge_text or inferred_badge

    palettes = {
        "brand": {
            "gradient": "linear-gradient(135deg, #00ae42 0%, #007a2f 100%)",
            "bg_fallback": "#00ae42",
            "accent": "#00ae42",
            "pill_bg": "rgba(255, 255, 255, 0.2)",
        },
        "danger": {
            "gradient": "linear-gradient(135deg, #e11d48 0%, #9f1239 100%)",
            "bg_fallback": "#e11d48",
            "accent": "#e11d48",
            "pill_bg": "rgba(255, 255, 255, 0.22)",
        },
        "warning": {
            "gradient": "linear-gradient(135deg, #d97706 0%, #92400e 100%)",
            "bg_fallback": "#d97706",
            "accent": "#d97706",
            "pill_bg": "rgba(255, 255, 255, 0.22)",
        },
        "security": {
            "gradient": "linear-gradient(135deg, #4f46e5 0%, #3730a3 100%)",
            "bg_fallback": "#4f46e5",
            "accent": "#4f46e5",
            "pill_bg": "rgba(255, 255, 255, 0.2)",
        },
    }
    palette = palettes.get(resolved_theme, palettes["brand"])
    timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    footer_note_block = (
        f'<p style="font-size: 13px; color: #64748b; border-top: 1px solid #e2e8f0; padding-top: 16px; margin: 24px 0 0 0;">{footer_note}</p>'
        if footer_note
        else ""
    )

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)}</title>
</head>
<body style="margin: 0; padding: 32px 16px; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.65; color: #1e293b; -webkit-font-smoothing: antialiased;">
    <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.08), 0 4px 10px -6px rgba(15, 23, 42, 0.04); overflow: hidden;">
        <div style="background: {palette["gradient"]}; background-color: {palette["bg_fallback"]}; padding: 30px; border-radius: 8px 8px 0 0;">
            <div style="margin-bottom: 12px;">
                <span style="display: inline-block; background-color: {palette["pill_bg"]}; color: #ffffff; font-size: 11px; font-weight: 700; letter-spacing: 1.1px; text-transform: uppercase; padding: 4px 10px; border-radius: 999px;">BAMBUDDY &bull; {html.escape(resolved_badge)}</span>
            </div>
            <h1 style="color: #ffffff; margin: 0; font-size: 23px; font-weight: 700; line-height: 1.3; letter-spacing: -0.3px; text-shadow: 0 1px 2px rgba(0,0,0,0.25);">{html.escape(title)}</h1>
        </div>
        <div style="background-color: #ffffff; padding: 32px 30px; color: #334155;">
            <div style="font-size: 16px;">{body_html}</div>
            {details_html}
            {cta_html}
            {callout_html}
            {footer_note_block}
        </div>
        <div style="background-color: #f8fafc; padding: 18px 30px; border-top: 1px solid #e2e8f0; text-align: center;">
            <p style="margin: 0; font-size: 12px; color: #64748b; font-weight: 500;">
                BamBuddy 3D Printer Management &bull; {timestamp_str}
            </p>
        </div>
    </div>
</body>
</html>
"""


def create_welcome_email(username: str, password: str, login_url: str) -> tuple[str, str, str]:
    """Create welcome email content for new user.

    Args:
        username: Username of the new user
        password: Auto-generated password
        login_url: URL to login page

    Returns:
        Tuple of (subject, text_body, html_body)
    """
    subject = "Welcome to BamBuddy - Your Account Details"

    text_body = f"""Welcome to BamBuddy!

Your account has been created. Here are your login details:

Username: {username}
Password: {password}

You can login at: {login_url}

For security reasons, please change your password after your first login.

Best regards,
BamBuddy Team
"""

    details_html = f"""
            <div style="background-color: #f8fafc; padding: 18px 20px; border-radius: 8px; margin: 22px 0; border: 1px solid #e2e8f0; border-left: 4px solid #4f46e5;">
                <p style="margin: 0 0 10px 0; font-size: 14px; color: #475569;"><strong style="color: #0f172a;">Username:</strong> <code style="background-color: #e2e8f0; color: #0f172a; padding: 3px 8px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 14px;">{html.escape(username)}</code></p>
                <p style="margin: 0; font-size: 14px; color: #475569;"><strong style="color: #0f172a;">Password:</strong> <code style="background-color: #e2e8f0; color: #0f172a; padding: 3px 8px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 14px;">{html.escape(password)}</code></p>
            </div>"""

    cta_html = f"""
            <div style="text-align: center; margin: 28px 0;">
                <a href="{html.escape(login_url)}" style="display: inline-block; background-color: #4f46e5; color: #ffffff; padding: 12px 32px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 15px; box-shadow: 0 2px 6px rgba(79, 70, 229, 0.25);">Login Now</a>
            </div>"""

    html_body = render_modern_email_html(
        "Welcome to BamBuddy!",
        "<p style=\"margin: 0;\">Your account has been created. Here are your login details:</p>",
        theme="security",
        badge_text="ACCOUNT CREATED",
        details_html=details_html,
        cta_html=cta_html,
        footer_note="<strong>Security Note:</strong> For security reasons, please change your password after your first login.",
    )

    return subject, text_body, html_body


def create_password_reset_email(username: str, password: str, login_url: str) -> tuple[str, str, str]:
    """Create password reset email content.

    Args:
        username: Username of the user
        password: New auto-generated password
        login_url: URL to login page

    Returns:
        Tuple of (subject, text_body, html_body)
    """
    subject = "BamBuddy - Your Password Has Been Reset"

    text_body = f"""Your BamBuddy password has been reset.

Your login details:

Username: {username}
New Password: {password}

You can login at: {login_url}

For security reasons, please change your password after logging in.

If you did not request this password reset, please contact your administrator immediately.

Best regards,
BamBuddy Team
"""

    details_html = f"""
            <div style="background-color: #f8fafc; padding: 18px 20px; border-radius: 8px; margin: 22px 0; border: 1px solid #e2e8f0; border-left: 4px solid #4f46e5;">
                <p style="margin: 0 0 10px 0; font-size: 14px; color: #475569;"><strong style="color: #0f172a;">Username:</strong> <code style="background-color: #e2e8f0; color: #0f172a; padding: 3px 8px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 14px;">{html.escape(username)}</code></p>
                <p style="margin: 0; font-size: 14px; color: #475569;"><strong style="color: #0f172a;">New Password:</strong> <code style="background-color: #e2e8f0; color: #0f172a; padding: 3px 8px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 14px;">{html.escape(password)}</code></p>
            </div>"""

    cta_html = f"""
            <div style="text-align: center; margin: 28px 0;">
                <a href="{html.escape(login_url)}" style="display: inline-block; background-color: #4f46e5; color: #ffffff; padding: 12px 32px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 15px; box-shadow: 0 2px 6px rgba(79, 70, 229, 0.25);">Login Now</a>
            </div>"""

    callout_html = """
            <div style="background-color: #fffbeb; border: 1px solid #fde68a; border-left: 4px solid #f59e0b; border-radius: 8px; padding: 14px 18px; margin: 22px 0;">
                <p style="margin: 0; font-size: 14px; color: #92400e;">
                    <strong>⚠️ Security Alert:</strong> If you did not request this password reset, please contact your administrator immediately.
                </p>
            </div>"""

    html_body = render_modern_email_html(
        "Password Reset",
        "<p style=\"margin: 0;\">Your BamBuddy password has been reset.</p>",
        theme="security",
        badge_text="SECURITY & ACCESS",
        details_html=details_html,
        cta_html=cta_html,
        callout_html=callout_html,
        footer_note="<strong>Security Note:</strong> For security reasons, please change your password after logging in.",
    )

    return subject, text_body, html_body


def create_password_reset_link_email(username: str, reset_url: str) -> tuple[str, str, str]:
    """Create a password-reset email that contains a secure link (not a plaintext password)."""
    subject = "BamBuddy - Password Reset Request"

    text_body = f"""A password reset was requested for your BamBuddy account.

Username: {username}

Click the link below to set a new password (valid for 1 hour):
{reset_url}

If you did not request this reset, you can safely ignore this email.

Best regards,
BamBuddy Team
"""

    cta_html = f"""
            <div style="text-align: center; margin: 28px 0;">
                <a href="{html.escape(reset_url)}" style="display: inline-block; background-color: #4f46e5; color: #ffffff; padding: 12px 32px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 15px; box-shadow: 0 2px 6px rgba(79, 70, 229, 0.25);">Reset Password</a>
            </div>"""

    callout_html = """
            <div style="background-color: #fffbeb; border: 1px solid #fde68a; border-left: 4px solid #f59e0b; border-radius: 8px; padding: 14px 18px; margin: 22px 0;">
                <p style="margin: 0; font-size: 14px; color: #92400e;">
                    <strong>Did not request this?</strong> You can safely ignore this email. Your password has not been changed.
                </p>
            </div>"""

    html_body = render_modern_email_html(
        "Password Reset Request",
        (
            f"<p style=\"margin: 0 0 12px 0;\">A password reset was requested for your BamBuddy account (<strong>{html.escape(username)}</strong>).</p>"
            "<p style=\"margin: 0;\">Click the button below to set a new password. This link is valid for <strong>1 hour</strong>.</p>"
        ),
        theme="security",
        badge_text="SECURITY & ACCESS",
        cta_html=cta_html,
        callout_html=callout_html,
    )
    return subject, text_body, html_body


async def create_password_reset_link_email_from_template(
    db: AsyncSession, username: str, reset_url: str
) -> tuple[str, str, str]:
    """Create password-reset link email, using DB template if configured."""
    template = await get_notification_template(db, "password_reset_link")
    if template:
        variables = {"username": username, "reset_url": reset_url}
        subject = render_template(template.subject or "BamBuddy - Password Reset Request", variables)
        text_body = render_template(template.body or "", variables)
        html_body = render_template(template.html_body or "", variables) if template.html_body else None
        if not html_body:
            _, text_body, html_body = create_password_reset_link_email(username, reset_url)
            return subject, text_body, html_body
        return subject, text_body, html_body
    return create_password_reset_link_email(username, reset_url)


async def create_welcome_email_from_template(
    db: AsyncSession, username: str, password: str, login_url: str, app_name: str = "BamBuddy"
) -> tuple[str, str, str]:
    """Create welcome email content using notification template from database.

    Args:
        db: Database session
        username: Username of the new user
        password: Auto-generated password
        login_url: URL to login page
        app_name: Application name (default: BamBuddy)

    Returns:
        Tuple of (subject, text_body, html_body)
    """
    # Try to get template from database
    template = await get_notification_template(db, "user_created")

    if template:
        # Render template with variables
        variables = {
            "app_name": app_name,
            "username": username,
            "password": password,
            "login_url": login_url,
        }

        subject = render_template(template.title_template, variables)
        text_body = render_template(template.body_template, variables)

        # Create HTML version with embedded login button
        # Escape text_body to prevent XSS vulnerabilities and convert newlines to <br> tags
        escaped_text_body = html.escape(text_body).replace("\n", "<br>\n")
        cta_html = f"""
            <div style="text-align: center; margin: 28px 0;">
                <a href="{html.escape(login_url)}" style="display: inline-block; background-color: #4f46e5; color: #ffffff; padding: 12px 32px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 15px; box-shadow: 0 2px 6px rgba(79, 70, 229, 0.25);">Login Now</a>
            </div>"""

        html_body = render_modern_email_html(
            subject,
            escaped_text_body,
            theme="security",
            badge_text="ACCOUNT CREATED",
            cta_html=cta_html,
        )

        logger.info("Using custom welcome email template from database")
        return subject, text_body, html_body
    else:
        # Fallback to hardcoded template
        logger.warning("No welcome email template found in database, using default")
        return create_welcome_email(username, password, login_url)


async def create_password_reset_email_from_template(
    db: AsyncSession, username: str, password: str, login_url: str, app_name: str = "BamBuddy"
) -> tuple[str, str, str]:
    """Create password reset email content using notification template from database.

    Args:
        db: Database session
        username: Username of the user
        password: New auto-generated password
        login_url: URL to login page
        app_name: Application name (default: BamBuddy)

    Returns:
        Tuple of (subject, text_body, html_body)
    """
    # Try to get template from database
    template = await get_notification_template(db, "password_reset")

    if template:
        # Render template with variables
        variables = {
            "app_name": app_name,
            "username": username,
            "password": password,
            "login_url": login_url,
        }

        subject = render_template(template.title_template, variables)
        text_body = render_template(template.body_template, variables)

        # Create HTML version with embedded login button
        # Escape text_body to prevent XSS vulnerabilities and convert newlines to <br> tags
        escaped_text_body = html.escape(text_body).replace("\n", "<br>\n")
        cta_html = f"""
            <div style="text-align: center; margin: 28px 0;">
                <a href="{html.escape(login_url)}" style="display: inline-block; background-color: #4f46e5; color: #ffffff; padding: 12px 32px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 15px; box-shadow: 0 2px 6px rgba(79, 70, 229, 0.25);">Login Now</a>
            </div>"""
        callout_html = """
            <div style="background-color: #fffbeb; border: 1px solid #fde68a; border-left: 4px solid #f59e0b; border-radius: 8px; padding: 14px 18px; margin: 22px 0;">
                <p style="margin: 0; font-size: 14px; color: #92400e;">
                    <strong>⚠️ Security Alert:</strong> If you did not request this password reset, please contact your administrator immediately.
                </p>
            </div>"""

        html_body = render_modern_email_html(
            subject,
            escaped_text_body,
            theme="security",
            badge_text="SECURITY & ACCESS",
            cta_html=cta_html,
            callout_html=callout_html,
        )

        logger.info("Using custom password reset email template from database")
        return subject, text_body, html_body
    else:
        # Fallback to hardcoded template
        logger.warning("No password reset email template found in database, using default")
        return create_password_reset_email(username, password, login_url)


async def send_user_print_notification(
    db: AsyncSession,
    event_type: str,
    user_email: str,
    username: str,
    variables: dict,
    image_data: bytes | None = None,
) -> None:
    """Send a print notification email to a user using Advanced Auth SMTP settings.

    Args:
        db: Database session
        event_type: One of 'user_print_start', 'user_print_complete', 'user_print_failed', 'user_print_stopped'
        user_email: Recipient email address
        username: Username of the recipient
        variables: Template variables (printer, filename, etc.)
        image_data: Camera snapshot bytes, if one was captured for the event.
            Inlined only when the template explicitly references
            {finish_photo_url} — same opt-in as the provider-based email path
            (#1792), so a user who never asked for a photo never gets one.
    """
    # Check that advanced auth is enabled (SMTP settings must be configured)
    smtp_settings = await get_smtp_settings(db)
    if not smtp_settings:
        logger.warning("Cannot send user print notification: SMTP settings not configured")
        return

    # Get the template
    template = await get_notification_template(db, event_type)
    if template is None:
        logger.warning("No template found for event type: %s", event_type)
        return

    # Add common variables (username, timestamp, app_name) merged with caller-supplied variables
    all_variables = {
        "username": username,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "app_name": "Bambuddy",
        **variables,
    }

    subject = render_template(template.title_template, all_variables)
    text_body = render_template(template.body_template, all_variables)

    finish_photo_url = variables.get("finish_photo_url")
    inline_photo_by_url = bool(image_data and finish_photo_url and finish_photo_url in text_body)
    inline_photo_by_event = bool(
        image_data
        and not inline_photo_by_url
        and event_type
        in (
            "user_plate_not_empty",
            "user_print_progress",
            "user_first_layer_complete",
            "user_print_confirm_request",
        )
    )
    inline_photo = inline_photo_by_url or inline_photo_by_event

    # Build HTML body — content comes entirely from the database template
    escaped_text_body = html.escape(text_body).replace("\n", "<br>\n")
    img_tag = (
        '<img src="cid:bambuddy-user-print-photo" '
        'alt="Printer camera snapshot" '
        'style="max-width:100%;height:auto;border:1px solid #e2e8f0;border-radius:8px;display:block;margin:18px auto 0 auto;box-shadow:0 4px 12px rgba(15,23,42,0.08);">'
    )
    if inline_photo_by_url and finish_photo_url:
        escaped_url = html.escape(finish_photo_url)
        escaped_text_body = escaped_text_body.replace(escaped_url, img_tag)
    elif inline_photo_by_event:
        escaped_text_body = f'{escaped_text_body}<div style="margin-top: 20px;">{img_tag}</div>'

    action_buttons_html = ""
    if event_type == "user_print_confirm_request":
        good_url = variables.get("good_url")
        reject_url = variables.get("reject_url")
        confirm_url = variables.get("confirm_url")
        if good_url and reject_url:
            action_buttons_html = f"""
            <div style="text-align: center; margin: 26px 0;">
                <a href="{html.escape(str(good_url))}" style="display: inline-block; background-color: #00ae42; color: #ffffff; padding: 11px 26px; text-decoration: none; border-radius: 8px; font-weight: 600; margin-right: 12px; box-shadow: 0 2px 6px rgba(0, 174, 66, 0.25);">👍 Good</a>
                <a href="{html.escape(str(reject_url))}" style="display: inline-block; background-color: #e11d48; color: #ffffff; padding: 11px 26px; text-decoration: none; border-radius: 8px; font-weight: 600; box-shadow: 0 2px 6px rgba(225, 29, 72, 0.25);">👎 Reject</a>
            </div>"""
        elif confirm_url:
            action_buttons_html = f"""
            <div style="text-align: center; margin: 26px 0;">
                <a href="{html.escape(str(confirm_url))}" style="display: inline-block; background-color: #00ae42; color: #ffffff; padding: 11px 26px; text-decoration: none; border-radius: 8px; font-weight: 600; box-shadow: 0 2px 6px rgba(0, 174, 66, 0.25);">Confirm Print Outcome</a>
            </div>"""

    html_body = render_modern_email_html(
        subject,
        escaped_text_body,
        event_type=event_type,
        cta_html=action_buttons_html,
    )

    try:
        send_email(
            smtp_settings,
            user_email,
            subject,
            text_body,
            html_body,
            image_data=image_data if inline_photo else None,
            image_cid="bambuddy-user-print-photo",
        )
        logger.info("Sent %s notification email to %s", event_type, user_email)
    except Exception as e:
        logger.error("Failed to send %s notification to %s: %s", event_type, user_email, e)


async def send_user_expiration_warning(
    db: AsyncSession,
    user_email: str,
    username: str,
    expires_at: datetime,
    time_remaining: str,
    target_label: str = "account",
) -> bool:
    """Send an impending account or group expiration warning email to a user.

    Returns True if the email was sent (or rendered when SMTP is mocked), False if SMTP is not configured.
    """
    smtp_settings = await get_smtp_settings(db)
    if not smtp_settings:
        logger.debug("Skipping expiration warning email for %s: SMTP not configured", username)
        return False

    template = await get_notification_template(db, "user_expiration_warning")
    expires_str = expires_at.strftime("%Y-%m-%d %H:%M UTC")
    variables = {
        "username": username,
        "app_name": "BamBuddy",
        "expires_at": expires_str,
        "time_remaining": time_remaining,
        "target_label": target_label,
    }

    if template:
        subject = render_template(template.title_template, variables)
        text_body = render_template(template.body_template, variables)
    else:
        subject = f"BamBuddy - Access Expires in {time_remaining}"
        text_body = (
            f"Hello {username},\n\n"
            f"Your {target_label} access on BamBuddy is scheduled to expire on {expires_str}.\n\n"
            "Please make sure any queued prints are completed and download any 3MF files or logs you need before then."
        )

    escaped_text_body = html.escape(text_body).replace("\n", "<br>\n")
    html_body = render_modern_email_html(
        subject,
        escaped_text_body,
        theme="warning",
        badge_text="ACCESS EXPIRING SOON",
    )

    try:
        send_email(smtp_settings, user_email, subject, text_body, html_body)
        logger.info("Sent expiration warning (%s) email to %s", time_remaining, user_email)
        return True
    except Exception as e:
        logger.error("Failed to send expiration warning email to %s: %s", user_email, e)
        return False
