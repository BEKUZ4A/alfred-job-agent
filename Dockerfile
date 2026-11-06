FROM python:3.11-slim

WORKDIR /app

# Tizim paketlarini yangilash
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Talab qilinadigan python kutubxonalarini o'rnatish
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Loyiha fayllarini ko'chirish
COPY . .

# Standart port
ENV PORT=8000
EXPOSE 8000

# Serverni ishga tushirish
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
