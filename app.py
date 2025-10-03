from flask import Flask, request, jsonify
import os, subprocess, uuid
import requests
from google.cloud import storage

print("=== CONTAINER STARTUP DEBUG ===")
print(f"Working directory: {os.getcwd()}")
print(f"Files in current dir: {os.listdir('.')}")
if os.path.exists('/app'):
    print(f"Files in /app: {os.listdir('/app')}")
print(f"Cookies file exists: {os.path.exists('/app/cookies.txt')}")
print(f"BUCKET env var: {os.environ.get('BUCKET', 'NOT SET')}")
print("=== END DEBUG ===")

app = Flask(__name__)
BUCKET = os.environ.get('BUCKET')

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'ok', 
        'bucket': BUCKET,
        'cookies_exists': os.path.exists('/app/cookies.txt'),
        'files': os.listdir('/app') if os.path.exists('/app') else []
    })

@app.route('/download', methods=['POST'])
def download_video():
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
    
    # Проверяем cookies
    if not os.path.exists('/app/cookies.txt'):
        return jsonify({'error': 'cookies.txt file not found in container'}), 500
        
    try:
        data = request.json
        video_url = data['url']
        folder = str(uuid.uuid4())
        os.makedirs(folder)
        
        print(f"Downloading video and audio: {video_url}")
        
        # Минимальные параметры
        base_params = [
            '--cookies', '/app/cookies.txt',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            '--referer', 'https://www.youtube.com/',
            '--no-check-certificate',
            '--sleep-requests', '2',
            '-R', '3',
            '-w',
            '--ignore-errors'
        ]
        
        # Скачиваем ВИДЕО
        video_filename = f"{folder}/video.%(ext)s"
        video_cmd = [
            'yt-dlp', video_url, 
            '-o', video_filename,
            '-f', 'best[height<=1080]/best[height<=720]/best',  # 1080p → 720p → любое
            '--no-audio'
        ] + base_params
        
        print(f"Running video command: {' '.join(video_cmd)}")
        video_result = subprocess.run(video_cmd, capture_output=True, text=True, timeout=300)
        
        # Скачиваем АУДИО
        audio_filename = f"{folder}/audio.%(ext)s"
        audio_cmd = [
            'yt-dlp', video_url,
            '-o', audio_filename,
            '-f', 'bestaudio',
            '--extract-audio',
            '--audio-format', 'mp3',
            '--audio-quality', '5'
        ] + base_params
        
        print(f"Running audio command: {' '.join(audio_cmd)}")
        audio_result = subprocess.run(audio_cmd, capture_output=True, text=True, timeout=300)
        
        # Проверяем результаты
        if video_result.returncode != 0:
            return jsonify({
                'error': 'Video download failed',
                'stderr': video_result.stderr[:500],
                'stdout': video_result.stdout[:500]
            }), 500
            
        if audio_result.returncode != 0:
            return jsonify({
                'error': 'Audio download failed',
                'stderr': audio_result.stderr[:500],
                'stdout': audio_result.stdout[:500]
            }), 500
        
        # Проверяем файлы
        all_files = os.listdir(folder)
        video_files = [f for f in all_files if f.startswith('video.')]
        audio_files = [f for f in all_files if f.startswith('audio.')]
        
        if not video_files or not audio_files:
            return jsonify({
                'error': 'Files not found',
                'all_files': all_files,
                'video_files': video_files,
                'audio_files': audio_files
            }), 400
        
        # Загружаем в Cloud Storage
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        
        # Видео
        video_path = f'{folder}/{video_files[0]}'
        video_blob_name = f"{folder}/{video_files[0]}"
        video_blob = bucket.blob(video_blob_name)
        video_blob.upload_from_filename(video_path)
        video_url_result = f"https://storage.googleapis.com/{BUCKET}/{video_blob_name}"
        
        # Аудио
        audio_path = f'{folder}/{audio_files[0]}'
        audio_blob_name = f"{folder}/{audio_files[0]}"
        audio_blob = bucket.blob(audio_blob_name)
        audio_blob.upload_from_filename(audio_path)
        audio_url_result = f"https://storage.googleapis.com/{BUCKET}/{audio_blob_name}"
        
        return jsonify({
            'success': True,
            'video_url': video_url_result,
            'audio_url': audio_url_result,
            'video_filename': video_files[0],
            'audio_filename': audio_files[0],
            'folder': folder
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("Starting Flask server on port 8080...")
    app.run(host='0.0.0.0', port=8080)

