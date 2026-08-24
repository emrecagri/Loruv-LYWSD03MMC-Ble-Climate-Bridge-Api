"""
LYWSD03MMC read-only GATT okuyucu.

Bu modül cihaz üzerinde hiçbir GATT WRITE işlemi yapmaz.

Okunanlar:
- sıcaklık
- nem
- pil voltajı
- cihazın raporladığı pil yüzdesi
- ekran sıcaklık birimi
- cihaz zamanı
- history indeksleri
- son saat history kaydı
- comfort değerleri
- firmware / hardware / software / model / serial / manufacturer
- Generic Access bilgileri
- diğer tüm okunabilir GATT karakteristiklerinin ham değerleri
- mümkünse GATT descriptor değerleri
"""

import asyncio
import logging
import struct
import time
from datetime import datetime, timezone
from typing import Any

from bleak import BleakClient

from app.loruv_climate_config import settings
from app.loruv_lywsd03mmc_discovery import LoruvDiscoveredDevice


logger = logging.getLogger(__name__)


# ============================================================
# LYWSD03MMC KARAKTERİSTİK UUID'LERİ
# ============================================================

UUID_CLIMATE_DATA = "ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6"
UUID_BATTERY_REPORTED = "ebe0ccc4-7a0a-4b0c-8a1a-6ff2997da3a6"
UUID_TEMPERATURE_UNIT = "ebe0ccbe-7a0a-4b0c-8a1a-6ff2997da3a6"

UUID_DEVICE_TIME = "ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6"
UUID_HISTORY_COUNT = "ebe0ccb9-7a0a-4b0c-8a1a-6ff2997da3a6"
UUID_HISTORY_INDEX = "ebe0ccba-7a0a-4b0c-8a1a-6ff2997da3a6"
UUID_HISTORY_LAST_HOUR = "ebe0ccbb-7a0a-4b0c-8a1a-6ff2997da3a6"

UUID_COMFORT_RANGE = "ebe0ccd7-7a0a-4b0c-8a1a-6ff2997da3a6"

UUID_DEVICE_NAME = "00002a00-0000-1000-8000-00805f9b34fb"
UUID_APPEARANCE = "00002a01-0000-1000-8000-00805f9b34fb"
UUID_CONNECTION_PARAMETERS = "00002a04-0000-1000-8000-00805f9b34fb"

UUID_MODEL = "00002a24-0000-1000-8000-00805f9b34fb"
UUID_SERIAL = "00002a25-0000-1000-8000-00805f9b34fb"
UUID_FIRMWARE = "00002a26-0000-1000-8000-00805f9b34fb"
UUID_HARDWARE = "00002a27-0000-1000-8000-00805f9b34fb"
UUID_SOFTWARE = "00002a28-0000-1000-8000-00805f9b34fb"
UUID_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"


def _safe_text(data: bytes) -> str | None:
    """
    Binary verinin gerçek okunabilir UTF-8 metin olup olmadığını kontrol eder.
    """

    try:
        text = data.decode(
            "utf-8",
            errors="strict",
        ).rstrip("\x00").strip()

    except UnicodeDecodeError:
        return None

    if not text:
        return None

    if not all(character.isprintable() for character in text):
        return None

    return text


def _raw_bytes_snapshot(data: bytes) -> dict[str, Any]:
    """
    Ham GATT değerini mümkün olduğunca bilgi kaybetmeden JSON'a dönüştürür.
    """

    result: dict[str, Any] = {
        "length": len(data),
        "hex": data.hex(" "),
        "hex_compact": data.hex(),
        "bytes_decimal": list(data),
    }

    text = _safe_text(data)

    if text is not None:
        result["utf8_text"] = text

    # Küçük binary değerlerde ham integer yorumlarını da sunarız.
    if 0 < len(data) <= 8:
        result["uint_little_endian"] = int.from_bytes(
            data,
            byteorder="little",
            signed=False,
        )

        result["int_little_endian"] = int.from_bytes(
            data,
            byteorder="little",
            signed=True,
        )

    return result


def _decode_text(
    raw_values: dict[str, bytes],
    uuid: str,
) -> str | None:
    """
    Belirtilen karakteristiği UTF-8 metin olarak çözer.
    """

    data = raw_values.get(uuid.lower())

    if data is None:
        return None

    return _safe_text(data)


def _safe_unix_timestamp(
    timestamp: int,
) -> str | None:
    """
    Cihaz zaman değeri makul bir Unix timestamp ise ISO-UTC üretir.

    Saat senkronize edilmemiş cihazlarda anlamsız eski tarihler
    oluşabileceği için 2000-2100 aralığını güvenilir kabul ediyoruz.
    """

    year_2000 = 946684800
    year_2100 = 4102444800

    if not year_2000 <= timestamp <= year_2100:
        return None

    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).isoformat()


def _decode_known_values(
    raw_values: dict[str, bytes],
) -> dict[str, Any]:
    """
    Anlamı bilinen LYWSD03MMC karakteristiklerini çözer.

    Bilmediğimiz değerler kaybolmaz;
    gatt_services bölümünde ham olarak ayrıca tutulurlar.
    """

    decoded: dict[str, Any] = {
        "measurements": {},
        "battery": {},
        "display": {},
        "device_info": {},
        "device_clock": {},
        "history": {},
        "comfort_profile": {},
        "generic_access": {},
    }

    # --------------------------------------------------------
    # SICAKLIK + NEM + PİL VOLTAJI
    # --------------------------------------------------------

    climate = raw_values.get(UUID_CLIMATE_DATA)

    if climate is not None and len(climate) >= 5:
        temperature_raw = int.from_bytes(
            climate[0:2],
            byteorder="little",
            signed=True,
        )

        humidity_percent = int(climate[2])

        battery_mv = int.from_bytes(
            climate[3:5],
            byteorder="little",
            signed=False,
        )

        decoded["measurements"] = {
            "temperature_c": round(
                temperature_raw / 100.0,
                2,
            ),
            "temperature_raw": temperature_raw,
            "humidity_percent": humidity_percent,
        }

        decoded["battery"]["voltage_mv"] = battery_mv
        decoded["battery"]["voltage_v"] = round(
            battery_mv / 1000.0,
            3,
        )

    # --------------------------------------------------------
    # STOCK FIRMWARE PİL YÜZDESİ
    # --------------------------------------------------------

    battery = raw_values.get(UUID_BATTERY_REPORTED)

    if battery:
        decoded["battery"]["reported_percent"] = int(
            battery[0]
        )

        # Stock firmware'de bu alan çoğunlukla 100 döner.
        decoded["battery"]["reported_percent_note"] = (
            "Stock firmware value; known to be unreliable on LYWSD03MMC."
        )

    # --------------------------------------------------------
    # SICAKLIK EKRAN BİRİMİ
    # --------------------------------------------------------

    temperature_unit = raw_values.get(
        UUID_TEMPERATURE_UNIT
    )

    if temperature_unit:
        unit_code = int(temperature_unit[0])

        decoded["display"]["temperature_unit_code"] = unit_code

        decoded["display"]["temperature_unit"] = {
            0: "C",
            1: "F",
        }.get(
            unit_code,
            "unknown",
        )

    # --------------------------------------------------------
    # DEVICE INFORMATION
    # --------------------------------------------------------

    decoded["device_info"] = {
        "bluetooth_name": _decode_text(
            raw_values,
            UUID_DEVICE_NAME,
        ),
        "model": _decode_text(
            raw_values,
            UUID_MODEL,
        ),
        "serial_number": _decode_text(
            raw_values,
            UUID_SERIAL,
        ),
        "firmware_revision": _decode_text(
            raw_values,
            UUID_FIRMWARE,
        ),
        "hardware_revision": _decode_text(
            raw_values,
            UUID_HARDWARE,
        ),
        "software_revision": _decode_text(
            raw_values,
            UUID_SOFTWARE,
        ),
        "manufacturer": _decode_text(
            raw_values,
            UUID_MANUFACTURER,
        ),
    }

    # --------------------------------------------------------
    # CİHAZ ZAMANI
    # --------------------------------------------------------

    device_time = raw_values.get(UUID_DEVICE_TIME)

    if device_time is not None and len(device_time) >= 4:
        timestamp_raw = int.from_bytes(
            device_time[:4],
            byteorder="little",
            signed=False,
        )

        decoded["device_clock"] = {
            "raw_seconds": timestamp_raw,
            "unix_iso_utc_if_plausible": _safe_unix_timestamp(
                timestamp_raw
            ),
        }

    # --------------------------------------------------------
    # HISTORY DATA COUNT
    # --------------------------------------------------------

    history_count = raw_values.get(UUID_HISTORY_COUNT)

    if history_count is not None and len(history_count) >= 8:
        first_value, second_value = struct.unpack(
            "<II",
            history_count[:8],
        )

        decoded["history"][
            "record_indexes"
        ] = {
            "last_calculated_hour_index": first_value,
            "next_record_index": second_value,
        }

    # --------------------------------------------------------
    # HISTORY FIRST INDEX
    # --------------------------------------------------------

    history_index = raw_values.get(UUID_HISTORY_INDEX)

    if history_index is not None and len(history_index) >= 4:
        decoded["history"]["first_history_index"] = int.from_bytes(
            history_index[:4],
            byteorder="little",
            signed=False,
        )

    # --------------------------------------------------------
    # SON SAATLİK HISTORY KAYDI
    # --------------------------------------------------------

    last_hour = raw_values.get(
        UUID_HISTORY_LAST_HOUR
    )

    if last_hour is not None and len(last_hour) >= 14:

        (
            record_index,
            timestamp,
            temperature_max_raw,
            humidity_max,
            temperature_min_raw,
            humidity_min,
        ) = struct.unpack(
            "<IIhBhB",
            last_hour[:14],
        )

        decoded["history"]["last_hour_record"] = {
            "record_index": record_index,
            "timestamp_raw": timestamp,
            "timestamp_iso_utc_if_plausible": _safe_unix_timestamp(
                timestamp
            ),
            "temperature_max_c": round(
                temperature_max_raw / 10.0,
                1,
            ),
            "humidity_max_percent": humidity_max,
            "temperature_min_c": round(
                temperature_min_raw / 10.0,
                1,
            ),
            "humidity_min_percent": humidity_min,
        }

    # --------------------------------------------------------
    # COMFORT PROFİLİ
    # --------------------------------------------------------

    comfort = raw_values.get(UUID_COMFORT_RANGE)

    if comfort is not None and len(comfort) >= 6:

        (
            temperature_a_raw,
            temperature_b_raw,
            humidity_a,
            humidity_b,
        ) = struct.unpack(
            "<hhBB",
            comfort[:6],
        )

        temperature_values = [
            round(temperature_a_raw / 100.0, 2),
            round(temperature_b_raw / 100.0, 2),
        ]

        humidity_values = [
            humidity_a,
            humidity_b,
        ]

        # Firmware varyasyonlarındaki byte sırası belirsizliği yüzünden
        # hem ham cihaz sırasını hem normalize min/max değerlerini veririz.
        decoded["comfort_profile"] = {
            "temperature_values_c_in_device_order": (
                temperature_values
            ),
            "temperature_min_c": min(
                temperature_values
            ),
            "temperature_max_c": max(
                temperature_values
            ),
            "humidity_values_percent_in_device_order": (
                humidity_values
            ),
            "humidity_min_percent": min(
                humidity_values
            ),
            "humidity_max_percent": max(
                humidity_values
            ),
        }

    # --------------------------------------------------------
    # GENERIC ACCESS: APPEARANCE
    # --------------------------------------------------------

    appearance = raw_values.get(UUID_APPEARANCE)

    if appearance is not None and len(appearance) >= 2:
        decoded["generic_access"]["appearance_code"] = (
            int.from_bytes(
                appearance[:2],
                byteorder="little",
                signed=False,
            )
        )

    # --------------------------------------------------------
    # GENERIC ACCESS: CONNECTION PARAMETERS
    # --------------------------------------------------------

    connection_parameters = raw_values.get(
        UUID_CONNECTION_PARAMETERS
    )

    if (
        connection_parameters is not None
        and len(connection_parameters) >= 8
    ):
        (
            min_interval,
            max_interval,
            latency,
            supervision_timeout,
        ) = struct.unpack(
            "<HHHH",
            connection_parameters[:8],
        )

        decoded["generic_access"][
            "preferred_connection_parameters"
        ] = {
            "minimum_interval_units": min_interval,
            "minimum_interval_ms": round(
                min_interval * 1.25,
                2,
            ),
            "maximum_interval_units": max_interval,
            "maximum_interval_ms": round(
                max_interval * 1.25,
                2,
            ),
            "slave_latency": latency,
            "supervision_timeout_units": supervision_timeout,
            "supervision_timeout_ms": (
                supervision_timeout * 10
            ),
        }

    return decoded


async def _read_with_timeout(
    client: BleakClient,
    characteristic: Any,
) -> bytes:
    """
    Bir GATT karakteristiğini timeout korumasıyla okur.
    """

    return bytes(
        await asyncio.wait_for(
            client.read_gatt_char(characteristic),
            timeout=settings.gatt_read_timeout_seconds,
        )
    )


async def _read_descriptor_with_timeout(
    client: BleakClient,
    descriptor_handle: int,
) -> bytes:
    """
    Descriptor değerini timeout korumasıyla read-only okur.
    """

    return bytes(
        await asyncio.wait_for(
            client.read_gatt_descriptor(
                descriptor_handle
            ),
            timeout=settings.gatt_read_timeout_seconds,
        )
    )


async def read_lywsd03mmc_device(
    discovered_device: LoruvDiscoveredDevice,
) -> dict[str, Any]:
    """
    Tek bir keşfedilmiş LYWSD03MMC cihazını tamamen read-only okur.
    """

    ble_device = discovered_device.ble_device

    mac_address = ble_device.address.upper()

    started_monotonic = time.monotonic()

    client = BleakClient(
        ble_device,
        timeout=settings.ble_connect_timeout_seconds,
    )

    # UUID -> gerçek binary değer.
    raw_values: dict[str, bytes] = {}

    gatt_services: list[dict[str, Any]] = []

    readable_characteristics = 0
    successful_characteristic_reads = 0
    failed_characteristic_reads = 0

    successful_descriptor_reads = 0
    failed_descriptor_reads = 0

    try:
        logger.info(
            "LYWSD03MMC cihazına bağlanılıyor: %s",
            mac_address,
        )

        await client.connect()

        # ----------------------------------------------------
        # TÜM GATT SERVİSLERİNİ GEZ
        # ----------------------------------------------------

        for service in client.services:

            service_snapshot: dict[str, Any] = {
                "uuid": service.uuid,
                "description": service.description,
                "handle": getattr(
                    service,
                    "handle",
                    None,
                ),
                "characteristics": [],
            }

            for characteristic in service.characteristics:

                characteristic_snapshot: dict[str, Any] = {
                    "uuid": characteristic.uuid,
                    "description": characteristic.description,
                    "handle": characteristic.handle,
                    "properties": list(
                        characteristic.properties
                    ),
                    "descriptors": [],
                }

                # --------------------------------------------
                # READ ÖZELLİĞİ VARSA HAM DEĞERİ OKU
                # --------------------------------------------

                if "read" in characteristic.properties:
                    readable_characteristics += 1

                    try:
                        data = await _read_with_timeout(
                            client,
                            characteristic,
                        )

                        successful_characteristic_reads += 1

                        raw_values.setdefault(
                            characteristic.uuid.lower(),
                            data,
                        )

                        characteristic_snapshot[
                            "read_value"
                        ] = _raw_bytes_snapshot(data)

                    except Exception as error:
                        failed_characteristic_reads += 1

                        characteristic_snapshot[
                            "read_error"
                        ] = {
                            "type": type(error).__name__,
                            "message": str(error),
                        }

                # --------------------------------------------
                # DESCRIPTOR'LAR
                # --------------------------------------------

                for descriptor in characteristic.descriptors:

                    descriptor_snapshot: dict[str, Any] = {
                        "uuid": descriptor.uuid,
                        "description": descriptor.description,
                        "handle": descriptor.handle,
                    }

                    if settings.read_gatt_descriptors:
                        try:
                            descriptor_data = (
                                await _read_descriptor_with_timeout(
                                    client,
                                    descriptor.handle,
                                )
                            )

                            successful_descriptor_reads += 1

                            descriptor_snapshot[
                                "read_value"
                            ] = _raw_bytes_snapshot(
                                descriptor_data
                            )

                        except Exception as error:
                            failed_descriptor_reads += 1

                            descriptor_snapshot[
                                "read_error"
                            ] = {
                                "type": type(error).__name__,
                                "message": str(error),
                            }

                    characteristic_snapshot[
                        "descriptors"
                    ].append(
                        descriptor_snapshot
                    )

                service_snapshot[
                    "characteristics"
                ].append(
                    characteristic_snapshot
                )

            gatt_services.append(
                service_snapshot
            )

        # ----------------------------------------------------
        # BİLİNEN DEĞERLERİ ÇÖZ
        # ----------------------------------------------------

        decoded = _decode_known_values(
            raw_values
        )

        bluetooth_name = (
            decoded
            .get("device_info", {})
            .get("bluetooth_name")
        )

        if not bluetooth_name:
            bluetooth_name = (
                discovered_device
                .advertisement
                .get("local_name")
                or ble_device.name
            )

        display_name = (
            settings.get_device_display_name(
                mac_address,
                bluetooth_name,
            )
        )

        compact_mac = mac_address.replace(
            ":",
            "",
        ).lower()

        device_id = (
            f"lywsd03mmc-{compact_mac}"
        )

        # Climate karakteristiği başarıyla okunmadıysa
        # bağlantı kurulmuş olsa bile partial kabul ediyoruz.
        has_measurement = bool(
            decoded
            .get("measurements", {})
            .get("temperature_c")
            is not None
        )

        status = (
            "online"
            if has_measurement
            else "partial"
        )

        elapsed_ms = round(
            (
                time.monotonic()
                - started_monotonic
            )
            * 1000,
            2,
        )

        return {
            "device_id": device_id,
            "name": display_name,
            "mac_address": mac_address,
            "status": status,
            "read_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "advertisement": (
                discovered_device.advertisement
            ),
            "decoded": decoded,
            "gatt_services": gatt_services,
            "diagnostics": {
                "total_duration_ms": elapsed_ms,
                "gatt_service_count": len(
                    gatt_services
                ),
                "readable_characteristic_count": (
                    readable_characteristics
                ),
                "successful_characteristic_reads": (
                    successful_characteristic_reads
                ),
                "failed_characteristic_reads": (
                    failed_characteristic_reads
                ),
                "successful_descriptor_reads": (
                    successful_descriptor_reads
                ),
                "failed_descriptor_reads": (
                    failed_descriptor_reads
                ),
                "write_operations_performed": 0,
            },
            "error": None,
        }

    except Exception as error:

        logger.exception(
            "LYWSD03MMC okuma hatası: %s",
            mac_address,
        )

        compact_mac = mac_address.replace(
            ":",
            "",
        ).lower()

        elapsed_ms = round(
            (
                time.monotonic()
                - started_monotonic
            )
            * 1000,
            2,
        )

        return {
            "device_id": (
                f"lywsd03mmc-{compact_mac}"
            ),
            "name": settings.get_device_display_name(
                mac_address,
                ble_device.name,
            ),
            "mac_address": mac_address,
            "status": "error",
            "read_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "advertisement": (
                discovered_device.advertisement
            ),
            "decoded": {},
            "gatt_services": gatt_services,
            "diagnostics": {
                "total_duration_ms": elapsed_ms,
                "write_operations_performed": 0,
            },
            "error": (
                f"{type(error).__name__}: {error}"
            ),
        }

    finally:

        # Her durumda BLE bağlantısını kapat.
        if client.is_connected:
            await client.disconnect()

            logger.info(
                "BLE bağlantısı kapatıldı: %s",
                mac_address,
            )
