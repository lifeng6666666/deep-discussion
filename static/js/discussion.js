let audioQueue = [];
let isPlaying = false;
let playbackSpeed = 1.0;
let socket = null;
let currentDiscussionId = null;
let currentAudio = null;  // 当前播放的 Audio 对象

// 模型到 Edge TTS 语音的映射（每个模型用不同声色）
const MODEL_VOICES = {
    // 讨论中的模型名（取 split('/').pop() 后的简称）
    'deepseek-v4-pro': 'zh-CN-YunxiNeural',            // 男声，沉稳
    'glm-5.2': 'zh-CN-XiaoyiNeural',                    // 女声，活泼
    'gpt-oss-120b': 'zh-CN-YunjianNeural',              // 男声，有力
    // 播客中的嘉宾简称
    'DeepSeek': 'zh-CN-YunxiNeural',
    'GLM': 'zh-CN-XiaoyiNeural',
    'GPT-OSS': 'zh-CN-YunjianNeural',
    'HOST': 'zh-CN-XiaoxiaoNeural',                     // 女声，温暖，主持人
};
const DEFAULT_VOICE = 'zh-CN-XiaoxiaoNeural';

// 角色图标与样式映射
const ROLE_META = {
    proposer:    { icon: '💡', label: '提案', cls: 'proposer-speech' },
    voter:       { icon: '🗳️', label: '投票', cls: 'voter-speech' },
    refiner:     { icon: '🔧', label: '完善', cls: 'refiner-speech' },
    synthesizer: { icon: '🎙️', label: '综合', cls: 'host-speech' },
    host:        { icon: '🎙️', label: '主持', cls: 'host-speech' },
    challenger:  { icon: '⚔️', label: '挑战', cls: 'challenger-speech' }
};

let podcastScript = [];
let podcastIndex = 0;
let isPodcastPlaying = false;
let podcastAudioCache = new Map();  // index -> Promise<HTMLAudioElement>，预加载下一段消除切换停顿

// 更新流程进度条
function updateWorkflowProgress(phase) {
    const steps = {
        'proposal': document.getElementById('step-proposal'),
        'voting': document.getElementById('step-voting'),
        'refinement': document.getElementById('step-refinement'),
        'complete': document.getElementById('step-complete')
    };

    // 清除所有状态
    Object.values(steps).forEach(step => {
        if (step) {
            step.classList.remove('active', 'completed');
        }
    });

    // 根据阶段更新状态
    const phaseOrder = ['proposal', 'voting', 'refinement', 'complete'];
    const currentIndex = phaseOrder.indexOf(phase);

    phaseOrder.forEach((p, i) => {
        const step = steps[p];
        if (step) {
            if (i < currentIndex) {
                step.classList.add('completed');
            } else if (i === currentIndex) {
                step.classList.add('active');
            }
        }
    });
}

// #region debug-point dbg:browser-report
function reportTtsDebug(hypothesisId, location, msg, data = {}, runId = 'pre-fix') {
    fetch('http://127.0.0.1:7777/event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            sessionId: 'tts-audio-416',
            runId,
            hypothesisId,
            location,
            msg,
            data,
            ts: Date.now()
        })
    }).catch(() => {});
}
// #endregion

function escapeForAttr(text) {
    return String(text).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n');
}

function escapeHtml(text) {
    return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// 统一的事件渲染器，历史回放和实时事件共用
function renderEvent(type, data) {
    const area = document.getElementById('discussion-area');
    switch (type) {
        case 'question':
            area.innerHTML += `<div class="question">📌 问题: ${escapeHtml(data.text)}</div>`;
            break;
        case 'phase_start': {
            const desc = data.description ? `<div class="phase-description">${escapeHtml(data.description)}</div>` : '';
            area.innerHTML += `<div class="phase-header">${escapeHtml(data.text)}${desc}</div>`;
            // 更新流程进度条
            updateWorkflowProgress(data.phase);
            break;
        }
        case 'round_start':
            area.innerHTML += `<div class="round-header">--- 第 ${data.round} 轮完善 ---</div>`;
            break;
        case 'speech': {
            const meta = ROLE_META[data.role] || ROLE_META.challenger;
            const modelShort = data.model.split('/').pop();
            const voice = getVoiceForModel(data.model, data.role);
            const voteTag = data.vote_num ? `<span class="vote-tag">→ 方案${data.vote_num}</span>` : '';
            let questionTag = '';
            if (data.role === 'refiner' && data.has_improvement !== undefined && data.has_improvement !== null) {
                questionTag = data.has_improvement
                    ? `<span class="improve-tag has-improvement">有改进</span>`
                    : `<span class="improve-tag no-improvement">无改进</span>`;
            }
            const block = document.createElement('div');
            block.className = `speech-block ${meta.cls}`;
            block.innerHTML = `
                <div class="speech-header">
                    ${meta.icon} <strong>${escapeHtml(modelShort)}</strong>
                    <span class="role-label">${meta.label}</span>
                    ${voteTag}
                    ${questionTag}
                    <button class="play-btn" onclick="playSpeech('${escapeForAttr(data.speech_text)}', '${voice}')">▶️ 播放</button>
                </div>
                <div class="speech-content">${escapeHtml(data.display_text).replace(/\n/g, '<br>')}</div>
            `;
            area.appendChild(block);
            audioQueue.push({ text: data.speech_text, model: data.model, role: data.role, voice });
            break;
        }
        case 'vote_result': {
            const winnerShort = data.winner_model.split('/').pop();
            area.innerHTML += `
                <div class="vote-result">
                    📊 投票结果: ${escapeHtml(data.vote_result_text)}<br>
                    🏆 获胜方案: 方案${data.winner_idx}（由 <strong>${escapeHtml(winnerShort)}</strong> 提出）
                </div>
            `;
            break;
        }
        case 'convergence': {
            const bar = document.getElementById('convergence-bar');
            bar.style.display = 'block';
            const progress = bar.querySelector('.convergence-progress');
            const text = bar.querySelector('.convergence-text');
            progress.style.width = `${data.agree_ratio * 100}%`;
            text.textContent = `无改进: ${Math.round(data.agree_ratio * 100)}% (${data.agree_count}/${data.total})`;
            break;
        }
        case 'convergence_reached':
            area.innerHTML += `<div class="convergence-notice">✅ 所有模型均认为方案无需大的改进，方案已收敛</div>`;
            break;
        case 'final_solution':
            area.innerHTML += `<div class="final-solution">🎯 最终方案: ${escapeHtml(data.solution).replace(/\n/g, '<br>')}</div>`;
            showPodcastButton();
            // 更新流程进度条到完成状态
            updateWorkflowProgress('complete');
            break;
    }
    area.scrollTop = area.scrollHeight;
}

function initDiscussion(discussionId, isOngoing) {
    currentDiscussionId = discussionId;

    // 回放历史事件
    const historyEl = document.getElementById('history-data');
    if (historyEl) {
        try {
            const events = JSON.parse(historyEl.textContent);
            events.forEach(ev => renderEvent(ev.type, ev.data));
        } catch (e) {
            console.error('历史事件解析失败', e);
        }
    }

    // 检查是否有已保存的播客
    const podcastEl = document.getElementById('podcast-data');
    if (podcastEl) {
        try {
            podcastScript = JSON.parse(podcastEl.textContent);
            renderPodcastPlayer();
        } catch (e) {
            console.error('播客数据解析失败', e);
            // 播客数据损坏，显示制作按钮
            showPodcastButton();
        }
    } else {
        // 没有播客数据，检查讨论是否已完成
        const hasFinal = historyEl && (() => {
            try {
                const events = JSON.parse(historyEl.textContent);
                return events.some(ev => ev.type === 'final_solution');
            } catch (e) {
                return false;
            }
        })();
        if (hasFinal) {
            showPodcastButton();
        }
    }

    // 仅进行中的讨论需要 WebSocket 实时推送
    if (!isOngoing) {
        document.getElementById('discussion-status').textContent = '讨论已完成';
        return;
    }

    socket = io();
    socket.on('connect', () => {
        socket.emit('join_discussion', { discussion_id: discussionId });
    });

    // 注册所有事件处理器
    const eventTypes = [
        'question', 'phase_start', 'round_start', 'speech',
        'vote_result', 'convergence', 'convergence_reached', 'final_solution'
    ];
    eventTypes.forEach(type => {
        socket.on(type, (data) => {
            renderEvent(type, data);
            if (type === 'final_solution') {
                document.getElementById('discussion-status').textContent = '讨论已完成';
            }
        });
    });

    // 播客生成事件
    socket.on('podcast_status', (data) => {
        const statusEl = document.getElementById('podcast-status');
        if (statusEl) {
            statusEl.textContent = data.message;
            statusEl.style.display = 'block';
        }
    });

    socket.on('podcast_ready', (data) => {
        podcastScript = data.script;
        renderPodcastPlayer();
        const statusEl = document.getElementById('podcast-status');
        if (statusEl) statusEl.style.display = 'none';
    });

    socket.on('podcast_error', (data) => {
        const statusEl = document.getElementById('podcast-status');
        if (statusEl) {
            statusEl.textContent = '播客生成失败: ' + data.message;
            statusEl.style.color = '#d32f2f';
        }
    });
}

// ================ 语音引擎 (Edge TTS) ================
function getVoiceForModel(model, role) {
    if (role === 'synthesizer' || role === 'host') {
        return MODEL_VOICES['HOST'];
    }
    const modelShort = model ? model.split('/').pop() : '';
    return MODEL_VOICES[modelShort] || DEFAULT_VOICE;
}

function getVoiceForSpeaker(speaker) {
    return MODEL_VOICES[speaker] || DEFAULT_VOICE;
}

async function fetchTTS(text, voice) {
    const resp = await fetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice })
    });
    const data = await resp.json();
    // #region debug-point B:fetch-tts-response
    reportTtsDebug('B', 'discussion.js:fetchTTS', '[DEBUG] Received TTS API response', {
        ok: resp.ok,
        status: resp.status,
        voice,
        textLength: text.length,
        url: data.url || null,
        cached: data.cached ?? null,
        error: data.error || null
    });
    // #endregion
    if (data.error) throw new Error(data.error);
    return data.url;
}

// ================ 普通播放控制 ================
async function playSpeech(text, voice) {
    const v = voice || DEFAULT_VOICE;
    // 停止当前播放
    if (currentAudio) {
        currentAudio.pause();
        currentAudio = null;
    }
    try {
        const url = await fetchTTS(text, v);
        currentAudio = new Audio(url);
        currentAudio.playbackRate = playbackSpeed;
        // #region debug-point E:play-speech-audio-error
        currentAudio.onerror = () => {
            reportTtsDebug('E', 'discussion.js:playSpeech', '[DEBUG] Audio element reported error during speech playback', {
                url: currentAudio.currentSrc || url,
                networkState: currentAudio.networkState,
                readyState: currentAudio.readyState,
                errorCode: currentAudio.error ? currentAudio.error.code : null
            });
        };
        // #endregion
        currentAudio.play();
    } catch (e) {
        // #region debug-point E:play-speech-catch
        reportTtsDebug('E', 'discussion.js:playSpeech-catch', '[DEBUG] playSpeech failed before browser fallback', {
            voice: v,
            textLength: text.length,
            error: e && e.message ? e.message : String(e)
        });
        // #endregion
        console.error('Edge TTS 失败，回退到浏览器语音:', e);
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = 'zh-CN';
        utterance.rate = playbackSpeed;
        speechSynthesis.speak(utterance);
    }
}

function playAll() {
    if (audioQueue.length === 0) {
        alert('暂无可播放内容');
        return;
    }
    isPlaying = true;
    playNext();
}

async function playNext() {
    if (!isPlaying || audioQueue.length === 0) {
        isPlaying = false;
        return;
    }
    const item = audioQueue.shift();
    const voice = getVoiceForModel(item.model, item.role);
    try {
        const url = await fetchTTS(item.text, voice);
        if (!isPlaying) return;  // 可能在等待期间被暂停
        currentAudio = new Audio(url);
        currentAudio.playbackRate = playbackSpeed;
        // #region debug-point E:play-next-audio-error
        currentAudio.onerror = () => {
            reportTtsDebug('E', 'discussion.js:playNext', '[DEBUG] Audio element reported error during queue playback', {
                url: currentAudio.currentSrc || url,
                networkState: currentAudio.networkState,
                readyState: currentAudio.readyState,
                errorCode: currentAudio.error ? currentAudio.error.code : null
            });
        };
        // #endregion
        currentAudio.onended = () => {
            if (isPlaying) playNext();
        };
        currentAudio.play();
    } catch (e) {
        // #region debug-point E:play-next-catch
        reportTtsDebug('E', 'discussion.js:playNext-catch', '[DEBUG] Queue playback failed', {
            voice,
            textLength: item.text.length,
            error: e && e.message ? e.message : String(e)
        });
        // #endregion
        console.error('TTS 失败，跳过:', e);
        if (isPlaying) playNext();
    }
}

function pauseAll() {
    isPlaying = false;
    if (currentAudio) {
        currentAudio.pause();
        currentAudio = null;
    }
    speechSynthesis.cancel();
}

function changeSpeed(speed) {
    playbackSpeed = parseFloat(speed);
}

// ================ 播客功能 ================
function showPodcastButton() {
    const btn = document.getElementById('make-podcast-btn');
    if (btn) btn.style.display = 'inline-block';
}

function generatePodcast() {
    if (!currentDiscussionId) return;
    const btn = document.getElementById('make-podcast-btn');
    btn.disabled = true;
    btn.textContent = '制作中...';
    const statusEl = document.getElementById('podcast-status');
    if (statusEl) {
        statusEl.textContent = '正在生成播客脚本...';
        statusEl.style.display = 'block';
        statusEl.style.color = '#666';
    }

    if (socket) {
        socket.emit('generate_podcast', { discussion_id: currentDiscussionId });
    } else {
        // 已完成的讨论也需要连接 WebSocket
        socket = io();
        socket.on('connect', () => {
            socket.emit('generate_podcast', { discussion_id: currentDiscussionId });
        });
        socket.on('podcast_status', (data) => {
            const el = document.getElementById('podcast-status');
            if (el) { el.textContent = data.message; el.style.display = 'block'; }
        });
        socket.on('podcast_ready', (data) => {
            podcastScript = data.script;
            renderPodcastPlayer();
            const el = document.getElementById('podcast-status');
            if (el) el.style.display = 'none';
        });
        socket.on('podcast_error', (data) => {
            const el = document.getElementById('podcast-status');
            if (el) { el.textContent = '播客生成失败: ' + data.message; el.style.color = '#d32f2f'; }
        });
    }
}

function renderPodcastPlayer() {
    // 新脚本到达，清掉旧的预加载缓存，避免用到上一版播客的音频
    podcastAudioCache = new Map();
    const btn = document.getElementById('make-podcast-btn');
    // 改为「重新生成」按钮，而不是隐藏
    if (btn) {
        btn.style.display = 'inline-block';
        btn.textContent = '🔄 重新生成播客';
        btn.disabled = false;
    }

    const player = document.getElementById('podcast-player');
    player.style.display = 'block';
    player.innerHTML = '<div class="podcast-title"> 播客节目</div>';

    const list = document.createElement('div');
    list.className = 'podcast-script-list';
    list.id = 'podcast-script-list';

    podcastScript.forEach((seg, i) => {
        const item = document.createElement('div');
        item.className = 'podcast-segment';
        item.id = `podcast-seg-${i}`;
        const speakerLabel = seg.speaker === 'HOST' ? '🎵 主持人' : `👤 ${escapeHtml(seg.speaker)}`;
        item.innerHTML = `
            <div class="podcast-speaker">${speakerLabel}</div>
            <div class="podcast-text">${escapeHtml(seg.text)}</div>
        `;
        list.appendChild(item);
    });

    player.appendChild(list);

    // 播放控制按钮
    const controls = document.createElement('div');
    controls.className = 'podcast-controls';
    controls.innerHTML = `
        <button class="podcast-play-btn" onclick="playPodcast()">▶️ 播放播客</button>
        <button class="podcast-pause-btn" onclick="pausePodcast()">⏸️ 暂停</button>
        <button class="podcast-download-btn" onclick="downloadPodcast()">⬇️ 下载 MP3</button>
        <span class="podcast-progress-text" id="podcast-progress-text">0 / ${podcastScript.length}</span>
    `;
    player.appendChild(controls);
}

async function downloadPodcast() {
    if (!currentDiscussionId || podcastScript.length === 0) return;
    const btn = document.querySelector('.podcast-download-btn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ 合成中...'; }
    try {
        const resp = await fetch(`/api/podcast/${currentDiscussionId}/download`);
        if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.error || `HTTP ${resp.status}`);
        }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `podcast_${currentDiscussionId}.mp3`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    } catch (e) {
        alert('下载失败: ' + e.message);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '⬇️ 下载 MP3'; }
    }
}

function playPodcast() {
    if (podcastScript.length === 0) return;
    isPodcastPlaying = true;
    podcastIndex = 0;
    playPodcastSegment();
}

// 预加载某段的音频（带缓存）：先合成 TTS url，再创建预加载的 Audio 元素
// 返回 Promise<HTMLAudioElement>；合成失败时移除缓存项，下次重试
function prepareSegment(index) {
    if (index < 0 || index >= podcastScript.length) return Promise.resolve(null);
    if (podcastAudioCache.has(index)) return podcastAudioCache.get(index);
    const seg = podcastScript[index];
    const voice = getVoiceForSpeaker(seg.speaker);
    const p = (async () => {
        const url = await fetchTTS(seg.text, voice);
        const audio = new Audio(url);
        audio.preload = 'auto';   // 提前下载 mp3，轮到它时无需再等
        audio.playbackRate = playbackSpeed;
        return audio;
    })();
    podcastAudioCache.set(index, p);
    p.catch(() => podcastAudioCache.delete(index));
    return p;
}

async function playPodcastSegment() {
    if (!isPodcastPlaying || podcastIndex >= podcastScript.length) {
        isPodcastPlaying = false;
        updatePodcastHighlight(-1);
        return;
    }

    // 停止当前播放
    if (currentAudio) {
        currentAudio.pause();
    }

    const idx = podcastIndex;
    const seg = podcastScript[idx];

    // 趁当前段播放，提前合成并预加载下一段，消除切换时的几秒停顿
    prepareSegment(idx + 1);

    try {
        const audio = await prepareSegment(idx);
        if (!isPodcastPlaying || !audio) return;

        updatePodcastHighlight(idx);
        const progressText = document.getElementById('podcast-progress-text');
        if (progressText) {
            progressText.textContent = `${idx + 1} / ${podcastScript.length}`;
        }

        currentAudio = audio;
        currentAudio.playbackRate = playbackSpeed;  // 速度可能中途改变过
        // #region debug-point E:podcast-audio-error
        currentAudio.onerror = () => {
            reportTtsDebug('E', 'discussion.js:playPodcastSegment', '[DEBUG] Audio element reported error during podcast playback', {
                speaker: seg.speaker,
                url: currentAudio.currentSrc || '',
                networkState: currentAudio.networkState,
                readyState: currentAudio.readyState,
                errorCode: currentAudio.error ? currentAudio.error.code : null
            });
        };
        // #endregion
        currentAudio.onended = () => {
            if (isPodcastPlaying) {
                podcastIndex++;
                playPodcastSegment();
            }
        };
        currentAudio.currentTime = 0;
        const playPromise = currentAudio.play();
        if (playPromise && typeof playPromise.catch === 'function') {
            playPromise.catch(() => {});  // 防止 play() 被中断时抛未处理拒绝
        }
    } catch (e) {
        // #region debug-point E:podcast-catch
        reportTtsDebug('E', 'discussion.js:playPodcastSegment-catch', '[DEBUG] Podcast playback failed', {
            speaker: seg.speaker,
            textLength: seg.text.length,
            error: e && e.message ? e.message : String(e)
        });
        // #endregion
        console.error('播客 TTS 失败:', e);
        if (isPodcastPlaying) {
            podcastIndex++;
            playPodcastSegment();
        }
    }
}

function updatePodcastHighlight(index) {
    document.querySelectorAll('.podcast-segment').forEach((el, i) => {
        el.classList.toggle('active', i === index);
    });
    if (index >= 0) {
        const active = document.getElementById(`podcast-seg-${index}`);
        if (active) active.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

function pausePodcast() {
    isPodcastPlaying = false;
    if (currentAudio) {
        currentAudio.pause();
        currentAudio = null;
    }
}
