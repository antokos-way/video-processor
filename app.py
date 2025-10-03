@app.route('/download', methods=['POST'])
def download_video():
    if not BUCKET:
        return jsonify({'error': 'BUCKET not configured'}), 500
    
    # Детальная проверка cookies
    cookies_path = '/app/cookies.txt'
    if not os.path.exists(cookies_path):
        return jsonify({
            'error': 'cookies.txt not found',
            'app_files': os.listdir('/app') if os.path.exists('/app') else [],
            'working_dir': os.getcwd(),
            'current_files': os.listdir('.')
        }), 500
    
    # Проверяем формат cookies
    with open(cookies_path, 'r') as f:
        cookies_content = f.read()
        if not (cookies_content.startswith('# Netscape') or cookies_content.startswith('# HTTP Cookie')):
            return jsonify({
                'error': 'Invalid cookies format',
                'cookies_first_line': cookies_content.split('\n')[0] if cookies_content else 'Empty file'
            }), 500
    
    try:
        data = request.json
        video_url = data['url']
        folder = str(uuid.uuid4())
        os.makedirs(folder)
        
        # Попробуем разные подходы
        base_params_variants = [
            # Вариант 1: без extractor-args
            [
                '--cookies', cookies_path,
                '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                '--referer', 'https://www.youtube.com/',
                '--no-check-certificate',
                '--sleep-requests', '3',
                '-R', '5',
                '-w'
            ],
            # Вариант 2: с tv_embedded
            [
                '--cookies', cookies_path,
                '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                '--referer', 'https://www.youtube.com/',
                '--no-check-certificate',
                '--extractor-args', 'youtube:player_client=tv_embedded',
                '--sleep-requests', '3',
                '-R', '5',
                '-w'
            ]
        ]
        
        video_result = None
        for i, base_params in enumerate(base_params_variants):
            print(f"Trying approach {i+1}...")
            
            video_filename = f"{folder}/video.%(ext)s"
            video_cmd = [
                'yt-dlp', video_url, 
                '-o', video_filename,
                '-f', 'best[height<=720]',  # Упростим для теста
            ] + base_params
            
            video_result = subprocess.run(video_cmd, capture_output=True, text=True, timeout=300)
            
            if video_result.returncode == 0:
                print(f"Success with approach {i+1}")
                break
            else:
                print(f"Approach {i+1} failed: {video_result.stderr[:200]}")
        
        if video_result.returncode != 0:
            return jsonify({
                'error': 'All download approaches failed',
                'last_stderr': video_result.stderr[:500],
                'suggestion': 'Update cookies or use external API'
            }), 500
        
        # Продолжаем с обычной логикой...
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
