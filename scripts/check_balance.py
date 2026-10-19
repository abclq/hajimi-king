#!/usr/bin/env python3
"""
对已验证有效的 key 查余额，按余额排序
"""
import os, json, time, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import requests

VALID_FILE = "/root/verify_results/valid_keys_20260527_083004.txt"
OUTPUT_DIR = "/root/verify_results"
MAX_WORKERS = 15
TIMEOUT = 15


def load_keys():
    keys = []
    with open(VALID_FILE) as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= 2:
                keys.append({"provider": parts[0], "key": parts[1]})
    return keys


def check_balance(item):
    provider = item["provider"]
    key = item["key"]

    # DeepSeek balance
    if provider == "deepseek":
        try:
            r = requests.get("https://api.deepseek.com/user/balance",
                             headers={"Authorization": "Bearer " + key}, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                bal = data.get("balance", {}).get("total_balance", 0)
                return provider, key, float(bal), "CNY", ""
        except Exception as e:
            return provider, key, 0, "", str(e)[:80]
        return provider, key, 0, "", "failed"

    # OpenRouter balance
    if provider == "openrouter":
        try:
            r = requests.get("https://openrouter.ai/api/v1/auth/key",
                             headers={"Authorization": "Bearer " + key}, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                limit = data.get("data", {}).get("limit")
                usage = data.get("data", {}).get("usage", 0)
                remaining = (float(limit) - float(usage)) if limit else -1
                label = data.get("data", {}).get("label", "")
                return provider, key, remaining, "USD", "label:" + label
        except Exception as e:
            return provider, key, 0, "", str(e)[:80]
        return provider, key, 0, "", "failed"

    # Groq - no direct balance API, just report valid
    if provider == "groq":
        return provider, key, -1, "", "valid_no_balance_api"

    # Aliyun - try to get balance
    if provider == "aliyun":
        try:
            # Try models endpoint to confirm validity, no easy balance API
            r = requests.post("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                              headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                              json={"model": "qwen-turbo", "messages": [{"role": "user", "content": "1+1"}], "max_tokens": 5},
                              timeout=TIMEOUT)
            if r.status_code == 200:
                return provider, key, -1, "", "valid_no_balance_api"
        except:
            pass
        return provider, key, 0, "", "failed"

    return provider, key, 0, "", "unknown_provider"


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    keys = load_keys()
    print("[" + datetime.now().strftime("%H:%M:%S") + "] 加载 " + str(len(keys)) + " 个有效 key，开始查余额...")

    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_balance, item): item for item in keys}
        for future in as_completed(futures):
            done += 1
            provider, key, balance, currency, note = future.result()
            masked = key[:10] + "..." + key[-4:] if len(key) > 14 else key[:6] + "..."
            results.append({
                "provider": provider, "key": key,
                "balance": balance, "currency": currency, "note": note
            })
            if balance > 0:
                sys.stdout.write("  " + provider + " " + masked + " -> " + str(balance) + " " + currency + " " + note + "\n")
            if done % 20 == 0:
                print("  ... " + str(done) + "/" + str(len(keys)))

    # 排序：有余额的排前面，按余额降序
    results.sort(key=lambda x: (-x["balance"], x["provider"]))

    # 保存结果
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = os.path.join(OUTPUT_DIR, "balance_result_" + ts + ".json")
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    txt_file = os.path.join(OUTPUT_DIR, "balance_result_" + ts + ".txt")
    with open(txt_file, "w") as f:
        for r in results:
            bal_str = str(r["balance"]) + " " + r["currency"] if r["balance"] >= 0 else "N/A"
            masked = r["key"][:10] + "..." + r["key"][-4:] if len(r["key"]) > 14 else r["key"][:6] + "..."
            f.write(r["provider"] + "\t" + masked + "\t" + bal_str + "\t" + r["note"] + "\n")

    # 统计
    print("")
    print("=" * 60)
    print("余额查询完成！")
    print("")

    # 有余额的
    with_balance = [r for r in results if r["balance"] > 0]
    no_balance = [r for r in results if r["balance"] <= 0]

    print("有余额的 key (" + str(len(with_balance)) + " 个)：")
    for r in with_balance:
        masked = r["key"][:10] + "..." + r["key"][-4:]
        print("  " + r["provider"] + "\t" + masked + "\t" + str(r["balance"]) + " " + r["currency"])

    print("")
    print("无余额/无 API 的 key (" + str(len(no_balance)) + " 个)：")
    from collections import Counter
    nc = Counter(r["provider"] for r in no_balance)
    for p, c in nc.most_common():
        print("  " + p + ": " + str(c))

    print("")
    print("结果: " + out_file)
    print("文本: " + txt_file)


if __name__ == "__main__":
    main()
