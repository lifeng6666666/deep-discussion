import requests
import json
import time

# OpenRouter API 配置
API_KEY = "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"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# 候选模型
MODELS = [
    "deepseek/deepseek-r1:free",
    "qwen/qwq-32b:free",
    "google/gemini-2.0-flash-thinking-exp:free"
]

# 挑战指数和讨论历史
challenge_counts = {model: 0 for model in MODELS}
discussion_history = []

# 调用 OpenRouter API
def call_model(model_id, prompt):
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4000,
        "temperature": 0.7
    }
    response = requests.post(API_URL, headers=HEADERS, data=json.dumps(payload))
    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"].strip()
    else:
        print(f"API 调用失败: {model_id}, 状态码: {response.status_code}")
        return "模型响应失败"

# 解析挑战模型输出
def parse_challenge_response(response):
    lines = response.split("\n")
    agreement = ""
    critique = ""
    for line in lines:
        if line.startswith("同意:"):
            agreement = line.replace("同意:", "").strip()
        elif line.startswith("批判:"):
            critique = line.replace("批判:", "").strip()
    return agreement, critique

# 选择主持人
def select_host(challenge_counts):
    return min(challenge_counts, key=challenge_counts.get)

# 获取多行输入
def get_multiline_input(prompt):
    print(prompt)
    print("（输入多行后，单独输入 'END' 或空行结束）")
    lines = []
    while True:
        line = input()
        if line.strip().upper() == "END" or line.strip() == "":
            break
        lines.append(line)
    return "\n".join(lines)

# 实时追加到 Markdown 文件
def append_to_md_file(entry):
    with open("deep_discussion.md", "a", encoding="utf-8") as f:
        if entry.startswith("问题:"):
            f.write(f"# {entry}\n\n")
        elif entry.startswith("第 "):
            f.write(f"\n# {entry}\n---\n")
        elif entry.startswith("最佳方案:"):
            f.write(f"\n# {entry}\n")
        else:
            parts = entry.split(" ", 1)
            if len(parts) == 2:
                model, text = parts
                f.write(f"## **{model}**\n{text}\n\n")
            else:
                f.write(f"{entry}\n\n")

# 讨论主逻辑
def deep_discussion(question, max_rounds=10):
    global challenge_counts
    # 初始化 Markdown 文件
    with open("debateforge_log.md", "w", encoding="utf-8") as f:
        f.write("")
    
    print(f"问题: {question}")
    discussion_history.append(f"问题: {question}")
    append_to_md_file(f"问题: {question}")
    
    # 第一轮：所有模型独立发言
    solutions = {}
    print("\n第一轮：所有模型提出初始方案")
    for model in MODELS:
        prompt = f"针对问题 '{question}'，请提出你的最佳方案，并说明理由。"
        solution = call_model(model, prompt)
        solutions[model] = solution
        discussion_history.append(f"{model} 初始方案: {solution}")
        append_to_md_file(f"{model} 初始方案: {solution}")
        print(f"{model} 初始方案: {solution}\n")
    
    # 第一轮挑战
    for model in MODELS:
        other_solutions = {m: s for m, s in solutions.items() if m != model}
        prompt = (
            f"当前问题是: {question}\n"
            f"其他模型的方案如下:\n{json.dumps(other_solutions, ensure_ascii=False)}\n"
            f"请按以下格式回答:\n"
            f"同意: [是/否]\n"
            f"批判: [指出其他方案的严重不足，若无则说明]\n"
            f"任务: 分析其他模型方案，指出逻辑漏洞或严重不足。"
        )
        response = call_model(model, prompt)
        _, critique = parse_challenge_response(response)
        if "严重不足" in critique:
            for m in other_solutions:
                challenge_counts[m] += 1
        discussion_history.append(f"{model} 第一轮批判: {response}")
        append_to_md_file(f"{model} 第一轮批判: {response}")
        print(f"{model} 第一轮批判: {response}\n")
    
    # 选择初始主持人
    host_model = select_host(challenge_counts)
    current_solution = solutions[host_model]
    print(f"初始主持人: {host_model} (挑战次数: {challenge_counts[host_model]})")
    
    # 后续轮次
    for round_num in range(1, max_rounds + 1):
        print(f"\n第 {round_num} 轮讨论开始")
        discussion_history.append(f"\n第 {round_num} 轮讨论开始")
        append_to_md_file(f"\n第 {round_num} 轮讨论开始")
        
        # 主持人发言
        host_prompt = (
            f"你是讨论主持人，问题: {question}\n"
            f"上一轮讨论如下:\n{json.dumps(discussion_history[-len(MODELS)-1:], ensure_ascii=False)}\n"
            f"请汇总上一轮讨论，提出当前最佳方案，并说明理由。"
        )
        current_solution = call_model(host_model, host_prompt)
        discussion_history.append(f"{host_model} 第 {round_num} 轮方案: {current_solution}")
        append_to_md_file(f"{host_model} 第 {round_num} 轮方案: {current_solution}")
        print(f"{host_model} 第 {round_num} 轮方案: {current_solution}\n")
        
        # 挑战者反驳
        challenge_responses = {}
        all_agree = True
        for model in MODELS:
            if model == host_model:
                continue
            challenge_prompt = (
                f"当前问题是: {question}\n"
                f"主持人 {host_model} 的方案是: {current_solution}\n"
                f"请按以下格式回答:\n"
                f"同意: [是/否]\n"
                f"批判: [若不同意，指出严重不足并提出替代建议；若同意，说明理由]\n"
                f"任务:\n"
                f"1. 分析主持人方案的不足。\n"
                f"2. 判断是否同意，若不同意，提出改进。"
            )
            response = call_model(model, challenge_prompt)
            agreement, critique = parse_challenge_response(response)
            challenge_responses[model] = (agreement, critique)
            discussion_history.append(f"{model} 第 {round_num} 轮响应: {response}")
            append_to_md_file(f"{model} 第 {round_num} 轮响应: {response}")
            print(f"{model} 第 {round_num} 轮响应: {response}\n")
            if "否" in agreement.lower():
                all_agree = False
            if "严重不足" in critique:
                challenge_counts[host_model] += 1
        
        # 检查一致性
        if all_agree:
            print("所有挑战模型同意主持人方案，讨论结束")
            print(f"\n最终方案: {current_solution}")
            confirm = input("是否同意此方案为最佳方案？(是/否): ")
            if confirm.lower() == "是":
                break
            else:
                print("继续讨论...")
        
        # 更新主持人
        new_host = select_host(challenge_counts)
        if new_host != host_model:
            print(f"主持人更换: {host_model} (挑战次数: {challenge_counts[host_model]}) -> {new_host} (挑战次数: {challenge_counts[new_host]})")
            host_model = new_host
        
        # 用户干预
        user_input = get_multiline_input("请参与讨论（补充细节/指出错误），或输入 '继续'/'结束'/'换主持人 [模型名]':")
        discussion_history.append(f"用户输入: {user_input}")
        append_to_md_file(f"用户输入: {user_input}")
        if user_input == "结束":
            break
        elif user_input.startswith("换主持人"):
            new_host = user_input.split("换主持人")[1].strip()
            if new_host in MODELS:
                host_model = new_host
                print(f"用户指定新主持人: {host_model}")
            else:
                print(f"无效模型名: {new_host}")
        elif user_input != "继续":
            current_solution = call_model(
                host_model,
                f"用户补充: {user_input}\n请根据用户输入调整你的方案: {current_solution}"
            )
            discussion_history.append(f"{host_model} 根据用户调整方案: {current_solution}")
            append_to_md_file(f"{host_model} 根据用户调整方案: {current_solution}")
            print(f"{host_model} 根据用户调整方案: {current_solution}\n")
    
    # 输出最佳方案
    final_solution = current_solution
    discussion_history.append(f"最佳方案: {final_solution}")
    append_to_md_file(f"最佳方案: {final_solution}")
    print(f"\n最佳方案: {final_solution}")

# 测试运行
if __name__ == "__main__":
    print("欢迎使用 Deep Discussion")
    question = get_multiline_input("请输入要讨论的问题:")
    deep_discussion(question)