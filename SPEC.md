# Xunlei CLI - SPEC.md

## Overview
A command-line interface tool for Xunlei (迅雷) cloud drive and download services. Supports VIP acceleration, offline downloading, cloud drive file management, and high-speed downloading.

## Architecture
```
xunlei/
├── __init__.py          # Package init, version
├── __main__.py          # Entry point (python -m xunlei)
├── models.py            # Data models (File, Task, UserInfo, etc.)
├── config.py            # Configuration management (token storage, settings)
├── auth.py              # Authentication (login, token refresh, logout)
├── api.py               # Xunlei cloud drive API client
├── offline.py           # Offline download task management
├── downloader.py        # Multi-threaded download engine with VIP support
└── cli.py               # Click CLI interface (commands & UI)
```

## Data Models (models.py)

```python
@dataclass
class TokenInfo:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"
    user_id: str = ""
    
@dataclass
class FileInfo:
    file_id: str
    name: str
    size: int
    kind: str          # "drive#file" | "drive#folder"
    parent_id: str
    created_time: str
    modified_time: str
    web_content_link: str = ""      # Normal download URL (speed limited)
    vip_download_link: str = ""     # VIP high-speed download URL
    thumbnail_link: str = ""
    mime_type: str = ""
    md5: str = ""

@dataclass  
class OfflineTask:
    task_id: str
    name: str
    url: str                    # magnet/http/ftp
    status: str                 # "downloading" | "completed" | "failed" | "waiting"
    progress: float             # 0.0 - 100.0
    file_id: str = ""           # Result file ID after completion
    message: str = ""
    created_time: str = ""
    completed_time: str = ""

@dataclass
class UserInfo:
    user_id: str
    nickname: str
    vip_type: str              # "none" | "vip" | "svip"
    vip_expiry: str = ""
    total_space: int = 0
    used_space: int = 0
```

## Configuration (config.py)

```python
class Config:
    CONFIG_DIR: Path = Path.home() / ".config" / "xunlei-cli"
    CONFIG_FILE: Path = CONFIG_DIR / "config.json"
    TOKEN_FILE: Path = CONFIG_DIR / "token.json"
    
    # Stores: username, device_id, client_id, user_agent
    def load_config() -> dict
    def save_config(data: dict) -> None
    def load_token() -> Optional[TokenInfo]
    def save_token(token: TokenInfo) -> None
    def clear_auth() -> None  # Remove token and config
```

## Authentication (auth.py)

```python
class AuthManager:
    def __init__(self, config: Config)
    
    # Step 1: Initialize captcha
    async def init_captcha(self) -> dict:
        """POST xluser-ssl.xunlei.com/v1/shield/captcha/init
        Returns: {"captcha_token": "", "expiration": 0, "url": ""}"""
    
    # Step 2: Login with username/password  
    async def login(self, username: str, password: str, captcha_token: str = "") -> TokenInfo:
        """POST xluser-ssl.xunlei.com/v1/auth/signin
        Returns TokenInfo with access_token and refresh_token"""
    
    # Login with refresh token
    async def login_with_refresh_token(self, refresh_token: str) -> TokenInfo:
        """POST xluser-ssl.xunlei.com/v1/auth/signin/token"""
    
    # Refresh access token
    async def refresh_token(self) -> TokenInfo:
        """Refresh using stored refresh_token"""
    
    # Auto-refresh if needed
    async def ensure_valid_token(self) -> str:
        """Return valid access_token, refresh if expired"""
    
    def is_logged_in(self) -> bool
    def logout(self) -> None
```

## Cloud Drive API (api.py)

```python
class XunleiAPI:
    def __init__(self, auth: AuthManager)
    
    # File Operations
    async def list_files(self, parent_id: str = "", page_token: str = "", filters: str = "") -> tuple[list[FileInfo], str]:
        """GET api-pan.xunleix.com/drive/v1/files
        Returns: (files, next_page_token)"""
    
    async def get_file_info(self, file_id: str) -> FileInfo:
        """GET api-pan.xunleix.com/drive/v1/files/{file_id}
        Includes download links in response"""
    
    async def create_folder(self, name: str, parent_id: str = "") -> FileInfo
    async def delete_file(self, file_id: str) -> bool
    async def rename_file(self, file_id: str, new_name: str) -> FileInfo
    
    # Search
    async def search_files(self, keyword: str) -> list[FileInfo]
    
    # User
    async def get_user_info(self) -> UserInfo
    
    # VIP Download URL
    async def get_vip_download_url(self, file_id: str) -> str:
        """Get VIP high-speed download link for a file"""
```

## Offline Download (offline.py)

```python
class OfflineManager:
    def __init__(self, api: XunleiAPI)
    
    async def create_task(self, url: str, parent_id: str = "") -> OfflineTask:
        """Create offline download task (magnet/http/ftp)
        Uses xunlei cloud drive API to create task"""
    
    async def list_tasks(self, limit: int = 100) -> list[OfflineTask]:
        """List recent offline tasks"""
    
    async def get_task(self, task_id: str) -> OfflineTask
    async def delete_task(self, task_id: str) -> bool
    
    # Convenience method
    async def wait_for_completion(self, task_id: str, timeout: int = 3600) -> OfflineTask:
        """Poll until task completes or fails"""
```

## Download Engine (downloader.py)

```python
class DownloadEngine:
    def __init__(self, api: XunleiAPI, max_workers: int = 8)
    
    async def download_file(
        self, 
        file_id: str,
        output_path: str = "",
        use_vip: bool = True,      # Use VIP high-speed link
        chunk_size: int = 8*1024*1024,  # 8MB chunks for multi-thread
        progress_callback: Optional[Callable] = None
    ) -> str:
        """Download file from cloud drive with multi-threading and VIP support"""
    
    async def download_url(
        self,
        url: str,
        output_path: str,
        headers: dict = None,
        use_vip: bool = False
    ) -> str:
        """Download from direct URL (for offline task results)"""
    
    def _download_chunk(self, url: str, start: int, end: int, output_file: str, headers: dict)
    def _show_progress(self, downloaded: int, total: int, speed: float)
```

## CLI Interface (cli.py)

Using Click library. Commands:

```
xunlei login                    # Interactive login
xunlei login --refresh-token    # Login with refresh token
xunlei logout                   # Clear credentials

xunlei ls [folder_id]           # List files in folder
xunlei mkdir <name> [parent_id] # Create folder
xunlei rm <file_id>             # Delete file/folder
xunlei search <keyword>         # Search files
xunlei info <file_id>           # Show file details with download links

xunlei offline <url> [folder_id]    # Create offline download task
xunlei offline-list                 # List offline tasks
xunlei offline-rm <task_id>         # Delete offline task
xunlei offline-wait <task_id>       # Wait for task completion

xunlei download <file_id> [output]  # Download file (auto VIP)
xunlei download-url <url> [output]  # Download direct URL

xunlei user                     # Show user info and VIP status
xunlei status                   # Show download speed/status
```

## API Endpoints Reference

### Authentication
- Base: `https://xluser-ssl.xunlei.com`
- POST `/v1/shield/captcha/init` - Init captcha
- POST `/v1/auth/signin` - Login with password
- POST `/v1/auth/signin/token` - Login with refresh token

### Cloud Drive
- Base: `https://api-pan.xunleix.com`  
- GET `/drive/v1/files?parent_id={}&page_token={}&filters={}` - List files
- GET `/drive/v1/files/{file_id}` - File info (includes download links)
- POST `/drive/v1/files` - Create folder
- DELETE `/drive/v1/files/{file_id}` - Delete file
- GET `/drive/v1/files:search?keyword={}` - Search

### Request Headers
```
Authorization: Bearer {access_token}
Content-Type: application/json
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
```

## Dependencies
```
httpx>=0.27.0          # Async HTTP client
click>=8.1.0           # CLI framework
rich>=13.0.0           # Terminal UI (progress bars, tables)
pydantic>=2.0.0        # Data validation (optional, use dataclasses)
```

## Entry Point
```python
# __main__.py
import asyncio
from .cli import cli

if __name__ == "__main__":
    asyncio.run(cli())
```

```python
# setup.py / pyproject.toml
[project.scripts]
xunlei = "xunlei.cli:main"
```
