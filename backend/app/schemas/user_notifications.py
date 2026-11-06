"""Schemas for user email notification preferences."""

from pydantic import BaseModel


class UserEmailPreferenceResponse(BaseModel):
    """Response schema for user email notification preferences."""

    notify_print_start: bool
    notify_print_complete: bool
    notify_print_failed: bool
    notify_print_stopped: bool
    notify_print_progress: bool = True
    notify_first_layer_complete: bool = True
    notify_print_confirm_request: bool = True
    notify_plate_clear_required: bool = True
    notify_plate_not_empty: bool = True
    notify_queue_waiting: bool = True
    notify_queue_skipped: bool = True
    notify_queue_failed: bool = True

    class Config:
        from_attributes = True


class UserEmailPreferenceUpdate(BaseModel):
    """Update schema for user email notification preferences."""

    notify_print_start: bool
    notify_print_complete: bool
    notify_print_failed: bool
    notify_print_stopped: bool
    notify_print_progress: bool | None = None
    notify_first_layer_complete: bool | None = None
    notify_print_confirm_request: bool | None = None
    notify_plate_clear_required: bool | None = None
    notify_plate_not_empty: bool | None = None
    notify_queue_waiting: bool | None = None
    notify_queue_skipped: bool | None = None
    notify_queue_failed: bool | None = None
