#!/usr/bin/env python3
"""
自动二次验证 + 导入 New API 脚本
流程：收集扫描数据 → 二次验证 → 导入 New API（带 wd-ys 映射）
用于 cron 定时执行
"""
import json, sys, time, os
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

# ====== 配置 ======
HAJIMI_DIR = Path("/home/wang/hajimi-king-local")
DATA_DIR = HAJIMI_DIR / "data"
VERIFY_SCRIPT = HAJIMI_DIR / "scripts" / "quick_verify_all.py"
NEW_API_DB = "/home/wang/new-api-data/one-api.db"
VENV_PYTHON = str(HAJIMI_DIR / ".venv" / "bin" / "python3")
STATE_FILE = HAJIMI_DIR / "verify_results" / "auto_verify_state.json"
MAX_KEYS_PER_RUN = 50
PER_PROVIDER_LIMIT = 8
FAILED_COOLDOWN_DAYS = 7
FAILED_SKIP_AFTER = 1
STATE_CLEANUP_DAYS = 14
# 高收益供应商优先，但每轮会轮询取样，避免某个目录抢满 50 个
PROVIDER_PRIORITY = [
    "nvidia", "aliyun", "volcengine", "deepseek", "xai", "groq",
    "openrouter", "openai", "anthropic", "gemini", "cohere", "zhipu",
    "kimi", "xiaomi", "minimax", "modelscope", "huggingface",
]

# 供应商导入配置
CONFIG = {
    'aliyun': {
        'type': 1,
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode',
        'models': 'qwen-plus,deepseek-v4-pro,deepseek-v4-flash,glm-5.1',
    },
    'openrouter': {
        'type': 1,
        'base_url': 'https://openrouter.ai/api/v1',
        'models': 'x-ai/grok-4.3',
    },
    'xai': {
        'type': 1,
        'base_url': 'https://api.x.ai/v1',
        'models': 'grok-4.3',
    },
    'groq': {
        'type': 14,
        'base_url': 'https://api.groq.com/openai',
        'models': 'whisper-large-v3-turbo,llama-3.3-70b-versatile',
        'no_wdys': True,  # whisper 不进聊天池
    },
    'deepseek': {
        'type': 43,
        'base_url': 'https://api.deepseek.com',
        'models': 'deepseek-v4-pro,deepseek-v4-flash',
    },
    'openai': {
        'type': 3,
        'base_url': 'https://api.openai.com/v1',
        'models': 'gpt-5.5,gpt-4o',
    },
    'anthropic': {
        'type': 11,
        'base_url': 'https://api.anthropic.com/v1',
        'models': 'claude-opus-4-6,claude-sonnet-4-6',
    },
    'gemini': {
        'type': 24,
        'base_url': '',
        'models': 'gemini-3.1-pro-preview,gemini-2.5-pro',
    },
    'nvidia': {
        'type': 27,
        'base_url': 'https://integrate.api.nvidia.com/v1',
        'models': 'deepseek-ai/deepseek-v4-pro',
    },
    'volcengine': {
        'type': 1,
        'base_url': 'https://ark.cn-beijing.volces.com/api',
        'models': 'doubao-seed-2-0-pro-260215',
    },
    'cohere': {
        'type': 1,
        'base_url': 'https://api.cohere.com/v1',
        'models': 'command-r-plus',
    },
    'xiaomi': {
        'type': 1,
        'base_url': 'https://api.xiaomi.com/v1',
        'models': 'mimo-v2.5-pro',
    },
    'zhipu': {
        'type': 1,
        'base_url': 'https://open.bigmodel.cn/api/paas/v4',
        'models': 'glm-5.1',
    },
    'kimi': {
        'type': 1,
        'base_url': 'https://api.moonshot.cn/v1',
        'models': 'kimi-k2.6',
    },
    'minimax': {
        'type': 1,
        'base_url': 'https://api.minimax.chat/v1',
        'models': 'MiniMax-Text-01',
    },
    'huggingface': {
        'type': 1,
        'base_url': 'https://api-inference.huggingface.co/v1',
        'models': 'Qwen/Qwen3-235B-A22B',
    },
}


def load_state():
    """读取失败记忆：失败过的 key 短期内不重复二验。"""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"failed": {}, "passed": {}, "runs": []}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(STATE_FILE) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def cleanup_state():
    """清理过期失败条目和过期 passed 条目，避免状态文件无限增长。"""
    state = load_state()
    now = datetime.now()
    cutoff = now - timedelta(days=STATE_CLEANUP_DAYS)
    cleaned_fail = 0
    cleaned_pass = 0
    for key, rec in list(state.get("failed", {}).items()):
        try:
            dt = datetime.fromisoformat(rec.get("last_attempt", ""))
            if dt < cutoff:
                del state["failed"][key]
                cleaned_fail += 1
        except Exception:
            del state["failed"][key]
            cleaned_fail += 1
    for key, rec in list(state.get("passed", {}).items()):
        try:
            dt = datetime.fromisoformat(rec.get("last_pass", ""))
            if dt < cutoff:
                del state["passed"][key]
                cleaned_pass += 1
        except Exception:
            pass
    if cleaned_fail or cleaned_pass:
        save_state(state)
        print(f"[{datetime.now()}] state 清理: failed={cleaned_fail} passed={cleaned_pass}")
    return state


def is_in_failed_cooldown(key, state):
    rec = state.get("failed", {}).get(key)
    if not rec:
        return False
    if int(rec.get("count", 0)) < FAILED_SKIP_AFTER:
        return False
    last = rec.get("last_attempt", "")
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    return datetime.now() - last_dt < timedelta(days=FAILED_COOLDOWN_DAYS)


def update_attempt_state(keys, passed):
    """记录本轮二验结果，避免 cron 反复打同一批失败 key。"""
    state = load_state()
    now = datetime.now().isoformat(timespec="seconds")
    passed_keys = {x["key"] for x in passed}
    state.setdefault("failed", {})
    state.setdefault("passed", {})
    for item in keys:
        key = item["key"]
        provider = item.get("provider", "")
        if key in passed_keys:
            state["passed"][key] = {"provider": provider, "last_pass": now}
            state["failed"].pop(key, None)
        else:
            rec = state["failed"].get(key, {"provider": provider, "count": 0})
            rec["provider"] = provider
            rec["count"] = int(rec.get("count", 0)) + 1
            rec["last_attempt"] = now
            state["failed"][key] = rec
    state.setdefault("runs", []).append({
        "time": now,
        "input": len(keys),
        "passed": len(passed),
        "providers": dict(__import__("collections").Counter(x.get("provider", "") for x in keys)),
    })
    state["runs"] = state["runs"][-50:]
    save_state(state)


def collect_keys():
    """收集扫描数据中的有效 key。

    优化点：
    1. 排除 New API 已存在 key；
    2. 排除最近失败过的 key，避免每 4 小时重复二验同一批死 key；
    3. 按供应商轮询取样，每个供应商单轮最多 PER_PROVIDER_LIMIT 个，避免 huggingface/openrouter 抢满 50 个。
    """
    conn = sqlite3.connect(NEW_API_DB)
    existing = set()
    for row in conn.execute("SELECT key FROM channels WHERE status IN (1,2,3)"):
        existing.add(row[0])
    conn.close()

    state = load_state()
    depleted = set(state.get("depleted_today", "").split(",")) if state.get("depleted_today", "") else set()
    depleted_date = state.get("depleted_date", "")
    today_str = datetime.now().strftime("%Y%m%d")
    if depleted_date != today_str:
        depleted = set()  # 新一天，重置耗尽标记

    seen = set()
    candidates = {}
    today = today_str
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    # 取样窗口：不只今明两天，改为最近 VALID_LOOKBACK_DAYS 天全部 valid 文件。
    # 原因：hajimi 产出 valid 文件不规律（常连续多天只有 invalid），若只取今明两天
    # 会导致候选池长期为空、cron 空转（9/30 起连续多轮 "通过 0，导入 0"）。
    # 新文件优先：按 mtime 倒序，保证最新产出先被验证。
    lookback_days = int(os.environ.get("VALID_LOOKBACK_DAYS", "30"))
    cutoff_ts = (datetime.now() - timedelta(days=lookback_days)).timestamp()

    provider_dirs = {p.name: p for p in DATA_DIR.iterdir() if p.is_dir()}
    providers = [p for p in PROVIDER_PRIORITY if p in provider_dirs]
    providers += sorted([p for p in provider_dirs if p not in providers])

    for provider in providers:
        if provider in depleted:
            continue
        provider_dir = provider_dirs[provider]
        items = []
        files = []
        # 新文件优先：最近 lookback_days 天内所有 keys_valid_*.txt，按 mtime 倒序
        files.extend(sorted(
            (f for f in provider_dir.glob("keys_valid_*.txt")
             if f.stat().st_mtime >= cutoff_ts),
            key=lambda x: x.stat().st_mtime, reverse=True))
        for f in files:
            with open(f) as fh:
                for line in fh:
                    key = line.strip()
                    if not key or key in seen or key in existing:
                        continue
                    seen.add(key)
                    if is_in_failed_cooldown(key, state):
                        continue
                    items.append({"provider": provider, "key": key})
                    if len(items) >= PER_PROVIDER_LIMIT:
                        break
            if len(items) >= PER_PROVIDER_LIMIT:
                break
        if items:
            candidates[provider] = items
        else:
            depleted.add(provider)

    # 轮询合并，最多 MAX_KEYS_PER_RUN
    all_keys = []
    idx = 0
    while len(all_keys) < MAX_KEYS_PER_RUN:
        added = False
        for provider in providers:
            arr = candidates.get(provider, [])
            if idx < len(arr):
                all_keys.append(arr[idx])
                added = True
                if len(all_keys) >= MAX_KEYS_PER_RUN:
                    break
        if not added:
            break
        idx += 1

    print(f"[{datetime.now()}] 候选供应商: " + ", ".join(f"{p}={len(v)}" for p, v in candidates.items()))
    if depleted:
        state["depleted_today"] = ",".join(sorted(depleted))
        state["depleted_date"] = today_str
        save_state(state)
    return all_keys


def run_verification(keys):
    """运行二次验证，返回通过列表（带完整模型列表）"""
    input_file = "/tmp/verify_input_auto.json"
    with open(input_file, "w") as f:
        json.dump({"valid_items": keys}, f)

    print(f"[{datetime.now()}] 验证输入: {len(keys)} keys")

    # 运行 quick_verify_all.py 并捕获输出
    import subprocess
    result = subprocess.run(
        [VENV_PYTHON, str(VERIFY_SCRIPT), input_file],
        capture_output=True, text=True, timeout=3600,
        env={**os.environ, "OPENROUTER_FAST_MODE": "1"}
    )

    # 解析输出 - 同时提取 best_model 和 models 列表
    passed = []
    output_lines = (result.stdout + result.stderr).split('\n')
    
    for i, line in enumerate(output_lines):
        if '✅' in line and '→' in line:
            # 格式: [provider] ✅ key_prefix... → best_model
            parts = line.strip().split('→')
            if len(parts) == 2:
                left = parts[0].strip()
                best_model = parts[1].strip()
                bracket = left.find(']')
                provider = left[1:bracket]
                key_part = left[bracket + 2:].strip()
                key_prefix = key_part.split()[1] if len(key_part.split()) > 1 else key_part[2:]
                
                # 查找下一行的模型列表（格式：models: model1, model2, ...）
                models = []
                if i + 1 < len(output_lines):
                    next_line = output_lines[i + 1].strip()
                    if next_line.startswith('models:'):
                        models_str = next_line[7:].strip()  # 去掉 "models: "
                        models = [m.strip() for m in models_str.split(',') if m.strip()]
                
                # 如果没有模型列表，至少包含 best_model
                if not models:
                    models = [best_model]
                
                # 从 input 中匹配完整 key
                for item in keys:
                    if item['provider'] == provider and item['key'].startswith(key_prefix):
                        passed.append({
                            "provider": provider,
                            "key": item['key'],
                            "best_model": best_model,
                            "models": models,  # 新增：实际支持的模型列表
                        })
                        break

    print(f"[{datetime.now()}] 验证通过: {len(passed)} keys")
    for p in passed:
        print(f"  {p['provider']}: {p['best_model']} ({len(p.get('models', []))} models)")

    return passed


def import_to_new_api(passed):
    """导入验证通过的 key 到 New API（逐模型拆分渠道）"""
    if not passed:
        print(f"[{datetime.now()}] 无新增有效 key")
        return 0

    conn = sqlite3.connect(NEW_API_DB)
    cur = conn.cursor()

    # 获取已存在的 key
    existing = set()
    for row in cur.execute("SELECT key FROM channels WHERE status IN (1,2,3)"):
        existing.add(row[0])

    count = 0
    for item in passed:
        key = item['key']
        provider = item['provider']
        best = item['best_model']
        models = item.get('models', [best])  # 实际支持的模型列表

        cfg = CONFIG.get(provider)
        if not cfg:
            print(f"SKIP: {provider} 无导入配置")
            continue

        do_wdys = not cfg.get('no_wdys', False)

        if key in existing:
            # 已有渠道，跳过（避免重复导入）
            print(f"SKIP: {provider} key 已存在")
            continue

        # 逐模型拆分创建渠道
        for model in models:
            # 渠道名：provider [model]
            name = f"{provider} [{model}]"
            
            # model_mapping 双映射：模型自己 + wd-ys → 该模型
            if do_wdys:
                mm = json.dumps({model: model, "wd-ys": model}, ensure_ascii=False)
            else:
                mm = json.dumps({model: model}, ensure_ascii=False)
            
            # channel_info
            ci = json.dumps({
                "is_multi_key": False,
                "multi_key_size": 0,
                "multi_key_mode": "random",
            }).encode()

            # 插入渠道（单模型）
            cur.execute("""INSERT INTO channels
                (type, name, key, status, base_url, models, model_mapping,
                 channel_info, created_time, auto_ban, balance, used_quota, `group`)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, 1, 0, 0, 'default')""",
                (cfg['type'], name, key, cfg['base_url'], json.dumps([model]),
                 mm, ci, int(time.time())))

            ch_id = cur.lastrowid

            # 写 abilities：模型自己 + wd-ys（如果 do_wdys）
            cur.execute("""INSERT OR IGNORE INTO abilities
                ("group", model, channel_id, enabled, priority, weight, tag)
                VALUES ('default', ?, ?, 1, 0, 0, '')""",
                (model, ch_id))
            
            if do_wdys:
                cur.execute("""INSERT OR IGNORE INTO abilities
                    ("group", model, channel_id, enabled, priority, weight, tag)
                    VALUES ('default', 'wd-ys', ?, 1, 0, 0, '')""",
                    (ch_id,))

            count += 1
            print(f"NEW: {provider} ch_id={ch_id} [{model}] (wd-ys: {do_wdys})")

    conn.commit()
    conn.close()
    print(f"[{datetime.now()}] 导入完成: {count} 个渠道")
    return count


def main():
    print(f"\n{'='*50}")
    print(f"[{datetime.now()}] 自动验证导入开始")
    print(f"{'='*50}")

    # 1. 收集 key（先清理过期状态）
    cleanup_state()
    keys = collect_keys()
    if not keys:
        print("无 key 需要验证")
        return

    # 2. 二次验证
    passed = run_verification(keys)
    update_attempt_state(keys, passed)

    # 3. 导入
    n = import_to_new_api(passed)

    # 4. 汇总
    total = 0
    conn = sqlite3.connect(NEW_API_DB)
    total = conn.execute("SELECT COUNT(*) FROM channels WHERE status=1").fetchone()[0]
    conn.close()

    print(f"\n{'='*50}")
    print(f"[{datetime.now()}] 完成: 收集 {len(keys)}, 通过 {len(passed)}, 导入 {n}")
    print(f"当前活跃渠道: {total}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
