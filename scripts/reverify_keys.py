#!/usr/bin/env python3
"""
二次验证 + 余额查询（修正版）
1. 从 verify_result JSON 读取已验证有效的 key
2. 重新真实对话验证
3. 正确解析余额
4. 并发处理
"""
import os, json, time, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from collections import Counter
import requests

INPUT_FILE = "/root/verify_results/verify_result_20260527_083004.json"
OUTPUT_DIR = "/root/verify_results"
MAX_WORKERS = 15
TIMEOUT = 15

PROVIDERS = {
    "deepseek": {
        "chat_url": "https://api.deepseek.com/v1/chat/completions",
        "chat_model": "deepseek-chat",
        "balance_url": "https://api.deepseek.com/user/balance",
        "balance_type": "deepseek"
    },
    "openai": {
        "chat_url": "https://api.openai.com/v1/chat/completions",
        "chat_model": "gpt-4o-mini",
        "balance_url": "https://api.openai.com/dashboard/billing/credit_grants",
        "balance_type": "openai"
    },
    "openrouter": {
        "chat_url": "https://openrouter.ai/api/v1/chat/completions",
        "chat_model": "openai/gpt-4o-mini",
        "balance_url": "https://openrouter.ai/api/v1/auth/key",
        "balance_type": "openrouter"
    },
    "groq": {
        "chat_url": "https://api.groq.com/openai/v1/chat/completions",
        "chat_model": "llama-3.1-8b-instant",
        "balance_url": None,
        "balance_type": "none"
    },
    "aliyun": {
        "chat_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "chat_model": "qwen-turbo",
        "balance_url": None,
        "balance_type": "none"
    },
}


def load_keys():
    with open(INPUT_FILE) as f:
        data = json.load(f)
    return data.get("valid", [])


def test_chat(provider, key):
    """真实对话验证"""
    cfg = PROVIDERS.get(provider)
    if not cfg:
        return False, "unknown provider", 0
    try:
        r = requests.post(cfg["chat_url"],
                          headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                          json={"model": cfg["chat_model"],
                                "messages": [{"role": "user", "content": "Say hi in 3 words"}],
                                "max_tokens": 20},
                          timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if data.get("choices"):
                txt = data["choices"][0].get("message", {}).get("content", "")
                tok = data.get("usage", {}).get("total_tokens", 0)
                return True, txt[:100], tok
        return False, "HTTP " + str(r.status_code), 0
    except Exception as e:
        return False, str(e)[:100], 0


def check_balance(provider, key):
    """查询余额（修正版）"""
    cfg = PROVIDERS.get(provider)
    if not cfg or not cfg.get("balance_url"):
        return -1, "", "no_balance_api"

    try:
        r = requests.get(cfg["balance_url"],
                         headers={"Authorization": "Bearer " + key},
                         timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()

            # DeepSeek 格式: {"balance_infos": [{"currency": "CNY", "total_balance": "9.79"}]}
            if cfg["balance_type"] == "deepseek":
                infos = data.get("balance_infos", [])
                if infos:
                    bal = float(infos[0].get("total_balance", 0))
                    cur = infos[0].get("currency", "CNY")
                    granted = infos[0].get("granted_balance", "0")
                    topup = infos[0].get("topped_up_balance", "0")
                    return bal, cur, "grant:" + str(granted) + " topup:" + str(topup)
                return 0, "CNY", "no_balance_infos"

            # OpenRouter 格式: {"data": {"limit": 10, "usage": 2.5}}
            elif cfg["balance_type"] == "openrouter":
                d = data.get("data", {})
                limit = d.get("limit")
                usage = d.get("usage", 0)
                label = d.get("label", "")
                if limit is not None:
                    remaining = float(limit) - float(usage)
                    return remaining, "USD", "limit:" + str(limit) + " used:" + str(usage) + " label:" + label
                return -1, "USD", "no_limit label:" + label

            # OpenAI 格式
            elif cfg["balance_type"] == "openai":
                total = data.get("total_granted", 0)
                used = data.get("total_used", 0)
                remaining = float(total) - float(used)
                return remaining, "USD", "granted:" + str(total) + " used:" + str(used)

        return 0, "", "HTTP " + str(r.status_code)
    except Exception as e:
        return 0, "", str(e)[:80]


def verify_one(item):
    """单个 key 的完整验证：对话 + 余额"""
    provider = item["provider"]
    key = item["key"]

    # 对话验证
    chat_ok, chat_msg, chat_tokens = test_chat(provider, key)

    # 余额查询
    balance, currency, note = check_balance(provider, key)

    return {
        "provider": provider,
        "key": key,
        "chat_ok": chat_ok,
        "chat_response": chat_msg,
        "chat_tokens": chat_tokens,
        "balance": balance,
        "currency": currency,
        "balance_note": note
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("[" + now + "] 加载 keys...")
    items = load_keys()
    print("  共 " + str(len(items)) + " 个 key 待验证")

    print("[" + now + "] 开始验证 + 余额查询（并发 " + str(MAX_WORKERS) + "）...")

    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(verify_one, item): item for item in items}
        for future in as_completed(futures):
            done += 1
            r = future.result()
            results.append(r)
            masked = r["key"][:12] + "..." + r["key"][-4:]
            status = "OK" if r["chat_ok"] else "FAIL"
            bal_str = ""
            if r["balance"] > 0:
                bal_str = " BAL:" + str(r["balance"]) + " " + r["currency"]
            sys.stdout.write("  " + status + " [" + r["provider"] + "] " + masked + bal_str + "\n")
            sys.stdout.flush()
            if done % 20 == 0:
                valid = sum(1 for x in results if x["chat_ok"])
                with_bal = sum(1 for x in results if x["balance"] > 0)
                print("  ... " + str(done) + "/" + str(len(items)) +
                      " chat_ok:" + str(valid) + " with_balance:" + str(with_bal))

    # 分类
    chat_valid = [r for r in results if r["chat_ok"]]
    chat_fail = [r for r in results if not r["chat_ok"]]
    with_balance = [r for r in chat_valid if r["balance"] > 0]

    # 按余额降序排列
    with_balance.sort(key=lambda x: -x["balance"])

    # 保存完整结果
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = os.path.join(OUTPUT_DIR, "reverify_" + ts + ".json")
    with open(out_json, "w") as f:
        json.dump({"all": results, "chat_valid": chat_valid, "with_balance": with_balance}, f, indent=2, ensure_ascii=False)

    # 保存可用 key 文本
    out_txt = os.path.join(OUTPUT_DIR, "reverify_keys_" + ts + ".txt")
    with open(out_txt, "w") as f:
        for r in with_balance:
            masked = r["key"][:12] + "..." + r["key"][-4:]
            f.write(r["provider"] + "\t" + r["key"] + "\t" + str(r["balance"]) + " " + r["currency"] + "\t" + r["balance_note"] + "\n")
        f.write("\n# 无余额但对话可用:\n")
        no_bal = [r for r in chat_valid if r["balance"] <= 0]
        for r in no_bal:
            masked = r["key"][:12] + "..." + r["key"][-4:]
            f.write(r["provider"] + "\t" + r["key"] + "\tN/A\t" + r["balance_note"] + "\n")

    # 打印报告
    print("")
    print("=" * 60)
    print("验证完成！")
    print("总计: " + str(len(items)))
    print("对话可用: " + str(len(chat_valid)))
    print("对话失败: " + str(len(chat_fail)))
    print("有余额: " + str(len(with_balance)))
    print("")

    if with_balance:
        print("有余额的 key (按余额降序)：")
        for r in with_balance:
            masked = r["key"][:12] + "..." + r["key"][-4:]
            print("  " + r["provider"] + "\t" + masked + "\t" + str(r["balance"]) + " " + r["currency"] + "\t" + r["balance_note"])

    print("")
    print("无余额但对话可用 (" + str(len([r for r in chat_valid if r["balance"] <= 0])) + " 个)：")
    nc = Counter(r["provider"] for r in chat_valid if r["balance"] <= 0)
    for p, c in nc.most_common():
        print("  " + p + ": " + str(c))

    print("")
    print("对话失败 (" + str(len(chat_fail)) + " 个)：")
    fc = Counter(r["provider"] for r in chat_fail)
    for p, c in fc.most_common():
        print("  " + p + ": " + str(c))

    print("")
    print("JSON: " + out_json)
    print("TXT: " + out_txt)


if __name__ == "__main__":
    main()
