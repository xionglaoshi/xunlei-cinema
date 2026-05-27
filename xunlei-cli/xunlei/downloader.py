"""Download engine with retry, resume, and integrity check.

Design:
- Single file: multi-thread (HTTP Range) with fallback to single-thread resume
- Multiple files: serial download (one at a time) for stability
- 503 handling: re-fetch download URL, single-thread resume from breakpoint
- Integrity: size verification only (迅雷API的MD5不可靠)
"""

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import httpx

from .api import XunleiAPI


class DownloadEngine:
    CHUNK_SIZE = 4 * 1024 * 1024   # 4MB per chunk for multi-thread
    BUFFER_SIZE = 256 * 1024        # 256KB stream buffer
    MAX_RETRIES = 5
    RETRY_DELAY = 5.0               # Base retry delay (seconds)

    def __init__(self, api: XunleiAPI, max_workers: int = 8, task_id: int = 0, db=None):
        self.api = api
        self.max_workers = max_workers
        self.task_id = task_id  # WebUI tracking task ID
        self._db = db           # WebUI TaskDB instance
        self._stop_event = asyncio.Event()
        self._client: Optional[httpx.AsyncClient] = None
        # Clean up stale temp dirs from previous crashed sessions
        self._cleanup_stale_temp_dirs()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(300.0, connect=30.0),
                follow_redirects=True,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    # ========================================================================
    # Public: serial multi-file download (most stable)
    # ========================================================================

    async def download_files(
        self,
        items: List[Tuple[str, str, int]],
        use_vip: bool = True,
        progress_callback: Optional[Callable[[str, int, int, float], None]] = None,
    ) -> Tuple[List[str], Dict[str, str]]:
        """Download multiple files serially (one at a time) for stability.

        Args:
            items: List of (file_id, output_path, total_size)
            use_vip: Use VIP acceleration
            progress_callback: Called with (filename, downloaded, total, speed)

        Returns:
            (success_paths, error_dict{name: error_message})
        """
        results: List[str] = []
        errors: Dict[str, str] = {}

        for idx, (file_id, path, size) in enumerate(items):
            name = os.path.basename(path)
            if progress_callback:
                progress_callback(name, 0, size, 0)  # Signal start

            try:
                def make_cb(n):
                    return lambda d, t, s: progress_callback(n, d, t, s) if progress_callback else None
                cb = make_cb(name)
                result = await self.download_file(file_id, path, use_vip, cb)
                results.append(result)
            except Exception as e:
                errors[name] = str(e)
                # Log to stderr for visibility
                import sys
                print(f"[download_files] {name}: {e}", file=sys.stderr)

        return results, errors

    # ========================================================================
    # Public: single file download
    # ========================================================================

    async def download_file(
        self,
        file_id: str,
        output_path: str = "",
        use_vip: bool = True,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
    ) -> str:
        """Download a single file with retry and integrity check."""
        file_info = await self.api.get_file_info(file_id)

        # Register a new active task for each download call so serial
        # multi-file downloads each get their own DB record.
        db_task_id = self.task_id
        if self._db:
            try:
                db_task_id = self._db.add_active_task(
                    file_name=file_info.name,
                    file_id=file_id,
                    magnet="",
                    size=file_info.size,
                    output_path=output_path or file_info.name,
                )
                self.task_id = db_task_id
            except Exception:
                pass  # DB sync is best-effort

        if not output_path:
            output_path = file_info.name
        output_path = os.path.abspath(output_path)
        tmp_path = output_path + ".xldltmp"
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Remove stale tmp file if exists (previous failed download)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        last_error = None

        try:
            for attempt in range(self.MAX_RETRIES):
                try:
                    if attempt > 0:
                        wait = self.RETRY_DELAY * attempt + 10
                        await asyncio.sleep(wait)
                        file_info = await self.api.get_file_info(file_id)
                        if progress_callback:
                            progress_callback(-2, file_info.size, 0)

                    url = self._pick_url(file_info, use_vip)
                    tmp_path = await self._do_download(
                        url, tmp_path, file_info.size, progress_callback
                    )

                    # Verify file size
                    actual_size = os.path.getsize(tmp_path)
                    if actual_size != file_info.size:
                        raise RuntimeError(
                            f"Size mismatch: expected {file_info.size}, got {actual_size}"
                        )

                    # Atomic move
                    shutil.move(tmp_path, output_path)
                    # DB sync: mark complete
                    if self._db and self.task_id:
                        try:
                            self._db.complete_task(self.task_id, "completed")
                        except Exception:
                            pass
                    return output_path

                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    if status in (503, 502, 504):
                        last_error = e
                        if progress_callback:
                            progress_callback(-2, file_info.size, 0)
                        continue
                    # Clean up on non-retryable error
                    self._cleanup_tmp(tmp_path)
                    raise
                except Exception:
                    self._cleanup_tmp(tmp_path)
                    raise

            # All retries exhausted
            self._cleanup_tmp(tmp_path)
            raise last_error or RuntimeError("Download failed after all retries")

        except Exception as e:
            # DB sync: mark failed
            if self._db and self.task_id:
                try:
                    self._db.fail_task(self.task_id, str(e))
                except Exception:
                    pass
            raise

        finally:
            # Reset so the next file in a serial batch gets a fresh record
            self.task_id = 0

    # ========================================================================
    # Public: download from direct URL
    # ========================================================================

    async def download_url(
        self,
        url: str,
        output_path: str,
        total_size: int = 0,
        file_id: str = "",
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
    ) -> str:
        """Download from a direct URL with retry."""
        output_path = os.path.abspath(output_path)
        tmp_path = output_path + ".xldltmp"
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Register a new active task so WebUI shows this download
        if self._db:
            try:
                self.task_id = self._db.add_active_task(
                    file_name=os.path.basename(output_path),
                    file_id=file_id,
                    magnet="",
                    size=total_size,
                    output_path=output_path,
                )
            except Exception:
                pass

        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        last_error = None
        current_url = url

        try:
            for attempt in range(self.MAX_RETRIES):
                try:
                    if attempt > 0:
                        wait = self.RETRY_DELAY * attempt + 10
                        await asyncio.sleep(wait)
                        if file_id:
                            try:
                                fi = await self.api.get_file_info(file_id)
                                current_url = fi.vip_download_link or fi.web_content_link or url
                            except Exception:
                                pass
                        if progress_callback:
                            progress_callback(-2, total_size, 0)

                    tmp_path = await self._do_download(
                        current_url, tmp_path, total_size, progress_callback
                    )

                    if total_size > 0:
                        actual = os.path.getsize(tmp_path)
                        if actual != total_size:
                            raise RuntimeError(f"Size mismatch: expected {total_size}, got {actual}")

                    shutil.move(tmp_path, output_path)
                    # DB sync: mark complete
                    if self._db and self.task_id:
                        try:
                            self._db.complete_task(self.task_id, "completed")
                        except Exception:
                            pass
                    return output_path

                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    if status in (503, 502, 504):
                        last_error = e
                        if progress_callback:
                            progress_callback(-2, total_size, 0)
                        continue
                    self._cleanup_tmp(tmp_path)
                    raise
                except Exception:
                    self._cleanup_tmp(tmp_path)
                    raise

            self._cleanup_tmp(tmp_path)
            raise last_error or RuntimeError("Download failed after all retries")

        except Exception as e:
            # DB sync: mark failed
            if self._db and self.task_id:
                try:
                    self._db.fail_task(self.task_id, str(e))
                except Exception:
                    pass
            raise

        finally:
            self.task_id = 0

    # ========================================================================
    # Core download dispatch
    # ========================================================================

    async def _do_download(self, url, output_path, total_size, progress_callback):
        """Try multi-thread first; on 503 fallback to single-thread resume."""
        self._stop_event.clear()

        headers = await self._build_headers()
        supports_range = await self._check_range_support(url, headers)

        if supports_range and total_size > self.CHUNK_SIZE:
            try:
                return await self._multi_thread(
                    url, output_path, total_size, headers, progress_callback
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (503, 502, 504):
                    if progress_callback:
                        progress_callback(-3, total_size, 0)
                    return await self._single_thread_resume(
                        url, output_path, total_size, headers, progress_callback
                    )
                raise
        else:
            return await self._single_thread(url, output_path, total_size, headers, progress_callback)

    async def _build_headers(self) -> Dict[str, str]:
        headers = {
            "User-Agent": self.api.auth.config.get_download_user_agent(),
            "Accept": "*/*",
        }
        try:
            token = await self.api.auth.ensure_valid_token()
            headers["Authorization"] = f"Bearer {token}"
        except Exception:
            pass
        return headers

    def _pick_url(self, file_info, use_vip: bool) -> str:
        if use_vip and file_info.vip_download_link:
            return file_info.vip_download_link
        if file_info.web_content_link:
            return file_info.web_content_link
        raise ValueError("No download link available")

    def _cleanup_tmp(self, tmp_path: str) -> None:
        """Remove temporary download file."""
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass

    def stop(self) -> None:
        self._stop_event.set()

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _cleanup_stale_temp_dirs(self) -> None:
        """Remove leftover xldl_* temp dirs from previous crashed sessions."""
        import tempfile
        import glob
        import time

        tmp_root = tempfile.gettempdir()
        stale_cutoff = time.time() - 3600  # 1 hour old
        cleaned = 0
        total_size = 0

        for stale_dir in glob.glob(os.path.join(tmp_root, "xldl_*")):
            if not os.path.isdir(stale_dir):
                continue
            try:
                # Safety: only remove dirs that contain our chunk files (c0, c1, ...)
                files = os.listdir(stale_dir)
                if not any(f.startswith("c") and f[1:].isdigit() for f in files):
                    continue
                # Only remove old ones (created > 1 hour ago) to avoid
                # interfering with a download currently in progress
                mtime = os.path.getmtime(stale_dir)
                if mtime > stale_cutoff:
                    continue
                # Calculate size for logging
                for root, _dirs, files in os.walk(stale_dir):
                    for f in files:
                        try:
                            total_size += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
                shutil.rmtree(stale_dir, ignore_errors=True)
                cleaned += 1
            except Exception:
                pass

        if cleaned > 0:
            import sys
            print(
                f"[xunlei] Cleaned {cleaned} stale temp dir(s) "
                f"({total_size / 1024 / 1024:.1f} MB)",
                file=sys.stderr,
            )

    # ========================================================================
    # Single-threaded download
    # ========================================================================

    async def _single_thread(self, url, output_path, total_size, headers, cb):
        downloaded = 0
        start = time.time()
        last = start

        for attempt in range(self.MAX_RETRIES):
            try:
                client = await self._get_client()
                async with client.stream("GET", url, headers=headers) as resp:
                    resp.raise_for_status()
                    if total_size == 0:
                        total_size = int(resp.headers.get("content-length", 0))

                    with open(output_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(self.BUFFER_SIZE):
                            if self._stop_event.is_set():
                                raise asyncio.CancelledError()
                            f.write(chunk)
                            downloaded += len(chunk)
                            now = time.time()
                            if now - last >= 0.5:
                                speed = downloaded / (now - start) if now > start else 0
                                if cb:
                                    cb(downloaded, total_size, speed)
                                # Sync progress to DB
                                if self._db and self.task_id:
                                    try:
                                        self._db.update_task_progress(
                                            self.task_id, downloaded, total_size, speed,
                                            status="downloading"
                                        )
                                    except Exception:
                                        pass
                                last = now
                if cb:
                    cb(downloaded, total_size, 0)
                return output_path

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in (503, 502, 504) and attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY * (2 ** attempt) + 10)
                    continue
                raise
            except (httpx.NetworkError, asyncio.TimeoutError):
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY * (2 ** attempt))
                    continue
                raise

    async def _single_thread_resume(self, url, output_path, total_size, headers, cb):
        """Single-threaded download with HTTP Range resume."""
        downloaded = 0
        if os.path.exists(output_path):
            downloaded = os.path.getsize(output_path)
            if downloaded >= total_size:
                return output_path

        start = time.time()
        last = start
        mode = "ab" if downloaded > 0 else "wb"

        for attempt in range(self.MAX_RETRIES):
            try:
                h = dict(headers)
                if downloaded > 0:
                    h["Range"] = f"bytes={downloaded}-{total_size - 1}"

                client = await self._get_client()
                async with client.stream("GET", url, headers=h) as resp:
                    if resp.status_code == 206:
                        pass  # Resume OK
                    elif resp.status_code == 200 and downloaded > 0:
                        downloaded = 0
                        mode = "wb"  # Restart
                    else:
                        resp.raise_for_status()

                    if total_size == 0:
                        total_size = int(resp.headers.get("content-length", 0))

                    with open(output_path, mode) as f:
                        async for chunk in resp.aiter_bytes(self.BUFFER_SIZE):
                            if self._stop_event.is_set():
                                raise asyncio.CancelledError()
                            f.write(chunk)
                            downloaded += len(chunk)
                            now = time.time()
                            if now - last >= 0.5:
                                speed = downloaded / (now - start) if now > start else 0
                                if cb:
                                    cb(downloaded, total_size, speed)
                                # Sync progress to DB
                                if self._db and self.task_id:
                                    try:
                                        self._db.update_task_progress(
                                            self.task_id, downloaded, total_size, speed,
                                            status="downloading"
                                        )
                                    except Exception:
                                        pass
                                last = now

                if cb:
                    cb(downloaded, total_size, 0)
                return output_path

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in (503, 502, 504) and attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY * (2 ** attempt) + 15)
                    mode = "ab"
                    continue
                raise
            except (httpx.NetworkError, asyncio.TimeoutError):
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY * (2 ** attempt))
                    mode = "ab"
                    continue
                raise

    # ========================================================================
    # Multi-threaded download (with strict chunk verification)
    # ========================================================================

    async def _multi_thread(self, url, output_path, total_size, headers, cb):
        import math
        import tempfile

        num_chunks = min(self.max_workers, max(1, math.ceil(total_size / self.CHUNK_SIZE)))
        chunk_size = math.ceil(total_size / num_chunks)

        tmp = tempfile.mkdtemp(prefix="xldl_")
        chunk_files = [os.path.join(tmp, f"c{i}") for i in range(num_chunks)]
        chunk_ranges = []
        for i in range(num_chunks):
            s = i * chunk_size
            e = min(s + chunk_size - 1, total_size - 1)
            chunk_ranges.append((s, e, e - s + 1))

        progress = [0] * num_chunks
        lock = asyncio.Lock()
        start = time.time()
        last_report = [start]

        try:
            async def reporter():
                while True:
                    await asyncio.sleep(0.5)
                    d = sum(progress)
                    elapsed = time.time() - start
                    speed = d / elapsed if elapsed > 0 else 0
                    now = time.time()
                    if now - last_report[0] >= 0.5 or d >= total_size:
                        if cb:
                            cb(d, total_size, speed)
                        # Sync progress to DB
                        if self._db and self.task_id:
                            try:
                                self._db.update_task_progress(
                                    self.task_id, d, total_size, speed,
                                    status="downloading"
                                )
                            except Exception:
                                pass
                        last_report[0] = now
                    if d >= total_size:
                        break

            async def get_chunk(idx: int):
                start_b, end_b, expected = chunk_ranges[idx]
                for attempt in range(3):
                    try:
                        h = dict(headers)
                        h["Range"] = f"bytes={start_b}-{end_b}"
                        received = 0
                        client = await self._get_client()
                        async with client.stream("GET", url, headers=h) as resp:
                            resp.raise_for_status()
                            with open(chunk_files[idx], "wb") as f:
                                async for data in resp.aiter_bytes(self.BUFFER_SIZE):
                                    if self._stop_event.is_set():
                                        raise asyncio.CancelledError()
                                    f.write(data)
                                    received += len(data)
                                    async with lock:
                                        progress[idx] = received
                        if received != expected:
                            raise RuntimeError(
                                f"Chunk {idx}: expected {expected}, got {received}"
                            )
                        return
                    except (httpx.HTTPError, httpx.NetworkError, asyncio.TimeoutError):
                        if attempt < 2:
                            await asyncio.sleep(self.RETRY_DELAY * (2 ** attempt) + 5)
                            async with lock:
                                progress[idx] = 0
                            continue
                        raise

            tasks = [get_chunk(i) for i in range(num_chunks)]
            tasks.append(reporter())
            await asyncio.gather(*tasks)

            # Strict merge: verify each chunk size, concatenate in order
            with open(output_path, "wb") as out:
                for idx, cf in enumerate(chunk_files):
                    _, _, expected = chunk_ranges[idx]
                    actual = os.path.getsize(cf)
                    if actual != expected:
                        raise RuntimeError(
                            f"Merge: chunk {idx} expected {expected}, actual {actual}"
                        )
                    with open(cf, "rb") as f:
                        while True:
                            d = f.read(self.BUFFER_SIZE)
                            if not d:
                                break
                            out.write(d)

            # Verify final size
            final_size = os.path.getsize(output_path)
            if final_size != total_size:
                raise RuntimeError(f"Final size: expected {total_size}, got {final_size}")

            if cb:
                cb(total_size, total_size, 0)
            return output_path

        finally:
            # Always clean up temp directory, no matter what happened
            shutil.rmtree(tmp, ignore_errors=True)

    async def _check_range_support(self, url: str, headers: Dict) -> bool:
        try:
            h = {k: v for k, v in headers.items()}
            h["Range"] = "bytes=0-0"
            client = await self._get_client()
            r = await client.head(url, headers=h, timeout=10)
            if r.status_code == 206:
                return True
            ar = r.headers.get("accept-ranges", "")
            return bool(ar and ar.lower() != "none")
        except Exception:
            return False
