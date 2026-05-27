"""Configuration management for xunlei-cli.

Thread-safe and process-safe configuration storage with file locking
and atomic writes to prevent data corruption when multiple CLI
instances run simultaneously.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

from filelock import FileLock, Timeout


class Config:
    """Manages configuration and token storage with process-safe locking."""

    def __init__(self):
        self.config_dir = Path.home() / ".config" / "xunlei-cli"
        self.config_file = self.config_dir / "config.json"
        self.token_file = self.config_dir / "token.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # File locks for process-safe access
        self._config_lock = FileLock(str(self.config_file) + ".lock")
        self._token_lock = FileLock(str(self.token_file) + ".lock")

    # ==================== Config (general settings) ====================

    def load_config(self) -> dict:
        """Load general configuration with read lock."""
        try:
            with self._config_lock.acquire(timeout=5):
                if not self.config_file.exists():
                    return {}
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Timeout:
            # If can't acquire lock, try reading anyway
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {}

    def save_config(self, data: dict) -> None:
        """Save general configuration with write lock and atomic write."""
        with self._config_lock:
            tmp = self.config_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(self.config_file)

    # ==================== Token (authentication) ====================

    def load_token(self) -> Optional[dict]:
        """Load stored token with read lock."""
        try:
            with self._token_lock.acquire(timeout=5):
                if not self.token_file.exists():
                    return None
                try:
                    with open(self.token_file, "r", encoding="utf-8") as f:
                        return json.load(f)
                except (json.JSONDecodeError, TypeError):
                    return None
        except Timeout:
            # Best-effort read without lock
            if self.token_file.exists():
                try:
                    with open(self.token_file, "r", encoding="utf-8") as f:
                        return json.load(f)
                except (json.JSONDecodeError, TypeError):
                    pass
            return None

    def save_token(self, token: dict) -> None:
        """Save token with write lock and atomic write.

        Thread-safe and process-safe. Uses atomic rename to prevent
        corruption when multiple processes write simultaneously.
        """
        # Ensure expire_at is set
        if "expire_at" not in token or not token["expire_at"]:
            expires_in = token.get("expires_in", 7200)
            token["expire_at"] = int(time.time()) + expires_in - 300

        with self._token_lock:
            tmp = self.token_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(token, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(self.token_file)
            os.chmod(self.token_file, 0o600)

    def clear_auth(self) -> None:
        """Remove all authentication data."""
        with self._token_lock:
            if self.token_file.exists():
                self.token_file.unlink()
        config = self.load_config()
        config.pop("username", None)
        config.pop("device_id", None)
        self.save_config(config)

    # ==================== Device ID ====================

    def get_device_id(self) -> str:
        """Get or generate a stable device ID."""
        config = self.load_config()
        device_id = config.get("device_id")
        if not device_id:
            import hashlib
            import uuid

            device_id = hashlib.md5(str(uuid.getnode()).encode()).hexdigest()
            config["device_id"] = device_id
            self.save_config(config)
        return device_id

    # ==================== User-Agent ====================

    def get_user_agent(self) -> str:
        """Get User-Agent string for API requests."""
        return (
            "ANDROID-com.xunlei.downloadprovider/8.31.0.9726 "
            "netWorkType/5G appid/40 deviceName/Xiaomi_M2004J7AC "
            "deviceModel/M2004J7AC OSVersion/12 protocolVersion/301 "
            "platformVersion/10 sdkVersion/512000 Oauth2Client/0.9 "
            "(Linux 4_14_186-perf-gddfs8vbb238b) (JAVA 0)"
        )

    def get_download_user_agent(self) -> str:
        """Get User-Agent for download requests."""
        return (
            "Dalvik/2.1.0 (Linux; U; Android 12; "
            "M2004J7AC Build/SP1A.210812.016)"
        )
