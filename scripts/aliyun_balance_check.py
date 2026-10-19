#!/usr/bin/env python3
"""
阿里云 DashScope Key 配额查询
从 verify_result 读取有效 aliyun key，查询 /api/v1/quotas 获取配额信息
"""
import os, json, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import requests

INPUT_FILE = "/root/verify_results/verify_result_20260527_083004.json"
OUTPUT_DIR = "/root/verify_results"
MAX_WORKERS = 10
TIMEOUT = 15

def load_aliyun_keys():
    with open(INPUT_FILE) as f:
        data = json.load(f)
    return [item["key"] for item in data.get("valid", []) if item["provider"] == "aliyun"]

def check_quotas(key):
    """查询 aliyun key 的配额"""
    try:
        r = requests.get("https://dashscope.aliyuncs.com/api/v1/quotas",
                         headers={"Authorization": "Bearer " + key},
                         timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if data.get("success"):
                quotas = data.get("output", {}).get("quotas", [])
                total = data.get("output", {}).get("total", 0)
                # Summarize: list models and their limits
                models = []
                for q in quotas:
                    model = q.get("model", "")
                    ml = q.get("model_limit", {})
                    req_limit = ml.get("request_limit", 0)
                    req_period = ml.get("request_limit_period", 0)
                    usage_limit = ml.get("usage_limit")
                    usage_field = ml.get("usage_limit_field", "")
                    usage_period = ml.get("usage_limit_period", 0)
                    models.append({
                        "model": model,
                        "request_limit": req_limit,
                        "request_period_hours": req_period,
                        "usage_limit": usage_limit,
                        "usage_field": usage_field,
                        "usage_period_hours": usage_period,
                    })
                return True, total, models
        return False, 0, []
    except Exception as e:
        return False, 0, str(e)[:100]

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    keys = load_aliyun_keys()
    print(f"共 {len(keys)} 个 aliyun key 待查询配额")

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_quotas, k): k for k in keys}
        for future in as_completed(futures):
            done += 1
            key = futures[future]
            ok, total, models = future.result()
            masked = key[:10] + "..." + key[-4:]
            if ok:
                model_names = [m["model"] for m in models[:5]]
                print(f"  OK {masked} models:{total} ({', '.join(model_names)}...)")
                results.append({"key": key, "ok": True, "total_models": total, "models": models})
            else:
                print(f"  FAIL {masked}")
                results.append({"key": key, "ok": False, "total_models": 0, "models": []})

    valid = [r for r in results if r["ok"]]
    print(f"\n{'='*60}")
    print(f"查询完成: {len(valid)}/{len(keys)} 成功")

    # Collect all unique models across all keys
    all_models = set()
    for r in valid:
        for m in r["models"]:
            all_models.add(m["model"])
    print(f"可用模型总数: {len(all_models)}")

    # Save full results
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = os.path.join(OUTPUT_DIR, "aliyun_quotas_" + ts + ".json")
    with open(out_file, "w") as f:
        json.dump({"total_keys": len(keys), "valid_keys": len(valid), "results": results}, f, indent=2, ensure_ascii=False)
    print(f"结果: {out_file}")

    # Summary: which keys have the most models/quota
    valid.sort(key=lambda x: -x["total_models"])
    print(f"\nTop 10 key（按模型数排序）:")
    for r in valid[:10]:
        masked = r["key"][:10] + "..." + r["key"][-4:]
        print(f"  {masked} — {r['total_models']} models")

if __name__ == "__main__":
    main()
