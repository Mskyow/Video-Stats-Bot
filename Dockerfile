# Video Stats Bot — образ для Railway
# Python 3.11+
FROM python:3.11-slim

WORKDIR /app

# Зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения
COPY src/ ./src/
COPY .env.example .env.example

# Запуск (long polling; для webhook потребуется смена точки входа)
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "src.main"]
