"""
FastAPI JSON / OpenAPI veri modelleri.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LoruvClimateDevice(BaseModel):
    """
    Tek bir LYWSD03MMC cihazının tam API çıktısı.
    """

    # MAC adresinden üretilen benzersiz API kimliği.
    device_id: str

    # Kullanıcı tarafından verilen veya otomatik oluşturulan isim.
    name: str

    # Gerçek Bluetooth MAC adresi.
    mac_address: str

    # Cihazın okuma durumu.
    status: str

    # Cihazın son okunma zamanı.
    read_at: datetime | None = None

    # BLE advertisement sırasında görülen tüm kullanılabilir bilgiler.
    advertisement: dict[str, Any] = Field(default_factory=dict)

    # Anlamını bildiğimiz karakteristiklerin çözümlenmiş hali.
    decoded: dict[str, Any] = Field(default_factory=dict)

    # Bütün GATT servisleri, karakteristikleri, descriptor'ları
    # ve okunabilen ham değerleri.
    gatt_services: list[dict[str, Any]] = Field(default_factory=list)

    # Bağlantı ve okuma istatistikleri.
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    # Cihaz bazlı hata oluşmuşsa açıklaması.
    error: str | None = None


class LoruvClimateDevicesResponse(BaseModel):
    """
    Tüm termometreleri döndüren ana API cevabı.
    """

    service: str
    api_version: str

    success: bool

    # Sonuç Bluetooth'tan mı yoksa RAM cache'den mi geldi?
    cached: bool

    # Cache'in saniye cinsinden yaşı.
    cache_age_seconds: float | None = None

    # API cevabının üretildiği zaman.
    timestamp: datetime

    # Sensörlerin gerçekten toplandığı zaman.
    captured_at: datetime

    # BLE taramasına ait bilgiler.
    scan: dict[str, Any] = Field(default_factory=dict)

    device_count: int

    devices: list[LoruvClimateDevice]


class LoruvSingleDeviceResponse(BaseModel):
    """
    Tek cihaz endpoint'inin cevabı.
    """

    service: str
    api_version: str

    cached: bool
    cache_age_seconds: float | None = None

    timestamp: datetime
    captured_at: datetime

    device: LoruvClimateDevice


class LoruvServiceStatusResponse(BaseModel):
    """
    Bluetooth taraması yapmadan servis durumunu döndürür.
    """

    service: str
    status: str
    version: str
    uptime_seconds: float

    cache: dict[str, Any]


class LoruvServiceInfoResponse(BaseModel):
    """
    API'nin teknik bilgilerini ve yeteneklerini açıklar.
    """

    name: str
    version: str
    api_version: str

    supported_device: str

    bluetooth_backend: str

    discovery_mode: str

    operation_mode: str

    features: list[str]

    endpoints: dict[str, str]
