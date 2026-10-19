#!/usr/bin/env python3
"""
全量验证 + 额度/余额查询（完整版）
无余额的供应商查额度/速率限制，写入结果
"""
import os, json, sys, time
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
    },
    "openrouter": {
        "chat_url": "https://openrouter.ai/api/v1/chat/completions",
        "chat_model": "openai/gpt-4o-mini",
        "balance_url": "https://openrouter.ai/api/v1/auth/key",
    },
    "groq": {
        "chat_url": "https://api.groq.com/openai/v1/chat/completions",
        "chat_model": "llama-3.1-8b-instant",
    },
    "aliyun": {
        "chat_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "chat_model": "qwen-turbo",
        "balance_url": "https://dashscope.aliyuncs.com/api/v1/quotas",
    },
}

def load_keys():
    with open(INPUT_FILE) as f:
        return json.load(f).get("valid", [])

def test_chat(provider, key):
    """真实对话验证，同时捕获 rate limit headers"""
    cfg = PROVIDERS[provider]
    rate_info = {}
    try:
        r = requests.post(cfg["chat_url"],
                          headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                          json={"model": cfg["chat_model"],
                                "messages": [{"role": "user", "content": "Say hi in 3 words"}],
                                "max_tokens": 20},
                          timeout=TIMEOUT)
        # Capture rate limit headers (Groq style)
        for h in r.headers:
            hl = h.lower()
            if "ratelimit" in hl or "rate-limit" in hl:
                rate_info[hl] = r.headers[h]
        if r.status_code == 200:
            data = r.json()
            if data.get("choices"):
                txt = data["choices"][0].get("message", {}).get("content", "")
                tok = data.get("usage", {}).get("total_tokens", 0)
                return True, txt[:80], tok, rate_info
        return False, "HTTP " + str(r.status_code), 0, rate_info
    except Exception as e:
        return False, str(e)[:80], 0, rate_info

def check_extra(provider, key):
    """查余额或额度"""
    cfg = PROVIDERS[provider]
    try:
        if provider == "deepseek":
            r = requests.get(cfg["balance_url"], headers={"Authorization": "Bearer " + key}, timeout=TIMEOUT)
            if r.status_code == 200:
                infos = r.json().get("balance_infos", [])
                if infos:
                    return {"type": "balance", "balance": float(infos[0].get("total_balance", 0)),
                            "currency": infos[0].get("currency", "CNY"),
                            "granted": infos[0].get("granted_balance", "0"),
                            "topup": infos[0].get("topped_up_balance", "0")}
            return {"type": "balance", "balance": 0, "error": "query_failed"}

        elif provider == "openrouter":
            r = requests.get(cfg["balance_url"], headers={"Authorization": "Bearer " + key}, timeout=TIMEOUT)
            if r.status_code == 200:
                d = r.json().get("data", {})
                return {"type": "account", "tier": "free" if d.get("is_free_tier") else "paid",
                        "usage": d.get("usage", 0), "limit": d.get("limit"),
                        "expires": d.get("expires_at", "")}
            return {"type": "account", "error": "query_failed"}

        elif provider == "aliyun":
            r = requests.get(cfg["balance_url"], headers={"Authorization": "Bearer " + key}, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if data.get("success"):
                    quotas = data.get("output", {}).get("quotas", [])
                    total = data.get("output", {}).get("total", 0)
                    # Extract top models with their limits
                    top_models = []
                    for q in quotas[:5]:
                        ml = q.get("model_limit", {})
                        top_models.append({
                            "model": q.get("model", ""),
                            "req_limit": ml.get("request_limit", 0),
                            "req_period_h": ml.get("request_limit_period", 0),
                            "token_limit": ml.get("usage_limit"),
                            "token_period_h": ml.get("usage_limit_period", 0),
                        })
                    return {"type": "quotas", "total_models": total, "top_models": top_models}
            return {"type": "quotas", "total_models": 0, "error": "query_failed"}

        elif provider == "groq":
            # No balance/quotas API - rate limits come from chat response headers
            return {"type": "rate_limit", "note": "see_chat_headers"}

    except Exception as e:
        return {"type": "error", "error": str(e)[:80]}
    return None

def format_quota_str(provider, extra, rate_info):
    """格式化额度/余额为可读字符串"""
    if not extra:
        return ""
    if extra.get("type") == "balance":
        return f"{extra.get('balance',0)} {extra.get('currency','CNY')}"
    elif extra.get("type") == "account":
        return f"tier={extra.get('tier','')} usage={extra.get('usage',0)} expires={str(extra.get('expires',''))[:10]}"
    elif extra.get("type") == "quotas":
        return f"models={extra.get('total_models',0)}"
    elif extra.get("type") == "rate_limit":
        # Parse groq rate limits from headers
        limit_req = rate_info.get("x-ratelimit-limit-requests", "")
        limit_tok = rate_info.get("x-ratelimit-limit-tokens", "")
        parts = []
        if limit_req:
            parts.append(f"RPM={limit_req}")
        if limit_tok:
            parts.append(f"TPM={limit_tok}")
        return " ".join(parts) if parts else ""
    return ""

def verify_one(item):
    provider = item["provider"]
    key = item["key"]
    chat_ok, chat_msg, chat_tokens, rate_info = test_chat(provider, key)
    extra = check_extra(provider, key) if chat_ok else None
    quota_str = format_quota_str(provider, extra, rate_info) if extra else ""
    return {
        "provider": provider, "key": key, "chat_ok": chat_ok,
        "chat_response": chat_msg, "chat_tokens": chat_tokens,
        "extra": extra, "quota_str": quota_str, "rate_info": rate_info
    }

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
            q = f" {r['quota_str']}" if r["quota_str"] else ""
            sys.stdout.write(f"  {status} [{r['provider']}] {done}/{len(items)}{q}\n")
            sys.stdout.flush()

    chat_valid = [r for r in results if r["chat_ok"]]
    chat_fail = [r for r in results if not r["chat_ok"]]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = os.path.join(OUTPUT_DIR, "full_verify_" + ts + ".json")
    with open(out_json, "w") as f:
        json.dump({"timestamp": ts, "total": len(items), "valid": len(chat_valid), "failed": len(chat_fail),
                    "results": results, "valid_items": chat_valid, "failed_items": chat_fail},
                   f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"总计: {len(items)}  通过: {len(chat_valid)}  失败: {len(chat_fail)}")
    vc = Counter(r["provider"] for r in chat_valid)
    for p, c in vc.most_common():
        print(f"  {p}: {c}")
    print(f"\nJSON: {out_json}")

if __name__ == "__main__":
    main()
