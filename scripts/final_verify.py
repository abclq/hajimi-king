#!/usr/bin/env python3
"""
全量验证131 key + 余额/配额查询，输出完整明文 key
"""
import os, json, sys
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
        "balance_url": "https://dashscope.aliyuncs.com/api/v1/quotas",
        "balance_type": "aliyun"
    },
}

def load_keys():
    with open(INPUT_FILE) as f:
        data = json.load(f)
    return data.get("valid", [])

def test_chat(provider, key):
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
                return True, txt[:80], tok
        return False, "HTTP " + str(r.status_code), 0
    except Exception as e:
        return False, str(e)[:80], 0

def check_balance(provider, key):
    cfg = PROVIDERS.get(provider)
    if not cfg or not cfg.get("balance_url"):
        return None
    try:
        if cfg["balance_type"] == "aliyun":
            r = requests.get(cfg["balance_url"], headers={"Authorization": "Bearer " + key}, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if data.get("success"):
                    total = data.get("output", {}).get("total", 0)
                    return {"type": "aliyun_quotas", "models": total}
            return {"type": "aliyun_quotas", "models": 0, "error": "HTTP " + str(r.status_code)}

        r = requests.get(cfg["balance_url"], headers={"Authorization": "Bearer " + key}, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if cfg["balance_type"] == "deepseek":
                infos = data.get("balance_infos", [])
                if infos:
                    return {"type": "deepseek", "balance": float(infos[0].get("total_balance", 0)),
                            "currency": infos[0].get("currency", "CNY"),
                            "granted": infos[0].get("granted_balance", "0"),
                            "topup": infos[0].get("topped_up_balance", "0")}
                return {"type": "deepseek", "balance": 0, "note": "no_balance_infos"}
            elif cfg["balance_type"] == "openrouter":
                d = data.get("data", {})
                return {"type": "openrouter", "tier": "free" if d.get("is_free_tier") else "paid",
                        "usage": d.get("usage", 0), "limit": d.get("limit"),
                        "expires": d.get("expires_at", "")}
        return {"type": cfg["balance_type"], "error": "HTTP " + str(r.status_code)}
    except Exception as e:
        return {"type": cfg.get("balance_type", "unknown"), "error": str(e)[:80]}

def verify_one(item):
    provider = item["provider"]
    key = item["key"]
    chat_ok, chat_msg, chat_tokens = test_chat(provider, key)
    balance = check_balance(provider, key) if chat_ok else None
    return {"provider": provider, "key": key, "chat_ok": chat_ok,
            "chat_response": chat_msg, "chat_tokens": chat_tokens, "balance": balance}

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    items = load_keys()
    print(f"共 {len(items)} 个 key")
    pc = Counter(i["provider"] for i in items)
    for p, c in pc.most_common():
        print(f"  {p}: {c}")

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(verify_one, item): item for item in items}
        for future in as_completed(futures):
            done += 1
            r = future.result()
            results.append(r)
            status = "OK" if r["chat_ok"] else "FAIL"
            sys.stdout.write(f"  {status} [{r['provider']}] {done}/{len(items)}\n")
            sys.stdout.flush()

    chat_valid = [r for r in results if r["chat_ok"]]
    chat_fail = [r for r in results if not r["chat_ok"]]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save full JSON with plaintext keys
    out_json = os.path.join(OUTPUT_DIR, "final_verify_" + ts + ".json")
    with open(out_json, "w") as f:
        json.dump({"timestamp": ts, "total": len(items), "valid": len(chat_valid), "failed": len(chat_fail),
                    "results": results, "valid_items": chat_valid, "failed_items": chat_fail},
                   f, indent=2, ensure_ascii=False)

    # Save plaintext key list (TSV: provider \t key \t balance_info)
    out_txt = os.path.join(OUTPUT_DIR, "final_keys_" + ts + ".txt")
    with open(out_txt, "w") as f:
        f.write("# provider\tkey\tbalance_info\n")
        for r in chat_valid:
            bal = r["balance"]
            bal_str = ""
            if bal:
                if bal.get("type") == "deepseek":
                    bal_str = f"balance={bal.get('balance',0)} {bal.get('currency','CNY')}"
                elif bal.get("type") == "openrouter":
                    bal_str = f"tier={bal.get('tier','')} usage={bal.get('usage',0)}"
                elif bal.get("type") == "aliyun_quotas":
                    bal_str = f"models={bal.get('models',0)}"
            f.write(f"{r['provider']}\t{r['key']}\t{bal_str}\n")

    print(f"\n{'='*60}")
    print(f"总计: {len(items)}  通过: {len(chat_valid)}  失败: {len(chat_fail)}")
    vc = Counter(r["provider"] for r in chat_valid)
    for p, c in vc.most_common():
        print(f"  {p}: {c}")
    print(f"\nJSON: {out_json}")
    print(f"TXT:  {out_txt}")

if __name__ == "__main__":
    main()
