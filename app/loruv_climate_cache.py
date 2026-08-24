"""
Loruv Climate Bridge RAM cache sistemi.

Amaç:
- Cache süresi dolmadıysa Bluetooth'a hiç dokunmamak.
- Aynı anda 20 API isteği gelse bile yalnızca bir BLE taraması yapmak.
"""

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable
from typing import Any


class LoruvAsyncTTLCache:
    """
    asyncio uyumlu RAM tabanlı TTL cache.

    Tek process / tek Uvicorn worker kullanılması gerekir.
    """

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds

        # Son başarılı snapshot.
        self._value: dict[str, Any] | None = None

        # Snapshot'ın monotonic clock zamanı.
        self._stored_at: float | None = None

        # Aynı anda yalnızca bir BLE refresh çalışmasını sağlar.
        self._refresh_lock = asyncio.Lock()

    def _age_seconds(self) -> float | None:
        """
        Cache varsa kaç saniyelik olduğunu hesaplar.
        """

        if self._stored_at is None:
            return None

        return max(0.0, time.monotonic() - self._stored_at)

    def _get_fresh_value(
        self,
    ) -> tuple[dict[str, Any] | None, float | None]:
        """
        Cache hâlâ geçerliyse kopyasını döndürür.
        """

        if self._value is None:
            return None, None

        age = self._age_seconds()

        if age is None:
            return None, None

        if age >= self.ttl_seconds:
            return None, age

        # Çağıran kod cache içeriğini değiştiremesin diye deepcopy.
        return copy.deepcopy(self._value), age

    async def get_or_refresh(
        self,
        refresh_function: Callable[[], Awaitable[dict[str, Any]]],
    ) -> tuple[dict[str, Any], bool, float | None]:
        """
        Fresh cache varsa doğrudan döndürür.

        Cache yoksa/dolduysa tek bir coroutine BLE refresh yapar.
        Diğer eşzamanlı istekler kilidin arkasında bekler.
        """

        cached_value, age = self._get_fresh_value()

        if cached_value is not None:
            return cached_value, True, age

        async with self._refresh_lock:

            # Kilidi beklerken başka request cache üretmiş olabilir.
            cached_value, age = self._get_fresh_value()

            if cached_value is not None:
                return cached_value, True, age

            # Gerçek BLE taraması ve cihaz okumaları burada yapılır.
            new_value = await refresh_function()

            self._value = copy.deepcopy(new_value)
            self._stored_at = time.monotonic()

            return copy.deepcopy(new_value), False, 0.0

    def inspect(self) -> dict[str, Any]:
        """
        Bluetooth kullanmadan cache durumunu döndürür.
        """

        age = self._age_seconds()

        return {
            "has_value": self._value is not None,
            "ttl_seconds": self.ttl_seconds,
            "age_seconds": round(age, 3) if age is not None else None,
            "fresh": (
                age is not None
                and self._value is not None
                and age < self.ttl_seconds
            ),
        }

    def clear(self) -> None:
        """
        Cache'i RAM'den temizler.

        Şimdilik public API endpoint'i olarak sunulmuyor.
        """

        self._value = None
        self._stored_at = None
