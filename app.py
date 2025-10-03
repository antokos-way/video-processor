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
        
        print(f"Downloading video and audio: {video_url}")
        
        # Скачиваем ВИДЕО с параметрами обхода блокировки
        video_filename = f"{folder}/video.%(ext)s"
        video_cmd = [
            'yt-dlp', video_url, 
            '-o', video_filename,
            '-f', 'best[height<=1080]/best[height<=720]/best',
            '-R', '3',
            '-w',
            '--no-audio',
            # Параметры обхода блокировки YouTube
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
            '--referer', 'https://www.youtube.com/',
            '--no-check-certificate',
            '--extractor-args', 'youtube:player_client=web,web_embed',
            '--sleep-requests', '1'
        ]
        
        print(f"Running video command: {' '.join(video_cmd)}")
        video_result = subprocess.run(video_cmd, capture_output=True, text=True, timeout=300)
        
        # Скачиваем АУДИО с теми же параметрами обхода
        audio_filename = f"{folder}/audio.%(ext)s"
        audio_cmd = [
            'yt-dlp', video_url,
            '-o', audio_filename,
            '-f', 'bestaudio/best',
            '-R', '3',
            '-w',
            '--extract-audio',
            '--audio-format', 'mp3',
            '--audio-quality', '0',
            # Те же параметры обхода
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
            '--referer', 'https://www.youtube.com/',
            '--no-check-certificate',
            '--extractor-args', 'youtube:player_client=web,web_embed',
            '--sleep-requests', '1'
        ]
        
        print(f"Running audio command: {' '.join(audio_cmd)}")
        audio_result = subprocess.run(audio_cmd, capture_output=True, text=True, timeout=300)
        
        # Проверяем результаты
        print(f"Video result: {video_result.returncode}")
        print(f"Audio result: {audio_result.returncode}")
        
        if video_result.returncode != 0:
            print(f"Video stderr: {video_result.stderr}")
            return jsonify({
                'error': 'Video download failed',
                'video_stderr': video_result.stderr[:500],
                'video_stdout': video_result.stdout[:500]
            }), 500
            
        if audio_result.returncode != 0:
            print(f"Audio stderr: {audio_result.stderr}")
            return jsonify({
                'error': 'Audio download failed', 
                'audio_stderr': audio_result.stderr[:500],
                'audio_stdout': audio_result.stdout[:500]
            }), 500
        
        # Проверяем загруженные файлы
        all_files = os.listdir(folder)
        video_files = [f for f in all_files if f.startswith('video.') and f.endswith(('.mp4', '.mkv', '.webm'))]
        audio_files = [f for f in all_files if f.startswith('audio.') and f.endswith(('.mp3', '.m4a', '.aac'))]
        
        print(f"All files: {all_files}")
        print(f"Video files: {video_files}")
        print(f"Audio files: {audio_files}")
        
        if not video_files:
            return jsonify({
                'error': 'Video file not found',
                'all_files': all_files
            }), 400
            
        if not audio_files:
            return jsonify({
                'error': 'Audio file not found',
                'all_files': all_files
            }), 400
        
        # Загружаем оба файла в Cloud Storage
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        
        # Загружаем видео
        video_path = f'{folder}/{video_files[0]}'
        video_blob_name = f"{folder}/{video_files[0]}"
        video_blob = bucket.blob(video_blob_name)
        video_blob.upload_from_filename(video_path)
        video_url_result = f"https://storage.googleapis.com/{BUCKET}/{video_blob_name}"
        
        # Загружаем аудио
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
        
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Download timeout (5 minutes)'}), 500
    except Exception as e:
        print(f"Exception: {str(e)}")
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
    """Скачивает видео+аудио и сразу делает скриншоты за один запрос"""
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
        
    try:
        data = request.json
        video_url = data['url']
        count = data.get('count', 5)
        
        folder = str(uuid.uuid4())
        os.makedirs(folder)
        
        # Скачиваем видео для скриншотов (с аудио для полноты) с обходом блокировки
        video_filename = f"{folder}/video_full.%(ext)s"
        cmd = [
            'yt-dlp', video_url, 
            '-o', video_filename,
            '-f', 'best[height<=1080]/best[height<=720]/best',
            '-R', '3',
            '-w',
            # Параметры обхода блокировки
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
            '--referer', 'https://www.youtube.com/',
            '--no-check-certificate',
            '--extractor-args', 'youtube:player_client=web,web_embed',
            '--sleep-requests', '1'
        ]
        
        print(f"Downloading full video: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode != 0:
            return jsonify({
                'error': 'Video download failed',
                'stderr': result.stderr[:500],
                'stdout': result.stdout[:500]
            }), 500
        
        # Находим скачанный файл
        files = [f for f in os.listdir(folder) if f.startswith('video_full.')]
        if not files:
            return jsonify({'error': 'Video not downloaded', 'files': os.listdir(folder)}), 400
        
        video_path = f'{folder}/{files[0]}'
        
        # Делаем скриншоты из локального файла
        cmd = ['ffmpeg', '-i', video_path, '-vf', 'fps=1/10', '-vframes', str(count), f'{folder}/shot_%03d.jpg']
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return jsonify({'error': f'ffmpeg failed: {result.stderr}'}), 500
        
        # Извлекаем только аудио из этого же файла
        audio_path = f'{folder}/audio.mp3'
        audio_cmd = ['ffmpeg', '-i', video_path, '-vn', '-acodec', 'mp3', '-ab', '192k', audio_path]
        subprocess.run(audio_cmd, check=True)
        
        # Загружаем всё в Cloud Storage
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        
        # Загружаем видео
        video_blob_name = f"{folder}/{files[0]}"
        video_blob = bucket.blob(video_blob_name)
        video_blob.upload_from_filename(video_path)
        video_public_url = f"https://storage.googleapis.com/{BUCKET}/{video_blob_name}"
        
        # Загружаем аудио
        audio_blob_name = f"{folder}/audio.mp3"
        audio_blob = bucket.blob(audio_blob_name)
        audio_blob.upload_from_filename(audio_path)
        audio_public_url = f"https://storage.googleapis.com/{BUCKET}/{audio_blob_name}"
        
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
            'audio_url': audio_public_url,
            'screenshots': screenshot_urls,
            'video_filename': files[0],
            'audio_filename': 'audio.mp3',
            'count': len(screenshot_urls)
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Download timeout (5 minutes)'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
