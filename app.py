from flask import Flask, request, jsonify
import os, subprocess, uuid
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
        
        # Скачиваем видео из Storage
        video_file = f"{folder}/input.mp4"
        subprocess.run(['wget', '-O', video_file, video_url], check=True)
        
        # Делаем скриншоты
        cmd = ['ffmpeg', '-i', video_file, '-vf', 'fps=1/60', '-vframes', str(count), f'{folder}/shot_%03d.jpg']
        subprocess.run(cmd, check=True)
        
        # Загружаем скриншоты в Storage
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        screenshot_urls = []
        
        for i in range(1, count + 1):
            shot_file = f'{folder}/shot_{i:03d}.jpg'
            if os.path.exists(shot_file):
                blob_name = f"{folder}/shot_{i:03d}.jpg"
                blob = bucket.blob(blob_name)
                blob.upload_from_filename(shot_file)
                # Простой публичный URL
                public_url = f"https://storage.googleapis.com/{BUCKET}/{blob_name}"
                screenshot_urls.append(public_url)
        
        return jsonify({'screenshots': screenshot_urls})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
