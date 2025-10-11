FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Установка yt-dlp
RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp && \
    chmod a+rx /usr/local/bin/yt-dlp

WORKDIR /app

# Копируем и устанавливаем Python зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY app.py .
COPY cookies.txt .

EXPOSE 8080

# Запуск через gunicorn
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
