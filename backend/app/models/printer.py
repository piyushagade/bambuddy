from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from backend.app.core.database import Base


class Printer(Base):
    __tablename__ = "printers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    serial_number: Mapped[str] = mapped_column(String(50), unique=True)
    ip_address: Mapped[str] = mapped_column(String(253))
    access_code: Mapped[str] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(50))
    location: Mapped[str | None] = mapped_column(String(100))  # Group/location name
    nozzle_count: Mapped[int] = mapped_column(default=1)  # 1 or 2, auto-detected from MQTT
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_archive: Mapped[bool] = mapped_column(Boolean, default=True)
    print_hours_offset: Mapped[float] = mapped_column(Float, default=0.0)  # Baseline hours to add
    # What an hour of printing costs in wear on this machine (#694). Empty or 0
    # leaves wear cost off for the printer.
    wear_cost_per_hour: Mapped[float | None] = mapped_column(Float, nullable=True)
    runtime_seconds: Mapped[int] = mapped_column(
        default=0
    )  # Accumulated active runtime (RUNNING state only — see #1521)
    last_runtime_update: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )  # Last time runtime was updated
    # External camera configuration
    external_camera_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_camera_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # mjpeg, rtsp, snapshot
    external_camera_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Optional single-frame snapshot URL — when set, used for snapshot / finish-photo
    # / timelapse / plate-detect captures instead of opening the live stream and
    # skipping a warm-up frame. Bypasses MJPEG warm-up issues on sources that
    # expose a dedicated frame endpoint (e.g. go2rtc's /api/frame.jpeg). #1177.
    external_camera_snapshot_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    camera_rotation: Mapped[int] = mapped_column(default=0)  # 0, 90, 180, 270 degrees
    # Plate detection - check if build plate is empty before starting print
    plate_detection_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # ROI for plate detection (percentages: 0.0-1.0)
    plate_detection_roi_x: Mapped[float | None] = mapped_column(Float, nullable=True)  # X start %
    plate_detection_roi_y: Mapped[float | None] = mapped_column(Float, nullable=True)  # Y start %
    plate_detection_roi_w: Mapped[float | None] = mapped_column(Float, nullable=True)  # Width %
    plate_detection_roi_h: Mapped[float | None] = mapped_column(Float, nullable=True)  # Height %
    # Queue: True after a print finishes/fails, until user acknowledges the plate is cleared.
    # Persisted so the gate survives crashes and power cycles (issue #961).
    awaiting_plate_clear: Mapped[bool] = mapped_column(Boolean, default=False)
    plate_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    pickup_sla_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    # Printer-to-group/user access control (empty lists = open access to all users with printer permissions)
    allowed_group_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    allowed_user_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    # Remote Bambuddy server proxy configuration
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False)
    remote_server_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    remote_printer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remote_auth_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "api_key" or "credentials"
    remote_api_key: Mapped[str | None] = mapped_column(String(500), nullable=True)  # Encrypted via mfa_encrypt
    remote_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    remote_password: Mapped[str | None] = mapped_column(String(500), nullable=True)  # Encrypted via mfa_encrypt
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    archives: Mapped[list["PrintArchive"]] = relationship(back_populates="printer", cascade="all, delete-orphan")
    smart_plugs: Mapped[list["SmartPlug"]] = relationship(back_populates="printer")
    notification_providers: Mapped[list["NotificationProvider"]] = relationship(back_populates="printer")
    maintenance_items: Mapped[list["PrinterMaintenance"]] = relationship(
        back_populates="printer", cascade="all, delete-orphan"
    )
    kprofile_notes: Mapped[list["KProfileNote"]] = relationship(back_populates="printer", cascade="all, delete-orphan")
    ams_history: Mapped[list["AMSSensorHistory"]] = relationship(back_populates="printer", cascade="all, delete-orphan")
    sensor_history: Mapped[list["PrinterSensorHistory"]] = relationship(
        back_populates="printer", cascade="all, delete-orphan"
    )
    ha_sensors: Mapped[list["PrinterHASensor"]] = relationship(back_populates="printer", cascade="all, delete-orphan")

    @property
    def has_remote_credentials(self) -> bool:
        return bool(self.remote_api_key or self.remote_password)


def is_user_authorized_for_printer(printer: "Printer | None", user) -> bool:
    """Return True if ``user`` is permitted to view/use ``printer``.

    Empty ``allowed_group_ids`` and ``allowed_user_ids`` means unrestricted (open access).
    When either list is non-empty, ``user`` must be an admin, listed in
    ``allowed_user_ids``, or a member of at least one non-expired group in
    ``allowed_group_ids``. When auth is disabled (``user is None``), access
    is unrestricted.
    """
    if printer is None or user is None:
        return True
    if getattr(user, "is_admin", False):
        return True
    allowed_groups = [int(gid) for gid in (printer.allowed_group_ids or []) if gid is not None]
    allowed_users = [int(uid) for uid in (printer.allowed_user_ids or []) if uid is not None]
    if not allowed_groups and not allowed_users:
        return True
    if getattr(user, "id", None) in allowed_users:
        return True
    if allowed_groups:
        user_groups = getattr(user, "groups", None) or []
        active_group_ids = {g.id for g in user_groups if not getattr(g, "is_expired", False)}
        if active_group_ids & set(allowed_groups):
            return True
    return False


from backend.app.models.ams_history import AMSSensorHistory  # noqa: E402
from backend.app.models.archive import PrintArchive  # noqa: E402
from backend.app.models.kprofile_note import KProfileNote  # noqa: E402
from backend.app.models.maintenance import PrinterMaintenance  # noqa: E402
from backend.app.models.notification import NotificationProvider  # noqa: E402
from backend.app.models.printer_ha_sensor import PrinterHASensor  # noqa: E402
from backend.app.models.printer_sensor_history import PrinterSensorHistory  # noqa: E402
from backend.app.models.smart_plug import SmartPlug  # noqa: E402
