#!/usr/bin/env python3
"""
一键扫描：一次验证 → 二次验证 → 导出 Excel
"""
import subprocess, sys, os, json, glob, time
from datetime import datetime

WORKDIR = "/root/hajimi-king"
VENV = f"{WORKDIR}/.venv/bin/activate"
SCRIPT1 = f"{WORKDIR}/hajimi-king.py"
SCRIPT2 = "/root/quick_verify_all.py"
RESULT_DIR = "/root/verify_results"
os.makedirs(RESULT_DIR, exist_ok=True)

ts = datetime.now().strftime("%Y%m%d_%H%M%S")

print("=" * 60)
print(f"🚀 开始全量扫描 {ts}")
print("=" * 60)

# ====== 第一步：一次验证 ======
print("\n📡 第一步：hajimi-king 一次验证...")
t1 = time.time()
cmd1 = f"source {VENV} && cd {WORKDIR} && python3 {SCRIPT1}"
r1 = subprocess.run(cmd1, shell=True, executable="/bin/bash", timeout=3600)
t1_done = time.time() - t1
print(f"⏱ 一次验证耗时: {t1_done:.0f}s")

if r1.returncode != 0:
    print(f"⚠️ 一次验证退出码: {r1.returncode}，继续查找结果文件...")

# ====== 找到一次验证的输出 JSON ======
jsons = sorted(glob.glob(f"{RESULT_DIR}/verify_result_*.json") + 
               glob.glob(f"{RESULT_DIR}/*valid*.json") +
               glob.glob(f"{WORKDIR}/verify_result*.json"),
               key=os.path.getmtime, reverse=True)

if not jsons:
    # 也检查 hajimi-king 输出目录
    jsons = sorted(glob.glob(f"{WORKDIR}/output/*.json"), key=os.path.getmtime, reverse=True)

if not jsons:
    print("❌ 找不到一次验证结果 JSON，退出")
    sys.exit(1)

input_json = jsons[0]
print(f"📄 使用结果: {input_json}")

# 检查 JSON 内容
with open(input_json) as f:
    data = json.load(f)
valid_count = len(data.get("valid_items", data.get("valid", [])))
print(f"📊 一次验证通过: {valid_count} keys")

if valid_count == 0:
    print("⚠️ 无有效 key，跳过二次验证")
    sys.exit(0)

# ====== 第二步：二次验证 ======
print(f"\n🔍 第二步：24 供应商二次验证...")
t2 = time.time()
cmd2 = f"source {VENV} && python3 {SCRIPT2} {input_json}"
r2 = subprocess.run(cmd2, shell=True, executable="/bin/bash", timeout=1800)
t2_done = time.time() - t2
print(f"⏱ 二次验证耗时: {t2_done:.0f}s")

# ====== 找到二次验证的 Excel ======
excels = sorted(glob.glob(f"{RESULT_DIR}/verified_keys_*.xlsx"), key=os.path.getmtime, reverse=True)
if excels:
    print(f"\n✅ 最终 Excel: {excels[0]}")
else:
    print("\n⚠️ 未找到导出 Excel")

total_time = t1_done + t2_done
print("\n" + "=" * 60)
print(f"🏁 全量扫描完成")
print(f"⏱ 总耗时: {total_time/60:.1f} 分钟")
print(f"📊 一次通过: {valid_count} → 二次验证完成")
print("=" * 60)
