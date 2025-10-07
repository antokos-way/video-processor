@app.route('/download', methods=['POST'])
def download_video():
    """Скачивание с минимальным использованием памяти"""
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
    
    if not os.path.exists('/app/cookies.txt'):
        return jsonify({'error': 'cookies.txt file not found'}), 500
    
    try:
        data = request.json
        video_url = data['url']
        folder = str(uuid.uuid4())
        
        # Создаём папку в /tmp (больше места)
        temp_dir = f'/tmp/{folder}'
        os.makedirs(temp_dir, exist_ok=True)
        
        print(f"=== DOWNLOADING: {video_url} ===")
        
        base_params = [
            '--cookies', '/app/cookies.txt',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            '--referer', 'https://www.youtube.com/',
            '--no-check-certificate',
            '--extractor-args', 'youtube:player_client=mweb',  # Обход PO Token
            '--sleep-requests', '2',
            '-R', '3',
            '--no-warnings',
            '--no-playlist'  # Не скачивать плейлист
        ]
        
        # Скачиваем ТОЛЬКО видео (меньше памяти)
        video_filename = f"{temp_dir}/video.%(ext)s"
        video_cmd = [
            'yt-dlp', video_url,
            '-o', video_filename,
            '-f', 'best[height<=720]/best',  # Ограничиваем 720p (меньше памяти!)
            '--merge-output-format', 'mp4',
            '--no-part'  # Не создавать .part файлы
        ] + base_params
        
        print("Downloading video...")
        video_result = subprocess.run(
            video_cmd, 
            capture_output=True, 
            text=True, 
            timeout=600
        )
        
        if video_result.returncode != 0:
            # Очистка при ошибке
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            
            return jsonify({
                'error': 'Video download failed',
                'stderr': video_result.stderr[:500],
                'stdout': video_result.stdout[:500]
            }), 500
        
        # Находим файл
        all_files = os.listdir(temp_dir)
        video_files = [f for f in all_files if f.startswith('video.')]
        
        if not video_files:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            return jsonify({'error': 'Video file not found'}), 400
        
        video_path = f'{temp_dir}/{video_files[0]}'
        video_size = os.path.getsize(video_path)
        
        print(f"Video downloaded: {video_size / 1024 / 1024:.2f} MB")
        
        if video_size < 10240:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            return jsonify({'error': f'File too small: {video_size} bytes'}), 500
        
        # Загружаем СРАЗУ в Cloud Storage (освобождаем память)
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET)
        
        video_blob_name = f"{folder}/{video_files[0]}"
        video_blob = bucket.blob(video_blob_name)
        
        print("Uploading video to Cloud Storage...")
        video_blob.upload_from_filename(video_path)
        video_url_result = f"https://storage.googleapis.com/{BUCKET}/{video_blob_name}"
        
        # СРАЗУ удаляем видео (освобождаем память!)
        os.unlink(video_path)
        print("Video file deleted from memory")
        
        # Извлекаем аудио через ffmpeg (без сохранения видео)
        audio_path = f'{temp_dir}/audio.mp3'
        
        # Скачиваем видео обратно для ffmpeg (нужен локальный файл)
        # Или используем прямой стрим
        print("Extracting audio...")
        
        # Вариант: скачиваем только аудио через yt-dlp
        audio_cmd = [
            'yt-dlp', video_url,
            '-o', audio_path,
            '-f', 'bestaudio',
            '-x',  # Только аудио
            '--audio-format', 'mp3',
            '--audio-quality', '5'  # Средняе качество (меньше размер)
        ] + base_params
        
        audio_result = subprocess.run(
            audio_cmd, 
            capture_output=True, 
            text=True, 
            timeout=300
        )
        
        audio_url_result = None
        if audio_result.returncode == 0 and os.path.exists(audio_path):
            audio_size = os.path.getsize(audio_path)
            
            if audio_size > 1024:
                audio_blob_name = f"{folder}/audio.mp3"
                audio_blob = bucket.blob(audio_blob_name)
                
                print("Uploading audio to Cloud Storage...")
                audio_blob.upload_from_filename(audio_path)
                audio_url_result = f"https://storage.googleapis.com/{BUCKET}/{audio_blob_name}"
                
                # СРАЗУ удаляем
                os.unlink(audio_path)
                print("Audio file deleted from memory")
        
        # Финальная очистка
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        
        return jsonify({
            'success': True,
            'video_url': video_url_result,
            'audio_url': audio_url_result,
            'video_filename': video_files[0],
            'audio_filename': 'audio.mp3' if audio_url_result else None,
            'video_size_mb': round(video_size / 1024 / 1024, 2)
        })
        
    except subprocess.TimeoutExpired:
        # Очистка при таймауте
        import shutil
        shutil.rmtree(f'/tmp/{folder}', ignore_errors=True)
        return jsonify({'error': 'Download timeout'}), 500
        
    except Exception as e:
        # Очистка при ошибке
        import shutil
        shutil.rmtree(f'/tmp/{folder}', ignore_errors=True)
        
        import traceback
        print(f"Exception: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
