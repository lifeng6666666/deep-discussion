"""讨论记录持久化存储，每个讨论存为独立 JSON 文件。"""
import json
import os
import uuid
from datetime import datetime

DISCUSSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "discussions")


def _ensure_dir():
    os.makedirs(DISCUSSIONS_DIR, exist_ok=True)


def _path(discussion_id):
    return os.path.join(DISCUSSIONS_DIR, f"{discussion_id}.json")


def create_discussion(question):
    """创建一个新讨论，返回讨论 id。"""
    _ensure_dir()
    discussion_id = uuid.uuid4().hex[:12]
    record = {
        "id": discussion_id,
        "question": question,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": "ongoing",
        "events": [],
    }
    with open(_path(discussion_id), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return discussion_id


def append_event(discussion_id, event_name, data):
    """向讨论追加一个事件。"""
    try:
        with open(_path(discussion_id), "r", encoding="utf-8") as f:
            record = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return

    record["events"].append({"type": event_name, "data": data})
    with open(_path(discussion_id), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)


def finish_discussion(discussion_id):
    """标记讨论完成。"""
    try:
        with open(_path(discussion_id), "r", encoding="utf-8") as f:
            record = json.load(f)
        record["status"] = "completed"
        with open(_path(discussion_id), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def save_podcast(discussion_id, podcast_script):
    """保存播客脚本到讨论记录。"""
    try:
        with open(_path(discussion_id), "r", encoding="utf-8") as f:
            record = json.load(f)
        record["podcast"] = podcast_script
        with open(_path(discussion_id), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def get_discussion(discussion_id):
    """获取单个讨论记录。"""
    try:
        with open(_path(discussion_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def list_discussions():
    """列出所有讨论，按创建时间倒序。"""
    _ensure_dir()
    items = []
    for fname in os.listdir(DISCUSSIONS_DIR):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(DISCUSSIONS_DIR, fname), "r", encoding="utf-8") as f:
                record = json.load(f)
            items.append({
                "id": record["id"],
                "question": record["question"],
                "created_at": record["created_at"],
                "status": record.get("status", "ongoing"),
                "event_count": len(record.get("events", [])),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items


def delete_discussion(discussion_id):
    """删除讨论记录文件，返回是否成功。"""
    path = _path(discussion_id)
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
