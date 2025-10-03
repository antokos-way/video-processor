FROM python:3.10-slim
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    pip install yt-dlp flask google-cloud-storage requests
WORKDIR /app
COPY . .
CMD ["python", "app.py"]
