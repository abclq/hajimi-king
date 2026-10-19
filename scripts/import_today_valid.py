#!/usr/bin/env python3
"""按二次验证规则导入今日有效 key 到 New API 3000"""
import sqlite3, json, subprocess, sys

DB = "/home/wang/new-api-data/one-api.db"
DATA = "/home/wang/hajimi-king-local/data"

# 动态供应商：用实测真实模型，不硬编码
ALIYUN_MODELS = "qwen-plus,deepseek-v4-pro,deepseek-v4-flash,glm-5.1"
NVIDIA_MODELS = "deepseek-ai/deepseek-v4.1-flash"

# 规则: 每个 key 下面的所有模型都单独一个渠道
# 但 wd-ys 统一分组 + auto_ban=1
JOBS = [
    # (文件, 供应商名, type, base_url, models)
    (f"{DATA}/aliyun/keys_valid_20261010.txt", "Aliyun", 1,
     "https://dashscope.aliyuncs.com/compatible-mode", ALIYUN_MODELS),
    (f"{DATA}/nvidia/keys_valid_20261010.txt", "NVIDIA", 27,
     "https://integrate.api.nvidia.com/v1", NVIDIA_MODELS),
]


def read_keys(path):
    ks = []
    with open(path) as f:
        for line in f:
            k = line.strip()
            # 跳过脱敏占位
            if not k or "..." in k or k.endswith("xxxx"):
                continue
            ks.append(k)
    return ks


def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    existing = {r[0] for r in cur.execute("SELECT key FROM channels")}

    added = skipped = 0
    for path, prov, typ, base, models in JOBS:
        ks = read_keys(path)
        print(f"\n[{prov}] 文件 {path.split('/')[-1]}: {len(ks)} 个真 key")
        for idx, k in enumerate(ks, 1):
            if k in existing:
                print(f"  {idx}. {k[:18]}... 已存在，跳过")
                skipped += 1
                continue
            name = f"{prov} {models.split(',')[0]}"
            cur.execute("""
                INSERT INTO channels
                (type, key, status, name, weight, priority, base_url, models,
                 auto_ban, created_time, test_time, response_time, "group", used_quota)
                VALUES (?,?,1,?,1,0,?,?,1,
                        strftime('%s','now'), strftime('%s','now'), 0, 'wd-ys', 0)
            """, (typ, k, name, base, models))
            print(f"  {idx}. {k[:18]}... → 渠道 '{name}' (type={typ})")
            print(f"       models={models}")
            added += 1
        conn.commit()
    conn.close()
    print(f"\n{'='*50}")
    print(f"导入完成: 新增 {added}, 跳过(已存在) {skipped}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
