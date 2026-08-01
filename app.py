from flask import Flask, render_template, redirect, url_for, request, jsonify, send_file
from flask_socketio import SocketIO, join_room
import threading
import os
import asyncio
import hashlib
import io

from discussion_store import create_discussion, list_discussions, get_discussion, save_podcast, delete_discussion

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

AUDIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "audio")


# #region debug-point dbg:report-event
def _report_debug_event(hypothesis_id, location, msg, data=None, run_id='pre-fix'):
    try:
        import json
        import urllib.request

        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.dbg', 'tts-audio-416.env')
        server_url = 'http://127.0.0.1:7777/event'
        session_id = 'tts-audio-416'
        if os.path.exists(env_path):
            with open(env_path, 'r', encoding='utf-8') as env_file:
                for line in env_file:
                    if line.startswith('DEBUG_SERVER_URL='):
                        server_url = line.split('=', 1)[1].strip()
                    elif line.startswith('DEBUG_SESSION_ID='):
                        session_id = line.split('=', 1)[1].strip()
        payload = {
            'sessionId': session_id,
            'runId': run_id,
            'hypothesisId': hypothesis_id,
            'location': location,
            'msg': msg,
            'data': data or {},
        }
        req = urllib.request.Request(
            server_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
        )
        urllib.request.urlopen(req, timeout=0.5).read()
    except Exception:
        pass
# #endregion


def is_valid_audio_cache(audio_path):
    return os.path.exists(audio_path) and os.path.getsize(audio_path) > 0


def remove_audio_cache(audio_path):
    if os.path.exists(audio_path):
        try:
            os.remove(audio_path)
        except OSError:
            pass


# 播客嘉宾 -> TTS 声色映射（与前端 MODEL_VOICES 保持一致）
PODCAST_VOICES = {
    'DeepSeek': 'zh-CN-YunxiNeural',
    'GLM': 'zh-CN-XiaoyiNeural',
    'GPT-OSS': 'zh-CN-YunjianNeural',
    'HOST': 'zh-CN-XiaoxiaoNeural',
}
PODCAST_DEFAULT_VOICE = 'zh-CN-XiaoxiaoNeural'


def synthesize_audio(text, voice):
    """合成 TTS 音频并缓存，返回 mp3 文件路径。失败抛异常。

    与 /api/tts 共用同一套缓存（按 文本+声色 哈希），已缓存则直接返回。
    """
    text = text.strip()
    if not text:
        raise ValueError('文本为空')
    if len(text) > 2000:
        text = text[:2000]
    cache_key = hashlib.md5(f"{text}_{voice}".encode('utf-8')).hexdigest()
    audio_path = os.path.join(AUDIO_DIR, f'{cache_key}.mp3')
    if is_valid_audio_cache(audio_path):
        return audio_path
    os.makedirs(AUDIO_DIR, exist_ok=True)

    import edge_tts
    err = {'msg': None}

    def _gen():
        async def _run():
            comm = edge_tts.Communicate(text, voice)
            await comm.save(audio_path)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run())
        except Exception as e:
            err['msg'] = str(e)
        finally:
            loop.close()

    t = threading.Thread(target=_gen)
    t.start()
    t.join(timeout=30)

    if is_valid_audio_cache(audio_path):
        return audio_path
    raise RuntimeError(f"语音生成失败: {err['msg'] or '超时或未知错误'}")


@app.route('/')
def index():
    """首页：讨论列表 + 新建讨论入口"""
    discussions = list_discussions()
    return render_template('index.html', discussions=discussions)


@app.route('/discussion/<discussion_id>')
def discussion_page(discussion_id):
    """讨论详情页"""
    record = get_discussion(discussion_id)
    if not record:
        return render_template('not_found.html'), 404
    return render_template('discussion.html', discussion=record)


@app.route('/api/discussion/<discussion_id>', methods=['DELETE'])
def api_delete_discussion(discussion_id):
    """删除讨论"""
    if delete_discussion(discussion_id):
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': '讨论不存在'}), 404


@socketio.on('join_discussion')
def handle_join_discussion(data):
    """客户端加入对应讨论的 room"""
    discussion_id = data.get('discussion_id')
    if discussion_id:
        join_room(discussion_id)


@socketio.on('start_discussion')
def handle_start_discussion(data):
    """创建新讨论并启动后台辩论"""
    question = data['question']
    discussion_id = create_discussion(question)
    print(f"[服务器] 创建讨论 {discussion_id}: {question}")

    # 立即加入 room，确保创建者能收到事件
    join_room(discussion_id)

    # 通知客户端跳转到讨论页
    socketio.emit('discussion_created', {'discussion_id': discussion_id})

    # 在后台线程中运行辩论
    def run_discussion():
        try:
            print(f"[服务器] 开始讨论 {discussion_id}...")
            from deep_discussion import deep_discussion_realtime
            deep_discussion_realtime(question, socketio, discussion_id)
            print(f"[服务器] 讨论 {discussion_id} 完成")
        except Exception as e:
            print(f"[服务器] 讨论 {discussion_id} 异常: {e}")
            import traceback
            traceback.print_exc()

    thread = threading.Thread(target=run_discussion, daemon=True)
    thread.start()
    print(f"[服务器] 后台线程已启动 (讨论 {discussion_id})")


@socketio.on('generate_podcast')
def handle_generate_podcast(data):
    """生成播客脚本"""
    discussion_id = data.get('discussion_id')
    if not discussion_id:
        return

    record = get_discussion(discussion_id)
    if not record:
        socketio.emit('podcast_error', {'message': '讨论不存在'}, room=discussion_id)
        return

    join_room(discussion_id)
    socketio.emit('podcast_status', {'message': '正在分析讨论内容...'}, room=discussion_id)

    def run_podcast():
        try:
            from deep_discussion import generate_podcast_script
            question = record['question']
            events = record.get('events', [])
            socketio.emit('podcast_status', {'message': '正在撰写播客脚本...'}, room=discussion_id)
            script = generate_podcast_script(question, events)
            if script:
                # 保存到数据库
                save_podcast(discussion_id, script)
                socketio.emit('podcast_ready', {'script': script}, room=discussion_id)
                print(f"[服务器] 播客生成完成 (讨论 {discussion_id}, {len(script)} 段)")
            else:
                socketio.emit('podcast_error', {'message': '脚本解析失败'}, room=discussion_id)
        except Exception as e:
            print(f"[服务器] 播客生成异常: {e}")
            import traceback
            traceback.print_exc()
            socketio.emit('podcast_error', {'message': str(e)}, room=discussion_id)

    thread = threading.Thread(target=run_podcast, daemon=True)
    thread.start()


# ================ TTS 语音合成 (Edge TTS) ================
@app.route('/api/tts', methods=['POST'])
def tts():
    """使用 Edge TTS 生成高质量神经网络语音"""
    data = request.get_json()
    if not data:
        return jsonify({'error': '无效请求'}), 400

    text = data.get('text', '').strip()
    voice = data.get('voice', 'zh-CN-XiaoxiaoNeural')

    if not text:
        return jsonify({'error': '文本为空'}), 400

    # 限制文本长度，避免过长等待
    if len(text) > 2000:
        text = text[:2000]

    # 缓存：以文本+语音的 hash 为文件名
    cache_key = hashlib.md5(f"{text}_{voice}".encode('utf-8')).hexdigest()
    audio_path = os.path.join(AUDIO_DIR, f'{cache_key}.mp3')
    audio_url = f'/static/audio/{cache_key}.mp3'
    existing_size = os.path.getsize(audio_path) if os.path.exists(audio_path) else None

    # #region debug-point A:tts-request-received
    _report_debug_event(
        'A',
        'app.py:tts',
        '[DEBUG] Received TTS request',
        {
            'cache_key': cache_key,
            'voice': voice,
            'text_length': len(text),
            'audio_path': audio_path,
            'file_exists': os.path.exists(audio_path),
            'file_size': existing_size,
        },
    )
    # #endregion

    if os.path.exists(audio_path) and not is_valid_audio_cache(audio_path):
        # #region debug-point A:tts-invalid-cache
        _report_debug_event(
            'A',
            'app.py:tts-invalid-cache',
            '[DEBUG] Removing invalid cached audio before regeneration',
            {'cache_key': cache_key, 'audio_path': audio_path, 'file_size': existing_size},
        )
        # #endregion
        remove_audio_cache(audio_path)
        existing_size = None

    # 已缓存且有效则直接返回
    if is_valid_audio_cache(audio_path):
        # #region debug-point A:tts-cache-hit
        _report_debug_event(
            'A',
            'app.py:tts-cache-hit',
            '[DEBUG] Returning cached audio path',
            {
                'cache_key': cache_key,
                'audio_path': audio_path,
                'audio_url': audio_url,
                'file_size': existing_size,
            },
        )
        # #endregion
        return jsonify({'url': audio_url, 'cached': True})

    # 生成音频（在新线程中运行 asyncio，避免与 Flask 事件循环冲突）
    try:
        import edge_tts
        generation_error = {'message': None}

        def generate_sync():
            async def _gen():
                comm = edge_tts.Communicate(text, voice)
                await comm.save(audio_path)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_gen())
            except Exception as e:
                generation_error['message'] = str(e)
                # #region debug-point D:tts-generate-thread-exception
                _report_debug_event(
                    'D',
                    'app.py:tts-generate-thread',
                    '[DEBUG] TTS generation thread raised exception',
                    {'cache_key': cache_key, 'error': str(e)},
                )
                # #endregion
            finally:
                loop.close()

        # #region debug-point C:tts-generate-start
        _report_debug_event(
            'C',
            'app.py:tts-generate-start',
            '[DEBUG] Starting TTS generation thread',
            {'cache_key': cache_key, 'audio_path': audio_path},
        )
        # #endregion
        t = threading.Thread(target=generate_sync)
        t.start()
        t.join(timeout=30)

        generated_exists = os.path.exists(audio_path)
        generated_size = os.path.getsize(audio_path) if generated_exists else None
        # #region debug-point A:tts-generate-finish
        _report_debug_event(
            'A',
            'app.py:tts-generate-finish',
            '[DEBUG] TTS generation finished or timed out',
            {
                'cache_key': cache_key,
                'thread_alive': t.is_alive(),
                'file_exists': generated_exists,
                'file_size': generated_size,
            },
        )
        # #endregion

        if is_valid_audio_cache(audio_path):
            return jsonify({'url': audio_url, 'cached': False})

        if generated_exists and not t.is_alive():
            remove_audio_cache(audio_path)

        if generation_error['message']:
            return jsonify({'error': f"语音生成失败: {generation_error['message']}"}), 500
        if t.is_alive():
            return jsonify({'error': '语音生成超时'}), 500
        return jsonify({'error': '语音生成失败'}), 500
    except Exception as e:
        # #region debug-point D:tts-exception
        _report_debug_event(
            'D',
            'app.py:tts-exception',
            '[DEBUG] TTS generation raised exception',
            {'cache_key': cache_key, 'error': str(e)},
        )
        # #endregion
        print(f"[TTS] 生成失败: {e}")
        return jsonify({'error': f'语音生成失败: {str(e)}'}), 500


@app.route('/api/podcast/<discussion_id>/download')
def download_podcast(discussion_id):
    """下载整段播客 MP3（按各段声色合成后拼接）"""
    record = get_discussion(discussion_id)
    if not record:
        return jsonify({'error': '讨论不存在'}), 404
    script = record.get('podcast')
    if not script:
        return jsonify({'error': '播客尚未生成'}), 404

    try:
        chunks = []
        for seg in script:
            text = (seg.get('text') or '').strip()
            if not text:
                continue
            speaker = seg.get('speaker', '')
            voice = PODCAST_VOICES.get(speaker, PODCAST_DEFAULT_VOICE)
            path = synthesize_audio(text, voice)
            with open(path, 'rb') as f:
                chunks.append(f.read())
        if not chunks:
            return jsonify({'error': '播客内容为空'}), 400
        merged = b''.join(chunks)
        return send_file(
            io.BytesIO(merged),
            mimetype='audio/mpeg',
            as_attachment=True,
            download_name=f'podcast_{discussion_id}.mp3',
        )
    except Exception as e:
        print(f"[播客下载] 失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'下载失败: {str(e)}'}), 500


if __name__ == '__main__':
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs('discussions', exist_ok=True)
    socketio.run(app, debug=True, port=5000)
