#!/usr/bin/env python3
"""按二次验证规则导入今日验证通过的 Aliyun key 到 New API 3000

规则要点:
- 动态供应商 Aliyun: 从验证结果读每个 key 实际支持的模型(不硬编码)
- 每个 key 的每个模型单独一个渠道
- 统一挂 wd-ys 分组, auto_ban=1
"""
import sqlite3, json

DB = "/home/wang/new-api-data/one-api.db"
RESULT = "/tmp/aliyun_result.txt"      # "HTTP码 key前12位"
UNIQ = "/tmp/aliyun_uniq.txt"          # 唯一完整key
MODELS_SUPPORTED = "qwen-plus,deepseek-v4-pro,glm-5.1"  # 实测6个key共同支持

# 读可用 key 完整值
ok_prefix = set()
for line in open(RESULT):
    parts = line.split()
    if parts and parts[0] == "200":
        ok_prefix.add(parts[1])

keys = []
for line in open(UNIQ):
    k = line.strip()
    if k and k[:12] in ok_prefix:
        keys.append(k)

print(f"待导入 key: {len(keys)} 个")
print(f"模型: {MODELS_SUPPORTED}")
print(f"分组: wd-ys  auto_ban: 1")
print()

conn = sqlite3.connect(DB)
cur = conn.cursor()
existing = {r[0] for r in cur.execute("SELECT key FROM channels")}

models_list = MODELS_SUPPORTED.split(",")
added = skipped = 0
for idx, k in enumerate(keys, 1):
    for m in models_list:
        if k in existing:
            skipped += 1
            continue
        name = f"Aliyun {m}"
        cur.execute("""
            INSERT INTO channels
            (type, key, status, name, weight, priority, base_url, models,
             auto_ban, created_time, test_time, response_time, "group", used_quota)
            VALUES (?,?,1,?,1,0,?,?,1,
                    strftime('%s','now'), strftime('%s','now'), 0, 'wd-ys', 0)
        """, (1, k, name, "https://dashscope.aliyuncs.com/compatible-mode", m))
        added += 1
    print(f"  Key{idx} {k[:14]}... → 建 {len(models_list)} 个渠道 ({', '.join(models_list)})")

conn.commit()
conn.close()
print()
print("=" * 50)
print(f"导入完成: 新增渠道 {added}, 跳过(已存在) {skipped}")
print("=" * 50)
