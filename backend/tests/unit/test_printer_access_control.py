from types import SimpleNamespace

from backend.app.models.printer import is_user_authorized_for_printer
from backend.app.schemas.printer import PrinterCreate, PrinterResponse, PrinterUpdate


def test_open_access_printer_allows_everyone():
    printer = SimpleNamespace(allowed_group_ids=[], allowed_user_ids=[])
    user = SimpleNamespace(id=10, is_admin=False, groups=[])
    assert is_user_authorized_for_printer(printer, user) is True
    assert is_user_authorized_for_printer(printer, None) is True


def test_restricted_printer_allows_admin():
    printer = SimpleNamespace(allowed_group_ids=[5], allowed_user_ids=[99])
    admin = SimpleNamespace(id=1, is_admin=True, groups=[])
    assert is_user_authorized_for_printer(printer, admin) is True


def test_restricted_printer_allows_matching_user_id():
    printer = SimpleNamespace(allowed_group_ids=[5], allowed_user_ids=[42])
    maker = SimpleNamespace(id=42, is_admin=False, groups=[])
    other = SimpleNamespace(id=43, is_admin=False, groups=[])
    assert is_user_authorized_for_printer(printer, maker) is True
    assert is_user_authorized_for_printer(printer, other) is False


def test_restricted_printer_allows_matching_active_group_only():
    printer = SimpleNamespace(allowed_group_ids=[7], allowed_user_ids=[])
    active_group = SimpleNamespace(id=7, is_expired=False)
    expired_group = SimpleNamespace(id=7, is_expired=True)
    other_group = SimpleNamespace(id=8, is_expired=False)

    maker_active = SimpleNamespace(id=10, is_admin=False, groups=[active_group])
    maker_expired = SimpleNamespace(id=11, is_admin=False, groups=[expired_group])
    maker_other = SimpleNamespace(id=12, is_admin=False, groups=[other_group])

    assert is_user_authorized_for_printer(printer, maker_active) is True
    assert is_user_authorized_for_printer(printer, maker_expired) is False
    assert is_user_authorized_for_printer(printer, maker_other) is False


def test_printer_schema_normalizes_access_lists():
    created = PrinterCreate(
        name="Lab X1C",
        serial_number="00M09A123456789",
        ip_address="192.168.1.50",
        access_code="12345678",
        allowed_group_ids=[3, 1, 3, -1],
        allowed_user_ids=[9, 2, 9, 0],
    )
    assert created.allowed_group_ids == [1, 3]
    assert created.allowed_user_ids == [2, 9]

    updated = PrinterUpdate(allowed_group_ids=None, allowed_user_ids=[4, 4, 2])
    assert updated.allowed_group_ids is None
    assert updated.allowed_user_ids == [2, 4]

    fake_orm = SimpleNamespace(
        id=1,
        name="Lab X1C",
        serial_number="00M09A123456789",
        ip_address="192.168.1.50",
        access_code="12345678",
        model="X1C",
        location="Room 101",
        nozzle_count=1,
        auto_archive=True,
        external_camera_url=None,
        external_camera_type=None,
        external_camera_enabled=False,
        external_camera_snapshot_url=None,
        camera_rotation=0,
        plate_detection_enabled=False,
        plate_detection_roi_x=None,
        plate_detection_roi_y=None,
        plate_detection_roi_w=None,
        plate_detection_roi_h=None,
        allowed_group_ids=[1, 3],
        allowed_user_ids=[2, 9],
        is_active=True,
        print_hours_offset=0.0,
        runtime_seconds=0,
        last_active_at=None,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )
    resp = PrinterResponse.from_orm_with_roi(fake_orm)
    assert resp.allowed_group_ids == [1, 3]
    assert resp.allowed_user_ids == [2, 9]
