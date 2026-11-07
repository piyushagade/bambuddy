"""Pydantic schemas for Group CRUD operations."""

from datetime import datetime

from pydantic import BaseModel


class GroupBrief(BaseModel):
    """Brief group info for embedding in other responses."""

    id: int
    name: str
    expires_at: str | None = None
    is_expired: bool = False
    require_project: bool = False

    class Config:
        from_attributes = True


class GroupCreate(BaseModel):
    """Schema for creating a new group."""

    name: str
    description: str | None = None
    permissions: list[str] = []
    expires_at: datetime | None = None
    max_print_duration_minutes: int | None = None
    max_filament_grams_per_job: float | None = None
    max_active_queue_items: int | None = None
    requires_approval: bool = False
    require_project: bool = False
    allowed_hours_start: int | None = None
    allowed_hours_end: int | None = None


class GroupUpdate(BaseModel):
    """Schema for updating a group."""

    name: str | None = None
    description: str | None = None
    permissions: list[str] | None = None
    expires_at: datetime | None = None
    max_print_duration_minutes: int | None = None
    max_filament_grams_per_job: float | None = None
    max_active_queue_items: int | None = None
    requires_approval: bool | None = None
    require_project: bool | None = None
    allowed_hours_start: int | None = None
    allowed_hours_end: int | None = None


class GroupResponse(BaseModel):
    """Schema for group response."""

    id: int
    name: str
    description: str | None
    permissions: list[str]
    is_system: bool
    user_count: int = 0
    expires_at: datetime | None = None
    is_expired: bool = False
    max_print_duration_minutes: int | None = None
    max_filament_grams_per_job: float | None = None
    max_active_queue_items: int | None = None
    requires_approval: bool = False
    require_project: bool = False
    allowed_hours_start: int | None = None
    allowed_hours_end: int | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class GroupDetailResponse(GroupResponse):
    """Schema for detailed group response including users."""

    users: list["UserBrief"] = []


class UserBrief(BaseModel):
    """Brief user info for embedding in group response."""

    id: int
    username: str
    is_active: bool

    class Config:
        from_attributes = True


class PermissionInfo(BaseModel):
    """Schema for permission information."""

    value: str
    label: str


class PermissionCategory(BaseModel):
    """Schema for a category of permissions."""

    name: str
    permissions: list[PermissionInfo]


class PermissionsListResponse(BaseModel):
    """Schema for listing all permissions by category."""

    categories: list[PermissionCategory]
    all_permissions: list[str]


# Update forward references
GroupDetailResponse.model_rebuild()
