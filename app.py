from flask import Flask, request, jsonify
import os, subprocess, uuid, shutil, gc, math
import requests
import psutil
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

def log_memory_usage(label=""):
    """Логирование использования памяти"""
    process = psutil.Process()
    memory_info = process.memory_info()
    memory_mb = memory_info.rss / 1024 / 1024
    print(f"[MEMORY {label}] {memory_mb:.2f} MB")
    return memory_mb

def get_video_info(video_url, base_params):
    """Получить информацию о видео (длительность в секундах)"""
    cmd = ['yt-dlp', video_url, '-j'] + base_params
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    
    if result.returncode != 0:
        return None
    
    import json
    info = json.loads(result.stdout)
    
    return {
        'duration': info.get('duration', 0),
        'title': info.get('title', 'Unknown')
    }

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    memory_mb = log_memory_usage("HEALTH_CHECK")
    return jsonify({
        'status': 'ok', 
        'bucket': BUCKET,
        'cookies_exists': os.path.exists('/app/cookies.txt'),
        'memory_mb': round(memory_mb, 2),
        'files': os.listdir('/app') if os.path.exists('/app') else []
    })

@app.route('/download-segments', methods=['POST'])
def download_video_segments():
    """
    Скачивание видео сегментами (потоково) + полное аудио
    
    Request body (варианты):
    
    1. Все сегменты подряд:
    {
        "url": "https://youtube.com/watch?v=...",
        "segment_duration": 600,  // Длительность каждого сегмента в секундах (10 минут)
        "max_segments": null      // null = все сегменты
    }
    
    2. Ограниченное количество сегментов:
    {
        "url": "https://youtube.com/watch?v=...",
        "segment_duration": 600,
        "max_segments": 3         // Первые 3 сегмента
    }
    
    3. Сегменты с интервалом (каждые X минут):
    {
        "url": "https://youtube.com/watch?v=...",
        "segment_duration": 600,      // Длительность каждого сегмента
        "max_segments": 5,            // Количество сегментов для скачивания
        "segment_interval": 1800      // Скачивать каждые 30 минут (1800 секунд)
    }
    
    Примеры:
    - segment_duration=600, segment_interval=1800: скачать 10-минутные сегменты на позициях 0, 30, 60, 90... минут
    - segment_duration=300, segment_interval=600: скачать 5-минутные сегменты каждые 10 минут
    
    4. Указать конкретные временные метки:
    {
        "url": "https://youtube.com/watch?v=...",
        "segments": [
            {"start": 0, "duration": 600},       // 0-10 минут
            {"start": 1800, "duration": 600},    // 30-40 минут
            {"start": 3600, "duration": 600}     // 60-70 минут
        ]
    }
    """
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
    
    if not os.path.exists('/app/cookies.txt'):
        return jsonify({'error': 'cookies.txt file not found'}), 500
    
    temp_dir = None
    
    try:
        log_memory_usage("START")
        
        data = request.json
        video_url = data['url']
        
        folder = str(uuid.uuid4())
        temp_dir = f'/tmp/{folder}'
        os.makedirs(temp_dir, exist_ok=True)
        
        print(f"=== DOWNLOADING SEGMENTS: {video_url} ===")
        
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
        
        # Получаем информацию о видео
        print("Getting video info...")
        video_info = get_video_info(video_url, base_params)
        
        if not video_info:
            return jsonify({'error': 'Failed to get video info'}), 500
        
        total_duration = video_info['duration']
        video_title = video_info['title']
        
        print(f"Video title: {video_title}")
        print(f"Total duration: {total_duration} seconds ({total_duration / 60:.1f} minutes)")
        
        # Определяем какие сегменты скачивать
        segments_to_download = []
        
        if 'segments' in data:
            # Режим 4: Вручную указанные сегменты
            print("Mode: Custom segments")
            for seg in data['segments']:
                start = seg['start']
                duration = seg.get('duration', data.get('segment_duration', 600))
                end = min(start + duration, total_duration)
                segments_to_download.append({
                    'start': start,
                    'end': end,
                    'duration': end - start
                })
        
        elif 'segment_interval' in data:
            # Режим 3: Сегменты с интервалом
            segment_duration = data.get('segment_duration', 600)
            segment_interval = data['segment_interval']
            max_segments = data.get('max_segments', None)
            
            print(f"Mode: Interval segments")
            print(f"Segment duration: {segment_duration}s ({segment_duration / 60:.1f}min)")
            print(f"Segment interval: {segment_interval}s ({segment_interval / 60:.1f}min)")
            
            current_pos = 0
            segment_count = 0
            
            while current_pos < total_duration:
                start = current_pos
                end = min(start + segment_duration, total_duration)
                
                segments_to_download.append({
                    'start': start,
                    'end': end,
                    'duration': end - start
                })
                
                segment_count += 1
                
                # Проверяем лимит сегментов
                if max_segments and segment_count >= max_segments:
                    break
                
                # Переходим к следующей позиции с интервалом
                current_pos += segment_interval
            
            print(f"Will download {len(segments_to_download)} segments with interval {segment_interval}s")
        
        else:
            # Режим 1-2: Последовательные сегменты
            segment_duration = data.get('segment_duration', 600)
            max_segments = data.get('max_segments', None)
            
            num_segments_total = math.ceil(total_duration / segment_duration)
            
            if max_segments and max_segments > 0:
                num_segments = min(num_segments_total, max_segments)
                print(f"Mode: Sequential segments (limited to {num_segments})")
            else:
                num_segments = num_segments_total
                print(f"Mode: Sequential segments (all {num_segments})")
            
            for i in range(num_segments):
                start = i * segment_duration
                end = min((i + 1) * segment_duration, total_duration)
                
                segments_to_download.append({
                    'start': start,
                    'end': end,
                    'duration': end - start
                })
        
        print(f"Total segments to download: {len(segments_to_download)}")
        
        log_memory_usage("AFTER_PLANNING")
        
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        
        segment_urls = []
        
        # ====== СКАЧИВАЕМ СЕГМЕНТЫ ВИДЕО (ПОТОКОВО) ======
        for idx, seg in enumerate(segments_to_download):
            start_time = seg['start']
            end_time = seg['end']
            duration = seg['duration']
            
            print(f"\n=== SEGMENT {idx + 1}/{len(segments_to_download)} ===")
            print(f"Time: {start_time}s - {end_time}s ({start_time / 60:.1f}min - {end_time / 60:.1f}min)")
            print(f"Duration: {duration}s ({duration / 60:.1f}min)")
            
            log_memory_usage(f"BEFORE_SEGMENT_{idx}")
            
            # Получаем прямую ссылку на видео
            get_url_cmd = [
                'yt-dlp', video_url,
                '--get-url',
                '-f', 'bestvideo[height<=720][fps<=60]/bestvideo[height<=720]/bestvideo[height<=480]'
            ] + base_params
            
            url_result = subprocess.run(get_url_cmd, capture_output=True, text=True, timeout=60)
            
            if url_result.returncode != 0:
                print(f"Failed to get URL for segment {idx}: {url_result.stderr[:200]}")
                continue
            
            direct_video_url = url_result.stdout.strip()
            
            # Определяем расширение
            video_ext = 'webm'
            if 'mime=video%2Fmp4' in direct_video_url or '&itag=136' in direct_video_url:
                video_ext = 'mp4'
            
            segment_blob_name = f"{folder}/video_segment_{idx:03d}.{video_ext}"
            segment_blob = bucket.blob(segment_blob_name)
            
            print(f"Streaming segment {idx} to Cloud Storage...")
            
            # ПОТОКОВАЯ ЗАГРУЗКА СЕГМЕНТА
            response = requests.get(
                direct_video_url,
                stream=True,
                timeout=900,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': '*/*'
                }
            )
            
            if response.status_code != 200:
                print(f"Failed to stream segment {idx}: HTTP {response.status_code}")
                continue
            
            # Скачиваем и загружаем потоком
            downloaded_bytes = 0
            
            with segment_blob.open('wb', chunk_size=1024*1024) as blob_writer:
                for chunk in response.iter_content(chunk_size=1024*1024):
                    if chunk:
                        blob_writer.write(chunk)
                        downloaded_bytes += len(chunk)
                        
                        if downloaded_bytes % (50 * 1024 * 1024) < 1024 * 1024:
                            print(f"  Progress: {downloaded_bytes / 1024 / 1024:.1f} MB")
            
            segment_size_mb = downloaded_bytes / 1024 / 1024
            print(f"Segment {idx} uploaded: {segment_size_mb:.2f} MB")
            
            segment_url = f"https://storage.googleapis.com/{BUCKET}/{segment_blob_name}"
            segment_urls.append({
                'segment': idx,
                'start_time': start_time,
                'end_time': end_time,
                'duration': duration,
                'start_time_formatted': f"{int(start_time // 60)}:{int(start_time % 60):02d}",
                'end_time_formatted': f"{int(end_time // 60)}:{int(end_time % 60):02d}",
                'url': segment_url,
                'size_mb': round(segment_size_mb, 2),
                'filename': f'video_segment_{idx:03d}.{video_ext}'
            })
            
            log_memory_usage(f"AFTER_SEGMENT_{idx}")
            
            gc.collect()
        
        log_memory_usage("ALL_SEGMENTS_DONE")
        
        # ====== СКАЧИВАЕМ ПОЛНОЕ АУДИО (ПОТОКОВО) ======
        print("\n=== DOWNLOADING FULL AUDIO ===")
        
        audio_url_cmd = [
            'yt-dlp', video_url,
            '--get-url',
            '-f', 'bestaudio/best'
        ] + base_params
        
        audio_url_result = subprocess.run(audio_url_cmd, capture_output=True, text=True, timeout=60)
        
        audio_url_full = None
        audio_size_mb = 0
        
        if audio_url_result.returncode == 0:
            direct_audio_url = audio_url_result.stdout.strip()
            
            audio_ext = 'm4a'
            if 'mime=audio%2Fmp4' in direct_audio_url:
                audio_ext = 'm4a'
            elif 'mime=audio%2Fwebm' in direct_audio_url:
                audio_ext = 'webm'
            
            audio_blob_name = f"{folder}/audio_full.{audio_ext}"
            audio_blob = bucket.blob(audio_blob_name)
            
            print("Streaming full audio to Cloud Storage...")
            
            audio_response = requests.get(
                direct_audio_url,
                stream=True,
                timeout=1800,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': '*/*'
                }
            )
            
            if audio_response.status_code == 200:
                audio_downloaded_bytes = 0
                
                with audio_blob.open('wb', chunk_size=512*1024) as audio_writer:
                    for chunk in audio_response.iter_content(chunk_size=512*1024):
                        if chunk:
                            audio_writer.write(chunk)
                            audio_downloaded_bytes += len(chunk)
                            
                            if audio_downloaded_bytes % (10 * 1024 * 1024) < 512 * 1024:
                                print(f"  Audio progress: {audio_downloaded_bytes / 1024 / 1024:.1f} MB")
                
                audio_size_mb = audio_downloaded_bytes / 1024 / 1024
                audio_url_full = f"https://storage.googleapis.com/{BUCKET}/{audio_blob_name}"
                print(f"Full audio uploaded: {audio_size_mb:.2f} MB")
            else:
                print(f"Failed to download audio: HTTP {audio_response.status_code}")
        else:
            print(f"Failed to get audio URL: {audio_url_result.stderr[:200]}")
        
        log_memory_usage("AFTER_AUDIO")
        
        final_memory = log_memory_usage("FINAL")
        
        total_video_size_mb = sum([s['size_mb'] for s in segment_urls])
        
        return jsonify({
            'success': True,
            'video_title': video_title,
            'total_duration_seconds': total_duration,
            'total_duration_minutes': round(total_duration / 60, 1),
            'segments': segment_urls,
            'num_segments_downloaded': len(segment_urls),
            'total_video_size_mb': round(total_video_size_mb, 2),
            'audio_url': audio_url_full,
            'audio_size_mb': round(audio_size_mb, 2),
            'folder': folder,
            'memory_used_mb': round(final_memory, 2)
        })
        
    except subprocess.TimeoutExpired:
        log_memory_usage("TIMEOUT")
        return jsonify({'error': 'Download timeout'}), 500
        
    except Exception as e:
        import traceback
        log_memory_usage("ERROR")
        print(f"Exception: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()[:500]}), 500
        
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f"Cleaned up {temp_dir}")
        
        gc.collect()
        log_memory_usage("CLEANUP")

@app.route('/screenshots', methods=['POST'])
def make_screenshots():
    """Создание скриншотов из видео в Cloud Storage"""
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
    
    temp_dir = None
    
    try:
        log_memory_usage("SCREENSHOTS_START")
        
        data = request.json
        video_url = data['video_url']
        count = data.get('count', 5)
        
        folder = str(uuid.uuid4())
        temp_dir = f'/tmp/{folder}'
        os.makedirs(temp_dir, exist_ok=True)
        
        url_parts = video_url.split(f"/{BUCKET}/")
        if len(url_parts) < 2:
            return jsonify({'error': 'Invalid video URL format'}), 400
        
        blob_name = url_parts[1]
        
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        blob = bucket.blob(blob_name)
        
        video_file = f"{temp_dir}/input.mp4"
        blob.download_to_filename(video_file)
        
        log_memory_usage("AFTER_VIDEO_DOWNLOAD")
        
        cmd = ['ffmpeg', '-i', video_file, '-vf', 'fps=1/10', '-vframes', str(count), f'{temp_dir}/shot_%03d.jpg']
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return jsonify({'error': f'ffmpeg failed: {result.stderr[:300]}'}), 500
        
        log_memory_usage("AFTER_SCREENSHOTS")
        
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
        log_memory_usage("SCREENSHOTS_ERROR")
        return jsonify({'error': str(e)}), 500
        
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        gc.collect()
        log_memory_usage("SCREENSHOTS_CLEANUP")

if __name__ == '__main__':
    print("Starting Flask server on port 8080...")
    app.run(host='0.0.0.0', port=8080)
