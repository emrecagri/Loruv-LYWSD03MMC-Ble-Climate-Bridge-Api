# ============================================================
# Loruv LYWSD03MMC BLE Climate Bridge API
# ============================================================

# Raspberry Pi ARM64 ve amd64 için resmi Python tabanı.
FROM python:3.13-slim

# Python .pyc dosyaları üretmesin.
ENV PYTHONDONTWRITEBYTECODE=1

# Loglar Docker/Portainer'a anında gönderilsin.
ENV PYTHONUNBUFFERED=1

# pip cache image içinde tutulmasın.
ENV PIP_NO_CACHE_DIR=1

# Bleak Linux backend'in host BlueZ system bus yolunu açık belirtir.
ENV DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket

# Uygulama çalışma klasörü.
WORKDIR /app

# Önce bağımlılık listesini kopyalamak Docker layer cache'i iyileştirir.
COPY requirements.txt /app/requirements.txt

# Python bağımlılıklarını kurar.
RUN pip install --no-cache-dir -r /app/requirements.txt

# Uygulama kaynaklarını image içine kopyalar.
COPY app /app/app

# REST API portu.
EXPOSE 8765

# Container healthcheck'i Bluetooth'a dokunmaz.
# Sadece FastAPI process'inin cevap verdiğini doğrular.
HEALTHCHECK \
    --interval=30s \
    --timeout=5s \
    --start-period=10s \
    --retries=3 \
    CMD python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/v1/bridge-status', timeout=3)"

# Tek worker bilinçli bir tercihtir.
#
# RAM cache ve asyncio BLE lock tek process içerisinde kalır.
# Birden fazla worker aynı anda Bluetooth işlemi başlatabilir.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]
