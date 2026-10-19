"""
Hajimi-King 多供应商扩展版本
支持: Google (Gemini), NVIDIA, ModelScope (魔搭), Xiaomi (小米)
"""

import os
import random
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Union, Any

import requests
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

from common.Logger import logger

sys.path.append('../')
from common.config import Config
from utils.github_client import GitHubClient
from utils.file_manager import file_manager, Checkpoint, checkpoint
from utils.sync_utils import sync_utils

# 创建GitHub工具实例和文件管理器
github_utils = GitHubClient.create_instance(Config.GITHUB_TOKENS)

# 统计信息
skip_stats = {
    "time_filter": 0,
    "sha_duplicate": 0,
    "age_filter": 0,
    "doc_filter": 0
}

# ============ 供应商配置 ============
PROVIDERS = {
    "gemini": {
        "name": "Google Gemini",
        "prefix": "AIzaSy",
        "pattern": r'(AIzaSy[A-Za-z0-9\-_]{33})',
        "enabled": True,
    },
    "nvidia": {
        "name": "NVIDIA",
        "prefix": "nvapi-",
        "pattern": r'(nvapi-[a-zA-Z0-9\-_]{40,})',  # NVIDIA keys typically 40+ chars
        "enabled": True,
    },
    "modelscope": {
        "name": "ModelScope (魔搭)",
        "prefix": "ms-",
        "pattern": r'(ms-[a-zA-Z0-9]{32,})',  # ModelScope format
        "enabled": True,
    },
    "xiaomi": {
        "name": "Xiaomi (小米)",
        "prefix": "tp-",
        "pattern": r'(tp-[a-zA-Z0-9\-_]{20,})',  # Xiaomi tp- prefix
        "enabled": True,
    }
}


def normalize_query(query: str) -> str:
    query = " ".join(query.split())

    parts = []
    i = 0
    while i < len(query):
        if query[i] == '"':
            end_quote = query.find('"', i + 1)
            if end_quote != -1:
                parts.append(query[i:end_quote + 1])
                i = end_quote + 1
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

    quoted_strings = []
    language_parts = []
    filename_parts = []
    path_parts = []
    other_parts = []

    for part in parts:
        if part.startswith('"') and part.endswith('"'):
            quoted_strings.append(part)
        elif part.startswith('language:'):
            language_parts.append(part)
        elif part.startswith('filename:'):
            filename_parts.append(part)
        elif part.startswith('path:'):
            path_parts.append(part)
        elif part.strip():
            other_parts.append(part)

    normalized_parts = []
    normalized_parts.extend(sorted(quoted_strings))
    normalized_parts.extend(sorted(other_parts))
    normalized_parts.extend(sorted(language_parts))
    normalized_parts.extend(sorted(filename_parts))
    normalized_parts.extend(sorted(path_parts))

    return " ".join(normalized_parts)


def extract_keys_from_content(content: str) -> Dict[str, List[str]]:
    """
    从内容中提取所有供应商的 API Key
    
    Returns:
        Dict[str, List[str]]: {provider: [keys]}
    """
    results = {}
    for provider_id, config in PROVIDERS.items():
        if not config["enabled"]:
            continue
        pattern = config["pattern"]
        keys = re.findall(pattern, content)
        if keys:
            results[provider_id] = keys
    return results


def should_skip_item(item: Dict[str, Any], checkpoint: Checkpoint) -> tuple[bool, str]:
    """
    检查是否应该跳过处理此item
    
    Returns:
        tuple: (should_skip, reason)
    """
    # 检查增量扫描时间
    if checkpoint.last_scan_time:
        try:
            last_scan_dt = datetime.fromisoformat(checkpoint.last_scan_time)
            repo_pushed_at = item["repository"].get("pushed_at")
            if repo_pushed_at:
                repo_pushed_dt = datetime.strptime(repo_pushed_at, "%Y-%m-%dT%H:%M:%SZ")
                if repo_pushed_dt <= last_scan_dt:
                    skip_stats["time_filter"] += 1
                    return True, "time_filter"
        except Exception as e:
            pass

    # 检查SHA是否已扫描
    if item.get("sha") in checkpoint.scanned_shas:
        skip_stats["sha_duplicate"] += 1
        return True, "sha_duplicate"

    # 检查仓库年龄
    repo_pushed_at = item["repository"].get("pushed_at")
    if repo_pushed_at:
        repo_pushed_dt = datetime.strptime(repo_pushed_at, "%Y-%m-%dT%H:%M:%SZ")
        if repo_pushed_dt < datetime.utcnow() - timedelta(days=Config.DATE_RANGE_DAYS):
            skip_stats["age_filter"] += 1
            return True, "age_filter"

    # 检查文档和示例文件
    lowercase_path = item["path"].lower()
    if any(token in lowercase_path for token in Config.FILE_PATH_BLACKLIST):
        skip_stats["doc_filter"] += 1
        return True, "doc_filter"

    return False, ""


def filter_placeholder_keys(keys: List[str], content: str) -> List[str]:
    """过滤占位符密钥"""
    filtered = []
    for key in keys:
        context_index = content.find(key)
        if context_index != -1:
            snippet = content[context_index:context_index + 45]
            if "..." in snippet or "YOUR_" in snippet.upper():
                continue
        filtered.append(key)
    return list(set(filtered))


# ============ 验证函数 ============

def validate_gemini_key(api_key: str) -> Union[bool, str]:
    """验证 Google Gemini API Key"""
    try:
        time.sleep(random.uniform(0.5, 1.5))

        proxy_config = Config.get_random_proxy()
        
        client_options = {
            "api_endpoint": "https://generativelanguage.googleapis.com"
        }
        
        if proxy_config:
            os.environ['grpc_proxy'] = proxy_config.get('http')

        genai.configure(
            api_key=api_key,
            client_options=client_options,
        )

        model = genai.GenerativeModel(Config.HAJIMI_CHECK_MODEL)
        response = model.generate_content("hi")
        return "ok"
    except (google_exceptions.PermissionDenied, google_exceptions.Unauthenticated) as e:
        return "not_authorized_key"
    except google_exceptions.TooManyRequests as e:
        return "rate_limited"
    except Exception as e:
        if "429" in str(e) or "rate limit" in str(e).lower() or "quota" in str(e).lower():
            return "rate_limited:429"
        elif "403" in str(e) or "SERVICE_DISABLED" in str(e) or "API has not been used" in str(e):
            return "disabled"
        else:
            return f"error:{e.__class__.__name__}"


def validate_nvidia_key(api_key: str) -> Union[bool, str]:
    """验证 NVIDIA API Key"""
    try:
        time.sleep(random.uniform(0.5, 1.5))
        
        proxy_config = Config.get_random_proxy()
        proxies = None
        if proxy_config:
            proxies = proxy_config
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # 尝试调用 NVIDIA NIM API 列出可用模型
        response = requests.get(
            "https://api.nvcf.nvidia.com/v2/nvcf/functions",
            headers=headers,
            proxies=proxies,
            timeout=10
        )
        
        if response.status_code == 200:
            return "ok"
        elif response.status_code == 401:
            return "not_authorized_key"
        elif response.status_code == 429:
            return "rate_limited"
        else:
            return f"error:{response.status_code}"
    except requests.exceptions.ProxyError as e:
        return f"proxy_error"
    except requests.exceptions.Timeout:
        return "timeout"
    except Exception as e:
        return f"error:{e.__class__.__name__}"


def validate_modelscope_key(api_key: str) -> Union[bool, str]:
    """验证 ModelScope (魔搭) API Key"""
    try:
        time.sleep(random.uniform(0.5, 1.5))
        
        proxy_config = Config.get_random_proxy()
        proxies = None
        if proxy_config:
            proxies = proxy_config
        
        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        
        # 调用 ModelScope API 获取用户信息
        response = requests.get(
            "https://www.modelscope.cn/api/v1/user",
            headers=headers,
            proxies=proxies,
            timeout=10
        )
        
        if response.status_code == 200:
            return "ok"
        elif response.status_code == 401:
            return "not_authorized_key"
        elif response.status_code == 429:
            return "rate_limited"
        else:
            return f"error:{response.status_code}"
    except requests.exceptions.ProxyError as e:
        return f"proxy_error"
    except requests.exceptions.Timeout:
        return "timeout"
    except Exception as e:
        return f"error:{e.__class__.__name__}"


def validate_xiaomi_key(api_key: str) -> Union[bool, str]:
    """验证 Xiaomi (小米) API Key (tp-开头)"""
    try:
        time.sleep(random.uniform(0.5, 1.5))
        
        proxy_config = Config.get_random_proxy()
        proxies = None
        if proxy_config:
            proxies = proxy_config
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # 小米大模型 API 端点（需要根据实际文档调整）
        # 这里使用一个通用的验证方式
        response = requests.get(
            "https://api.xiaomi.com/ai/v1/models",  # 假设的端点
            headers=headers,
            proxies=proxies,
            timeout=10
        )
        
        if response.status_code == 200:
            return "ok"
        elif response.status_code == 401:
            return "not_authorized_key"
        elif response.status_code == 429:
            return "rate_limited"
        else:
            # 如果端点不确定，可以尝试其他验证方式
            return f"error:{response.status_code}"
    except requests.exceptions.ProxyError as e:
        return f"proxy_error"
    except requests.exceptions.Timeout:
        return "timeout"
    except Exception as e:
        return f"error:{e.__class__.__name__}"


# 验证函数映射
VALIDATORS = {
    "gemini": validate_gemini_key,
    "nvidia": validate_nvidia_key,
    "modelscope": validate_modelscope_key,
    "xiaomi": validate_xiaomi_key,
}


def validate_key(provider: str, api_key: str) -> Union[bool, str]:
    """根据供应商验证 API Key"""
    validator = VALIDATORS.get(provider)
    if validator:
        return validator(api_key)
    return "unknown_provider"


def process_item(item: Dict[str, Any]) -> Dict[str, int]:
    """
    处理单个GitHub搜索结果item
    
    Returns:
        Dict[str, int]: {provider: valid_count}
    """
    delay = random.uniform(1, 4)
    file_url = item["html_url"]

    repo_name = item["repository"]["full_name"]
    file_path = item["path"]
    time.sleep(delay)

    content = github_utils.get_file_content(item)
    if not content:
        logger.warning(f"⚠️ Failed to fetch content for file: {file_url}")
        return {}

    # 提取所有供应商的 keys
    all_keys = extract_keys_from_content(content)
    
    if not all_keys:
        return {}

    results = {}
    
    for provider, keys in all_keys.items():
        # 过滤占位符
        filtered_keys = filter_placeholder_keys(keys, content)
        
        if not filtered_keys:
            continue
            
        logger.info(f"🔑 [{PROVIDERS[provider]['name']}] Found {len(filtered_keys)} suspected key(s), validating...")

        valid_keys = []
        rate_limited_keys = []

        for key in filtered_keys:
            validation_result = validate_key(provider, key)
            if validation_result and "ok" in validation_result:
                valid_keys.append(key)
                logger.info(f"✅ [{PROVIDERS[provider]['name']}] VALID: {key[:20]}...")
            elif validation_result == "rate_limited":
                rate_limited_keys.append(key)
                logger.warning(f"⚠️ [{PROVIDERS[provider]['name']}] RATE LIMITED: {key[:20]}...")
            else:
                logger.info(f"❌ [{PROVIDERS[provider]['name']}] INVALID: {key[:20]}...")

        # 保存结果
        if valid_keys:
            file_manager.save_valid_keys(repo_name, file_path, file_url, valid_keys, provider=provider)
            logger.info(f"💾 [{PROVIDERS[provider]['name']}] Saved {len(valid_keys)} valid key(s)")
            try:
                sync_utils.add_keys_to_queue(valid_keys, provider=provider)
                logger.info(f"📥 [{PROVIDERS[provider]['name']}] Added {len(valid_keys)} key(s) to sync queues")
            except Exception as e:
                logger.error(f"📥 Error adding keys to sync queues: {e}")

        if rate_limited_keys:
            file_manager.save_rate_limited_keys(repo_name, file_path, file_url, rate_limited_keys, provider=provider)
            logger.info(f"💾 [{PROVIDERS[provider]['name']}] Saved {len(rate_limited_keys)} rate limited key(s)")

        results[provider] = len(valid_keys)

    return results


def print_skip_stats():
    """打印跳过统计信息"""
    total_skipped = sum(skip_stats.values())
    if total_skipped > 0:
        logger.info(f"📊 Skipped {total_skipped} items - Time: {skip_stats['time_filter']}, Duplicate: {skip_stats['sha_duplicate']}, Age: {skip_stats['age_filter']}, Docs: {skip_stats['doc_filter']}")


def reset_skip_stats():
    """重置跳过统计"""
    global skip_stats
    skip_stats = {"time_filter": 0, "sha_duplicate": 0, "age_filter": 0, "doc_filter": 0}


def main():
    start_time = datetime.now()

    logger.info("=" * 60)
    logger.info("🚀 HAJIMI KING - MULTI PROVIDER EDITION")
    logger.info("=" * 60)
    logger.info(f"⏰ Started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 显示支持的供应商
    logger.info("📋 SUPPORTED PROVIDERS:")
    for pid, config in PROVIDERS.items():
        status = "✅" if config["enabled"] else "❌"
        logger.info(f"   {status} {config['name']} (prefix: {config['prefix']})")

    # 检查配置
    if not Config.check():
        logger.info("❌ Config check failed. Exiting...")
        sys.exit(1)
    if not file_manager.check():
        logger.error("❌ FileManager check failed. Exiting...")
        sys.exit(1)

    if sync_utils.balancer_enabled:
        logger.info("🔗 SyncUtils ready for async key syncing")
        
    balancer_queue_count = len(checkpoint.wait_send_balancer)
    gpt_load_queue_count = len(checkpoint.wait_send_gpt_load)
    logger.info(f"📊 Queue status - Balancer: {balancer_queue_count}, GPT Load: {gpt_load_queue_count}")

    search_queries = file_manager.get_search_queries()
    logger.info("📋 SYSTEM INFORMATION:")
    logger.info(f"🔑 GitHub tokens: {len(Config.GITHUB_TOKENS)} configured")
    logger.info(f"🔍 Search queries: {len(search_queries)} loaded")
    logger.info(f"📅 Date filter: {Config.DATE_RANGE_DAYS} days")
    if Config.PROXY_LIST:
        logger.info(f"🌐 Proxy: {len(Config.PROXY_LIST)} proxies configured")

    if checkpoint.last_scan_time:
        logger.info(f"💾 Checkpoint found - Incremental scan mode")
        logger.info(f"   Last scan: {checkpoint.last_scan_time}")
        logger.info(f"   Scanned files: {len(checkpoint.scanned_shas)}")
        logger.info(f"   Processed queries: {len(checkpoint.processed_queries)}")
    else:
        logger.info(f"💾 No checkpoint - Full scan mode")

    logger.info("✅ System ready - Starting king")
    logger.info("=" * 60)

    # 统计各供应商的 key 数量
    total_keys_found = {pid: 0 for pid in PROVIDERS.keys()}
    total_rate_limited_keys = {pid: 0 for pid in PROVIDERS.keys()}
    loop_count = 0

    while True:
        try:
            loop_count += 1
            logger.info(f"🔄 Loop #{loop_count} - {datetime.now().strftime('%H:%M:%S')}")

            query_count = 0
            loop_processed_files = 0
            reset_skip_stats()

            for i, q in enumerate(search_queries, 1):
                normalized_q = normalize_query(q)
                if normalized_q in checkpoint.processed_queries:
                    logger.info(f"🔍 Skipping already processed query: [{q}],index:#{i}")
                    continue

                res = github_utils.search_for_keys(q)

                if res and "items" in res:
                    items = res["items"]
                    if items:
                        query_valid_keys = {pid: 0 for pid in PROVIDERS.keys()}
                        query_rate_limited_keys = {pid: 0 for pid in PROVIDERS.keys()}
                        query_processed = 0

                        for item_index, item in enumerate(items, 1):
                            if item_index % 20 == 0:
                                total_valid = sum(total_keys_found.values())
                                logger.info(
                                    f"📈 Progress: {item_index}/{len(items)} | query: {q} | total valid: {total_valid}")
                                file_manager.save_checkpoint(checkpoint)
                                file_manager.update_dynamic_filenames()

                            should_skip, skip_reason = should_skip_item(item, checkpoint)
                            if should_skip:
                                logger.info(f"🚫 Skipping item,name: {item.get('path','').lower()},index:{item_index} - reason: {skip_reason}")
                                continue

                            results = process_item(item)
                            
                            for provider, count in results.items():
                                query_valid_keys[provider] += count
                                total_keys_found[provider] += count

                            query_processed += 1
                            checkpoint.add_scanned_sha(item.get("sha"))
                            loop_processed_files += 1

                        total_valid = sum(query_valid_keys.values())
                        if query_processed > 0:
                            logger.info(f"✅ Query {i}/{len(search_queries)} complete - Processed: {query_processed}, Valid: +{total_valid}")
                        else:
                            logger.info(f"⏭️ Query {i}/{len(search_queries)} complete - All items skipped")

                        print_skip_stats()
                    else:
                        logger.info(f"📭 Query {i}/{len(search_queries)} - No items found")
                else:
                    logger.warning(f"❌ Query {i}/{len(search_queries)} failed")

                checkpoint.add_processed_query(normalized_q)
                query_count += 1

                checkpoint.update_scan_time()
                file_manager.save_checkpoint(checkpoint)
                file_manager.update_dynamic_filenames()

                if query_count % 5 == 0:
                    logger.info(f"⏸️ Processed {query_count} queries, taking a break...")
                    time.sleep(1)

            total_valid = sum(total_keys_found.values())
            logger.info(f"🏁 Loop #{loop_count} complete - Processed {loop_processed_files} files | Total valid: {total_valid}")
            
            # 显示各供应商统计
            for pid, count in total_keys_found.items():
                if count > 0:
                    logger.info(f"   📊 {PROVIDERS[pid]['name']}: {count}")

            logger.info(f"💤 Sleeping for 10 seconds...")
            time.sleep(10)

        except KeyboardInterrupt:
            logger.info("⛔ Interrupted by user")
            checkpoint.update_scan_time()
            file_manager.save_checkpoint(checkpoint)
            total_valid = sum(total_keys_found.values())
            logger.info(f"📊 Final stats - Total valid keys: {total_valid}")
            for pid, count in total_keys_found.items():
                logger.info(f"   📊 {PROVIDERS[pid]['name']}: {count}")
            logger.info("🔚 Shutting down sync utils...")
            sync_utils.shutdown()
            break
        except Exception as e:
            logger.error(f"💥 Unexpected error: {e}")
            traceback.print_exc()
            logger.info("🔄 Continuing...")
            continue


if __name__ == "__main__":
    main()
