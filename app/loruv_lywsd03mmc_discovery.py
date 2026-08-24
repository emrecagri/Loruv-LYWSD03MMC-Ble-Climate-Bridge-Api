"""
LYWSD03MMC otomatik BLE keşif modülü.

Çevrede kaç adet uygun cihaz varsa hepsini bulur.
Önceden bilinen MAC adreslerine bağımlı değildir.
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from app.loruv_climate_config import settings


@dataclass
class LoruvDiscoveredDevice:
    """
    BLE taramasında bulunan tek bir LYWSD03MMC cihazı.
    """

    ble_device: BLEDevice

    # JSON uyumlu advertisement snapshot'ı.
    advertisement: dict[str, Any]


def _bytes_to_json(data: bytes) -> dict[str, Any]:
    """
    Binary BLE verisini JSON uyumlu hale getirir.
    """

    return {
        "length": len(data),
        "hex": data.hex(" "),
        "hex_compact": data.hex(),
        "bytes_decimal": list(data),
    }


def _advertisement_to_json(
    device: BLEDevice,
    advertisement_data: AdvertisementData,
) -> dict[str, Any]:
    """
    Bleak AdvertisementData nesnesini tamamen JSON uyumlu hale getirir.
    """

    manufacturer_data = []

    for company_id, data in advertisement_data.manufacturer_data.items():
        manufacturer_data.append(
            {
                "company_id": company_id,
                "company_id_hex": f"0x{company_id:04X}",
                "data": _bytes_to_json(bytes(data)),
            }
        )

    service_data = []

    for service_uuid, data in advertisement_data.service_data.items():
        service_data.append(
            {
                "service_uuid": service_uuid,
                "data": _bytes_to_json(bytes(data)),
            }
        )

    return {
        "bluetooth_name": device.name,
        "local_name": advertisement_data.local_name,
        "rssi_dbm": advertisement_data.rssi,
        "tx_power_dbm": advertisement_data.tx_power,
        "service_uuids": list(advertisement_data.service_uuids),
        "manufacturer_data": manufacturer_data,
        "service_data": service_data,
    }


def _matches_target_device(
    device: BLEDevice,
    advertisement_data: AdvertisementData,
) -> bool:
    """
    Advertisement cihaz adının hedef modelle eşleşip eşleşmediğini kontrol eder.

    startswith kullanılmasının sebebi ileride aynı model adının
    ufak suffix varyasyonlarının da kaçırılmamasıdır.
    """

    target = settings.ble_target_device_name.strip().upper()

    possible_names = [
        device.name,
        advertisement_data.local_name,
    ]

    for name in possible_names:
        if name and name.strip().upper().startswith(target):
            return True

    return False


async def discover_lywsd03mmc_devices(
) -> tuple[list[LoruvDiscoveredDevice], dict[str, Any]]:
    """
    Çevredeki bütün hedef LYWSD03MMC cihazlarını keşfeder.

    Tarama sadece çağrıldığı anda çalışır.
    Arka planda sürekli BLE scan yapılmaz.
    """

    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()

    # MAC adresine göre tekilleştirilmiş cihazlar.
    discovered: dict[str, LoruvDiscoveredDevice] = {}

    def detection_callback(
        device: BLEDevice,
        advertisement_data: AdvertisementData,
    ) -> None:
        """
        Her advertisement paketinde çağrılır.

        Sadece hedef cihaz adına uyan termometreleri saklar.
        """

        if not _matches_target_device(device, advertisement_data):
            return

        mac_address = device.address.upper()

        discovered[mac_address] = LoruvDiscoveredDevice(
            ble_device=device,
            advertisement=_advertisement_to_json(
                device,
                advertisement_data,
            ),
        )

    scanner = BleakScanner(
        detection_callback=detection_callback,
    )

    await scanner.start()

    try:
        # Yalnızca ayarlanan süre kadar tarama yapar.
        await asyncio.sleep(settings.ble_scan_seconds)

    finally:
        # Hata olsa bile scanner mutlaka kapatılır.
        await scanner.stop()

    elapsed_ms = round(
        (time.monotonic() - started_monotonic) * 1000,
        2,
    )

    # MAC adresine göre stabil sıralama.
    devices = sorted(
        discovered.values(),
        key=lambda item: item.ble_device.address.upper(),
    )

    scan_info = {
        "target_device_name": settings.ble_target_device_name,
        "started_at": started_at.isoformat(),
        "requested_duration_seconds": settings.ble_scan_seconds,
        "actual_duration_ms": elapsed_ms,
        "found_count": len(devices),
        "discovery_filter": "bluetooth_device_name",
        "mac_filtering": False,
    }

    return devices, scan_info
