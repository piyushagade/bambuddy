"""Remote Bambuddy server client and proxy service.

Allows a Bambuddy instance to add and proxy printers that are connected to a
different Bambuddy server over HTTP/HTTPS using either an API key or admin
credentials (username + password).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from backend.app.api.routes._url_safety import assert_safe_lan_service_url
from backend.app.core.encryption import mfa_decrypt
from backend.app.services.bambu_mqtt import (
    ExtruderSlot,
    FilaSwitchState,
    HMSError,
    MQTTLogEntry,
    NozzleInfo,
    PrinterState,
    PrintOptions,
)

logger = logging.getLogger(__name__)

REMOTE_POLL_INTERVAL_SECONDS = 3.0
REMOTE_HTTP_TIMEOUT_SECONDS = 10.0


def normalize_remote_server_url(url: str) -> str:
    """Validate and normalize a remote Bambuddy server URL.

    Strips trailing slashes and an accidental ``/api/v1`` suffix, and enforces
    the LAN-service SSRF policy (http/https only, no cloud metadata or
    numeric-encoded IPs).
    """
    cleaned = (url or "").strip().rstrip("/")
    if cleaned.endswith("/api/v1"):
        cleaned = cleaned[: -len("/api/v1")].rstrip("/")
    elif cleaned.endswith("/api"):
        cleaned = cleaned[: -len("/api")].rstrip("/")
    assert_safe_lan_service_url(cleaned, label="Remote Bambuddy server URL")
    return cleaned


async def resolve_remote_auth_headers(
    client: httpx.AsyncClient,
    base_url: str,
    auth_type: str | None,
    *,
    api_key: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, str]:
    """Build authentication headers for a remote Bambuddy server.

    Supports:
    - ``auth_type == "api_key"`` -> ``X-API-Key: <api_key>``
    - ``auth_type == "credentials"`` -> logs in via ``POST /api/v1/auth/login``
      and returns ``Authorization: Bearer <access_token>``
    """
    mode = (auth_type or "api_key").strip().lower()
    if mode == "api_key":
        if not api_key:
            raise ValueError("API key is required for API key authentication")
        return {"X-API-Key": api_key.strip()}

    if mode == "credentials":
        if not username or not password:
            raise ValueError("Username and password are required for credential authentication")
        resp = await client.post(
            f"{base_url}/api/v1/auth/login",
            json={"username": username.strip(), "password": password},
        )
        if resp.status_code == 401:
            raise PermissionError("Invalid username or password on remote Bambuddy server")
        if resp.status_code >= 400:
            raise RuntimeError(f"Remote login failed with HTTP {resp.status_code}")
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise RuntimeError("Remote server did not return an access_token")
        return {"Authorization": f"Bearer {token}"}

    raise ValueError(f"Unsupported remote_auth_type: {auth_type}")


async def fetch_remote_printers(
    server_url: str,
    auth_type: str,
    *,
    api_key: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> list[dict]:
    """Connect to a remote Bambuddy server and return its configured printers."""
    base_url = normalize_remote_server_url(server_url)
    async with httpx.AsyncClient(timeout=REMOTE_HTTP_TIMEOUT_SECONDS, follow_redirects=False) as http:
        headers = await resolve_remote_auth_headers(
            http,
            base_url,
            auth_type,
            api_key=api_key,
            username=username,
            password=password,
        )
        resp = await http.get(f"{base_url}/api/v1/printers/", headers=headers)
        if resp.status_code in (401, 403):
            host = (urlparse(base_url).hostname or "").lower()
            loopback_hint = (
                " (Note: 'localhost' / '127.0.0.1' connects to this Bambuddy server itself, "
                "not your browser's computer — use the remote machine's LAN or Tailscale IP instead.)"
                if host in ("localhost", "127.0.0.1", "::1")
                else ""
            )
            raise PermissionError(
                f"Remote Bambuddy server rejected credentials or lacks printers:read permission{loopback_hint}"
            )
        resp.raise_for_status()
        printers = resp.json()
        if not isinstance(printers, list):
            raise RuntimeError("Unexpected response from remote Bambuddy server")
        return [
            {
                "remote_printer_id": int(p["id"]),
                "name": str(p.get("name") or ""),
                "serial_number": str(p.get("serial_number") or ""),
                "ip_address": str(p.get("ip_address") or ""),
                "model": p.get("model"),
                "location": p.get("location"),
                "is_active": bool(p.get("is_active", True)),
            }
            for p in printers
            if isinstance(p, dict) and "id" in p and "serial_number" in p
        ]


async def test_remote_printer_connection(
    server_url: str,
    remote_printer_id: int,
    auth_type: str,
    *,
    api_key: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> dict:
    """Verify a specific printer on a remote Bambuddy server is reachable."""
    base_url = normalize_remote_server_url(server_url)
    try:
        async with httpx.AsyncClient(timeout=REMOTE_HTTP_TIMEOUT_SECONDS, follow_redirects=False) as http:
            headers = await resolve_remote_auth_headers(
                http,
                base_url,
                auth_type,
                api_key=api_key,
                username=username,
                password=password,
            )
            resp = await http.get(f"{base_url}/api/v1/printers/{remote_printer_id}/status", headers=headers)
            if resp.status_code == 404:
                return {"success": False, "state": None, "model": None, "reason": "not_found"}
            if resp.status_code in (401, 403):
                return {"success": False, "state": None, "model": None, "reason": "auth_rejected"}
            resp.raise_for_status()
            status_data = resp.json()
            return {
                "success": True,
                "state": status_data.get("state"),
                "model": None,
                "reason": None,
            }
    except PermissionError:
        return {"success": False, "state": None, "model": None, "reason": "auth_rejected"}
    except Exception as exc:
        logger.warning("Remote Bambuddy connection test failed (%s printer %s): %s", base_url, remote_printer_id, exc)
        return {"success": False, "state": None, "model": None, "reason": "refused"}


def apply_remote_status_to_state(state: PrinterState, data: dict) -> None:
    """Map a remote Bambuddy ``PrinterStatus`` JSON payload onto ``PrinterState``."""
    state.connected = bool(data.get("connected", False))
    state.state = str(data.get("state") or "unknown")
    state.current_print = data.get("current_print")
    state.subtask_name = data.get("subtask_name")
    state.gcode_file = data.get("gcode_file")
    state.progress = float(data.get("progress") or 0.0)
    state.remaining_time = int(data.get("remaining_time") or 0)
    state.layer_num = int(data.get("layer_num") or 0)
    state.total_layers = int(data.get("total_layers") or 0)
    state.temperatures = dict(data.get("temperatures") or {})
    state.sdcard = bool(data.get("sdcard", False))
    state.sdcard_reported = True
    state.store_to_sdcard = bool(data.get("store_to_sdcard", False))
    state.timelapse = bool(data.get("timelapse", False))
    state.ipcam = bool(data.get("ipcam", False))
    state.wifi_signal = data.get("wifi_signal")
    state.wired_network = bool(data.get("wired_network", False))
    state.door_open = bool(data.get("door_open", False))
    state.stg_cur = int(data.get("stg_cur", -1) if data.get("stg_cur") is not None else -1)
    state.stg = list(data.get("stg") or [])
    state.airduct_mode = int(data.get("airduct_mode") or 0)
    state.speed_level = int(data.get("speed_level") or 2)
    state.chamber_light = bool(data.get("chamber_light", False))
    state.active_extruder = int(data.get("active_extruder") or 0)
    state.tray_now = int(data.get("tray_now", 255) if data.get("tray_now") is not None else 255)
    state.ams_status_main = int(data.get("ams_status_main") or 0)
    state.ams_status_sub = int(data.get("ams_status_sub") or 0)
    state.mc_print_sub_stage = int(data.get("mc_print_sub_stage") or 0)
    state.last_ams_update = float(data.get("last_ams_update") or time.time())
    state.cooling_fan_speed = data.get("cooling_fan_speed")
    state.big_fan1_speed = data.get("big_fan1_speed")
    state.big_fan2_speed = data.get("big_fan2_speed")
    state.heatbreak_fan_speed = data.get("heatbreak_fan_speed")
    state.left_aux_fan_speed = data.get("left_aux_fan_speed")
    state.exhaust_fan_present = bool(data.get("exhaust_fan_present", False))
    state.firmware_version = data.get("firmware_version")
    state.developer_mode = data.get("developer_mode")
    state.ams_filament_backup = data.get("ams_filament_backup")
    state.ams_extruder_map = dict(data.get("ams_extruder_map") or {})
    state.ams_switch_inlet = dict(data.get("ams_switch_inlet") or {})

    # HMS errors
    hms_raw = data.get("hms_errors") or []
    state.hms_errors = [
        HMSError(
            code=str(e.get("code") or ""),
            attr=int(e.get("attr") or 0),
            module=int(e.get("module") or 0),
            severity=int(e.get("severity") or 0),
            description=e.get("description"),
            actions=list(e.get("actions") or []),
            job_id=e.get("job_id"),
            full_code=str(e.get("full_code") or ""),
        )
        for e in hms_raw
        if isinstance(e, dict)
    ]

    # Nozzles
    nozzles_raw = data.get("nozzles") or []
    if isinstance(nozzles_raw, list) and nozzles_raw:
        state.nozzles = [
            NozzleInfo(
                nozzle_type=str(n.get("nozzle_type") or ""),
                nozzle_diameter=str(n.get("nozzle_diameter") or ""),
            )
            for n in nozzles_raw
            if isinstance(n, dict)
        ]

    # Nozzle rack
    rack_raw = data.get("nozzle_rack") or []
    state.nozzle_rack = [
        {
            "id": n.get("id", 0),
            "type": n.get("nozzle_type") or n.get("type") or "",
            "diameter": n.get("nozzle_diameter") or n.get("diameter") or "",
            "wear": n.get("wear"),
            "stat": n.get("stat"),
            "max_temp": n.get("max_temp", 0),
            "serial_number": n.get("serial_number", ""),
            "filament_color": n.get("filament_color", ""),
            "filament_id": n.get("filament_id", ""),
            "filament_type": n.get("filament_type", ""),
        }
        for n in rack_raw
        if isinstance(n, dict)
    ]

    # Print options
    po = data.get("print_options")
    if isinstance(po, dict):
        state.print_options = PrintOptions(
            spaghetti_detector=bool(po.get("spaghetti_detector", False)),
            print_halt=bool(po.get("print_halt", False)),
            halt_print_sensitivity=str(po.get("halt_print_sensitivity") or "medium"),
            first_layer_inspector=bool(po.get("first_layer_inspector", False)),
            printing_monitor=bool(po.get("printing_monitor", False)),
            buildplate_marker_detector=bool(po.get("buildplate_marker_detector", False)),
            allow_skip_parts=bool(po.get("allow_skip_parts", False)),
            nozzle_clumping_detector=bool(po.get("nozzle_clumping_detector", True)),
            nozzle_clumping_sensitivity=str(po.get("nozzle_clumping_sensitivity") or "medium"),
            pileup_detector=bool(po.get("pileup_detector", True)),
            pileup_sensitivity=str(po.get("pileup_sensitivity") or "medium"),
            airprint_detector=bool(po.get("airprint_detector", True)),
            airprint_sensitivity=str(po.get("airprint_sensitivity") or "medium"),
            auto_recovery_step_loss=bool(po.get("auto_recovery_step_loss", True)),
            filament_tangle_detect=bool(po.get("filament_tangle_detect", False)),
        )

    # Filament Track Switch
    fs = data.get("fila_switch")
    if isinstance(fs, dict) and fs.get("installed"):
        state.fila_switch = FilaSwitchState(
            installed=True,
            in_slots=list(fs.get("in_slots") or []),
            out_extruders=list(fs.get("out_extruders") or []),
            stat=int(fs.get("stat") or 0),
            info=int(fs.get("info") or 0),
        )
    else:
        state.fila_switch = FilaSwitchState(installed=False)

    # Extruder slots
    ext_slots = data.get("extruder_slots")
    if isinstance(ext_slots, dict):
        state.extruder_slots = {
            int(k) if str(k).isdigit() else k: ExtruderSlot(
                ams_id=v.get("ams_id"),
                slot_id=v.get("slot_id"),
                has_filament=bool(v.get("has_filament", False)),
            )
            for k, v in ext_slots.items()
            if isinstance(v, dict)
        }

    # Populate raw_data for AMS and vt_tray serializers in routes/printers.py and printer_manager.py
    ams_units = data.get("ams") or []
    raw_ams = []
    for unit in ams_units:
        if not isinstance(unit, dict):
            continue
        raw_ams.append(
            {
                "id": unit.get("id", 0),
                "humidity_raw": unit.get("humidity"),
                "temp": unit.get("temp"),
                "tray": unit.get("tray") or [],
                "sn": unit.get("serial_number") or "",
                "sw_ver": unit.get("sw_ver") or "",
                "dry_time": unit.get("dry_time") or 0,
                "dry_countdown_stalled": unit.get("dry_countdown_stalled") or False,
                "module_type": unit.get("module_type") or "",
            }
        )
    state.raw_data["ams"] = raw_ams
    state.raw_data["vt_tray"] = list(data.get("vt_tray") or [])
    state.raw_data["ams_mapping"] = list(data.get("ams_mapping") or [])
    state.raw_data["ams_extruder_map"] = dict(data.get("ams_extruder_map") or {})


class RemoteBambuddyClient:
    """Client adapter that proxies a printer hosted on another Bambuddy server.

    Exposes the same interface as ``BambuMQTTClient`` so ``PrinterManager`` and
    route handlers can interact with remote printers transparently.
    """

    is_remote_client: bool = True

    def __init__(
        self,
        *,
        server_url: str,
        remote_printer_id: int,
        auth_type: str | None,
        api_key_encrypted: str | None = None,
        username: str | None = None,
        password_encrypted: str | None = None,
        serial_number: str = "",
        model: str | None = None,
        on_state_change: Callable[[PrinterState], None] | None = None,
        on_print_start: Callable[[dict], None] | None = None,
        on_print_complete: Callable[[dict], None] | None = None,
        on_ams_change: Callable[[list], None] | None = None,
        on_layer_change: Callable[[int], None] | None = None,
        on_print_progress: Callable[[int], None] | None = None,
        on_bed_temp_update: Callable[[float], None] | None = None,
    ):
        self.base_url = normalize_remote_server_url(server_url)
        self.remote_printer_id = int(remote_printer_id)
        self.auth_type = (auth_type or "api_key").strip().lower()
        self._api_key = mfa_decrypt(api_key_encrypted) if api_key_encrypted else None
        self.username = username
        self._password = mfa_decrypt(password_encrypted) if password_encrypted else None
        self.serial_number = serial_number
        self.model = model

        self.state = PrinterState()
        self._drying_targets: dict[int, dict] = {}
        self.logging_enabled: bool = False
        self._logs: deque[MQTTLogEntry] = deque(maxlen=500)
        self.last_connect_error: str | None = None

        self._on_state_change = on_state_change
        self._on_print_start = on_print_start
        self._on_print_complete = on_print_complete
        self._on_ams_change = on_ams_change
        self._on_layer_change = on_layer_change
        self._on_print_progress = on_print_progress
        self._on_bed_temp_update = on_bed_temp_update

        self._cached_headers: dict[str, str] | None = None
        self._poll_task: asyncio.Task | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def _log_event(self, direction: str, endpoint: str, payload: dict) -> None:
        if not self.logging_enabled:
            return
        self._logs.append(
            MQTTLogEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                topic=f"remote:{endpoint}",
                direction=direction,
                payload=payload,
            )
        )

    async def _get_headers(self, http: httpx.AsyncClient, *, force_refresh: bool = False) -> dict[str, str]:
        if self._cached_headers is not None and not force_refresh:
            return self._cached_headers
        self._cached_headers = await resolve_remote_auth_headers(
            http,
            self.base_url,
            self.auth_type,
            api_key=self._api_key,
            username=self.username,
            password=self._password,
        )
        return self._cached_headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: object = None,
        files: dict | None = None,
        timeout: float = REMOTE_HTTP_TIMEOUT_SECONDS,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as http:
            headers = await self._get_headers(http)
            self._log_event("out", f"{method} {path}", {"params": params, "json": json_body})
            resp = await http.request(method, url, headers=headers, params=params, json=json_body, files=files)
            if resp.status_code == 401 and self.auth_type == "credentials":
                headers = await self._get_headers(http, force_refresh=True)
                resp = await http.request(method, url, headers=headers, params=params, json=json_body, files=files)
            return resp

    async def poll_once(self) -> bool:
        """Fetch latest status from the remote Bambuddy server and update state."""
        prev_state = self.state.state
        prev_layer = self.state.layer_num
        prev_progress = int(self.state.progress or 0)
        try:
            resp = await self._request("GET", f"/api/v1/printers/{self.remote_printer_id}/status")
            if resp.status_code in (401, 403):
                self.last_connect_error = "auth_rejected"
                self.state.connected = False
                return False
            resp.raise_for_status()
            data = resp.json()
            self._log_event("in", f"GET /api/v1/printers/{self.remote_printer_id}/status", data)
            apply_remote_status_to_state(self.state, data)
            self.last_connect_error = None

            if self._on_state_change:
                self._on_state_change(self.state)
            if self._on_ams_change and self.state.raw_data.get("ams"):
                self._on_ams_change(self.state.raw_data["ams"])
            if self.state.layer_num != prev_layer and self._on_layer_change:
                self._on_layer_change(self.state.layer_num)
            cur_progress = int(self.state.progress or 0)
            if cur_progress > prev_progress and self._on_print_progress:
                self._on_print_progress(cur_progress)
            bed_temp = self.state.temperatures.get("bed")
            if bed_temp is not None and self._on_bed_temp_update:
                self._on_bed_temp_update(float(bed_temp))

            if prev_state not in ("RUNNING", "PREPARE") and self.state.state == "RUNNING" and self._on_print_start:
                self._on_print_start(
                    {
                        "filename": self.state.current_print or self.state.subtask_name or "",
                        "subtask_name": self.state.subtask_name or "",
                        "gcode_file": self.state.gcode_file or "",
                    }
                )
            elif prev_state in ("RUNNING", "PAUSE") and self.state.state in ("FINISH", "FAILED") and self._on_print_complete:
                self._on_print_complete(
                    {
                        "status": "completed" if self.state.state == "FINISH" else "failed",
                        "filename": self.state.current_print or self.state.subtask_name or "",
                        "subtask_name": self.state.subtask_name or "",
                    }
                )
            return self.state.connected
        except Exception as exc:
            logger.debug("Remote poll failed for %s printer %s: %s", self.base_url, self.remote_printer_id, exc)
            self.last_connect_error = "refused"
            was_connected = self.state.connected
            self.state.connected = False
            if was_connected and self._on_state_change:
                self._on_state_change(self.state)
            return False

    async def _poll_loop(self) -> None:
        while self._running:
            await self.poll_once()
            await asyncio.sleep(REMOTE_POLL_INTERVAL_SECONDS)

    def connect(self) -> None:
        """Start background polling of the remote Bambuddy server."""
        self._running = True
        try:
            loop = asyncio.get_running_loop()
            self._loop = loop
            self._poll_task = loop.create_task(self._poll_loop())
        except RuntimeError:
            pass

    def disconnect(self, timeout: float = 0) -> None:
        """Stop background polling."""
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = None
        self.state.connected = False

    def check_staleness(self) -> bool:
        return self.state.connected

    def mark_power_off(self) -> bool:
        if self.state.connected:
            self.state.connected = False
            return True
        return False

    def enable_logging(self, enabled: bool = True) -> None:
        self.logging_enabled = enabled

    def get_logs(self) -> list[MQTTLogEntry]:
        return list(self._logs)

    def clear_logs(self) -> None:
        self._logs.clear()

    def _run_coro_sync(self, coro) -> bool:
        """Execute an async remote API call from a synchronous client method."""
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(coro)
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            return True
        except RuntimeError:
            if self._loop and self._loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
                try:
                    return bool(fut.result(timeout=REMOTE_HTTP_TIMEOUT_SECONDS))
                except Exception:
                    return False
            return asyncio.run(coro)

    async def _post_action(
        self,
        subpath: str,
        *,
        params: dict | None = None,
        json_body: object = None,
    ) -> bool:
        try:
            resp = await self._request(
                "POST",
                f"/api/v1/printers/{self.remote_printer_id}{subpath}",
                params=params,
                json_body=json_body,
            )
            return resp.status_code < 400
        except Exception as exc:
            logger.warning("Remote action %s failed on printer %s: %s", subpath, self.remote_printer_id, exc)
            return False

    def request_status_update(self) -> bool:
        return self._run_coro_sync(self._post_action("/refresh-status"))

    def stop_print(self) -> bool:
        return self._run_coro_sync(self._post_action("/print/stop"))

    def pause_print(self) -> bool:
        return self._run_coro_sync(self._post_action("/print/pause"))

    def resume_print(self) -> bool:
        return self._run_coro_sync(self._post_action("/print/resume"))

    def set_print_speed(self, mode: int) -> bool:
        return self._run_coro_sync(self._post_action("/print-speed", params={"mode": mode}))

    def set_nozzle_temperature(self, target: int, nozzle: int = 0) -> bool:
        return self._run_coro_sync(
            self._post_action("/temperature/nozzle", params={"target": target, "nozzle": nozzle})
        )

    def set_bed_temperature(self, target: int) -> bool:
        return self._run_coro_sync(self._post_action("/temperature/bed", params={"target": target}))

    def set_chamber_temperature(self, target: int) -> bool:
        return self._run_coro_sync(self._post_action("/temperature/chamber", params={"target": target}))

    def set_fan_speed(self, fan_id: int, pwm_speed: int) -> bool:
        fan_map = {1: "part", 2: "aux", 3: "chamber", 10: "aux2"}
        fan_name = fan_map.get(fan_id)
        if not fan_name:
            return False
        pct = max(0, min(100, round(pwm_speed * 100 / 255)))
        return self._run_coro_sync(self._post_action("/fan-speed", params={"fan": fan_name, "speed": pct}))

    def select_extruder(self, extruder: int) -> bool:
        return self._run_coro_sync(self._post_action("/select-extruder", params={"extruder": extruder}))

    def set_airduct_mode(self, mode: str) -> bool:
        return self._run_coro_sync(self._post_action("/airduct-mode", params={"mode": mode}))

    def set_chamber_light(self, on: bool) -> bool:
        return self._run_coro_sync(self._post_action("/chamber-light", params={"on": on}))

    def clear_hms_errors(self) -> bool:
        return self._run_coro_sync(self._post_action("/hms/clear"))

    def skip_objects(self, object_ids: list[int]) -> bool:
        return self._run_coro_sync(self._post_action("/print/skip-objects", json_body=object_ids))

    def send_drying_command(
        self,
        ams_id: int,
        temp: int,
        duration: int,
        mode: int = 1,
        filament: str = "",
        rotate_tray: bool = False,
    ) -> bool:
        if mode == 0:
            self._drying_targets.pop(ams_id, None)
            return self._run_coro_sync(self._post_action("/drying/stop", params={"ams_id": ams_id}))
        self._drying_targets[ams_id] = {"filament": filament, "temp": temp}
        return self._run_coro_sync(
            self._post_action(
                "/drying/start",
                params={
                    "ams_id": ams_id,
                    "temp": temp,
                    "duration": duration,
                    "filament": filament,
                    "rotate_tray": rotate_tray,
                },
            )
        )

    def set_xcam_option(
        self,
        module_name: str,
        enabled: bool,
        print_halt: bool = True,
        sensitivity: str = "medium",
    ) -> bool:
        return self._run_coro_sync(
            self._post_action(
                "/print-options",
                params={
                    "module_name": module_name,
                    "enabled": enabled,
                    "print_halt": print_halt,
                    "sensitivity": sensitivity,
                },
            )
        )

    def set_ams_filament_backup(self, enabled: bool) -> bool:
        return self._run_coro_sync(self._post_action("/ams-backup", params={"enabled": enabled}))

    def ams_load_filament(self, tray_id: int, extruder_id: int | None = None) -> bool:
        params: dict[str, int] = {"tray_id": tray_id}
        if extruder_id is not None:
            params["extruder_id"] = extruder_id
        return self._run_coro_sync(self._post_action("/ams/load", params=params))

    def ams_unload_filament(self, tray_id: int | None = None) -> bool:
        params: dict[str, int] = {}
        if tray_id is not None:
            params["tray_id"] = tray_id
        return self._run_coro_sync(self._post_action("/ams/unload", params=params))

    async def ams_refresh_tray(self, ams_id: int, slot_id: int) -> tuple[bool, str]:
        ok = await self._post_action(f"/ams/{ams_id}/slot/{slot_id}/refresh")
        return (True, "RFID refresh requested") if ok else (False, "Failed to refresh slot on remote server")

    def send_gcode(self, gcode: str) -> bool:
        """Translate standard jog/home G-code sequences to remote Bambuddy REST endpoints."""
        stripped = gcode.strip()
        if stripped == "G28":
            return self._run_coro_sync(self._post_action("/home-axes", params={"axes": "all"}))
        z_match = re.search(r"G1\s+Z([+-]?\d+(?:\.\d+)?)", stripped)
        if z_match:
            return self._run_coro_sync(self._post_action("/bed-jog", params={"distance": float(z_match.group(1))}))
        e_match = re.search(r"G1\s+E([+-]?\d+(?:\.\d+)?)", stripped)
        if e_match:
            return self._run_coro_sync(self._post_action("/extruder-jog", params={"distance": float(e_match.group(1))}))
        x_match = re.search(r"X([+-]?\d+(?:\.\d+)?)", stripped)
        y_match = re.search(r"Y([+-]?\d+(?:\.\d+)?)", stripped)
        if x_match or y_match:
            params: dict[str, float] = {}
            if x_match:
                params["x"] = float(x_match.group(1))
            if y_match:
                params["y"] = float(y_match.group(1))
            return self._run_coro_sync(self._post_action("/xy-jog", params=params))
        return False

    def start_print(
        self,
        filename: str,
        plate_id: int = 1,
        ams_mapping: list[int] | None = None,
        timelapse: bool = False,
        bed_levelling: str = "auto",
        flow_cali: str = "auto",
        vibration_cali: bool = True,
        layer_inspect: bool = False,
        use_ams: bool = True,
        nozzle_offset_cali: str = "auto",
        nozzle_mapping: str | None = None,
        nozzle_slot_extruders: str | None = None,
    ) -> bool:
        """Remote prints are started via ``dispatch_print_file`` in the scheduler."""
        return True

    async def dispatch_print_file(
        self,
        file_path: Path,
        filename: str,
        *,
        plate_id: int = 1,
        ams_mapping: list[int] | None = None,
        bed_levelling: str = "auto",
        flow_cali: str = "auto",
        vibration_cali: bool = True,
        layer_inspect: bool = False,
        timelapse: bool = False,
        use_ams: bool = True,
        nozzle_offset_cali: str = "auto",
        nozzle_mapping: str | None = None,
    ) -> bool:
        """Upload a .3mf to the remote Bambuddy server and enqueue it for immediate dispatch."""
        try:
            content = file_path.read_bytes()
            resp = await self._request(
                "POST",
                "/api/v1/archives/upload",
                params={"printer_id": self.remote_printer_id, "prefer_filename_for_name": True},
                files={"file": (filename, content, "application/octet-stream")},
                timeout=120.0,
            )
            if resp.status_code >= 400:
                logger.error("Remote archive upload failed (HTTP %s): %s", resp.status_code, resp.text)
                return False
            archive_data = resp.json()
            remote_archive_id = archive_data.get("id")
            if not remote_archive_id:
                return False

            queue_payload = {
                "printer_id": self.remote_printer_id,
                "archive_id": remote_archive_id,
                "plate_id": plate_id,
                "ams_mapping": ams_mapping,
                "bed_levelling": bed_levelling,
                "flow_cali": flow_cali,
                "vibration_cali": vibration_cali,
                "layer_inspect": layer_inspect,
                "timelapse": timelapse,
                "use_ams": use_ams,
                "nozzle_offset_cali": nozzle_offset_cali,
                "nozzle_mapping": nozzle_mapping,
                "manual_start": False,
            }
            q_resp = await self._request("POST", "/api/v1/queue/", json_body=queue_payload, timeout=30.0)
            if q_resp.status_code >= 400:
                logger.error("Remote queue creation failed (HTTP %s): %s", q_resp.status_code, q_resp.text)
                return False
            return True
        except Exception as exc:
            logger.error("Failed to dispatch print to remote Bambuddy server: %s", exc)
            return False

    async def fetch_cover(self, view: str | None = None) -> bytes | None:
        """Fetch the active print cover image from the remote Bambuddy server."""
        try:
            async with httpx.AsyncClient(timeout=REMOTE_HTTP_TIMEOUT_SECONDS, follow_redirects=False) as http:
                headers = await self._get_headers(http)
                token_resp = await http.post(f"{self.base_url}/api/v1/auth/media-token", headers=headers)
                params: dict[str, str] = {}
                if view:
                    params["view"] = view
                if token_resp.status_code < 400:
                    tok = token_resp.json().get("token")
                    if tok:
                        params["token"] = tok
                resp = await http.get(
                    f"{self.base_url}/api/v1/printers/{self.remote_printer_id}/cover",
                    headers=headers,
                    params=params,
                )
                if resp.status_code == 200 and resp.content:
                    return resp.content
        except Exception as exc:
            logger.debug("Remote cover fetch failed for printer %s: %s", self.remote_printer_id, exc)
        return None

    async def fetch_camera_snapshot(self, *, ensure_light: bool = False) -> bytes | None:
        """Fetch a camera snapshot from the remote Bambuddy server."""
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as http:
                headers = await self._get_headers(http)
                token_resp = await http.post(f"{self.base_url}/api/v1/printers/camera/stream-token", headers=headers)
                params: dict[str, object] = {"ensure_light": ensure_light}
                if token_resp.status_code < 400:
                    tok = token_resp.json().get("token")
                    if tok:
                        params["token"] = tok
                resp = await http.get(
                    f"{self.base_url}/api/v1/printers/{self.remote_printer_id}/camera/snapshot",
                    headers=headers,
                    params=params,
                )
                if resp.status_code == 200 and resp.content:
                    return resp.content
        except Exception as exc:
            logger.debug("Remote camera snapshot failed for printer %s: %s", self.remote_printer_id, exc)
        return None

    async def stream_camera_mjpeg(self, *, fps: int = 10) -> AsyncGenerator[bytes, None]:
        """Proxy the multipart MJPEG stream from the remote Bambuddy server."""
        async with httpx.AsyncClient(timeout=None, follow_redirects=False) as http:
            headers = await self._get_headers(http)
            token_resp = await http.post(
                f"{self.base_url}/api/v1/printers/camera/stream-token",
                headers=headers,
                timeout=REMOTE_HTTP_TIMEOUT_SECONDS,
            )
            params: dict[str, object] = {"fps": fps}
            if token_resp.status_code < 400:
                tok = token_resp.json().get("token")
                if tok:
                    params["token"] = tok
            async with http.stream(
                "GET",
                f"{self.base_url}/api/v1/printers/{self.remote_printer_id}/camera/stream",
                headers=headers,
                params=params,
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=8192):
                    if chunk:
                        yield chunk
