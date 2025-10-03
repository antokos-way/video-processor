@app.route('/screenshots', methods=['POST'])
def make_screenshots():
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
        
    try:
        data = request.json
        video_url = data['video_url']
        count = data.get('count', 5)
        folder_from_url = data.get('folder')  # Получаем folder из предыдущего запроса
        
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
