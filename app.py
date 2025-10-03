from flask import Flask, request, jsonify
import os, subprocess, uuid
import requests
from google.cloud import storage

app = Flask(__name__)
BUCKET = os.environ.get('BUCKET')

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok', 'bucket': BUCKET})

@app.route('/download', methods=['POST'])
def download_video():
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
        
    try:
        data = request.json
        video_url = data['url']
        folder = str(uuid.uuid4())
        os.makedirs(folder)
        
        # Скачиваем видео
        filename = f"{folder}/video.%(ext)s"
        cmd = ['yt-dlp', video_url, '-o', filename, '-f', 'best[height<=720]']
        subprocess.run(cmd, check=True)
        
        # Находим скачанный файл
        files = [f for f in os.listdir(folder) if f.endswith(('.mp4', '.mkv', '.webm'))]
        if not files:
            return jsonify({'error': 'Видео не скачалось'}), 400
        
        video_path = f'{folder}/{files[0]}'
        
        # Загружаем в Cloud Storage
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        blob_name = f"{folder}/{files[0]}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(video_path)
        
        # Простой публичный URL (без подписи)
        public_url = f"https://storage.googleapis.com/{BUCKET}/{blob_name}"
        
        return jsonify({
            'success': True,
            'video_url': public_url,
            'filename': files[0],
            'folder': folder
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/screenshots', methods=['POST'])
def make_screenshots():
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
        
    try:
        data = request.json
        video_url = data['video_url']
        count = data.get('count', 5)
        
        folder = str(uuid.uuid4())
        os.makedirs(folder)
        
        # Извлекаем blob_name из URL
        # URL: https://storage.googleapis.com/bucket/path/file.mp4
        # Нужно получить: path/file.mp4
        url_parts = video_url.split(f"/{BUCKET}/")
        if len(url_parts) < 2:
            return jsonify({'error': 'Invalid video URL format'}), 400
        
        blob_name = url_parts[1]
        
        # Скачиваем видео через Cloud Storage API
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        blob = bucket.blob(blob_name)
        
        video_file = f"{folder}/input.mp4"
        blob.download_to_filename(video_file)
        
        print(f"Video downloaded from Cloud Storage to: {video_file}")
        
        # Проверяем, что файл существует
        if not os.path.exists(video_file):
            return jsonify({'error': 'Video file not downloaded'}), 500
        
        # Делаем скриншоты каждые 10 секунд
        cmd = ['ffmpeg', '-i', video_file, '-vf', 'fps=1/10', '-vframes', str(count), f'{folder}/shot_%03d.jpg']
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return jsonify({'error': f'ffmpeg failed: {result.stderr}'}), 500
        
        # Загружаем скриншоты в Storage
        screenshot_urls = []
        
        for i in range(1, count + 1):
            shot_file = f'{folder}/shot_{i:03d}.jpg'
            if os.path.exists(shot_file):
                blob_name_shot = f"{folder}/shot_{i:03d}.jpg"
                blob_shot = bucket.blob(blob_name_shot)
                blob_shot.upload_from_filename(shot_file)
                public_url = f"https://storage.googleapis.com/{BUCKET}/{blob_name_shot}"
                screenshot_urls.append(public_url)
        
        return jsonify({
            'success': True,
            'screenshots': screenshot_urls,
            'count': len(screenshot_urls)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download_and_screenshots', methods=['POST'])
def download_and_screenshots():
    """Скачивает видео и сразу делает скриншоты за один запрос"""
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
        
    try:
        data = request.json
        video_url = data['url']
        count = data.get('count', 5)
        
        folder = str(uuid.uuid4())
        os.makedirs(folder)
        
        # Скачиваем видео
        filename = f"{folder}/video.%(ext)s"
        cmd = ['yt-dlp', video_url, '-o', filename, '-f', 'best[height<=720]']
        subprocess.run(cmd, check=True)
        
        # Находим скачанный файл
        files = [f for f in os.listdir(folder) if f.endswith(('.mp4', '.mkv', '.webm'))]
        if not files:
            return jsonify({'error': 'Видео не скачалось'}), 400
        
        video_path = f'{folder}/{files[0]}'
        
        # Сразу делаем скриншоты из локального файла
        cmd = ['ffmpeg', '-i', video_path, '-vf', 'fps=1/10', '-vframes', str(count), f'{folder}/shot_%03d.jpg']
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return jsonify({'error': f'ffmpeg failed: {result.stderr}'}), 500
        
        # Загружаем ВСЁ в Cloud Storage
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        
        # Загружаем видео
        blob_name = f"{folder}/{files[0]}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(video_path)
        video_public_url = f"https://storage.googleapis.com/{BUCKET}/{blob_name}"
        
        # Загружаем скриншоты
        screenshot_urls = []
        for i in range(1, count + 1):
            shot_file = f'{folder}/shot_{i:03d}.jpg'
            if os.path.exists(shot_file):
                blob_name_shot = f"{folder}/shot_{i:03d}.jpg"
                blob_shot = bucket.blob(blob_name_shot)
                blob_shot.upload_from_filename(shot_file)
                public_url = f"https://storage.googleapis.com/{BUCKET}/{blob_name_shot}"
                screenshot_urls.append(public_url)
        
        return jsonify({
            'success': True,
            'video_url': video_public_url,
            'screenshots': screenshot_urls,
            'count': len(screenshot_urls)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
