"""API routes for user email notification preferences."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.auth import RequireAdminIfAuthEnabled, RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.user import User
from backend.app.models.user_email_pref import UserEmailPreference
from backend.app.schemas.user_notifications import UserEmailPreferenceResponse, UserEmailPreferenceUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user-notifications", tags=["user-notifications"])

_OPTIONAL_PREF_FIELDS = (
    "notify_print_progress",
    "notify_first_layer_complete",
    "notify_print_confirm_request",
    "notify_plate_clear_required",
    "notify_plate_not_empty",
    "notify_queue_waiting",
    "notify_queue_skipped",
    "notify_queue_failed",
)

_ADMIN_ONLY_DISABLE_FIELDS = (
    "notify_plate_clear_required",
    "notify_plate_not_empty",
)


def _default_preferences() -> UserEmailPreferenceResponse:
    return UserEmailPreferenceResponse(
        notify_print_start=True,
        notify_print_complete=True,
        notify_print_failed=True,
        notify_print_stopped=True,
        notify_print_progress=True,
        notify_first_layer_complete=True,
        notify_print_confirm_request=True,
        notify_plate_clear_required=True,
        notify_plate_not_empty=True,
        notify_queue_waiting=True,
        notify_queue_skipped=True,
        notify_queue_failed=True,
    )


async def _is_user_admin(db: AsyncSession, user: User) -> bool:
    result = await db.execute(select(User).where(User.id == user.id).options(selectinload(User.groups)))
    loaded = result.scalar_one_or_none()
    return bool(loaded and loaded.is_admin)


def _apply_preference_update(
    pref: UserEmailPreference,
    data: UserEmailPreferenceUpdate,
    *,
    is_admin: bool,
) -> None:
    for field in _ADMIN_ONLY_DISABLE_FIELDS:
        incoming = getattr(data, field)
        current = getattr(pref, field, True)
        if incoming is False and not is_admin and current is not False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators can disable plate clear notifications",
            )

    pref.notify_print_start = data.notify_print_start
    pref.notify_print_complete = data.notify_print_complete
    pref.notify_print_failed = data.notify_print_failed
    pref.notify_print_stopped = data.notify_print_stopped

    for field in _OPTIONAL_PREF_FIELDS:
        val = getattr(data, field)
        if val is not None:
            setattr(pref, field, val)


@router.get("/preferences", response_model=UserEmailPreferenceResponse)
async def get_user_email_preferences(
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.NOTIFICATIONS_USER_EMAIL),
    db: AsyncSession = Depends(get_db),
):
    """Get the current user's email notification preferences.

    Returns defaults (all enabled) if no preferences are saved yet.
    """
    if current_user is None:
        # Auth is disabled; no user context available, return defaults
        return _default_preferences()

    result = await db.execute(select(UserEmailPreference).where(UserEmailPreference.user_id == current_user.id))
    pref = result.scalar_one_or_none()

    if pref is None:
        return _default_preferences()

    return pref


@router.put("/preferences", response_model=UserEmailPreferenceResponse)
async def update_user_email_preferences(
    data: UserEmailPreferenceUpdate,
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.NOTIFICATIONS_USER_EMAIL),
    db: AsyncSession = Depends(get_db),
):
    """Update the current user's email notification preferences."""
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication must be enabled to save user notification preferences",
        )

    if not current_user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User must have an email address to receive notifications",
        )

    is_admin = await _is_user_admin(db, current_user)

    result = await db.execute(select(UserEmailPreference).where(UserEmailPreference.user_id == current_user.id))
    pref = result.scalar_one_or_none()

    if pref is None:
        pref = UserEmailPreference(
            user_id=current_user.id,
            notify_print_start=True,
            notify_print_complete=True,
            notify_print_failed=True,
            notify_print_stopped=True,
            notify_print_progress=True,
            notify_first_layer_complete=True,
            notify_print_confirm_request=True,
            notify_plate_clear_required=True,
            notify_plate_not_empty=True,
            notify_queue_waiting=True,
            notify_queue_skipped=True,
            notify_queue_failed=True,
        )
        _apply_preference_update(pref, data, is_admin=is_admin)
        db.add(pref)
    else:
        _apply_preference_update(pref, data, is_admin=is_admin)

    await db.commit()
    await db.refresh(pref)

    logger.info(
        "Updated email notification preferences for user %s: start=%s, complete=%s, failed=%s, stopped=%s, plate_clear=%s",
        current_user.username,
        pref.notify_print_start,
        pref.notify_print_complete,
        pref.notify_print_failed,
        pref.notify_print_stopped,
        pref.notify_plate_clear_required,
    )

    return pref


@router.get("/preferences/{user_id}", response_model=UserEmailPreferenceResponse)
async def get_target_user_email_preferences(
    user_id: int,
    _admin: User | None = RequireAdminIfAuthEnabled(),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific user's email notification preferences (admin only)."""
    user_result = await db.execute(select(User).where(User.id == user_id))
    target_user = user_result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    result = await db.execute(select(UserEmailPreference).where(UserEmailPreference.user_id == user_id))
    pref = result.scalar_one_or_none()
    if pref is None:
        return _default_preferences()
    return pref


@router.put("/preferences/{user_id}", response_model=UserEmailPreferenceResponse)
async def update_target_user_email_preferences(
    user_id: int,
    data: UserEmailPreferenceUpdate,
    _admin: User | None = RequireAdminIfAuthEnabled(),
    db: AsyncSession = Depends(get_db),
):
    """Update a specific user's email notification preferences (admin only, allows toggling plate clear)."""
    user_result = await db.execute(select(User).where(User.id == user_id))
    target_user = user_result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    result = await db.execute(select(UserEmailPreference).where(UserEmailPreference.user_id == user_id))
    pref = result.scalar_one_or_none()

    if pref is None:
        pref = UserEmailPreference(
            user_id=user_id,
            notify_print_start=True,
            notify_print_complete=True,
            notify_print_failed=True,
            notify_print_stopped=True,
            notify_print_progress=True,
            notify_first_layer_complete=True,
            notify_print_confirm_request=True,
            notify_plate_clear_required=True,
            notify_plate_not_empty=True,
            notify_queue_waiting=True,
            notify_queue_skipped=True,
            notify_queue_failed=True,
        )
        _apply_preference_update(pref, data, is_admin=True)
        db.add(pref)
    else:
        _apply_preference_update(pref, data, is_admin=True)

    await db.commit()
    await db.refresh(pref)

    logger.info("Admin updated email notification preferences for user %s (id=%s)", target_user.username, user_id)
    return pref
