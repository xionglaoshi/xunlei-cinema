"""Data models for Xunlei CLI."""

from typing import Optional


class TokenInfo:
    """Authentication token information."""

    def __init__(
        self,
        access_token: str = "",
        refresh_token: str = "",
        expires_in: int = 7200,
        token_type: str = "Bearer",
        user_id: str = "",
        expire_at: int = 0,
    ):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_in = expires_in
        self.token_type = token_type
        self.user_id = user_id
        self.expire_at = expire_at


class FileInfo:
    """Cloud drive file or folder information."""

    def __init__(
        self,
        file_id: str = "",
        name: str = "",
        size: int = 0,
        kind: str = "drive#file",
        parent_id: str = "",
        created_time: str = "",
        modified_time: str = "",
        web_content_link: str = "",
        vip_download_link: str = "",
        thumbnail_link: str = "",
        mime_type: str = "",
        md5: str = "",
    ):
        self.file_id = file_id
        self.name = name
        self.size = size
        self.kind = kind
        self.parent_id = parent_id
        self.created_time = created_time
        self.modified_time = modified_time
        self.web_content_link = web_content_link
        self.vip_download_link = vip_download_link
        self.thumbnail_link = thumbnail_link
        self.mime_type = mime_type
        self.md5 = md5


class OfflineTask:
    """Offline download task information."""

    def __init__(
        self,
        task_id: str = "",
        name: str = "",
        url: str = "",
        status: str = "waiting",
        progress: float = 0.0,
        file_id: str = "",
        message: str = "",
        created_time: str = "",
        completed_time: str = "",
    ):
        self.task_id = task_id
        self.name = name
        self.url = url
        self.status = status
        self.progress = progress
        self.file_id = file_id
        self.message = message
        self.created_time = created_time
        self.completed_time = completed_time


class UserInfo:
    """User account information."""

    def __init__(
        self,
        user_id: str = "",
        nickname: str = "",
        vip_type: str = "none",
        vip_expiry: str = "",
        total_space: int = 0,
        used_space: int = 0,
    ):
        self.user_id = user_id
        self.nickname = nickname
        self.vip_type = vip_type
        self.vip_expiry = vip_expiry
        self.total_space = total_space
        self.used_space = used_space
