"""
Loruv LYWSD03MMC BLE Climate Bridge API
Merkezi uygulama yapılandırması.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class LoruvClimateSettings(BaseSettings):
    """
    Environment değişkenlerinden uygulama ayarlarını okur.

    Docker ortamında environment değerleri kullanılır.
    Lokal geliştirmede .env dosyası otomatik okunur.
    """

    # --------------------------------------------------------
    # API AYARLARI
    # --------------------------------------------------------

    app_name: str = "Loruv LYWSD03MMC BLE Climate Bridge API"
    app_version: str = "0.1.0"
    api_port: int = 8765
    log_level: str = "INFO"

    # --------------------------------------------------------
    # BLE AYARLARI
    # --------------------------------------------------------

    # Otomatik keşifte aranacak Bluetooth cihaz adı.
    ble_target_device_name: str = "LYWSD03MMC"

    # BLE çevre taramasının süresi.
    ble_scan_seconds: float = 10.0

    # Bir termometreye bağlanmak için maksimum süre.
    ble_connect_timeout_seconds: float = 30.0

    # Tek bir GATT read işleminin maksimum süresi.
    gatt_read_timeout_seconds: float = 5.0

    # GATT descriptor değerleri de okunacak mı?
    read_gatt_descriptors: bool = True

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    # Son sensör okumasının RAM'de geçerli kalacağı süre.
    cache_ttl_seconds: int = 60

    # --------------------------------------------------------
    # CİHAZ ÖZEL İSİMLERİ
    # --------------------------------------------------------

    # Örnek:
    # A4:C1:38:AA:BB:CC=Ev;A4:C1:38:DD:EE:FF=Çatı
    #
    # Bu alan keşif filtresi DEĞİLDİR.
    device_aliases: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def get_device_aliases(self) -> dict[str, str]:
        """
        DEVICE_ALIASES değerini Python sözlüğüne dönüştürür.
        """

        aliases: dict[str, str] = {}

        if not self.device_aliases.strip():
            return aliases

        for entry in self.device_aliases.split(";"):
            entry = entry.strip()

            if not entry or "=" not in entry:
                continue

            mac_address, alias = entry.split("=", 1)

            mac_address = mac_address.strip().upper()
            alias = alias.strip()

            if mac_address and alias:
                aliases[mac_address] = alias

        return aliases

    def get_device_display_name(
        self,
        mac_address: str,
        bluetooth_name: str | None = None,
    ) -> str:
        """
        API'de cihazın hangi isimle görüneceğini belirler.

        Öncelik:
        1. .env içindeki özel isim.
        2. Bluetooth cihaz adı + MAC sonu.
        3. LYWSD03MMC + MAC sonu.
        """

        normalized_mac = mac_address.upper()

        aliases = self.get_device_aliases()

        if normalized_mac in aliases:
            return aliases[normalized_mac]

        compact_mac = normalized_mac.replace(":", "")
        short_mac = compact_mac[-6:]

        if bluetooth_name:
            return f"{bluetooth_name}-{short_mac}"

        return f"LYWSD03MMC-{short_mac}"


@lru_cache(maxsize=1)
def get_settings() -> LoruvClimateSettings:
    """
    Ayarları uygulama ömrü boyunca tek kez oluşturur.
    """

    return LoruvClimateSettings()


settings = get_settings()
