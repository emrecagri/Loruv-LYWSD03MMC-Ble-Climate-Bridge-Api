"""
Loruv LYWSD03MMC BLE Climate Bridge API
FastAPI ana uygulaması.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException

from app import __version__
from app.loruv_climate_cache import LoruvAsyncTTLCache
from app.loruv_climate_config import settings
from app.loruv_climate_models import (
    LoruvClimateDevicesResponse,
    LoruvServiceInfoResponse,
    LoruvServiceStatusResponse,
    LoruvSingleDeviceResponse,
)
from app.loruv_lywsd03mmc_discovery import (
    discover_lywsd03mmc_devices,
)
from app.loruv_lywsd03mmc_reader import (
    read_lywsd03mmc_device,
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=getattr(
        logging,
        settings.log_level.upper(),
        logging.INFO,
    ),
    format=(
        "%(asctime)s | %(levelname)s | "
        "%(name)s | %(message)s"
    ),
)

logger = logging.getLogger(__name__)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Read-only BLE-to-REST bridge for dynamically "
        "discovered LYWSD03MMC climate monitors."
    ),
)


# ============================================================
# UYGULAMA DURUMU
# ============================================================

APPLICATION_STARTED_AT = time.monotonic()

API_VERSION = "v1"

cache = LoruvAsyncTTLCache(
    ttl_seconds=settings.cache_ttl_seconds
)


def utc_now() -> datetime:
    """
    UTC timezone-aware datetime üretir.
    """

    return datetime.now(timezone.utc)


async def build_fresh_snapshot() -> dict[str, Any]:
    """
    Gerçek Bluetooth işlemini gerçekleştirir.

    1. Çevredeki bütün LYWSD03MMC cihazlarını tara.
    2. Bulunanları sırayla oku.
    3. Bütün bağlantıları kapat.
    4. Snapshot oluştur.
    """

    discovered_devices, scan_info = (
        await discover_lywsd03mmc_devices()
    )

    devices: list[dict[str, Any]] = []

    # Termometreleri paralel değil SIRAYLA okuyoruz.
    # Raspberry Pi Bluetooth tarafını daha stabil tutar.
    for discovered_device in discovered_devices:
        device_result = (
            await read_lywsd03mmc_device(
                discovered_device
            )
        )

        devices.append(
            device_result
        )

    return {
        "captured_at": utc_now(),
        "scan": scan_info,
        "devices": devices,
    }


async def get_devices_response(
) -> LoruvClimateDevicesResponse:
    """
    Cache kontrolünü ve gerekiyorsa Bluetooth refresh işlemini yapar.
    """

    try:
        snapshot, cached, cache_age = (
            await cache.get_or_refresh(
                build_fresh_snapshot
            )
        )

    except Exception as error:

        logger.exception(
            "BLE snapshot oluşturulamadı."
        )

        raise HTTPException(
            status_code=503,
            detail={
                "error": "bluetooth_refresh_failed",
                "type": type(error).__name__,
                "message": str(error),
            },
        ) from error

    devices = snapshot["devices"]

    # Cihaz bulunmaması API hatası sayılmaz.
    # Bulunan cihazlardan biri error/partial ise success false olur.
    success = bool(devices) and all(
        device.get("status") == "online"
        for device in devices
    )

    return LoruvClimateDevicesResponse(
        service=settings.app_name,
        api_version=API_VERSION,
        success=success,
        cached=cached,
        cache_age_seconds=(
            round(cache_age, 3)
            if cache_age is not None
            else None
        ),
        timestamp=utc_now(),
        captured_at=snapshot["captured_at"],
        scan=snapshot["scan"],
        device_count=len(devices),
        devices=devices,
    )


# ============================================================
# ROOT
# ============================================================

@app.get(
    "/",
    summary="API giriş bilgisi",
)
async def root() -> dict[str, Any]:
    """
    Bluetooth'a dokunmadan API navigasyonu sağlar.
    """

    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "api_version": API_VERSION,
        "docs": "/docs",
        "openapi": "/openapi.json",
        "devices": "/api/v1/lywsd03mmc-devices",
        "status": "/api/v1/bridge-status",
        "info": "/api/v1/bridge-info",
    }


# ============================================================
# TÜM CİHAZLAR
# ============================================================

@app.get(
    "/api/v1/lywsd03mmc-devices",
    response_model=LoruvClimateDevicesResponse,
    summary="Çevredeki tüm LYWSD03MMC cihazlarını getir",
)
async def get_all_devices(
) -> LoruvClimateDevicesResponse:
    """
    Cache geçerliyse BLE kullanmaz.

    Cache dolmuşsa çevredeki tüm LYWSD03MMC cihazlarını
    otomatik keşfeder ve sırayla okur.
    """

    return await get_devices_response()


# ============================================================
# TEK CİHAZ
# ============================================================

@app.get(
    "/api/v1/lywsd03mmc-devices/{device_id}",
    response_model=LoruvSingleDeviceResponse,
    summary="Tek LYWSD03MMC cihazını getir",
)
async def get_single_device(
    device_id: str,
) -> LoruvSingleDeviceResponse:
    """
    Tek cihazı device_id ile döndürür.

    Cache stale ise önce tüm BLE snapshot yenilenir.
    """

    response = await get_devices_response()

    for device in response.devices:

        if device.device_id == device_id:

            return LoruvSingleDeviceResponse(
                service=settings.app_name,
                api_version=API_VERSION,
                cached=response.cached,
                cache_age_seconds=(
                    response.cache_age_seconds
                ),
                timestamp=utc_now(),
                captured_at=response.captured_at,
                device=device,
            )

    raise HTTPException(
        status_code=404,
        detail={
            "error": "device_not_found",
            "device_id": device_id,
        },
    )


# ============================================================
# SERVİS DURUMU
# ============================================================

@app.get(
    "/api/v1/bridge-status",
    response_model=LoruvServiceStatusResponse,
    summary="Bridge servis durumunu getir",
)
async def bridge_status(
) -> LoruvServiceStatusResponse:
    """
    Bluetooth scan BAŞLATMAZ.

    Sadece API process ve RAM cache durumunu verir.
    Docker healthcheck için de kullanılır.
    """

    uptime = time.monotonic() - APPLICATION_STARTED_AT

    return LoruvServiceStatusResponse(
        service=settings.app_name,
        status="ok",
        version=__version__,
        uptime_seconds=round(
            uptime,
            3,
        ),
        cache=cache.inspect(),
    )


# ============================================================
# SERVİS BİLGİSİ
# ============================================================

@app.get(
    "/api/v1/bridge-info",
    response_model=LoruvServiceInfoResponse,
    summary="Bridge teknik bilgilerini getir",
)
async def bridge_info(
) -> LoruvServiceInfoResponse:
    """
    Bluetooth'a dokunmadan API yeteneklerini açıklar.
    """

    return LoruvServiceInfoResponse(
        name=settings.app_name,
        version=settings.app_version,
        api_version=API_VERSION,
        supported_device=(
            settings.ble_target_device_name
        ),
        bluetooth_backend="BlueZ / D-Bus / Bleak",
        discovery_mode=(
            "Dynamic BLE discovery by device name; "
            "no fixed MAC allow-list"
        ),
        operation_mode=(
            "Read-only; BLE is used only when cache expires "
            "and an API device request arrives"
        ),
        features=[
            "Dynamic LYWSD03MMC discovery",
            "No fixed device count",
            "Temperature",
            "Humidity",
            "Battery voltage",
            "Stock firmware reported battery percentage",
            "Temperature display unit",
            "Device clock",
            "History metadata",
            "Last-hour history record",
            "Comfort profile",
            "Firmware revision",
            "Hardware revision",
            "Software revision",
            "Manufacturer",
            "Model",
            "Serial number",
            "BLE advertisement metadata",
            "RSSI",
            "All readable GATT characteristics",
            "Readable GATT descriptors",
            "Raw HEX and decimal byte values",
            "60-second RAM cache",
            "Single-flight Bluetooth refresh lock",
            "Read-only BLE operation",
        ],
        endpoints={
            "devices": (
                "/api/v1/lywsd03mmc-devices"
            ),
            "single_device": (
                "/api/v1/lywsd03mmc-devices/{device_id}"
            ),
            "status": (
                "/api/v1/bridge-status"
            ),
            "info": (
                "/api/v1/bridge-info"
            ),
            "swagger": "/docs",
            "openapi": "/openapi.json",
        },
    )
