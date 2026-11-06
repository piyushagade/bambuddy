"""Unit tests for Remote Bambuddy Server printer federation."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.printer import Printer
from backend.app.schemas.printer import PrinterCreate, PrinterResponse
from backend.app.services.bambu_mqtt import PrinterState
from backend.app.services.remote_bambuddy import (
    RemoteBambuddyClient,
    apply_remote_status_to_state,
    fetch_remote_printers,
    normalize_remote_server_url,
    resolve_remote_auth_headers,
)


def test_normalize_remote_server_url_accepts_lan_http_and_strips_slash():
    url = normalize_remote_server_url("http://192.168.2.50:8000/")
    assert url == "http://192.168.2.50:8000"


def test_normalize_remote_server_url_rejects_cloud_metadata():
    with pytest.raises(ValueError, match="metadata"):
        normalize_remote_server_url("http://169.254.169.254/latest/meta-data")


def test_normalize_remote_server_url_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        normalize_remote_server_url("ftp://192.168.2.50:8000")


@pytest.mark.asyncio
async def test_resolve_remote_auth_headers_api_key():
    mock_client = AsyncMock()
    headers = await resolve_remote_auth_headers(
        mock_client,
        "http://192.168.2.50:8000",
        "api_key",
        api_key="bb_test_secret_key",
    )
    assert headers == {"X-API-Key": "bb_test_secret_key"}


@pytest.mark.asyncio
async def test_resolve_remote_auth_headers_credentials():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "jwt_token_123"}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp

    headers = await resolve_remote_auth_headers(
        mock_client,
        "http://192.168.2.50:8000",
        "credentials",
        username="admin",
        password="secretpassword",
    )
    assert headers == {"Authorization": "Bearer jwt_token_123"}
    mock_client.post.assert_awaited_once_with(
        "http://192.168.2.50:8000/api/v1/auth/login",
        json={"username": "admin", "password": "secretpassword"},
    )


@pytest.mark.asyncio
async def test_fetch_remote_printers_returns_printer_list():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {
            "id": 7,
            "name": "Remote X1C",
            "serial_number": "00M09A350199999",
            "ip_address": "10.20.30.40",
            "model": "X1C",
            "location": "Lab B",
            "is_active": True,
        }
    ]

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_client
    mock_cm.__aexit__.return_value = False

    with patch("backend.app.services.remote_bambuddy.httpx.AsyncClient", return_value=mock_cm):
        printers = await fetch_remote_printers(
            server_url="http://192.168.2.50:8000",
            auth_type="api_key",
            api_key="bb_test_secret_key",
        )

    assert len(printers) == 1
    assert printers[0]["remote_printer_id"] == 7
    assert printers[0]["name"] == "Remote X1C"
    mock_client.get.assert_awaited_once_with(
        "http://192.168.2.50:8000/api/v1/printers/",
        headers={"X-API-Key": "bb_test_secret_key"},
    )


def test_apply_remote_status_to_state_maps_temperatures_ams_and_progress():
    state = PrinterState()
    remote_payload = {
        "connected": True,
        "state": "RUNNING",
        "current_print": "benchy.3mf",
        "subtask_name": "3DBenchy",
        "progress": 64.5,
        "remaining_time": 18,
        "layer_num": 120,
        "total_layers": 200,
        "temperatures": {
            "nozzle": 220.0,
            "nozzle_target": 220.0,
            "bed": 60.0,
            "bed_target": 60.0,
            "chamber": 38.0,
        },
        "wifi_signal": -52,
        "ams": [
            {
                "id": 0,
                "humidity": 23,
                "temp": 26.5,
                "is_ams_ht": False,
                "tray": [
                    {
                        "id": 0,
                        "tray_color": "00AE42FF",
                        "tray_type": "PLA",
                        "remain": 80,
                    }
                ],
            }
        ],
        "vt_tray": None,
        "hms_errors": [],
    }

    apply_remote_status_to_state(state, remote_payload)

    assert state.connected is True
    assert state.state == "RUNNING"
    assert state.current_print == "benchy.3mf"
    assert state.subtask_name == "3DBenchy"
    assert state.progress == 64.5
    assert state.remaining_time == 18
    assert state.layer_num == 120
    assert state.total_layers == 200
    assert state.temperatures["nozzle"] == 220.0
    assert state.wifi_signal == -52
    assert len(state.raw_data["ams"]) == 1
    assert state.raw_data["ams"][0]["humidity_raw"] == 23
    assert state.raw_data["ams"][0]["tray"][0]["tray_type"] == "PLA"


def test_remote_bambuddy_client_forwards_control_commands():
    client = RemoteBambuddyClient(
        server_url="http://192.168.2.50:8000",
        remote_printer_id=7,
        auth_type="api_key",
        api_key_encrypted="bb_key_123",
        serial_number="00M09A350199999",
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    client._request = AsyncMock(return_value=mock_resp)

    assert client.pause_print() is True
    client._request.assert_awaited_with(
        "POST",
        "/api/v1/printers/7/print/pause",
        params=None,
        json_body=None,
    )

    assert client.set_chamber_light(True) is True
    client._request.assert_awaited_with(
        "POST",
        "/api/v1/printers/7/chamber-light",
        params={"on": True},
        json_body=None,
    )

    assert client.send_gcode("G28") is True
    client._request.assert_awaited_with(
        "POST",
        "/api/v1/printers/7/home-axes",
        params={"axes": "all"},
        json_body=None,
    )


def test_printer_schema_and_response_do_not_leak_remote_secrets():
    create_payload = PrinterCreate(
        name="Remote P1S",
        serial_number="01S00A000000001",
        ip_address="192.168.2.101",
        access_code="",
        is_remote=True,
        remote_server_url="http://192.168.2.50:8000",
        remote_printer_id=3,
        remote_auth_type="api_key",
        remote_api_key="bb_super_secret",
    )
    assert create_payload.access_code == "REMOTE"

    now = datetime.now(timezone.utc)
    printer_orm = Printer(
        id=10,
        name="Remote P1S",
        serial_number="01S00A000000001",
        ip_address="192.168.2.101",
        access_code="REMOTE",
        is_remote=True,
        remote_server_url="http://192.168.2.50:8000",
        remote_printer_id=3,
        remote_auth_type="api_key",
        remote_api_key="fernet:encrypted_secret",
        is_active=True,
        auto_archive=True,
        nozzle_count=1,
        print_hours_offset=0.0,
        external_camera_enabled=False,
        camera_rotation=0,
        plate_detection_enabled=False,
        created_at=now,
        updated_at=now,
    )
    assert printer_orm.has_remote_credentials is True

    resp = PrinterResponse.from_orm_with_roi(printer_orm)
    dumped = resp.model_dump()
    assert dumped["is_remote"] is True
    assert dumped["remote_server_url"] == "http://192.168.2.50:8000"
    assert dumped["remote_printer_id"] == 3
    assert dumped["has_remote_credentials"] is True
    assert "remote_api_key" not in dumped
    assert "remote_password" not in dumped
