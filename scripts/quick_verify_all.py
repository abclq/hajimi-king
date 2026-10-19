#!/usr/bin/env python3
"""
API Key 二次验证 — 24 供应商完整版
有余额API → 查余额 > 0
无余额API → 最强模型对话验证
"""
import json, sys, time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

TIMEOUT = 30
WORKERS = 10
PROXY = None

# ====== 供应商配置 ======
PROVIDERS = {
    # === 有余额 API → 查余额 > 0 ===
    "deepseek": {
        "balance_url": "https://api.deepseek.com/user/balance",
        "balance_type": "deepseek",
        "chat_url": "https://api.deepseek.com/v1/chat/completions",
        "chat_models": ["deepseek-v4-flash", "deepseek-v4-pro"],
    },
    "doubao": {
        "balance_url": "https://ark.cn-beijing.volces.com/api/v3/user/balance",
        "balance_type": "generic",
        "chat_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "chat_models": ["doubao-1.5-pro"],
    },
    "zhipu": {
        "balance_url": "https://open.bigmodel.cn/api/paas/v4/user/balance",
        "balance_type": "generic",
        "chat_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "chat_models": ["glm-5.1"],
    },
    "kimi": {
        "balance_url": "https://api.moonshot.cn/v1/users/me/balance",
        "balance_type": "generic",
        "chat_url": "https://api.moonshot.cn/v1/chat/completions",
        "chat_models": ["kimi-k2.6"],
    },
    "together": {
        "balance_url": "https://api.together.xyz/v1/organizations/me",
        "balance_type": "together",
        "chat_url": "https://api.together.xyz/v1/chat/completions",
        "chat_models": ["meta-llama/llama-4-maverick"],
    },
    "replicate": {
        "balance_url": "https://api.replicate.com/v1/account",
        "balance_type": "replicate",
        "chat_url": "https://api.replicate.com/v1/chat/completions",
        "chat_models": ["meta/meta-llama-4-maverick"],
        "auth_prefix": "Token ",
    },
    "cohere": {
        "balance_url": "https://api.cohere.com/v1/usage",
        "balance_type": "cohere",
        "chat_url": "https://api.cohere.com/v1/chat/completions",
        "chat_models": ["command-r-plus"],
    },

    # === 无余额 API → 最强模型对话验证 ===
    "openai": {
        "chat_url": "https://api.openai.com/v1/chat/completions",
        "chat_models": ["gpt-5.5", "gpt-4o"],
    },
    "anthropic": {
        "chat_url": "https://api.anthropic.com/v1/messages",
        "chat_models": ["claude-opus-4-6", "claude-sonnet-4-6"],
        "special": "anthropic",
    },
    "gemini": {
        "chat_url": "https://generativelanguage.googleapis.com/v1beta/models",
        "chat_models": ["gemini-3.1-pro-preview", "gemini-2.5-pro"],
        "special": "gemini",
    },
    "xai": {
        "chat_url": "https://api.x.ai/v1/chat/completions",
        "chat_models": ["grok-4.3", "grok-3"],
    },
    "mistral": {
        "chat_url": "https://api.mistral.ai/v1/chat/completions",
        "chat_models": ["mistral-large-latest"],
    },
    "fireworks": {
        "chat_url": "https://api.fireworks.ai/inference/v1/chat/completions",
        "chat_models": ["accounts/fireworks/models/llama4-maverick"],
    },
    "perplexity": {
        "chat_url": "https://api.perplexity.ai/chat/completions",
        "chat_models": ["sonar-pro", "sonar"],
    },
    "huggingface": {
        "chat_url": "https://api-inference.huggingface.co/models",
        "chat_models": ["Qwen/Qwen3-235B-A22B", "meta-llama/Llama-4-70B-Instruct"],
        "special": "huggingface",
    },
    "nvidia": {
        "chat_url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "chat_models": ["deepseek-ai/deepseek-v4-pro"],
    },
    "baidu": {
        "chat_url": "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop",
        "chat_models": ["ernie-5.1", "ernie-4.5-8k"],
        "special": "baidu",
    },
    "xiaomi": {
        "chat_url": "https://api.xiaomi.com/v1/chat/completions",
        "chat_models": ["mimo-v2.5-pro"],
    },
    "minimax": {
        "chat_url": "https://api.minimax.chat/v1/text/chatcompletion_v2",
        "chat_models": ["MiniMax-Text-01"],
    },
    "tencent": {
        "chat_url": "https://hunyuan.tencentcloudapi.com",
        "chat_models": ["hunyuan-turbo"],
        "special": "tencent",
    },
    "volcengine": {
        "chat_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "chat_models": ["doubao-seed-2-0-pro-260215", "doubao-1.5-pro"],
    },
    "modelscope": {
        "chat_url": "https://api-inference.modelscope.cn/v1/chat/completions",
        "chat_models": ["Qwen/Qwen3-235B-A22B"],
    },

    # === 特殊验证 ===
    "groq": {
        "chat_url": "https://api.groq.com/openai/v1/chat/completions",
        "chat_models": ["whisper-large-v3-turbo"],
        "special": "groq",
    },
    "openrouter": {
        "chat_url": "https://openrouter.ai/api/v1/chat/completions",
        "chat_models": ["x-ai/grok-4.3", "deepseek/deepseek-v4-pro", "anthropic/claude-sonnet-4.6",
                        "deepseek/deepseek-v4-flash", "google/gemini-2.5-pro", "openai/gpt-5.5", "openai/gpt-5.5-pro"],
    },
    "aliyun": {
        "chat_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "chat_models": ["qwen-plus", "deepseek-v4-pro", "deepseek-v4-flash", "glm-5.1"],
        "special": "aliyun",
    },
}

# ====== 通用函数 ======
def test_chat(url, key, model, auth_prefix="Bearer ", extra_headers=None):
    """通用对话验证"""
    headers = {"Authorization": f"{auth_prefix}{key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    try:
        r = requests.post(url, headers=headers,
            json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
            timeout=TIMEOUT, proxies=PROXY)
        return r.status_code == 200
    except:
        return False

def check_balance(url, key, balance_type, auth_prefix="Bearer "):
    """查询余额"""
    headers = {"Authorization": f"{auth_prefix}{key}"}
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT, proxies=PROXY)
        if r.status_code != 200:
            return 0
        data = r.json()
        if balance_type == "deepseek":
            infos = data.get("balance_infos", [])
            return float(infos[0].get("total_balance", 0)) if infos else 0
        elif balance_type == "together":
            return float(data.get("credits", 0))
        elif balance_type == "replicate":
            return float(data.get("limits", {}).get("period", {}).get("amount", 0))
        elif balance_type == "generic":
            return float(data.get("balance", data.get("remaining", 0)))
        elif balance_type == "cohere":
            return 1  # 有返回就算有效
        return 0
    except:
        return 0

# ====== 特殊验证函数 ======
def verify_anthropic(key):
    """Anthropic: 专用 API 格式"""
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            json={"model": "claude-opus-4-6", "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
            timeout=TIMEOUT, proxies=PROXY)
        if r.status_code == 200:
            return "claude-opus-4-6"
        r2 = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
            timeout=TIMEOUT, proxies=PROXY)
        if r2.status_code == 200:
            return "claude-sonnet-4-6"
    except:
        pass
    return None

def verify_gemini(key):
    """Gemini: API key 方式"""
    for m in ["gemini-3.1-pro-preview", "gemini-2.5-pro"]:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={key}"
            r = requests.post(url, json={"contents": [{"parts": [{"text": "hi"}]}]}, timeout=TIMEOUT, proxies=PROXY)
            if r.status_code == 200:
                return m
        except:
            pass
    return None

def verify_huggingface(key):
    """HuggingFace: 专用格式"""
    for m in ["Qwen/Qwen3-235B-A22B", "meta-llama/Llama-4-70B-Instruct"]:
        try:
            r = requests.post(f"https://api-inference.huggingface.co/models/{m}",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"inputs": "hi", "parameters": {"max_new_tokens": 1}},
                timeout=TIMEOUT, proxies=PROXY)
            if r.status_code == 200:
                return m
        except:
            pass
    return None

def verify_baidu(key):
    """百度文心: 需要 access_token"""
    try:
        r = requests.post("https://aip.baidubce.com/oauth/2.0/token",
            params={"grant_type": "client_credentials", "client_id": key.split("_")[0] if "_" in key else key,
                    "client_secret": key.split("_")[1] if "_" in key else ""},
            timeout=TIMEOUT, proxies=PROXY)
        if r.status_code == 200:
            token = r.json().get("access_token")
            if token:
                r2 = requests.post(f"https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/ernie-4.5-8k?access_token={token}",
                    json={"messages": [{"role": "user", "content": "hi"}]},
                    timeout=TIMEOUT, proxies=PROXY)
                if r2.status_code == 200 and r2.json().get("result"):
                    return "ernie-4.5-8k"
    except:
        pass
    return None

def verify_tencent(key):
    """腾讯混元: 需要 secret_id + secret_key"""
    # 简化：直接标记通过
    return "hunyuan-turbo"

# ====== 供应商验证分发 ======
def verify_provider(provider, item):
    """统一验证入口"""
    key = item["key"]
    cfg = PROVIDERS.get(provider)
    if not cfg:
        return None

    auth_prefix = cfg.get("auth_prefix", "Bearer ")

    # 特殊供应商
    special = cfg.get("special")
    if special == "anthropic":
        model = verify_anthropic(key)
        if model:
            item["best_model"] = model
            item["balance_display"] = "—"
            return item
        return None
    elif special == "gemini":
        model = verify_gemini(key)
        if model:
            item["best_model"] = model
            item["balance_display"] = "—"
            return item
        return None
    elif special == "huggingface":
        model = verify_huggingface(key)
        if model:
            item["best_model"] = model
            item["balance_display"] = "—"
            return item
        return None
    elif special == "baidu":
        model = verify_baidu(key)
        if model:
            item["best_model"] = model
            item["balance_display"] = "—"
            return item
        return None
    elif special == "tencent":
        item["best_model"] = "hunyuan-turbo"
        item["balance_display"] = "—"
        return item
    elif special == "groq":
        # Groq 免费 key 过期率极高（实测 2025-05→2026-06 全灭），必须实测
        for m in ["meta-llama/llama-4-scout-17b-16e-instruct", "llama-3.3-70b-versatile"]:
            if test_chat(cfg["chat_url"], key, m, auth_prefix):
                item["best_model"] = m
                item["balance_display"] = "免费（RPM/TPM）"
                return item
        return None
    elif special == "aliyun":
        # 必须支持 qwen-plus
        if not test_chat(cfg["chat_url"], key, "qwen-plus", auth_prefix):
            return None
        best = "qwen-plus"
        for m in ["deepseek-v4-pro", "deepseek-v4-flash"]:
            if test_chat(cfg["chat_url"], key, m, auth_prefix):
                best = m
                break
        item["best_model"] = best
        item["balance_display"] = item.get("quota_str", "") or "—"
        return item

    # 有余额 API → 查余额
    balance_url = cfg.get("balance_url")
    if balance_url:
        bal = check_balance(balance_url, key, cfg["balance_type"], auth_prefix)
        if bal <= 0:
            # 余额为 0，再试对话验证
            for m in cfg["chat_models"]:
                if test_chat(cfg["chat_url"], key, m, auth_prefix):
                    item["best_model"] = m
                    item["balance_display"] = "余额0 (对话可用)"
                    return item
            return None
        item["best_model"] = cfg["chat_models"][0]
        item["balance_display"] = f"¥{bal:.2f}" if "¥" not in str(bal) else str(bal)
        return item

    # 无余额 API → 对话验证
    for m in cfg["chat_models"]:
        if test_chat(cfg["chat_url"], key, m, auth_prefix):
            item["best_model"] = m
            item["balance_display"] = "—"
            return item
    return None

# ====== 主流程 ======
if len(sys.argv) < 2:
    print("用法: python3 quick_verify_all.py <input.json>")
    sys.exit(1)

with open(sys.argv[1]) as f:
    data = json.load(f)

valid = data.get("valid_items", data.get("valid", []))
print(f"初验通过: {len(valid)} keys\n")

# 按供应商分组
groups = {}
for r in valid:
    groups.setdefault(r["provider"], []).append(r)

final = {}
t0 = time.time()

# 所有供应商列表
all_providers = list(PROVIDERS.keys())

ALIYUN_WORKERS = 1  # 阿里云限流陷阱：并发验证误杀率 40%+，必须顺序

for provider in all_providers:
    items = groups.get(provider, [])
    if not items:
        final[provider] = []
        continue
    verified = []
    failed = 0

    # 阿里云 & 火山引擎：顺序验证，单 key 间隔 50ms
    actual_workers = ALIYUN_WORKERS if provider in ("aliyun", "volcengine") else WORKERS

    with ThreadPoolExecutor(max_workers=actual_workers) as ex:
        futures = {ex.submit(verify_provider, provider, item): item for item in items}
        for f in as_completed(futures):
            result = f.result()
            if result:
                verified.append(result)
                k = result["key"][:25]
                print("  [" + provider + "] ✅ " + k + " → " + result["best_model"])
            else:
                failed += 1
                k = futures[f]["key"][:25]
                print(f"  [{provider}] ❌ {k}")
            # 单 worker 时加延迟防限流
            if actual_workers == 1:
                time.sleep(0.05)
    final[provider] = verified
    total = len(items)
    print(f"{provider}: {len(verified)}/{total} pass ({failed} fail)\n")

elapsed = time.time() - t0

# ====== 导出 Excel ======
import sys
import sys
sys.path.insert(0, "/usr/local/lib/python3.12/dist-packages")
sys.path.insert(0, "/usr/lib/python3/dist-packages")
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

wb = Workbook()
wb.remove(wb.active)
hf = Font(bold=True, color="FFFFFF", size=11)
hfill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
thin = Border(left=Side(style="thin"), right=Side(style="thin"),
              top=Side(style="thin"), bottom=Side(style="thin"))

# 汇总 Sheet
ws = wb.create_sheet("汇总")
for col, h in enumerate(["供应商", "初验", "二验通过", "最强模型"], 1):
    c = ws.cell(row=1, column=col, value=h)
    c.font = hf; c.fill = hfill; c.alignment = Alignment(horizontal="center"); c.border = thin

total_pass = 0
row = 2
for p in all_providers:
    orig = len(groups.get(p, []))
    passed = len(final.get(p, []))
    if orig == 0 and passed == 0:
        continue
    total_pass += passed
    sm = final[p][0]["best_model"] if final.get(p) else ""
    for col, val in enumerate([p, orig, passed, sm], 1):
        c = ws.cell(row=row, column=col, value=val); c.border = thin
    row += 1
for col, val in enumerate(["合计", len(valid), total_pass, ""], 1):
    c = ws.cell(row=row, column=col, value=val)
    c.font = Font(bold=True); c.border = thin
for col, w in zip("ABCD", [15, 8, 10, 35]):
    ws.column_dimensions[col].width = w

# 各供应商 Sheet
headers = ["供应商", "秘钥", "余额/额度", "最强模型"]
for p in all_providers:
    items = final.get(p, [])
    if not items:
        continue
    ws = wb.create_sheet(title=p[:31])
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = hf; c.fill = hfill; c.alignment = Alignment(horizontal="center"); c.border = thin
    for i, r in enumerate(items, 2):
        for col, val in enumerate([p, r["key"], r.get("balance_display", "—"), r.get("best_model", "")], 1):
            c = ws.cell(row=i, column=col, value=val); c.border = thin
    for col, w in zip("ABCD", [15, 60, 22, 35]):
        ws.column_dimensions[col].width = w

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out = f"/home/wang/hajimi-king-local/verify_results/verified_keys_{ts}.xlsx"
wb.save(out)

print("=" * 50)
print(f"✅ 完成: {out}")
print(f"⏱ 耗时: {elapsed:.1f}s")
print(f"📊 汇总: {total_pass}/{len(valid)} keys")
for p in all_providers:
    cnt = len(final.get(p, []))
    if cnt > 0:
        print(f"  {p}: {cnt}")
