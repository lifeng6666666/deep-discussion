const socket = io();
let audioQueue = [];
let currentAudio = null;
let isPlaying = false;
let playbackSpeed = 1.0;

// 角色图标与样式映射
const ROLE_META = {
    proposer:    { icon: '💡', label: '提案', cls: 'proposer-speech' },
    voter:       { icon: '🗳️', label: '投票', cls: 'voter-speech' },
    refiner:     { icon: '🔧', label: '优化', cls: 'refiner-speech' },
    synthesizer: { icon: '🎙️', label: '综合', cls: 'host-speech' },
    host:        { icon: '🎙️', label: '主持', cls: 'host-speech' },
    challenger:  { icon: '⚔️', label: '挑战', cls: 'challenger-speech' }
};

// 转义文本，避免破坏 HTML/JS
function escapeForAttr(text) {
    return text.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n');
}

function escapeHtml(text) {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// WebSocket 事件处理
socket.on('question', (data) => {
    const area = document.getElementById('discussion-area');
    area.innerHTML += `<div class="question">📌 问题: ${escapeHtml(data.text)}</div>`;
});

socket.on('phase_start', (data) => {
    const area = document.getElementById('discussion-area');
    area.innerHTML += `<div class="phase-header">${escapeHtml(data.text)}</div>`;
});

socket.on('round_start', (data) => {
    const area = document.getElementById('discussion-area');
    area.innerHTML += `<div class="round-header">--- 第 ${data.round} 轮优化 ---</div>`;
});

socket.on('speech', (data) => {
    const area = document.getElementById('discussion-area');
    const meta = ROLE_META[data.role] || ROLE_META.challenger;
    const modelShort = data.model.split('/').pop();
    const voteTag = data.vote_num ? `<span class="vote-tag">→ 方案${data.vote_num}</span>` : '';
    let improveTag = '';
    if (data.role === 'refiner' && data.has_improvement !== undefined && data.has_improvement !== null) {
        improveTag = data.has_improvement
            ? `<span class="improve-tag has-improvement">有改进</span>`
            : `<span class="improve-tag no-improvement">无改进</span>`;
    }

    const speechBlock = document.createElement('div');
    speechBlock.className = `speech-block ${meta.cls}`;
    speechBlock.innerHTML = `
        <div class="speech-header">
            ${meta.icon} <strong>${escapeHtml(modelShort)}</strong>
            <span class="role-label">${meta.label}</span>
            ${voteTag}
            ${improveTag}
            <button class="play-btn" onclick="playSpeech('${escapeForAttr(data.speech_text)}')">▶️ 播放</button>
        </div>
        <div class="speech-content">${escapeHtml(data.display_text).replace(/\n/g, '<br>')}</div>
    `;
    area.appendChild(speechBlock);
    area.scrollTop = area.scrollHeight;

    // 添加到音频队列
    audioQueue.push({
        text: data.speech_text,
        model: data.model,
        role: data.role,
        element: speechBlock
    });
});

socket.on('vote_result', (data) => {
    const area = document.getElementById('discussion-area');
    const winnerShort = data.winner_model.split('/').pop();
    area.innerHTML += `
        <div class="vote-result">
            📊 投票结果: ${escapeHtml(data.vote_result_text)}<br>
            🏆 获胜方案: 方案${data.winner_idx}（由 <strong>${escapeHtml(winnerShort)}</strong> 提出）
        </div>
    `;
});

socket.on('audio_ready', (data) => {
    console.log('Audio ready:', data.url);
});

socket.on('convergence', (data) => {
    const bar = document.getElementById('convergence-bar');
    bar.style.display = 'block';
    const progress = bar.querySelector('.convergence-progress');
    const text = bar.querySelector('.convergence-text');
    progress.style.width = `${data.agree_ratio * 100}%`;
    text.textContent = `认为无改进: ${Math.round(data.agree_ratio * 100)}% (${data.agree_count}/${data.total})`;
});

socket.on('convergence_reached', (data) => {
    const area = document.getElementById('discussion-area');
    area.innerHTML += `<div class="convergence-notice">✅ 所有模型均认为方案无需大的改进，方案已收敛</div>`;
});

socket.on('final_solution', (data) => {
    const area = document.getElementById('discussion-area');
    area.innerHTML += `<div class="final-solution">🎯 最终方案: ${escapeHtml(data.solution).replace(/\n/g, '<br>')}</div>`;
});

// 控制函数
function startDiscussion() {
    const question = document.getElementById('question-input').value.trim();
    if (!question) {
        alert('请输入讨论问题');
        return;
    }
    
    document.getElementById('discussion-area').innerHTML = '';
    document.getElementById('convergence-bar').style.display = 'none';
    audioQueue = [];
    
    socket.emit('start_discussion', { question });
}

function playSpeech(text) {
    // 使用 Web Speech API 或请求后端生成的音频
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'zh-CN';
    utterance.rate = playbackSpeed;
    speechSynthesis.speak(utterance);
}

function playAll() {
    if (audioQueue.length === 0) {
        alert('暂无可播放内容');
        return;
    }
    
    isPlaying = true;
    playNext();
}

function playNext() {
    if (!isPlaying || audioQueue.length === 0) {
        return;
    }
    
    const item = audioQueue.shift();
    const utterance = new SpeechSynthesisUtterance(item.text);
    utterance.lang = 'zh-CN';
    utterance.rate = playbackSpeed;
    
    utterance.onend = () => {
        if (isPlaying) {
            playNext();
        }
    };
    
    speechSynthesis.speak(utterance);
}

function pauseAll() {
    isPlaying = false;
    speechSynthesis.cancel();
}

function changeSpeed(speed) {
    playbackSpeed = parseFloat(speed);
}
