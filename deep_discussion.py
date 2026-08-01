import requests
import json
import time
import threading
import queue
import os
import html
from dotenv import load_dotenv

load_dotenv()

# NVIDIA NIM API 配置（真实 key 存放在 .env，不入库）
API_KEY = os.getenv("NVIDIA_API_KEY", "")
API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json"
}

# 候选模型（NVIDIA NIM 免费模型，已验证可用）
MODELS = [
    "deepseek-ai/deepseek-v4-pro",
    "z-ai/glm-5.2",
    "openai/gpt-oss-120b"
]

# 模型友好名称（与前端 discussion.js 的 MODEL_VOICES 键保持一致）
MODEL_FRIENDLY_NAMES = {
    "deepseek-ai/deepseek-v4-pro": "DeepSeek",
    "z-ai/glm-5.2": "GLM",
    "openai/gpt-oss-120b": "GPT-OSS",
    "minimaxai/minimax-m3": "MiniMax",
}


def friendly_name(model_id):
    """模型 id 转友好名称，用作播客发言者标签"""
    return MODEL_FRIENDLY_NAMES.get(model_id, model_id.split("/")[-1])


# 挑战指数和讨论历史
challenge_counts = {model: 0 for model in MODELS}
discussion_history = []

MODEL_FAILURE_MARKERS = (
    "NVIDIA API key 未设置",
    "模型响应失败",
    "模型返回空内容",
)


def is_model_failure(response):
    return any(marker in str(response) for marker in MODEL_FAILURE_MARKERS)


def failed_challenge_response(reason):
    return f"同意: 是\n意见: 模型发言失败，默认不阻塞当前方案继续收敛。原因: {reason}"


# 调用 NVIDIA NIM API
def call_model(model_id, prompt):
    if not API_KEY:
        return "NVIDIA API key 未设置，请先设置环境变量 NVIDIA_API_KEY"

    model_short = model_id.split('/')[-1]
    print(f"  📤 正在调用 {model_short}... (timeout=120s)")

    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4000,
        "temperature": 0.7
    }
    try:
        response = requests.post(API_URL, headers=HEADERS, json=payload, timeout=120)
    except requests.Timeout:
        print(f"  ❌ {model_short} 请求超时 (120s)，可能被 API 限流")
        return "模型响应失败"
    except requests.RequestException as exc:
        print(f"   {model_short} 请求异常: {exc}")
        return "模型响应失败"

    try:
        data = response.json()
    except ValueError:
        print(f"  ❌ {model_short} 返回非 JSON, 状态码: {response.status_code}")
        return "模型响应失败"

    if response.status_code == 429:
        print(f"   {model_short} 被 API 限流 (429 Too Many Requests)，请稍后重试")
        return "模型响应失败"
    if response.status_code == 503:
        print(f"  ⏳ {model_short} 服务不可用 (503)，模型可能过载")
        return "模型响应失败"
    if response.status_code != 200:
        err_msg = data.get("error", {}).get("message", "") if isinstance(data, dict) else ""
        print(f"  ❌ {model_short} 调用失败, 状态码: {response.status_code}, 错误: {err_msg}")
        return "模型响应失败"

    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        print(f"   {model_short} 返回结构异常")
        return "模型响应失败"

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        char_count = len(content.strip())
        print(f"  ✅ {model_short} 返回成功 ({char_count} 字)")
        return content.strip()

    reasoning = message.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        print(f"  ⚠️ {model_short} 未返回 content，改用 reasoning")
        return reasoning.strip()

    print(f"  ❌ {model_short} 返回空内容")
    return "模型返回空内容"


# 解析挑战模型输出
def parse_challenge_response(response):
    if is_model_failure(response):
        return "是", f"模型发言失败，默认不阻塞当前方案继续收敛。原始响应: {response}"

    lines = response.replace("**", "").split("\n")
    agreement = "否"
    critique = ""
    for line in lines:
        line = line.strip()
        if line.startswith("同意:"):
            agreement = line.replace("同意:", "").strip()
        elif line.startswith("意见:") or line.startswith("建议:"):
            critique = line.replace("意见:", "").replace("建议:", "").strip()

    if "是" in agreement:
        agreement = "是"
    elif "否" in agreement:
        agreement = "否"
    else:
        agreement = "否"
        if not critique:
            critique = f"未按格式返回明确的同意/不同意判断。原始响应: {response[:500]}"

    return agreement, critique


# 解析投票输出，返回投票的方案编号和理由
def parse_vote_response(response, num_proposals):
    if is_model_failure(response):
        # 失败时默认投方案 1
        return 1, f"模型投票失败，默认投方案 1。原始响应: {response}"

    lines = response.replace("**", "").split("\n")
    vote_num = None
    reason = ""
    for line in lines:
        line = line.strip()
        if line.startswith("投票:"):
            vote_text = line.replace("投票:", "").strip()
            # 提取数字
            for ch in vote_text:
                if ch.isdigit():
                    num = int(ch)
                    if 1 <= num <= num_proposals:
                        vote_num = num
                        break
            if vote_num is None:
                # 尝试匹配"方案X"
                import re
                match = re.search(r'方案?\s*(\d+)', vote_text)
                if match:
                    num = int(match.group(1))
                    if 1 <= num <= num_proposals:
                        vote_num = num
        elif line.startswith("理由:"):
            reason = line.replace("理由:", "").strip()

    if vote_num is None:
        # 无法解析，默认投方案 1
        vote_num = 1
        if not reason:
            reason = f"未按格式返回明确投票，默认投方案 1。原始响应: {response[:300]}"

    return vote_num, reason


# 解析完善环节输出，返回 (有无改进, 说明)
def parse_refinement_response(response):
    """解析完善环节评审，返回 (has_improvement: bool, detail: str)"""
    if is_model_failure(response):
        return False, f"模型发言失败，默认无改进。原始响应: {response}"

    lines = response.replace("**", "").split("\n")
    has_improvement = None
    detail = ""
    for line in lines:
        line = line.strip()
        if line.startswith("改进:"):
            judge_text = line.replace("改进:", "").strip()
            if "有" in judge_text:
                has_improvement = True
            elif "无" in judge_text:
                has_improvement = False
        elif line.startswith("说明:"):
            detail = line.replace("说明:", "").strip()

    if has_improvement is None:
        has_improvement = False
        if not detail:
            detail = f"未按格式返回明确判断，默认无改进。原始响应: {response[:300]}"

    return has_improvement, detail


# 选择主持人
def select_host(challenge_counts):
    return min(challenge_counts, key=challenge_counts.get)


def select_initial_host(solutions):
    available_models = [model for model, solution in solutions.items() if not is_model_failure(solution)]
    if not available_models:
        return select_host(challenge_counts)
    return min(available_models, key=lambda model: challenge_counts[model])


def select_next_host(current_host):
    available_models = [model for model in MODELS if model != current_host]
    if not available_models:
        return current_host
    return min(available_models, key=lambda model: challenge_counts[model])


# 获取多行输入（初始问题）
def get_multiline_input(prompt):
    print(prompt)
    print("（输入完问题后，连输入两次回车开始讨论）")
    lines = []
    while True:
        line = input()
        if line.strip().upper() == "END" or line.strip() == "":
            break
        lines.append(line)
    return "\n".join(lines)


# 带超时的输入函数（简化版）
def timeout_input(prompt, timeout=5):
    print(prompt)
    print(f"（5秒内无输入将自动继续；输入 '1' 继续，'2' 结束，'换主持人 [模型名]' 更换主持人）")

    input_queue = queue.Queue()

    def get_input():
        try:
            user_input = input()
            input_queue.put(user_input.strip())
        except:
            input_queue.put("")

    input_thread = threading.Thread(target=get_input)
    input_thread.daemon = True
    input_thread.start()

    try:
        user_input = input_queue.get(timeout=timeout)
    except queue.Empty:
        user_input = ""

    return user_input


# 实时追加到 Markdown 文件
def make_preview(text, max_length=120):
    preview = " ".join(str(text).split())
    if len(preview) <= max_length:
        return preview
    return preview[:max_length] + "..."


def print_compact_entry(title, text, max_length=160):
    print(f"{title}: {make_preview(text, max_length)}\n")


def append_to_md_file(entry):
    with open("deep_discussion.md", "a", encoding="utf-8") as f:
        entry = entry.lstrip()
        if entry.startswith("问题:"):
            f.write(f"# {entry}\n\n")
        elif entry.startswith("第 "):
            f.write(f"\n# {entry}\n---\n")
        elif entry.startswith("最佳方案:"):
            f.write(f"\n# {entry}\n")
        elif entry.startswith("用户输入:") or entry.startswith("用户确认:"):
            f.write(f"> {entry}\n\n")
        else:
            parts = entry.split(" ", 1)
            if len(parts) == 2:
                model, text = parts
                preview = make_preview(text)
                f.write(
                    "<details>\n"
                    f"<summary><strong>{html.escape(model)}</strong> - {html.escape(preview)}</summary>\n\n"
                    f"{text}\n\n"
                    "</details>\n\n"
                )
            else:
                f.write(f"{entry}\n\n")


# 三阶段讨论流程的 prompt 生成函数

def get_proposal_prompt(question):
    """阶段一：提案 prompt，每个模型独立提出方案"""
    return (
        f"针对问题 '{question}'，请提出你的最佳方案。\n"
        f"**要求：**\n"
        f"1. 发言控制在 800 字以内\n"
        # f"2. 聚焦核心观点，提出 2-3 个关键要点\n"
        # f"3. 保留必要的论证深度，避免过度简化\n"
        # f"4. 避免过度结构化和工具化\n"
        # f"5. 直接陈述你的方案，不要加'我认为'之类开场白"
    )


def get_vote_prompt(question, proposals):
    """阶段二：投票 prompt，从所有方案中选出最佳"""
    proposals_text = ""
    for i, (model, solution) in enumerate(proposals.items(), 1):
        model_short = model.split("/")[-1]
        proposals_text += f"\n方案 {i}（由 {model_short} 提出）：\n{solution}\n"
    return (
        f"当前问题是: {question}\n"
        f"以下是各位讨论者提出的方案：{proposals_text}\n"
        f"请投票选出你认为最好的方案，并说明理由。\n"
        f"**要求：发言控制在 300 字以内，聚焦于方案本身的价值判断。**\n"
        f"请按以下格式回答：\n"
        f"投票: [方案编号，如 1/2/3]\n"
        f"理由: [为什么选这个方案，以及其他方案的不足]"
    )


def get_refinement_prompt(question, winner_model, winning_solution):
    """阶段三：完善环节，判断是否有大的补充或改进之处"""
    winner_short = winner_model.split("/")[-1]
    return (
        f"当前问题是: {question}\n"
        f"当前方案（由 {winner_short} 提出）：{winning_solution}\n"
        f"请评审此方案，判断是否存在大的补充、错误或质疑之处。\n"
        f"**要求：发言控制在 500 字以内，聚焦于是否存在实质性的补充或改进空间。**\n"
        f"请按以下格式回答：\n"
        f"改进: [有/无]\n"
        f"说明: [若'有'，指出关键问题并给出建议；若'无'，简要说明方案已较为完善]"
    )


def get_synthesis_prompt(question, winning_solution, feedback_list):
    """阶段三：主持人整合补充/改进建议，产出优化后的方案"""
    feedback_text = "\n".join(
        f"- {m.split('/')[-1]}: {fb}" for m, fb in feedback_list
    )
    return (
        f"你是方案主持人，问题: {question}\n"
        f"你的当前方案: {winning_solution}\n"
        f"其他讨论者提出的问题及建议:\n{feedback_text}\n"
        f"请回应问题,整合意见提出优化后的完整方案。\n"
         f"**要求：发言控制在 800 字以内，吸收合理建议，保留方案核心价值。**"
    )


# 生成语音文本（自然语言转换）
def generate_speech_text(response, role, model_name):
    """将格式化输出转换为自然语言，适合语音播放"""
    lines = response.replace("**", "").split("\n")
    model_short = model_name.split("/")[-1] if "/" in model_name else model_name

    if role in ("host", "proposer", "synthesizer"):
        # 提案/方案类发言直接朗读（已经是自然语言）
        return response

    if role == "voter":
        # 投票发言：提取投票和理由
        vote_num = None
        reason = ""
        for line in lines:
            line = line.strip()
            if line.startswith("投票:"):
                vote_text = line.replace("投票:", "").strip()
                for ch in vote_text:
                    if ch.isdigit():
                        vote_num = ch
                        break
            elif line.startswith("理由:"):
                reason = line.replace("理由:", "").strip()
        speech = f"{model_short}投票给方案{vote_num}。" if vote_num else f"{model_short}发言。"
        if reason:
            speech += " " + reason
        return speech

    # refiner / challenger：提取改进有/无或同意/建议
    has_improvement = None
    agreement = None
    critique_parts = []
    for line in lines:
        line = line.strip()
        if line.startswith("改进:"):
            judge = line.replace("改进:", "").strip()
            has_improvement = "有" if "有" in judge else ("无" if "无" in judge else None)
        elif line.startswith("同意:"):
            agreement = "是" if "是" in line else "否"
        elif line.startswith("建议:") or line.startswith("意见:") or line.startswith("说明:"):
            critique_text = line.split(":", 1)[1].strip()
            if critique_text:
                critique_parts.append(critique_text)
        elif line and not line.startswith("#"):
            critique_parts.append(line)

    # 优先使用"改进: 有/无"判断
    if has_improvement == "有":
        speech = f"{model_short}认为方案有改进之处。"
    elif has_improvement == "无":
        speech = f"{model_short}认为方案已较为完善。"
    elif agreement == "是":
        speech = f"{model_short}认同当前方案。"
    elif agreement == "否":
        speech = f"{model_short}认为当前方案需要改进。"
    else:
        speech = f"{model_short}发言。"

    if critique_parts:
        speech += " " + " ".join(critique_parts)

    return speech


# 实时辩论函数（支持 WebSocket 推送）
def deep_discussion_realtime(question, socketio, discussion_id, max_rounds=3):
    """三阶段实时辩论流程：提案 → 投票 → 共同优化（带 room 隔离与持久化）"""
    global challenge_counts, discussion_history
    from discussion_store import append_event, finish_discussion

    # 统一的 emit：推送到对应 room 并持久化事件
    def emit(event_name, data):
        socketio.emit(event_name, data, room=discussion_id)
        append_event(discussion_id, event_name, data)

    # 重置状态
    challenge_counts = {model: 0 for model in MODELS}
    discussion_history = []

    # 发送问题
    emit('question', {'text': question})
    discussion_history.append(f"问题: {question}")

    model_list = list(MODELS)

    # ==================== 阶段一：所有模型独立提案 ====================
    emit('phase_start', {
        'phase': 'proposal',
        'text': '📋 阶段一：各方提出方案',
        'description': '每个模型独立思考并提出自己的方案，互不干扰'
    })

    proposals = {}  # {model: solution}
    proposal_prompt = get_proposal_prompt(question)
    for model in model_list:
        model_short = model.split('/')[-1]
        print(f"\n[阶段一·提案] 正在请求 {model_short} 提出方案...")
        solution = call_model(model, proposal_prompt)
        proposals[model] = solution

        speech_text = generate_speech_text(solution, "proposer", model)
        emit('speech', {
            'role': 'proposer',
            'model': model,
            'display_text': solution,
            'speech_text': speech_text
        })
        discussion_history.append(f"{model} 提案: {solution}")
        print(f"[阶段一·提案] {model_short} 提案完成")

    # ==================== 阶段二：投票选出最佳方案 ====================
    emit('phase_start', {
        'phase': 'voting',
        'text': '🗳️ 阶段二：投票表决最佳方案',
        'description': '每个模型投票选出最佳方案，并说明理由'
    })

    votes = {}  # {model: (vote_num, reason)}
    vote_counts = {i: 0 for i in range(1, len(model_list) + 1)}
    vote_prompt = get_vote_prompt(question, proposals)

    for model in model_list:
        model_short = model.split('/')[-1]
        print(f"\n[阶段二·投票] 正在请求 {model_short} 投票...")
        response = call_model(model, vote_prompt)
        vote_num, reason = parse_vote_response(response, len(model_list))
        votes[model] = (vote_num, reason)
        vote_counts[vote_num] = vote_counts.get(vote_num, 0) + 1

        speech_text = generate_speech_text(response, "voter", model)
        emit('speech', {
            'role': 'voter',
            'model': model,
            'display_text': response,
            'speech_text': speech_text,
            'vote_num': vote_num
        })
        discussion_history.append(f"{model} 投票: 方案{vote_num}, 理由: {reason}")
        print(f"[阶段二·投票] {model_short} 投了方案{vote_num}")

    # 统计投票结果
    vote_result_text = "、".join(
        f"方案{i}({vote_counts.get(i, 0)}票)" for i in range(1, len(model_list) + 1)
    )
    winner_idx = max(vote_counts, key=vote_counts.get)
    winner_model = model_list[winner_idx - 1]
    winning_solution = proposals[winner_model]

    emit('vote_result', {
        'vote_counts': vote_counts,
        'vote_result_text': vote_result_text,
        'winner_model': winner_model,
        'winner_idx': winner_idx,
        'winning_solution': winning_solution
    })
    discussion_history.append(f"投票结果: {vote_result_text}; 获胜: 方案{winner_idx} ({winner_model})")

    # ==================== 阶段三：共同优化获胜方案 ====================
    emit('phase_start', {
        'phase': 'refinement',
        'text': '🔧 阶段三：共同完善方案',
        'description': '其他模型指出大的补充/改进之处，主持人综合优化，直到所有模型认为无需大的改进'
    })

    current_solution = winning_solution

    for round_num in range(1, max_rounds + 1):
        emit('round_start', {'round': round_num})
        print(f"\n[阶段三·完善] 第 {round_num}/{max_rounds} 轮开始")

        feedback_list = []  # [(model, detail)]  仅收集"有改进"的反馈
        refinement_responses = {}  # {model: (has_improvement, detail)}

        for model in model_list:
            if model == winner_model:
                continue

            model_short = model.split('/')[-1]
            print(f"\n[阶段三·完善] 第{round_num}轮 - 正在请求 {model_short} 评审...")
            refinement_prompt = get_refinement_prompt(question, winner_model, current_solution)
            response = call_model(model, refinement_prompt)
            has_improvement, detail = parse_refinement_response(response)
            refinement_responses[model] = (has_improvement, detail)

            # 仅在"有改进"时纳入主持人综合的反馈
            if has_improvement:
                feedback_list.append((model, detail))
                print(f"[阶段三·完善] {model_short} 认为有改进: {detail[:50]}...")
            else:
                print(f"[阶段三·完善] {model_short} 认为无需改进")

            speech_text = generate_speech_text(response, "refiner", model)
            emit('speech', {
                'role': 'refiner',
                'model': model,
                'display_text': response,
                'speech_text': speech_text,
                'has_improvement': has_improvement
            })
            discussion_history.append(f"{model} 第{round_num}轮评审: {response}")

        # 收敛检测：所有模型都回答"无"（无大的改进之处）时结束
        total_challengers = len(refinement_responses)
        no_improvement_count = sum(1 for _, (has_imp, _) in refinement_responses.items() if not has_imp)
        no_improvement_ratio = no_improvement_count / total_challengers if total_challengers > 0 else 1.0

        emit('convergence', {
            'agree_ratio': no_improvement_ratio,
            'agree_count': no_improvement_count,
            'total': total_challengers
        })

        # 全部认为"无改进"时收敛
        if no_improvement_count == total_challengers:
            emit('convergence_reached', {'solution': current_solution})
            break

        # 仍有改进建议：主持人综合反馈，产出优化后的方案
        if round_num < max_rounds:
            winner_short = winner_model.split('/')[-1]
            print(f"\n[阶段三·完善] 第{round_num}轮 - 正在请求 {winner_short} 综合优化方案...")
            synthesis_prompt = get_synthesis_prompt(question, current_solution, feedback_list)
            current_solution = call_model(winner_model, synthesis_prompt)

            speech_text = generate_speech_text(current_solution, "synthesizer", winner_model)
            emit('speech', {
                'role': 'synthesizer',
                'model': winner_model,
                'display_text': current_solution,
                'speech_text': speech_text
            })
            discussion_history.append(f"{winner_model} 第{round_num}轮综合方案: {current_solution}")
            print(f"[阶段三·完善] {winner_short} 综合方案完成")
        else:
            print(f"\n[阶段三·完善] 已达最大轮数({max_rounds})，强制结束")

    # 发送最终方案
    emit('final_solution', {'solution': current_solution})
    finish_discussion(discussion_id)


# 生成播客脚本
def generate_podcast_script(question, events):
    """将讨论过程转换为引人入胜的播客对话脚本"""
    # 整理讨论摘要
    summary_parts = []
    for ev in events:
        etype = ev.get("type")
        edata = ev.get("data", {})
        if etype == "speech":
            model_label = friendly_name(edata.get("model", ""))
            role = edata.get("role", "")
            role_label = {"proposer": "提案", "voter": "投票", "refiner": "提问", "synthesizer": "解答"}.get(role, role)
            text = edata.get("display_text", "")
            summary_parts.append(f"[{model_label} - {role_label}] {text}")
        elif etype == "vote_result":
            summary_parts.append(f"[投票结果] {edata.get('vote_result_text', '')}, 获胜: {friendly_name(edata.get('winner_model', ''))}")
        elif etype == "final_solution":
            summary_parts.append(f"[最终方案] {edata.get('solution', '')}")

    summary = "\n".join(summary_parts)

    # 当前参与讨论的嘉宾名单（动态生成，避免硬编码模型名）
    speakers = [friendly_name(m) for m in MODELS]
    speakers_text = "、".join(speakers)

    prompt = (
        f"你是一个专业播客制作人。以下是关于问题「{question}」的多模型讨论记录。\n"
        f"请将讨论内容转换成一段几位嘉宾围炉讨论的播客对话脚本，无主持人。\n\n"
        f"讨论记录：\n{summary}\n\n"
        f"**要求：**\n"
        f"1. 无主持人，由几位模型嘉宾直接对话，发言者用模型简称：{speakers_text}\n"
        f"2. 对话分两个阶段自然推进：\n"
        f"   - 第一阶段「各自提思路」：每位嘉宾依次提出自己的思路和方案，讲清核心观点与依据\n"
        f"   - 第二阶段「完善最佳方案」：按讨论记录中实际的提问与回应顺序展开，逐步收敛到最终方案\n"
        f"3. 忠实于讨论记录里谁问谁答的实际流程，不要自行编造记录中没有的问答或交锋；把它转成自然的口语对话，顺着真实讨论的节奏推进，而不是轮流念稿\n"
        f"4. 用口语化表达：多用短句、反问、感叹、语气词（嗯、对、就是、哎），避免书面语和长句\n"
        f"5. 【关键】必须完整保留方案中的具体概念、框架、步骤和比喻（如春夏秋冬、T 型飞轮等），不要过度简化或丢失细节，用通俗语言解释清楚但不能省略\n"
        f"6. 不同嘉宾的立场和分歧已在讨论记录中，照实呈现即可，不要让所有人全程附和\n"
        f"7. 总段数控制在 12-20 段，让两阶段讨论充分展开\n"
        f"8. 严格按以下 JSON 数组格式输出，不要有任何其他内容、不要 markdown 代码块：\n"
        f'   [{{"speaker": "{speakers[0]}", "text": "..."}}, {{"speaker": "{speakers[-1]}", "text": "..."}}, ...]'
    )

    for model_name in MODELS:
        response = call_model(model_name, prompt)
        if is_model_failure(response):
            print(f"播客脚本生成失败，尝试下一个模型: {model_name}")
            continue

        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()

            script = json.loads(cleaned)
            if isinstance(script, list) and all("speaker" in s and "text" in s for s in script):
                return script
            raise ValueError("返回格式不符合预期")
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            print(f"播客脚本解析失败: {e}")
            print(f"原始响应: {response[:500]}")

    print("播客脚本生成全部失败")
    return None


# 讨论主逻辑
def deep_discussion(question, max_rounds=10):
    global challenge_counts
    # # 初始化 Markdown 文件
    # with open("deep_discussion.md", "w", encoding="utf-8") as f:
    #     f.write("")

    print(f"问题: {question}")
    discussion_history.append(f"问题: {question}")
    append_to_md_file(f"问题: {question}")

    # 选择默认主持人，并由主持人生成初始方案
    host_model = select_host(challenge_counts)
    print(f"初始主持人: {host_model} (挑战次数: {challenge_counts[host_model]})")
    initial_prompt = f"针对问题 '{question}'，请提出你的最佳方案，并说明理由。"
    current_solution = call_model(host_model, initial_prompt)
    initial_attempts = 0
    while is_model_failure(current_solution) and initial_attempts < len(MODELS) - 1:
        failed_host = host_model
        host_model = select_next_host(host_model)
        initial_attempts += 1
        message = f"{failed_host} 初始主持发言失败，更换主持人: {host_model}。原因: {current_solution}"
        print(message)
        discussion_history.append(message)
        append_to_md_file(message)
        current_solution = call_model(host_model, initial_prompt)
    # discussion_history.append(f"{host_model} 初始方案: {current_solution}")
    # append_to_md_file(f"{host_model} 初始方案: {current_solution}")
    # print(f"{host_model} 初始方案: {current_solution}\n")

    # 后续轮次
    for round_num in range(1, max_rounds + 1):
        print(f"\n第 {round_num} 轮讨论开始")
        discussion_history.append(f"\n第 {round_num} 轮讨论开始")
        append_to_md_file(f"\n第 {round_num} 轮讨论开始")
        
        if round_num > 1:
            # 主持人发言
            host_prompt = (

                f"你是讨论主持人，问题: {question}\n"
                f"上一轮讨论如下:\n{json.dumps(discussion_history[-len(MODELS) - 1:], ensure_ascii=False)}\n"
                f"请汇总上一轮讨论，提出当前完整最佳方案，并说明理由。"
            )
            next_solution = call_model(host_model, host_prompt)
            if is_model_failure(next_solution):
                failed_host = host_model
                host_model = select_next_host(host_model)
                message = f"{failed_host} 主持发言失败，下一轮更换主持人: {host_model}。原因: {next_solution}"
                print(message)
                discussion_history.append(message)
                append_to_md_file(message)
                continue
            current_solution = next_solution
        discussion_history.append(f"{host_model} 第 {round_num} 轮方案: {current_solution}")
        append_to_md_file(f"{host_model} 第 {round_num} 轮方案: {current_solution}")
        print_compact_entry(f"{host_model} 第 {round_num} 轮方案", current_solution)

        # 挑战者反驳
        challenge_responses = {}
        all_agree = True
        for model in MODELS:
            if model == host_model:
                continue
            challenge_prompt = (
                f"当前问题是: {question}\n"
                f"主持人 {host_model} 的方案是: {current_solution}\n"
                f"你的任务是：1. 严格评审主持人方案\n"
                f"2. 判断是否同意，若不同意，提出改进。"
                f"请按以下格式回答:\n"
                f"同意: [是/否]\n"
                f"建议: [若不同意，指出严重不足并提出替代建议；若同意，说明理由]\n"
              
            )
            try:
                response = call_model(model, challenge_prompt)
                if is_model_failure(response):
                    response = failed_challenge_response(response)
                agreement, critique = parse_challenge_response(response)
                challenge_responses[model] = (agreement, critique)
            except Exception as exc:
                print(f"{model} 响应失败")
                response = failed_challenge_response(str(exc))
                agreement, critique = parse_challenge_response(response)
                challenge_responses[model] = (agreement, critique)
            discussion_history.append(f"{model} 第 {round_num} 轮响应: {response}")
            append_to_md_file(f"{model} 第 {round_num} 轮响应: {response}")
            print_compact_entry(f"{model} 第 {round_num} 轮响应", response)
            if agreement != "是":
                all_agree = False
                challenge_counts[host_model] += 1

        # 检查一致性
        if all_agree:
            print("所有挑战模型同意主持人方案，等待用户确认...")
            confirm = timeout_input(f"是否同意此方案为最佳方案？当前方案: {current_solution}", timeout=5)
            discussion_history.append(f"用户确认: {confirm}")
            append_to_md_file(f"用户确认: {confirm}")
            if confirm.lower() == "是" or confirm == "":
                print("用户同意，讨论结束。")
                # final_solution = current_solution
                # discussion_history.append(f"最佳方案: {final_solution}")
                # append_to_md_file(f"最佳方案: {final_solution}")
                # print_compact_entry("\n最佳方案", final_solution, max_length=300)
                break
            elif confirm.lower() == "否":
                print("用户不同意，继续讨论...")
            else:
                print("无效输入，继续讨论...")

        # 检查最大轮次
        if round_num == max_rounds:
            final_solution = current_solution
            discussion_history.append(f"最佳方案: {final_solution}")
            append_to_md_file(f"最佳方案: {final_solution}")
            print_compact_entry("\n达到最大轮次，最终方案", final_solution, max_length=300)
            break

        # 用户干预
        user_input = timeout_input("请参与讨论（补充细节/指出错误）")
        discussion_history.append(f"用户输入: {user_input}")
        append_to_md_file(f"用户输入: {user_input}")

        if user_input == "2":
            final_solution = current_solution
            discussion_history.append(f"最佳方案: {final_solution}")
            append_to_md_file(f"最佳方案: {final_solution}")
            print_compact_entry("\n最佳方案", final_solution, max_length=300)
            break
        elif user_input.startswith("换主持人"):
            new_host = user_input.split("换主持人")[1].strip()
            if new_host in MODELS:
                host_model = new_host
                print(f"用户指定新主持人: {host_model}")
            else:
                print(f"无效模型名: {new_host}")
        elif user_input != "" and user_input != "1":
            adjusted_solution = call_model(
                host_model,
                f"用户补充: {user_input}\n请根据用户输入调整你的方案: {current_solution}"
            )
            if is_model_failure(adjusted_solution):
                print(f"{host_model} 根据用户调整方案失败，沿用当前方案: {adjusted_solution}")
                adjusted_solution = current_solution
            current_solution = adjusted_solution
            discussion_history.append(f"{host_model} 根据用户调整方案: {current_solution}")
            append_to_md_file(f"{host_model} 根据用户调整方案: {current_solution}")
            print_compact_entry(f"{host_model} 根据用户调整方案", current_solution)
        else:
            print("5秒未输入，自动继续下一轮...")

        # 更新主持人
        new_host = select_host(challenge_counts) if round_num % 2 == 0 else host_model
        if new_host != host_model:
            print(
                f"主持人更换: {host_model} (挑战次数: {challenge_counts[host_model]}) -> {new_host} (挑战次数: {challenge_counts[new_host]})")
            host_model = new_host


# 测试运行
if __name__ == "__main__":
    print("欢迎使用 Deep Discussion")
    question = get_multiline_input("请输入要讨论的问题:")
    deep_discussion(question)
