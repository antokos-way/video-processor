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
        
        # МИНИМАЛЬНЫЕ параметры с cookies (без extractor-args)
        base_params = [
            '--cookies', '/app/cookies.txt',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
            '--referer', 'https://www.youtube.com/',
            '--no-check-certificate',
            '--sleep-requests', '3',  # Увеличена пауза
            '-R', '5',  # Больше попыток
            '-w',
            '--ignore-errors'  # Игнорировать некоторые ошибки
        ]
        
        # Скачиваем ВИДЕО с более простым форматом
        video_filename = f"{folder}/video.%(ext)s"
        video_cmd = [
            'yt-dlp', video_url, 
            '-o', video_filename,
            '-f', 'worst[height>=720]/best',  # Попробуй худшее качество для обхода
            '--no-audio'
        ] + base_params
        
        print(f"Running video command: {' '.join(video_cmd)}")
        video_result = subprocess.run(video_cmd, capture_output=True, text=True, timeout=600)
        
        # Если видео не скачалось, попробуй без ограничений формата
        if video_result.returncode != 0:
            print("Trying fallback video format...")
            video_cmd = [
                'yt-dlp', video_url, 
                '-o', video_filename,
                '-f', 'mp4',  # Любое mp4
                '--no-audio'
            ] + base_params
            
            video_result = subprocess.run(video_cmd, capture_output=True, text=True, timeout=600)
        
        # Скачиваем АУДИО
        audio_filename = f"{folder}/audio.%(ext)s"
        audio_cmd = [
            'yt-dlp', video_url,
            '-o', audio_filename,
            '-f', 'bestaudio',  # Упростил формат
            '--extract-audio',
            '--audio-format', 'mp3',
            '--audio-quality', '5'  # Среднее качество для быстроты
        ] + base_params
        
        print(f"Running audio command: {' '.join(audio_cmd)}")
        audio_result = subprocess.run(audio_cmd, capture_output=True, text=True, timeout=600)
        
        # Проверяем результаты
        print(f"Video result: {video_result.returncode}")
        print(f"Audio result: {audio_result.returncode}")
        
        if video_result.returncode != 0:
            print(f"Video stderr: {video_result.stderr}")
            return jsonify({
                'error': 'Video download failed',
                'video_stderr': video_result.stderr[:500],
                'video_stdout': video_result.stdout[:500],
                'suggestion': 'YouTube blocking detected. Consider using external API service.'
            }), 500
        
        # Остальной код тот же...
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ... остальные функции
