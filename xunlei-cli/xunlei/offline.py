"""Offline download task management for Xunlei cloud drive.

Provides high-level interface for creating and managing offline download tasks.
"""

import asyncio
import time
from typing import Callable, List, Optional

from .models import OfflineTask
from .api import XunleiAPI


class OfflineManager:
    """Manages offline download tasks on Xunlei cloud drive.

    Offline download allows you to download files (magnet links, HTTP URLs, FTP)
    to Xunlei's cloud servers first, then download them locally at high speed
    using VIP acceleration.
    """

    def __init__(self, api: XunleiAPI):
        self.api = api

    async def create_task(self, url: str, parent_id: str = "") -> OfflineTask:
        """Create a new offline download task.

        Args:
            url: Magnet link, HTTP URL, or FTP URL to download
            parent_id: Target folder ID in cloud drive (empty for root)

        Returns:
            OfflineTask with task details
        """
        return await self.api.create_offline_task(url, parent_id)

    async def list_tasks(self, limit: int = 100) -> List[OfflineTask]:
        """List all offline download tasks.

        Args:
            limit: Maximum number of tasks to return

        Returns:
            List of OfflineTask ordered by creation time (newest first)
        """
        return await self.api.list_offline_tasks(limit)

    async def get_task(self, task_id: str) -> OfflineTask:
        """Get specific task details.

        Args:
            task_id: The task ID

        Returns:
            OfflineTask with current status
        """
        return await self.api.get_offline_task(task_id)

    async def delete_task(self, task_id: str) -> bool:
        """Delete an offline task.

        Args:
            task_id: Task ID to delete

        Returns:
            True if deleted successfully
        """
        return await self.api.delete_offline_task(task_id)

    async def wait_for_completion(
        self,
        task_id: str,
        timeout: int = 3600,
        poll_interval: int = 10,
        progress_callback: Optional[Callable[[OfflineTask], None]] = None,
    ) -> OfflineTask:
        """Poll task until completion or timeout.

        Args:
            task_id: Task ID to monitor
            timeout: Maximum wait time in seconds
            poll_interval: Seconds between status checks
            progress_callback: Called with updated task on each poll

        Returns:
            Final OfflineTask (completed or failed)

        Raises:
            TimeoutError: If task doesn't complete within timeout
        """
        start = time.time()

        while time.time() - start < timeout:
            task = await self.get_task(task_id)

            if progress_callback:
                progress_callback(task)

            if task.status in ("completed", "failed"):
                return task

            await asyncio.sleep(poll_interval)

        raise TimeoutError(
            f"Task {task_id} did not complete within {timeout} seconds"
        )
