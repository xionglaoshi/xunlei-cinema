"""Xunlei Cloud Drive API client.

Provides access to file management, offline downloads, and VIP download links.
Based on AList thunder driver implementation.
"""

from typing import Any, Dict, List, Optional, Tuple

import httpx

from .auth import (
    APPID,
    APP_KEY,
    CLIENT_ID,
    CLIENT_VERSION,
    DRIVE_API_URL,
    PACKAGE_NAME,
    USER_AGENT,
    XLUSER_API_URL,
    XLUSER_CORE_URL,
    AuthManager,
)
from .models import FileInfo, OfflineTask, UserInfo


class XunleiAPI:
    """Client for Xunlei Cloud Drive REST API."""

    def __init__(self, auth: AuthManager):
        self.auth = auth
        self.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    def _headers(self) -> Dict[str, str]:
        return {
            "user-agent": USER_AGENT,
            "accept": "application/json;charset=UTF-8",
            "x-device-id": self.auth.device_id,
            "x-client-id": CLIENT_ID,
            "x-client-version": CLIENT_VERSION,
            "Authorization": self.auth.authorization,
            "X-Captcha-Token": self.auth._captcha_token,
        }

    async def _request(self, method: str, url: str, **kwargs: Any) -> Dict[str, Any]:
        """Make authenticated API request with auto captcha token refresh."""
        headers = kwargs.pop("headers", {})
        headers = {**self._headers(), **headers}

        # Ensure captcha token is valid before request
        if not self.auth._captcha_token:
            action = f"{method}:{url.split('?')[0]}"
            try:
                await self.auth.refresh_captcha_token_at_login(action)
            except Exception:
                pass
            headers["X-Captcha-Token"] = self.auth._captcha_token

        resp = await self.client.request(method, url, headers=headers, **kwargs)

        # Handle captcha_invalid by refreshing captcha token and retrying once
        if resp.status_code in (400, 401):
            is_captcha_error = False
            try:
                err_data = resp.json()
                if err_data.get("error") in ("captcha_invalid",):
                    is_captcha_error = True
            except Exception:
                pass

            if is_captcha_error or resp.status_code == 401:
                action = f"{method}:{url.split('?')[0]}"
                try:
                    await self.auth.refresh_captcha_token_at_login(action)
                except Exception:
                    pass
                headers["X-Captcha-Token"] = self.auth._captcha_token
                # Also refresh access token on 401
                if resp.status_code == 401:
                    stored = self.auth.config.load_token()
                    if stored and stored.get("refresh_token"):
                        await self.auth.refresh_access_token(
                            stored["refresh_token"]
                        )
                        headers["Authorization"] = self.auth.authorization
                resp = await self.client.request(
                    method, url, headers=headers, **kwargs
                )

        # Handle HTTP errors with detailed error info
        if resp.status_code >= 400:
            try:
                err_data = resp.json()
                err_msg = err_data.get("error", "")
                err_desc = err_data.get("error_description", "")
                err_details = err_data.get("error_details", "")
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}: {err_msg} - {err_desc} "
                    f"(details: {err_details})",
                    request=resp.request,
                    response=resp,
                )
            except (ValueError, KeyError):
                # Not JSON response
                resp.raise_for_status()

        data = resp.json()

        # Check for API-level errors
        error_code = data.get("error_code")
        error = data.get("error", "")
        if error_code or (error and error != "success"):
            error_msg = data.get("error", "")
            error_desc = data.get("error_description", "")
            error_details = data.get("error_details", "")
            raise ValueError(
                f"API error: {error_msg} - {error_desc} "
                f"(details: {error_details})"
            )

        return data

    # ==================== File Operations ====================

    async def list_files(
        self,
        parent_id: str = "",
        page_token: str = "",
        filters: str = "",
        page_size: int = 100,
    ) -> Tuple[List[FileInfo], str]:
        params: Dict[str, str] = {
            "space": "",
            "__type": "drive",
            "refresh": "true",
            "__sync": "true",
            "parent_id": parent_id,
            "page_token": page_token,
            "with_audit": "true",
            "limit": str(page_size),
            "filters": '{"phase":{"eq":"PHASE_TYPE_COMPLETE"},"trashed":{"eq":false}}',
        }

        data = await self._request("GET", f"{DRIVE_API_URL}/files", params=params)

        files = [self._parse_file(item) for item in data.get("files", [])]
        return files, data.get("next_page_token", "")

    async def get_file_info(self, file_id: str, debug: bool = False) -> FileInfo:
        data = await self._request("GET", f"{DRIVE_API_URL}/files/{file_id}")
        if debug:
            import json
            print("[DEBUG] get_file_info raw response:")
            print(json.dumps(data, indent=2, ensure_ascii=False)[:2000])
        return self._parse_file(data)

    async def get_file_info_raw(self, file_id: str) -> dict:
        """Get raw file info for debugging."""
        return await self._request("GET", f"{DRIVE_API_URL}/files/{file_id}")

    async def get_download_link(self, file_id: str) -> str:
        """Get direct download URL for a file.

        Tries multiple approaches to get the best download link.
        """
        # First try file info
        file_info = await self.get_file_info(file_id)

        if file_info.vip_download_link:
            return file_info.vip_download_link

        if file_info.web_content_link:
            return file_info.web_content_link

        # Try raw API with specific parameters for download URL
        try:
            data = await self._request(
                "GET",
                f"{DRIVE_API_URL}/files/{file_id}",
                params={"space": "", "__type": "drive"},
            )
            # Try all possible link fields (handle both str and dict)
            for key in ["web_content_link", "original_url", "redirect_link", "url"]:
                val = data.get(key)
                if val:
                    if isinstance(val, dict) and val.get("url"):
                        return val["url"]
                    elif isinstance(val, str):
                        return val

            links = data.get("links", {})
            if isinstance(links, dict):
                for key in ["application/octet-stream", "*/*", ""]:
                    val = links.get(key)
                    if val:
                        if isinstance(val, dict) and val.get("url"):
                            return val["url"]
                        elif isinstance(val, str):
                            return val

            # Last resort: check if response itself has url
            if isinstance(data, dict) and data.get("url"):
                url_val = data["url"]
                if isinstance(url_val, dict) and url_val.get("url"):
                    return url_val["url"]
                elif isinstance(url_val, str):
                    return url_val
        except Exception:
            pass

        raise ValueError(f"No download link available for file {file_id}")

    async def create_folder(self, name: str, parent_id: str = "") -> FileInfo:
        payload: Dict[str, Any] = {"kind": "drive#folder", "name": name}
        if parent_id:
            payload["parent_id"] = parent_id
        data = await self._request("POST", f"{DRIVE_API_URL}/files", json=payload)
        return self._parse_file(data)

    async def delete_file(self, file_id: str) -> bool:
        await self._request(
            "PATCH", f"{DRIVE_API_URL}/files/{file_id}/trash", json={}
        )
        return True

    async def rename_file(self, file_id: str, new_name: str) -> FileInfo:
        data = await self._request(
            "PATCH", f"{DRIVE_API_URL}/files/{file_id}", json={"name": new_name}
        )
        return self._parse_file(data)

    async def search_files(self, keyword: str, limit: int = 50) -> List[FileInfo]:
        params = {"keyword": keyword, "limit": str(limit)}
        data = await self._request(
            "GET", f"{DRIVE_API_URL}/files:search", params=params
        )
        return [self._parse_file(item) for item in data.get("files", [])]

    # ==================== VIP Download ====================

    async def get_vip_download_url(self, file_id: str) -> str:
        file_info = await self.get_file_info(file_id)
        if file_info.vip_download_link:
            return file_info.vip_download_link
        if file_info.web_content_link:
            return file_info.web_content_link
        raise ValueError(f"No download link available for file {file_id}")

    # ==================== Offline Download ====================

    async def create_offline_task(self, url: str, parent_id: str = "") -> OfflineTask:
        payload: Dict[str, Any] = {
            "kind": "drive#file",
            "name": "",
            "upload_type": "UPLOAD_TYPE_URL",
            "url": {"url": url},
        }
        if parent_id:
            payload["parent_id"] = parent_id

        data = await self._request(
            "POST", f"{DRIVE_API_URL}/files", json=payload
        )

        # The response may contain the task info in different places
        task_data = data.get("task", data)
        return self._parse_task(task_data)

    async def list_offline_tasks(self, limit: int = 100) -> List[OfflineTask]:
        params = {"type": "offline", "limit": str(limit), "page_token": ""}
        data = await self._request("GET", f"{DRIVE_API_URL}/tasks", params=params)
        return [self._parse_task(item) for item in data.get("tasks", [])]

    async def get_offline_task(self, task_id: str) -> OfflineTask:
        data = await self._request("GET", f"{DRIVE_API_URL}/tasks/{task_id}")
        return self._parse_task(data)

    async def delete_offline_task(self, task_id: str) -> bool:
        await self._request("DELETE", f"{DRIVE_API_URL}/tasks/{task_id}")
        return True

    # ==================== User Info ====================

    async def get_user_info(self) -> UserInfo:
        user_id = self.auth.user_id
        nickname = ""
        vip_type = "none"
        vip_expiry = ""
        total_space = 0
        used_space = 0

        try:
            data = await self._request("GET", f"{XLUSER_API_URL}/user/me")

            user_id = data.get("id", data.get("user_id", user_id))
            nickname = data.get("name", data.get("nick_name", data.get("nickname", "")))

            # Parse VIP from vips/vip_info arrays
            vip_type, vip_expiry = self._parse_vip(data)

            # Storage info not in this endpoint
            total_space = 0
            used_space = 0

        except Exception:
            pass  # Fall back to token data

        return UserInfo(
            user_id=user_id,
            nickname=nickname,
            vip_type=vip_type,
            vip_expiry=vip_expiry,
            total_space=total_space,
            used_space=used_space,
        )

    def _parse_vip(self, data: dict) -> tuple[str, str]:
        """Parse VIP info from response, return (vip_type, expiry).

        Real API response has 'vips' and 'vip_info' arrays.
        """
        vip_type = "none"
        vip_expiry = ""

        # Check 'vips' array: id like 'vip2_1_8_5_2_0' means VIP
        vips = data.get("vips", [])
        if isinstance(vips, list) and vips:
            first_vip = vips[0]
            if isinstance(first_vip, dict):
                vip_id = first_vip.get("id", "")
                if vip_id.startswith("vip2_"):
                    vip_type = "svip"
                elif vip_id.startswith("vip"):
                    vip_type = "vip"
                vip_expiry = first_vip.get("expires_at", "")

        # Check 'vip_info' array for is_vip field
        vip_info = data.get("vip_info", [])
        if isinstance(vip_info, list) and vip_info:
            first_info = vip_info[0]
            if isinstance(first_info, dict):
                is_vip = str(first_info.get("is_vip", "0"))
                if is_vip == "1":
                    if vip_type == "none":
                        vip_type = "vip"
                    # Parse expire date (format: 20260603)
                    expire = first_info.get("expire", "")
                    if expire and not vip_expiry:
                        try:
                            from datetime import datetime
                            dt = datetime.strptime(expire, "%Y%m%d")
                            vip_expiry = dt.strftime("%Y-%m-%d")
                        except ValueError:
                            vip_expiry = expire

        # Check 'group' array as fallback
        if vip_type == "none":
            groups = data.get("group", [])
            if isinstance(groups, list) and groups:
                first_group = groups[0]
                if isinstance(first_group, dict):
                    group_id = first_group.get("id", "")
                    if "vip" in group_id.lower() or "svip" in group_id.lower():
                        vip_type = "vip" if "svip" not in group_id.lower() else "svip"
                        vip_expiry = first_group.get("expires_at", "")

        return vip_type, vip_expiry

    # ==================== Helpers ====================

    def _parse_file(self, data: Dict[str, Any]) -> FileInfo:
        vip_link = ""

        # 1. Check links dict (value can be str or dict{"url", "token"})
        links = data.get("links", {})
        if isinstance(links, dict):
            val = links.get("application/octet-stream", "")
            if isinstance(val, dict) and val.get("url"):
                vip_link = val["url"]
            elif isinstance(val, str):
                vip_link = val

        # 2. Check medias array
        if not vip_link:
            medias = data.get("medias") or []
            for media in medias:
                if isinstance(media, dict) and media.get("link", {}).get("url"):
                    vip_link = media["link"]["url"]
                    break

        # 3. Check media dict (singular)
        if not vip_link:
            media = data.get("media", {})
            if isinstance(media, dict) and media.get("url"):
                vip_link = media["url"]

        # 4. Check direct url field (can be str or dict)
        if not vip_link:
            url_val = data.get("url", "")
            if isinstance(url_val, dict) and url_val.get("url"):
                vip_link = url_val["url"]
            elif isinstance(url_val, str):
                vip_link = url_val

        # Get web_content_link with fallbacks (can be str or dict)
        web_content_link = data.get("web_content_link", "")
        if isinstance(web_content_link, dict) and web_content_link.get("url"):
            web_content_link = web_content_link["url"]
        elif not isinstance(web_content_link, str):
            web_content_link = ""
        if not web_content_link:
            orig = data.get("original_url", "")
            if isinstance(orig, dict) and orig.get("url"):
                web_content_link = orig["url"]
            elif isinstance(orig, str):
                web_content_link = orig
        if not web_content_link:
            redir = data.get("redirect_link", "")
            if isinstance(redir, dict) and redir.get("url"):
                web_content_link = redir["url"]
            elif isinstance(redir, str):
                web_content_link = redir

        return FileInfo(
            file_id=data.get("id", ""),
            name=data.get("name", ""),
            size=int(data.get("size", 0) or 0),
            kind=data.get("kind", "drive#file"),
            parent_id=data.get("parent_id", ""),
            created_time=str(data.get("created_time", "")),
            modified_time=str(data.get("modified_time", "")),
            web_content_link=web_content_link,
            vip_download_link=vip_link,
            thumbnail_link=data.get("thumbnail_link", ""),
            mime_type=data.get("mime_type", ""),
            md5=data.get("hash", ""),
        )

    def _parse_task(self, data: Dict[str, Any]) -> OfflineTask:
        phase = data.get("phase", "")
        status_map = {
            "PHASE_TYPE_PENDING": "waiting",
            "PHASE_TYPE_RUNNING": "downloading",
            "PHASE_TYPE_COMPLETE": "completed",
            "PHASE_TYPE_ERROR": "failed",
        }
        status = status_map.get(phase, "unknown")
        progress = data.get("progress", 0)
        if isinstance(progress, str):
            progress = int(progress)

        return OfflineTask(
            task_id=data.get("id", ""),
            name=data.get("name", data.get("file_name", "")),
            url=data.get("original_url", ""),
            status=status,
            progress=float(progress),
            file_id=data.get("file_id", ""),
            message=data.get("message", ""),
            created_time=data.get("created_time", ""),
            completed_time=data.get("updated_time", ""),
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
