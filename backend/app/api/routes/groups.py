"""Group management API routes."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.api.routes.users import purge_user_account
from backend.app.core.auth import RequireAdminIfAuthEnabled, RequirePermissionIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.core.permissions import (
    ALL_PERMISSIONS,
    PERMISSION_CATEGORIES,
    Permission,
)
from backend.app.models.enrollment_invite import EnrollmentInvite
from backend.app.models.group import Group, user_groups
from backend.app.models.library import LibraryFolderPermission
from backend.app.models.user import User
from backend.app.utils.library_paths import remove_library_photos_dir
from backend.app.utils.local_time import to_naive_utc
from backend.app.schemas.group import (
    GroupCreate,
    GroupDetailResponse,
    GroupResponse,
    GroupUpdate,
    PermissionCategory,
    PermissionInfo,
    PermissionsListResponse,
    UserBrief,
)

router = APIRouter(prefix="/groups", tags=["groups"])


# Permissions whose derived label would misdescribe what is being granted.
# The derived form for USERS_READ_SLIM is "Read Slim Users", which reads as a
# property of the users rather than of the response -- and an admin ticking a
# box in the group editor has nothing else to go on (#1894).
_PERMISSION_LABEL_OVERRIDES: dict[Permission, str] = {
    Permission.USERS_READ_SLIM: "List User Names (id + username only)",
}


def _permission_label(perm: Permission) -> str:
    """Convert permission enum to human-readable label."""
    if perm in _PERMISSION_LABEL_OVERRIDES:
        return _PERMISSION_LABEL_OVERRIDES[perm]
    # e.g., "printers:read" -> "Read Printers"
    parts = perm.value.split(":")
    if len(parts) == 2:
        resource, action = parts
        resource = resource.replace("_", " ").title()
        action = action.replace("_", " ").title()
        return f"{action} {resource}"
    return perm.value


@router.get("/permissions", response_model=PermissionsListResponse)
async def list_permissions(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_READ),
):
    """List all available permissions organized by category."""
    categories = []
    for name, perms in PERMISSION_CATEGORIES.items():
        categories.append(
            PermissionCategory(
                name=name,
                permissions=[PermissionInfo(value=p.value, label=_permission_label(p)) for p in perms],
            )
        )
    return PermissionsListResponse(
        categories=categories,
        all_permissions=ALL_PERMISSIONS,
    )


def _group_to_response(group: Group, user_count: int | None = None) -> GroupResponse:
    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        permissions=group.permissions or [],
        is_system=group.is_system,
        user_count=len(group.users) if user_count is None else user_count,
        expires_at=group.expires_at,
        is_expired=group.is_expired,
        max_print_duration_minutes=group.max_print_duration_minutes,
        max_filament_grams_per_job=group.max_filament_grams_per_job,
        max_active_queue_items=group.max_active_queue_items,
        requires_approval=bool(group.requires_approval),
        allowed_hours_start=group.allowed_hours_start,
        allowed_hours_end=group.allowed_hours_end,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


@router.get("", response_model=list[GroupResponse])
@router.get("/", response_model=list[GroupResponse])
async def list_groups(
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_READ),
    db: AsyncSession = Depends(get_db),
):
    """List all groups."""
    result = await db.execute(select(Group).options(selectinload(Group.users)).order_by(Group.name))
    groups = result.scalars().all()
    return [_group_to_response(group) for group in groups]


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    group_data: GroupCreate,
    _admin: User | None = RequireAdminIfAuthEnabled(),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_CREATE),
    db: AsyncSession = Depends(get_db),
):
    """Create a new group."""
    # Check if group name already exists
    existing = await db.execute(select(Group).where(Group.name == group_data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Group name already exists",
        )

    # Validate permissions
    invalid_perms = [p for p in group_data.permissions if p not in ALL_PERMISSIONS]
    if invalid_perms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid permissions: {', '.join(invalid_perms)}",
        )

    group = Group(
        name=group_data.name,
        description=group_data.description,
        permissions=group_data.permissions,
        is_system=False,  # User-created groups are not system groups
        expires_at=to_naive_utc(group_data.expires_at),
        max_print_duration_minutes=group_data.max_print_duration_minutes,
        max_filament_grams_per_job=group_data.max_filament_grams_per_job,
        max_active_queue_items=group_data.max_active_queue_items,
        requires_approval=bool(group_data.requires_approval),
        allowed_hours_start=group_data.allowed_hours_start,
        allowed_hours_end=group_data.allowed_hours_end,
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)

    return _group_to_response(group, user_count=0)


@router.get("/{group_id}", response_model=GroupDetailResponse)
async def get_group(
    group_id: int,
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_READ),
    db: AsyncSession = Depends(get_db),
):
    """Get a group by ID with user list. Read-only — gated on
    ``GROUPS_READ`` only."""
    result = await db.execute(select(Group).where(Group.id == group_id).options(selectinload(Group.users)))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    base = _group_to_response(group)
    return GroupDetailResponse(
        **base.model_dump(),
        users=[UserBrief(id=u.id, username=u.username, is_active=u.is_active) for u in group.users],
    )


@router.patch("/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: int,
    group_data: GroupUpdate,
    _admin: User | None = RequireAdminIfAuthEnabled(),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    """Update a group."""
    result = await db.execute(select(Group).where(Group.id == group_id).options(selectinload(Group.users)))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    # Check if updating name to one that already exists
    if group_data.name is not None and group_data.name != group.name:
        existing = await db.execute(select(Group).where(Group.name == group_data.name, Group.id != group_id))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Group name already exists",
            )
        # System groups cannot have their name changed
        if group.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot rename system groups",
            )
        group.name = group_data.name

    if group_data.description is not None:
        group.description = group_data.description

    if group_data.permissions is not None:
        # System groups (Administrators in particular) have fixed permission
        # sets that the app depends on — stripping them is a denial-of-
        # service vector that even admin callers shouldn't trigger by
        # accident through the generic edit form. Mirrors the rename block
        # immediately above.
        if group.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot modify permissions of system groups",
            )
        # Validate permissions
        invalid_perms = [p for p in group_data.permissions if p not in ALL_PERMISSIONS]
        if invalid_perms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid permissions: {', '.join(invalid_perms)}",
            )
        group.permissions = group_data.permissions

    if "expires_at" in group_data.model_fields_set:
        if group.is_system and group_data.expires_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot set expiration date on system groups",
            )
        group.expires_at = to_naive_utc(group_data.expires_at)
        group.expiry_warned_7d_at = None
        group.expiry_warned_1d_at = None

    for quota_field in (
        "max_print_duration_minutes",
        "max_filament_grams_per_job",
        "max_active_queue_items",
        "allowed_hours_start",
        "allowed_hours_end",
    ):
        if quota_field in group_data.model_fields_set:
            setattr(group, quota_field, getattr(group_data, quota_field))

    if group_data.requires_approval is not None:
        group.requires_approval = group_data.requires_approval

    await db.commit()
    await db.refresh(group)

    return _group_to_response(group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int,
    delete_users: bool = Query(
        False,
        description="Also delete non-admin users who belong solely to this group",
    ),
    delete_user_items: bool = Query(
        False,
        description="When delete_users=True, also delete archives, queue items, and library files created by those users",
    ),
    _admin: User | None = RequireAdminIfAuthEnabled(),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_DELETE),
    db: AsyncSession = Depends(get_db),
):
    """Delete a group (non-system groups only).

    If ``delete_users=True``, users whose **sole** group membership is this
    group (and who are neither admins nor the currently authenticated user)
    are deleted along with the group. Users who also belong to other groups
    are kept and simply removed from this group.
    """
    result = await db.execute(
        select(Group)
        .where(Group.id == group_id)
        .options(selectinload(Group.users).selectinload(User.groups))
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    if group.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete system groups",
        )

    doomed_library_file_ids: list[int] = []
    if delete_users:
        for member in list(group.users):
            if member.is_admin:
                continue
            if current_user and member.id == current_user.id:
                continue
            other_groups = [g for g in member.groups if g.id != group.id]
            if not other_groups:
                file_ids = await purge_user_account(db, member, delete_items=delete_user_items)
                doomed_library_file_ids.extend(file_ids)

    await db.execute(delete(LibraryFolderPermission).where(LibraryFolderPermission.group_id == group_id))
    await db.execute(update(EnrollmentInvite).where(EnrollmentInvite.group_id == group_id).values(group_id=None))

    await db.delete(group)
    await db.commit()

    for file_id in doomed_library_file_ids:
        remove_library_photos_dir(file_id)


@router.post("/{group_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_user_to_group(
    group_id: int,
    user_id: int,
    expires_at: datetime | None = Query(default=None),
    _admin: User | None = RequireAdminIfAuthEnabled(),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    """Add a user to a group (with optional per-membership expiration datetime)."""
    # Get group with users
    result = await db.execute(select(Group).where(Group.id == group_id).options(selectinload(Group.users)))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    # Get user
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Check if user is already in group
    if user in group.users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already in this group",
        )

    await db.execute(
        insert(user_groups).values(
            user_id=user.id,
            group_id=group.id,
            expires_at=to_naive_utc(expires_at),
        )
    )
    await db.commit()


@router.delete("/{group_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user_from_group(
    group_id: int,
    user_id: int,
    _admin: User | None = RequireAdminIfAuthEnabled(),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.GROUPS_UPDATE),
    db: AsyncSession = Depends(get_db),
):
    """Remove a user from a group."""
    # Get group with users
    result = await db.execute(select(Group).where(Group.id == group_id).options(selectinload(Group.users)))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    # Get user
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Check if user is in group
    if user not in group.users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not in this group",
        )

    group.users.remove(user)
    await db.commit()
