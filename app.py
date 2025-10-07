from flask import Flask, request, jsonify
import os, subprocess, uuid, shutil, gc
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
    """Health check endpoint"""
    return jsonify({
        'status': 'ok', 
        'bucket': BUCKET,
        'cookies_exists': os.path.exists('/app/cookies.txt'),
        'files': os.listdir('/app') if os.path.exists('/app') else []
    })

@app.route('/download', methods=['POST'])
def download_video():
    """Скачивание РАЗДЕЛЬНО: видео (до 720p 60fps) + аудио (MP3)"""
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
    
    if not os.path.exists('/app/cookies.txt'):
        return jsonify({'error': 'cookies.txt file not found'}), 500
    
    temp_dir = None
    
    try:
        data = request.json
        video_url = data['url']
        folder = str(uuid.uuid4())
        
        # Используем /tmp для экономии памяти
        temp_dir = f'/tmp/{folder}'
        os.makedirs(temp_dir, exist_ok=True)
        
        print(f"=== DOWNLOADING: {video_url} ===")
        
        base_params = [
            '--cookies', '/app/cookies.txt',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            '--referer', 'https://www.youtube.com/',
            '--no-check-certificate',
            '--extractor-args', 'youtube:player_client=mweb',
            '--sleep-requests', '2',
            '-R', '3',
            '--no-warnings',
            '--no-playlist'
        ]
        
        # Отладка: показываем доступные форматы
        print("Checking available formats...")
        debug_cmd = ['yt-dlp', video_url, '-F'] + base_params
        debug_result = subprocess.run(debug_cmd, capture_output=True, text=True, timeout=60)
        
        if debug_result.returncode == 0:
            print("Available formats:")
            print(debug_result.stdout[:1500])
        else:
            print(f"Format check failed: {debug_result.stderr[:500]}")
        
        # ====== СКАЧИВАЕМ ВИДЕО (ТОЛЬКО ВИДЕО, БЕЗ АУДИО) ======
        video_filename = f"{temp_dir}/video.%(ext)s"
        video_cmd = [
            'yt-dlp', video_url,
            '-o', video_filename,
            # ТОЛЬКО видео до 720p с поддержкой 60fps
            '-f', 'bestvideo[height<=720][fps<=60]/bestvideo[height<=720]/bestvideo[height<=480]',
            '--no-part'
        ] + base_params
        
        print("Downloading VIDEO only (720p max, 60fps)...")
        video_result = subprocess.run(video_cmd, capture_output=True, text=True, timeout=600)
        
        if video_result.returncode != 0:
            return jsonify({
                'error': 'Video download failed',
                'stderr': video_result.stderr[:800],
                'stdout': video_result.stdout[:800],
                'available_formats': debug_result.stdout[:1000] if debug_result.returncode == 0 else None
            }), 500
        
        # Проверяем видео файл
        all_files = os.listdir(temp_dir)
        video_files = [f for f in all_files if f.startswith('video.')]
        
        if not video_files:
            return jsonify({
                'error': 'Video file not found',
                'all_files': all_files
            }), 400
        
        video_path = f'{temp_dir}/{video_files[0]}'
        video_size = os.path.getsize(video_path)
        
        print(f"Video downloaded: {video_files[0]}, size: {video_size / 1024 / 1024:.2f} MB")
        
        if video_size < 10240:
            return jsonify({'error': f'Video file too small: {video_size} bytes'}), 500
        
        # ====== СКАЧИВАЕМ АУДИО (ТОЛЬКО АУДИО, MP3) ======
        audio_filename = f"{temp_dir}/audio.mp3"
        audio_cmd = [
            'yt-dlp', video_url,
            '-o', audio_filename,
            # ТОЛЬКО аудио, конвертируем в MP3
            '-f', 'bestaudio/best',
            '-x',  # Извлечь аудио
            '--audio-format', 'mp3',
            '--audio-quality', '5',  # 0 (лучшее) - 9 (худшее), 5 = среднее
            '--no-part'
        ] + base_params
        
        print("Downloading AUDIO only (MP3)...")
        audio_result = subprocess.run(audio_cmd, capture_output=True, text=True, timeout=300)
        
        if audio_result.returncode != 0:
            print(f"Audio download failed: {audio_result.stderr[:500]}")
            return jsonify({
                'error': 'Audio download failed',
                'stderr': audio_result.stderr[:500]
            }), 500
        
        # Проверяем аудио файл
        if not os.path.exists(audio_filename):
            return jsonify({'error': 'Audio file not found'}), 400
        
        audio_size = os.path.getsize(audio_filename)
        print(f"Audio downloaded: audio.mp3, size: {audio_size / 1024 / 1024:.2f} MB")
        
        if audio_size < 1024:
            return jsonify({'error': f'Audio file too small: {audio_size} bytes'}), 500
        
        # ====== ЗАГРУЖАЕМ В CLOUD STORAGE ======
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        
        # Видео
        video_blob_name = f"{folder}/{video_files[0]}"
        video_blob = bucket.blob(video_blob_name)
        
        print("Uploading video to Cloud Storage...")
        video_blob.upload_from_filename(video_path)
        video_url_result = f"https://storage.googleapis.com/{BUCKET}/{video_blob_name}"
        
        # СРАЗУ удаляем видео из памяти
        os.unlink(video_path)
        print("Video file deleted from memory")
        
        # Аудио
        audio_blob_name = f"{folder}/audio.mp3"
        audio_blob = bucket.blob(audio_blob_name)
        
        print("Uploading audio to Cloud Storage...")
        audio_blob.upload_from_filename(audio_filename)
        audio_url_result = f"https://storage.googleapis.com/{BUCKET}/{audio_blob_name}"
        
        # СРАЗУ удаляем аудио из памяти
        os.unlink(audio_filename)
        print("Audio file deleted from memory")
        
        return jsonify({
            'success': True,
            'video_url': video_url_result,
            'audio_url': audio_url_result,
            'video_filename': video_files[0],
            'audio_filename': 'audio.mp3',
            'video_size_mb': round(video_size / 1024 / 1024, 2),
            'audio_size_mb': round(audio_size / 1024 / 1024, 2),
            'folder': folder
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Download timeout'}), 500
        
    except Exception as e:
        import traceback
        print(f"Exception: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
        
    finally:
        # ВСЕГДА чистим временные файлы
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f"Cleaned up {temp_dir}")
        
        # Принудительная сборка мусора
        gc.collect()

@app.route('/download-direct', methods=['POST'])
def download_direct_link():
    """Скачивание прямых ссылок БЕЗ cookies"""
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
    
    temp_dir = None
    
    try:
        data = request.json
        video_url = data['url']
        folder = str(uuid.uuid4())
        
        temp_dir = f'/tmp/{folder}'
        os.makedirs(temp_dir, exist_ok=True)
        
        print(f"Downloading direct link: {video_url[:100]}...")
        
        if not video_url.startswith('http'):
            return jsonify({'error': 'Invalid URL format'}), 400
        
        output_file = f'{temp_dir}/video.mp4'
        
        # Скачиваем через requests
        response = requests.get(
            video_url, 
            stream=True, 
            timeout=300,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': '*/*'
            }
        )
        
        if response.status_code == 403:
            return jsonify({
                'error': 'Download forbidden (403)',
                'reasons': ['Link expired', 'IP mismatch', 'Invalid signature']
            }), 403
        
        if response.status_code != 200:
            return jsonify({'error': f'HTTP {response.status_code}'}), 500
        
        # Скачиваем
        downloaded_bytes = 0
        with open(output_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded_bytes += len(chunk)
        
        print(f"Downloaded {downloaded_bytes / 1024 / 1024:.2f} MB")
        
        if downloaded_bytes < 10240:
            return jsonify({'error': f'File too small: {downloaded_bytes} bytes'}), 500
        
        # Загружаем в Cloud Storage
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        
        video_blob_name = f"{folder}/video.mp4"
        video_blob = bucket.blob(video_blob_name)
        video_blob.upload_from_filename(output_file)
        
        video_url_result = f"https://storage.googleapis.com/{BUCKET}/{video_blob_name}"
        
        # Извлекаем аудио через ffmpeg
        audio_file = f'{temp_dir}/audio.mp3'
        audio_cmd = ['ffmpeg', '-i', output_file, '-vn', '-acodec', 'mp3', '-ab', '192k', audio_file, '-y']
        audio_result = subprocess.run(audio_cmd, capture_output=True, text=True)
        
        audio_url_result = None
        if audio_result.returncode == 0 and os.path.exists(audio_file):
            audio_size = os.path.getsize(audio_file)
            
            if audio_size > 1024:
                audio_blob_name = f"{folder}/audio.mp3"
                audio_blob = bucket.blob(audio_blob_name)
                audio_blob.upload_from_filename(audio_file)
                audio_url_result = f"https://storage.googleapis.com/{BUCKET}/{audio_blob_name}"
        
        return jsonify({
            'success': True,
            'video_url': video_url_result,
            'audio_url': audio_url_result,
            'size_mb': round(downloaded_bytes / 1024 / 1024, 2)
        })
        
    except Exception as e:
        import traceback
        print(f"Exception: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
        
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        gc.collect()

@app.route('/screenshots', methods=['POST'])
def make_screenshots():
    """Создание скриншотов из видео в Cloud Storage"""
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
    
    temp_dir = None
    
    try:
        data = request.json
        video_url = data['video_url']
        count = data.get('count', 5)
        
        folder = str(uuid.uuid4())
        temp_dir = f'/tmp/{folder}'
        os.makedirs(temp_dir, exist_ok=True)
        
        # Извлекаем blob_name из URL
        url_parts = video_url.split(f"/{BUCKET}/")
        if len(url_parts) < 2:
            return jsonify({'error': 'Invalid video URL format'}), 400
        
        blob_name = url_parts[1]
        
        # Скачиваем видео
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        blob = bucket.blob(blob_name)
        
        video_file = f"{temp_dir}/input.mp4"
        blob.download_to_filename(video_file)
        
        # Делаем скриншоты
        cmd = ['ffmpeg', '-i', video_file, '-vf', 'fps=1/10', '-vframes', str(count), f'{temp_dir}/shot_%03d.jpg']
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return jsonify({'error': f'ffmpeg failed: {result.stderr[:300]}'}), 500
        
        # Загружаем скриншоты
        screenshot_urls = []
        
        for i in range(1, count + 1):
            shot_file = f'{temp_dir}/shot_{i:03d}.jpg'
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
        
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        gc.collect()

@app.route('/download_and_screenshots', methods=['POST'])
def download_and_screenshots():
    """Скачивает раздельно видео+аудио и делает скриншоты"""
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
    
    temp_dir = None
    
    try:
        data = request.json
        video_url = data['url']
        count = data.get('count', 5)
        
        folder = str(uuid.uuid4())
        temp_dir = f'/tmp/{folder}'
        os.makedirs(temp_dir, exist_ok=True)
        
        base_params = [
            '--cookies', '/app/cookies.txt',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            '--referer', 'https://www.youtube.com/',
            '--no-check-certificate',
            '--extractor-args', 'youtube:player_client=mweb',
            '--sleep-requests', '2',
            '-R', '3',
            '--no-warnings',
            '--no-playlist'
        ]
        
        # Скачиваем видео (только видео)
        video_filename = f"{temp_dir}/video.%(ext)s"
        video_cmd = [
            'yt-dlp', video_url, 
            '-o', video_filename,
            '-f', 'bestvideo[height<=720][fps<=60]/bestvideo[height<=720]/bestvideo',
            '--no-part'
        ] + base_params
        
        print("Downloading video...")
        video_result = subprocess.run(video_cmd, capture_output=True, text=True, timeout=600)
        
        if video_result.returncode != 0:
            return jsonify({
                'error': 'Video download failed',
                'stderr': video_result.stderr[:500]
            }), 500
        
        # Скачиваем аудио
        audio_filename = f'{temp_dir}/audio.mp3'
        audio_cmd = [
            'yt-dlp', video_url,
            '-o', audio_filename,
            '-f', 'bestaudio',
            '-x',
            '--audio-format', 'mp3',
            '--audio-quality', '5'
        ] + base_params
        
        print("Downloading audio...")
        audio_result = subprocess.run(audio_cmd, capture_output=True, text=True, timeout=300)
        
        if audio_result.returncode != 0:
            return jsonify({'error': 'Audio download failed'}), 500
        
        # Находим видео файл
        files = [f for f in os.listdir(temp_dir) if f.startswith('video.')]
        if not files:
            return jsonify({'error': 'Video not found'}), 400
        
        video_path = f'{temp_dir}/{files[0]}'
        
        # Делаем скриншоты
        cmd = ['ffmpeg', '-i', video_path, '-vf', 'fps=1/10', '-vframes', str(count), f'{temp_dir}/shot_%03d.jpg']
        subprocess.run(cmd, capture_output=True, text=True)
        
        # Загружаем всё в Cloud Storage
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        
        # Видео
        video_blob_name = f"{folder}/{files[0]}"
        video_blob = bucket.blob(video_blob_name)
        video_blob.upload_from_filename(video_path)
        video_public_url = f"https://storage.googleapis.com/{BUCKET}/{video_blob_name}"
        
        # Аудио
        audio_blob_name = f"{folder}/audio.mp3"
        audio_blob = bucket.blob(audio_blob_name)
        audio_blob.upload_from_filename(audio_filename)
        audio_public_url = f"https://storage.googleapis.com/{BUCKET}/{audio_blob_name}"
        
        # Скриншоты
        screenshot_urls = []
        for i in range(1, count + 1):
            shot_file = f'{temp_dir}/shot_{i:03d}.jpg'
            if os.path.exists(shot_file):
                blob_name_shot = f"{folder}/shot_{i:03d}.jpg"
                blob_shot = bucket.blob(blob_name_shot)
                blob_shot.upload_from_filename(shot_file)
                screenshot_urls.append(f"https://storage.googleapis.com/{BUCKET}/{blob_name_shot}")
        
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
        return jsonify({'error': 'Download timeout'}), 500
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
        
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        gc.collect()

if __name__ == '__main__':
    print("Starting Flask server on port 8080...")
    app.run(host='0.0.0.0', port=8080)
