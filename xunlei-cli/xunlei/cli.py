"""Command-line interface for Xunlei CLI tool.

Provides a rich, interactive CLI using Click and Rich libraries.
"""

import asyncio
import functools
import json
import os
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text

from .api import XunleiAPI
from .auth import AuthManager, ReviewPanelException
from .config import Config
from .cinema import find_duplicate, resolve_folder, validate_source
from .downloader import DownloadEngine
from .models import FileInfo, OfflineTask
from .offline import OfflineManager
from .utils import validate_magnet

console = Console()


def async_cmd(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


class Context:
    """Shared CLI context."""

    def __init__(self) -> None:
        self.config = Config()
        self.auth = AuthManager(self.config)
        self.api = XunleiAPI(self.auth)
        self.offline = OfflineManager(self.api)
        # Auto-connect to webui.db if it exists so CLI downloads
        # show up in the Web UI dashboard automatically.
        task_db = self._try_load_task_db()
        self.downloader = DownloadEngine(self.api, db=task_db)

    def _try_load_task_db(self):
        """Load TaskDB if webui.db exists (for WebUI progress sync)."""
        try:
            db_path = Path.home() / ".config" / "xunlei-cli" / "webui.db"
            if db_path.exists():
                from .webui import TaskDB
                return TaskDB()
        except Exception:
            pass
        return None

    async def close(self) -> None:
        await self.api.close()
        await self.auth.close()


pass_context = click.make_pass_decorator(Context, ensure=True)


async def _do_download(
    ctx: Context, file_id: str, output_path: str, use_vip: bool
) -> None:
    """Download a single file."""
    file_info = await ctx.api.get_file_info(file_id)
    output_path = output_path or file_info.name

    console.print(f"[bold]Downloading:[/bold] {file_info.name}")
    console.print(f"Size: {format_size(file_info.size)}")
    console.print(
        f"VIP: {'[green]On[/green]' if use_vip else '[yellow]Off[/yellow]' }"
    )
    console.print()

    with Progress(
        TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
        BarColumn(bar_width=None),
        "[progress.percentage]{task.percentage:>3.1f}%",
        "•",
        DownloadColumn(binary_units=True),
        "•",
        TransferSpeedColumn(),
        "•",
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task(
            "download", filename=file_info.name, total=file_info.size
        )

        def on_progress(downloaded: int, total: int, speed: float) -> None:
            progress.update(task_id, completed=downloaded, total=total)

        result_path = await ctx.downloader.download_file(
            file_id, output_path, use_vip=use_vip, progress_callback=on_progress
        )

    console.print(f"\n[bold green]Complete![/bold green]")
    console.print(f"Saved: [cyan]{result_path}[/cyan]")


async def _do_download_multi(
    ctx: Context, files: list, output_dir: str, use_vip: bool
) -> None:
    """Download multiple files concurrently."""
    # Build download items
    items = []
    for f in files:
        path = os.path.join(output_dir, f.name)
        items.append((f.file_id, path, f.size))

    console.print(f"[bold]Downloading {len(items)} files concurrently[/bold]\n")

    # Track progress per file
    file_progress: dict[str, dict] = {}
    file_errors: dict[str, str] = {}
    for f in files:
        file_progress[f.name] = {"done": 0, "total": f.size, "status": "pending"}

    def on_progress(name: str, downloaded: int, total: int, speed: float) -> None:
        if downloaded < 0:
            file_progress[name]["status"] = "retrying"
            return
        file_progress[name]["done"] = downloaded
        file_progress[name]["total"] = total
        if downloaded >= total:
            file_progress[name]["status"] = "done"

    # Show initial status
    for f in files:
        console.print(f"  [dim]Queued:[/dim] {f.name} ({format_size(f.size)})")
    console.print()

    # Run concurrent downloads with error tracking
    results, errors = await ctx.downloader.download_files(items, use_vip, on_progress)

    # Summary
    console.print()
    success = len(results)
    failed = len(errors)
    if success:
        console.print(f"[bold green]Success: {success}/{len(items)}[/bold green]")
    if failed:
        console.print(f"[bold red]Failed: {failed}/{len(items)}[/bold red]")
        for name, err in errors.items():
            console.print(f"  [red][FAIL][/red] {name}: {err}")
    for f in files:
        if any(r.endswith(f.name) for r in results):
            console.print(f"  [green][OK][/green] {f.name}")
        elif f.name not in errors:
            console.print(f"  [red][FAIL][/red] {f.name}")


# ==================== Helper Functions ====================


def format_size(size_bytes: int) -> str:
    """Format byte size to human readable string."""
    if size_bytes == 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def create_files_table(files: list, title: str = "Files") -> Table:
    """Create a Rich table for file listing."""
    table = Table(title=title, show_header=True, header_style="bold magenta")
    table.add_column("ID", style="dim", no_wrap=True, width=20)
    table.add_column("Name", style="cyan")
    table.add_column("Size", justify="right", style="green", width=12)
    table.add_column("Type", style="yellow", width=10)
    table.add_column("Modified", style="dim", width=20)

    for f in files:
        icon = "📁" if f.kind == "drive#folder" else "📄"
        size = "-" if f.kind == "drive#folder" else format_size(f.size)
        table.add_row(
            f.file_id[:16] + "...",
            f"{icon} {f.name}",
            size,
            f.kind.replace("drive#", ""),
            f.modified_time[:19] if f.modified_time else "",
        )
    return table


def create_tasks_table(tasks: list, title: str = "Offline Tasks") -> Table:
    """Create a Rich table for task listing."""
    table = Table(title=title, show_header=True, header_style="bold magenta")
    table.add_column("Task ID", style="dim", no_wrap=True, width=16)
    table.add_column("Name", style="cyan")
    table.add_column("Status", style="yellow", width=12)
    table.add_column("Progress", width=15)

    status_colors = {
        "waiting": "yellow",
        "downloading": "blue",
        "completed": "green",
        "failed": "red",
    }

    for t in tasks:
        color = status_colors.get(t.status, "white")
        bar = "█" * int(t.progress / 10) + "░" * (10 - int(t.progress / 10))
        table.add_row(
            t.task_id[:12] + "...",
            t.name[:40],
            f"[{color}]{t.status}[/{color}]",
            f"{bar} {t.progress:.1f}%",
        )
    return table


# ==================== CLI Group ====================


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Xunlei CLI - Cloud drive and VIP download acceleration tool.

    Manage your Xunlei cloud drive, create offline downloads,
    and enjoy VIP speed acceleration.

    \b
    Quick Start:
        xunlei login                    # Login with your account
        xunlei ls                       # List root folder files
        xunlei offline <magnet_url>     # Create offline download
        xunlei download <file_id>       # Download with VIP speed

    For more help: xunlei <command> --help
    """
    ctx.ensure_object(Context)


def main() -> None:
    """Entry point for the CLI."""
    import sys
    # Ensure process name shows as xunlei-cli in ps/top
    sys.argv[0] = "xunlei-cli"
    cli()


# ==================== Auth Commands ====================


def _show_review_panel_help(review_data) -> None:
    """Explain the official wrapper-page flow without printing one-time credentials."""
    console.print(
        Panel(
            "[bold yellow]New Device Verification Required[/bold yellow]\n\n"
            "This is a security check from Xunlei for new devices.\n"
            "You need to complete SMS verification in your browser.",
            title="Security Check",
            border_style="yellow",
        )
    )

    console.print("\nAgent verification page: https://i.xunlei.com/xlcaptcha/android.html")
    console.print("Ask the Agent to start verification, finish SMS there, then press Enter here.")


@cli.command()
@click.option("--username", "-u", help="Username (phone/email)")
@click.option("--password", "-p", help="Password")
@click.option("--refresh-token", "-r", help="Login with refresh token")
@click.option("--creditkey", "-c", help="Credit key from SMS verification")
@async_cmd
@pass_context
async def login(
    ctx: Context, username: str, password: str, refresh_token: str, creditkey: str
) -> None:
    """Login to Xunlei account.

    For new devices, you'll need to complete SMS verification.
    The CLI will guide you through the process.
    """
    console.print(Panel("[bold blue]Xunlei Login[/bold blue]"))

    try:
        if refresh_token:
            with console.status("[bold green]Authenticating..."):
                token = await ctx.auth.login_with_refresh_token(refresh_token)
            console.print("[green]Login successful![/green]")
            console.print(f"User ID: {token.user_id}")
            return

        if not username:
            console.print(
                "[dim]Tip: Use your phone number (e.g. 18665718082) "
                "or email or Xunlei User ID[/dim]\n"
            )
            username = console.input("[bold]Username (phone/email/ID): [/bold]")
        if not password:
            password = console.input("[bold]Password: [/bold]", password=True)

        # Attempt login, handle review panel if needed
        attempt = 0
        while True:
            try:
                with console.status("[bold green]Logging in..."):
                    token = await ctx.auth.login(username, password, creditkey)

                console.print(f"\n[bold green]Login successful![/bold green]")
                console.print(f"User ID: [cyan]{token.user_id}[/cyan]")

                return

            except ReviewPanelException as e:
                attempt += 1
                if attempt > 3:
                    raise ValueError("Verification was not accepted after three attempts")

                _show_review_panel_help(e.review_data)
                pending_path = ctx.config.config_dir / "pending-review.json"
                descriptor = os.open(
                    pending_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
                )
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as pending_file:
                        json.dump(e.review_data.to_dict(), pending_file)
                    os.chmod(pending_path, 0o600)
                    reply = console.input("[bold green]After Agent/SMS verification press Enter (or q to quit): [/bold green]")
                finally:
                    pending_path.unlink(missing_ok=True)
                if reply.strip().lower() == "q":
                    console.print("[yellow]Login cancelled.[/yellow]")
                    return
                verified_path = ctx.config.config_dir / "verified-creditkey.json"
                if verified_path.exists():
                    try:
                        if verified_path.stat().st_mode & 0o077:
                            raise ValueError("Verified key file is not private")
                        verified_data = json.loads(verified_path.read_text(encoding="utf-8"))
                        creditkey = verified_data["creditkey"]
                    finally:
                        verified_path.unlink(missing_ok=True)
                else:
                    creditkey = e.review_data.creditkey
                if not creditkey:
                    creditkey = console.input("[bold]Credit key from verification page: [/bold]")

    except Exception as e:
        console.print(f"[bold red]Login failed:[/bold red] {e}")
        sys.exit(1)
    finally:
        await ctx.close()


@cli.command()
@async_cmd
@pass_context
async def logout(ctx: Context) -> None:
    """Logout and clear credentials."""
    ctx.auth.logout()
    console.print("[green]Logged out successfully.[/green]")
    await ctx.close()


# ==================== File Commands ====================


@cli.command()
@click.argument("folder_id", required=False, default="")
@click.option("--limit", "-l", default=100, help="Max items per page")
@async_cmd
@pass_context
async def ls(ctx: Context, folder_id: str, limit: int) -> None:
    """List files in a folder.

    FOLDER_ID: The folder ID (empty for root folder)
    """
    if not ctx.auth.is_logged_in():
        console.print("[red]Not logged in. Run 'xunlei login' first.[/red]")
        return

    try:
        with console.status("[bold green]Loading files..."):
            files, _ = await ctx.api.list_files(parent_id=folder_id, page_size=limit)

        if not files:
            console.print("[yellow]No files found.[/yellow]")
            return

        title = f"Files in folder: {folder_id or 'root'}"
        table = create_files_table(files, title)
        console.print(table)
        console.print(f"\n[dim]Total: {len(files)} items[/dim]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        await ctx.close()


@cli.command()
@click.argument("name")
@click.argument("parent_id", required=False, default="")
@async_cmd
@pass_context
async def mkdir(ctx: Context, name: str, parent_id: str) -> None:
    """Create a new folder.

    NAME: Folder name
    PARENT_ID: Parent folder ID (empty for root)
    """
    if not ctx.auth.is_logged_in():
        console.print("[red]Not logged in. Run 'xunlei login' first.[/red]")
        return

    try:
        with console.status("[bold green]Creating folder..."):
            folder = await ctx.api.create_folder(name, parent_id)
        console.print(
            f"[green]Created:[/green] {folder.name} (ID: {folder.file_id})"
        )
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        await ctx.close()


@cli.command()
@click.argument("file_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@async_cmd
@pass_context
async def rm(ctx: Context, file_id: str, yes: bool) -> None:
    """Delete a file or folder.

    FILE_ID: The file/folder ID to delete
    """
    if not ctx.auth.is_logged_in():
        console.print("[red]Not logged in. Run 'xunlei login' first.[/red]")
        return

    if not yes:
        confirm = console.input(f"[yellow]Delete {file_id}? [y/N]: [/yellow]")
        if confirm.lower() != "y":
            console.print("Cancelled.")
            return

    try:
        with console.status("[bold green]Deleting..."):
            await ctx.api.delete_file(file_id)
        console.print("[green]Deleted successfully.[/green]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        await ctx.close()


@cli.command()
@click.argument("keyword")
@click.option("--limit", "-l", default=50, help="Max results")
@async_cmd
@pass_context
async def search(ctx: Context, keyword: str, limit: int) -> None:
    """Search files by keyword.

    KEYWORD: Search term
    """
    if not ctx.auth.is_logged_in():
        console.print("[red]Not logged in. Run 'xunlei login' first.[/red]")
        return

    try:
        with console.status(f"[bold green]Searching '{keyword}'..."):
            files = await ctx.api.search_files(keyword, limit)

        if not files:
            console.print("[yellow]No results found.[/yellow]")
            return

        table = create_files_table(files, f'Search: "{keyword}"')
        console.print(table)
        console.print(f"\n[dim]Found: {len(files)} items[/dim]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        await ctx.close()


@cli.command()
@click.argument("file_id")
@click.option("--debug", "-d", is_flag=True, help="Show raw API response")
@async_cmd
@pass_context
async def info(ctx: Context, file_id: str, debug: bool) -> None:
    """Show file details and download links.

    FILE_ID: The file ID to inspect
    """
    if not ctx.auth.is_logged_in():
        console.print("[red]Not logged in. Run 'xunlei login' first.[/red]")
        return

    try:
        with console.status("[bold green]Loading..."):
            file_info = await ctx.api.get_file_info(file_id, debug=debug)

        content = Text()
        content.append("ID: ", style="bold")
        content.append(f"{file_info.file_id}\n")
        content.append("Name: ", style="bold")
        content.append(f"{file_info.name}\n")
        content.append("Size: ", style="bold")
        content.append(f"{format_size(file_info.size)}\n")
        content.append("Type: ", style="bold")
        content.append(f"{file_info.kind}\n")
        content.append("MIME: ", style="bold")
        content.append(f"{file_info.mime_type}\n")
        content.append("Modified: ", style="bold")
        content.append(f"{file_info.modified_time}\n")

        if file_info.vip_download_link:
            content.append("\n[VIP Download]: ", style="bold green")
            content.append(f"{file_info.vip_download_link}\n", style="green")

        if file_info.web_content_link:
            content.append("[Normal Download]: ", style="bold yellow")
            content.append(f"{file_info.web_content_link}\n", style="yellow")

        panel = Panel(content, title=f"File: {file_info.name}", border_style="blue")
        console.print(panel)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        await ctx.close()


# ==================== Offline Download Commands ====================


@cli.command("cinema-save")
@click.argument("url")
@click.option("--folder", required=True, help="Cloud folder path, e.g. 家庭影院/星球大战系列")
@click.option("--expect-name", default="", help="Expected file name for duplicate checking")
@click.option("--execute", is_flag=True, help="Create missing folders and submit the task")
@async_cmd
@pass_context
async def cinema_save(ctx: Context, url: str, folder: str, expect_name: str, execute: bool) -> None:
    """Save a direct link to a cloud folder; preview by default."""
    try:
        source = validate_source(url)
        if not ctx.auth.is_logged_in():
            raise ValueError("Not logged in. Run 'xunlei login' first")

        folder_id, missing = await resolve_folder(ctx.api, folder, create=False)
        if not missing:
            duplicate = await find_duplicate(ctx.api, source, folder_id, expect_name)
            if duplicate:
                console.print(f"[yellow]Skipped: {duplicate}[/yellow]")
                return

        console.print(f"Target: {folder}")
        console.print(f"Missing folders: {' / '.join(missing) if missing else 'none'}")
        if not execute:
            console.print("[yellow]Preview only. Add --execute to submit.[/yellow]")
            return

        if missing:
            folder_id, _ = await resolve_folder(ctx.api, folder, create=True)
        task = await ctx.api.create_offline_task(source, folder_id)
        console.print(f"[green]Task created[/green]: {task.task_id}")
        console.print(f"Target folder ID: {folder_id}")
        console.print("Check task status and read back the target folder before reporting success.")
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise click.ClickException(str(exc)) from exc
    finally:
        await ctx.close()


@cli.command("offline")
@click.argument("url")
@click.argument("folder_id", required=False, default="")
@async_cmd
@pass_context
async def offline_cmd(ctx: Context, url: str, folder_id: str) -> None:
    """Create an offline download task.

    URL: Magnet link or HTTP/FTP URL
    FOLDER_ID: Target folder in cloud drive (empty for root)
    """
    if not ctx.auth.is_logged_in():
        console.print("[red]Not logged in. Run 'xunlei login' first.[/red]")
        return

    try:
        # Validate magnet link format before submitting
        if url.startswith("magnet:"):
            is_valid, err_msg = validate_magnet(url)
            if not is_valid:
                console.print(f"[red]Invalid magnet: {err_msg}[/red]")
                return

        with console.status("[bold green]Creating task..."):
            task = await ctx.offline.create_task(url, folder_id)

        console.print("[green]Task created![/green]")
        console.print(f"Task ID: [cyan]{task.task_id}[/cyan]")
        console.print(f"Name: {task.name}")
        console.print(f"\n[dim]Use 'xunlei offline-list' to check progress[/dim]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        await ctx.close()


@cli.command("offline-list")
@click.option("--limit", "-l", default=20, help="Number of tasks")
@click.option("--watch", "-w", is_flag=True, help="Watch mode (refresh every 10s)")
@async_cmd
@pass_context
async def offline_list(ctx: Context, limit: int, watch: bool) -> None:
    """List offline download tasks."""
    if not ctx.auth.is_logged_in():
        console.print("[red]Not logged in. Run 'xunlei login' first.[/red]")
        return

    try:
        while True:
            with console.status("[bold green]Loading..."):
                tasks = await ctx.offline.list_tasks(limit)

            if not tasks:
                console.print("[yellow]No tasks found.[/yellow]")
                return

            if watch:
                console.clear()

            table = create_tasks_table(tasks)
            console.print(table)

            if not watch:
                break

            await asyncio.sleep(10)

    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        await ctx.close()


@cli.command("offline-rm")
@click.argument("task_id")
@async_cmd
@pass_context
async def offline_rm(ctx: Context, task_id: str) -> None:
    """Delete an offline task.

    TASK_ID: The task ID to delete
    """
    if not ctx.auth.is_logged_in():
        console.print("[red]Not logged in. Run 'xunlei login' first.[/red]")
        return

    try:
        with console.status("[bold green]Deleting..."):
            await ctx.offline.delete_task(task_id)
        console.print("[green]Task deleted.[/green]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        await ctx.close()


@cli.command("offline-wait")
@click.argument("task_id")
@click.option("--timeout", "-t", default=3600, help="Timeout in seconds")
@click.option("--poll", "-p", default=10, help="Poll interval in seconds")
@async_cmd
@pass_context
async def offline_wait(ctx: Context, task_id: str, timeout: int, poll: int) -> None:
    """Wait for an offline task to complete.

    TASK_ID: The task ID to monitor
    """
    if not ctx.auth.is_logged_in():
        console.print("[red]Not logged in. Run 'xunlei login' first.[/red]")
        return

    try:
        with console.status(f"[bold green]Waiting...") as status:

            def on_progress(task: OfflineTask) -> None:
                status.update(
                    f"[bold]{task.name}[/bold] - {task.status} - {task.progress:.1f}%"
                )

            task = await ctx.offline.wait_for_completion(
                task_id,
                timeout=timeout,
                poll_interval=poll,
                progress_callback=on_progress,
            )

        if task.status == "completed":
            console.print("[bold green]Completed![/bold green]")
            console.print(f"File ID: [cyan]{task.file_id}[/cyan]")
            console.print(f"Use 'xunlei download {task.file_id}' to download")
        else:
            console.print(f"[bold red]Failed:[/bold red] {task.message}")

    except TimeoutError:
        console.print("[yellow]Timeout.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        await ctx.close()


# ==================== Download Commands ====================


@cli.command()
@click.argument("file_id")
@click.argument("output", required=False, default="")
@click.option("--no-vip", is_flag=True, help="Don't use VIP acceleration")
@click.option("--workers", "-w", default=8, help="Download threads")
@click.option("--all", "-a", is_flag=True, help="Auto download all files in folder (no prompt)")
@click.option("--bg", is_flag=True, help="Run download in background (daemon mode)")
@async_cmd
@pass_context
async def download(
    ctx: Context,
    file_id: str,
    output: str,
    no_vip: bool,
    workers: int,
    all: bool,
    bg: bool,
) -> None:
    """Download a file from cloud drive with VIP acceleration.

    FILE_ID: Cloud drive file ID
    OUTPUT: Output path (default: original filename)

    \b
    Examples:
        xunlei download <file_id>
        xunlei download <folder_id> ~/Downloads --all       # download all, no prompt
        xunlei download <folder_id> ~/Downloads --all --bg  # background
    """
    import sys

    # Handle background mode: respawn self without --bg
    if bg:
        import subprocess
        import time

        # Rebuild command line without --bg
        argv = sys.argv[1:]  # exclude script name
        argv = [a for a in argv if a != "--bg"]
        # Use nohup to survive terminal close
        cmd = ["nohup", sys.executable, "-m", "xunlei"] + argv
        log_file = os.path.expanduser("~/.config/xunlei-cli/download.log")
        with open(log_file, "a") as log:
            log.write(f"\n{'=' * 50}\n")
            log.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting: {' '.join(cmd)}\n")
            subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # Detach from parent process group
            )
        console.print(f"[green]Download started in background.[/green]")
        console.print(f"[dim]Log: {log_file}[/dim]")
        console.print(f"[dim]Check: tail -f {log_file}[/dim]")
        return

    if not ctx.auth.is_logged_in():
        console.print("[red]Not logged in. Run 'xunlei login' first.[/red]")
        return

    use_vip = not no_vip
    ctx.downloader.max_workers = workers

    try:
        file_info = await ctx.api.get_file_info(file_id)

        # Handle folder: list contents and let user pick (or auto --all)
        if file_info.kind == "drive#folder":
            console.print(f"[bold]{file_info.name}[/bold] is a folder.\n")
            files, _ = await ctx.api.list_files(parent_id=file_id)

            if not files:
                console.print("[yellow]Folder is empty.[/yellow]")
                return

            # Show files
            table = Table(title=f"Files in {file_info.name}", show_header=True)
            table.add_column("#", style="dim", width=4)
            table.add_column("ID", style="dim", no_wrap=True, width=20)
            table.add_column("Name", style="cyan")
            table.add_column("Size", justify="right", style="green")
            for i, f in enumerate(files):
                table.add_row(str(i), f.file_id[:16] + "...", f.name, format_size(f.size))
            console.print(table)

            if all:
                console.print(f"\n[dim]--all flag set, downloading all {len(files)} files...[/dim]\n")
                await _do_download_multi(ctx, files, output or ".", use_vip)
            else:
                choice = console.input("\nEnter # to download (or 'a' for all): ")
                if choice.lower() == 'a':
                    await _do_download_multi(ctx, files, output or ".", use_vip)
                else:
                    try:
                        idx = int(choice)
                        if 0 <= idx < len(files):
                            f = files[idx]
                            output_path = output or f.name
                            await _do_download(ctx, f.file_id, output_path, use_vip)
                        else:
                            console.print("[red]Invalid selection.[/red]")
                    except ValueError:
                        console.print("[red]Invalid input.[/red]")
            return

        # Single file download
        output_path = output or file_info.name
        await _do_download(ctx, file_id, output_path, use_vip)

    except Exception as e:
        console.print(f"[bold red]Failed:[/bold red] {e}")
        # Auto-debug: print raw file info
        try:
            import json
            raw = await ctx.api.get_file_info_raw(file_id)
            console.print("\n[dim]--- Debug: Raw API response ---[/dim]")
            console.print(json.dumps(raw, indent=2, ensure_ascii=False)[:2000])
            console.print("[dim]--- End debug ---[/dim]")
        except Exception:
            pass
        sys.exit(1)
    finally:
        await ctx.close()


@cli.command("download-url")
@click.argument("url")
@click.argument("output")
@click.option("--workers", "-w", default=8, help="Download threads")
@async_cmd
@pass_context
async def download_url_cmd(ctx: Context, url: str, output: str, workers: int) -> None:
    """Download from a direct URL.

    URL: Direct download URL
    OUTPUT: Output file path
    """
    ctx.downloader.max_workers = workers

    try:
        console.print("[bold]Downloading from URL...[/bold]")

        with Progress(
            TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            DownloadColumn(binary_units=True),
            "•",
            TransferSpeedColumn(),
            "•",
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task(
                "download", filename=Path(output).name, total=0
            )

            def on_progress(downloaded: int, total: int, speed: float) -> None:
                progress.update(task_id, completed=downloaded, total=total or 0)

            result_path = await ctx.downloader.download_url(
                url, output, progress_callback=on_progress
            )

        console.print(f"\n[bold green]Complete![/bold green]")
        console.print(f"Saved: [cyan]{result_path}[/cyan]")

    except Exception as e:
        console.print(f"[bold red]Failed:[/bold red] {e}")
        sys.exit(1)
    finally:
        await ctx.close()


@cli.command("download-magnet")
@click.argument("url")
@click.argument("output", required=False, default="")
@click.option("--no-vip", is_flag=True, help="Don't use VIP acceleration")
@click.option("--workers", "-w", default=8, help="Number of download threads")
@click.option(
    "--timeout", "-t", default=3600, help="Max wait time for offline task (seconds)"
)
@click.option("--all", "-a", is_flag=True, help="Auto download all files (no prompt)")
@click.option("--bg", is_flag=True, help="Run download in background (daemon mode)")
@async_cmd
@pass_context
async def download_magnet(
    ctx: Context,
    url: str,
    output: str,
    no_vip: bool,
    workers: int,
    timeout: int,
    all: bool,
    bg: bool,
) -> None:
    """One-click download magnet link: submit offline task -> wait -> download to local.

    URL: Magnet link (magnet:?xt=urn:btih:...)
    OUTPUT: Output folder path (optional, defaults to current dir)

    \b
    Examples:
        xunlei download-magnet "magnet:..." ~/Downloads
        xunlei download-magnet "magnet:..." ~/Downloads --all       # no prompt
        xunlei download-magnet "magnet:..." ~/Downloads --all --bg  # background
    """
    import sys

    # Handle background mode: respawn self without --bg
    if bg:
        import subprocess
        import time

        argv = [a for a in sys.argv[1:] if a != "--bg"]
        cmd = ["nohup", sys.executable, "-m", "xunlei"] + argv
        log_file = os.path.expanduser("~/.config/xunlei-cli/download.log")
        with open(log_file, "a") as log:
            log.write(f"\n{'=' * 50}\n")
            log.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting: {' '.join(cmd)}\n")
            subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        console.print(f"[green]Download started in background.[/green]")
        console.print(f"[dim]Log: {log_file}[/dim]")
        console.print(f"[dim]Check: tail -f {log_file}[/dim]")
        return

    if not ctx.auth.is_logged_in():
        console.print("[red]Not logged in. Run 'xunlei login' first.[/red]")
        return

    use_vip = not no_vip
    ctx.downloader.max_workers = workers

    try:
        # Step 1: Submit offline task
        console.print(f"[bold]Submitting offline task...[/bold]")
        console.print(f"URL: {url[:80]}...")

        with console.status("[bold green]Creating offline task..."):
            task = await ctx.offline.create_task(url)

        console.print(f"[green]Task created:[/green] {task.task_id}")
        console.print(f"Name: {task.name or '(pending)'}\n")

        # Step 2: Wait for completion
        console.print("[bold]Waiting for offline download to complete...[/bold]")
        console.print("[dim](Using Xunlei VIP servers to download)[/dim]\n")

        start_time = asyncio.get_event_loop().time()
        last_progress = -1

        while True:
            task = await ctx.offline.get_task(task.task_id)
            elapsed = asyncio.get_event_loop().time() - start_time

            # Show progress
            if task.progress != last_progress:
                bar = "█" * int(task.progress / 10) + "░" * (10 - int(task.progress / 10))
                console.print(
                    f"\r[bold]{task.name or 'Downloading'}[/bold] "
                    f"[{bar}] {task.progress:.1f}% | {task.status}",
                    end="",
                )
                last_progress = task.progress

            if task.status == "completed":
                console.print()  # New line after progress
                console.print(f"\n[bold green]Offline download complete![/bold green]")
                break

            if task.status == "failed":
                console.print()
                console.print(f"[bold red]Offline task failed:[/bold red] {task.message}")
                return

            if elapsed >= timeout:
                console.print()
                console.print(f"[yellow]Timeout after {timeout}s. Task still running.[/yellow]")
                console.print(f"Check later with: xunlei offline-list")
                return

            await asyncio.sleep(5)

        # Step 3: Get file info and handle folder
        if not task.file_id:
            console.print("[red]No file_id returned from offline task[/red]")
            return

        file_info = await ctx.api.get_file_info(task.file_id)
        output_dir = output or "."
        os.makedirs(output_dir, exist_ok=True)

        # Handle folder: list contents and let user pick (or auto --all)
        if file_info.kind == "drive#folder":
            console.print(f"\n[bold]Folder: {file_info.name}[/bold]")
            files, _ = await ctx.api.list_files(parent_id=task.file_id)
            files = [f for f in files if f.kind == "drive#file"]

            if not files:
                console.print("[yellow]Folder is empty.[/yellow]")
                return

            # Show files
            table = Table(title=f"Files ({len(files)})", show_header=True)
            table.add_column("#", style="dim", width=4)
            table.add_column("ID", style="dim", no_wrap=True, width=20)
            table.add_column("Name", style="cyan")
            table.add_column("Size", justify="right", style="green")
            for i, f in enumerate(files):
                table.add_row(str(i), f.file_id[:16] + "...", f.name, format_size(f.size))
            console.print(table)

            if all:
                console.print(f"\n[dim]--all flag set, downloading all {len(files)} files...[/dim]\n")
                await _do_download_multi(ctx, files, output_dir, use_vip)
            else:
                choice = console.input("\nEnter # to download (or 'a' for all): ")
                if choice.lower() == 'a':
                    await _do_download_multi(ctx, files, output_dir, use_vip)
                else:
                    try:
                        idx = int(choice)
                        if 0 <= idx < len(files):
                            f = files[idx]
                            out_path = os.path.join(output_dir, f.name)
                            await _do_download(ctx, f.file_id, out_path, use_vip)
                        else:
                            console.print("[red]Invalid selection.[/red]")
                    except ValueError:
                        console.print("[red]Invalid input.[/red]")
            return

        # Single file download
        output_path = os.path.join(output_dir, file_info.name)
        console.print(f"\n[bold]Downloading to local...[/bold]")
        console.print(f"File: {file_info.name}")
        console.print(f"Size: {format_size(file_info.size)}")
        console.print(
            f"VIP: {'[green]On[/green]' if use_vip else '[yellow]Off[/yellow]' }\n"
        )
        await _do_download(ctx, task.file_id, output_path, use_vip)

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        sys.exit(1)
    finally:
        await ctx.close()


@cli.command("dl")
@click.argument("url")
@click.argument("output", required=False, default="")
@click.option("--no-vip", is_flag=True, help="Don't use VIP acceleration")
@click.option("--workers", "-w", default=8, help="Number of download threads per file")
@click.option(
    "--timeout", "-t", default=3600, help="Max wait time for offline task (seconds)"
)
@click.option("--keep", is_flag=True, help="Keep files in cloud after download")
@async_cmd
@pass_context
async def dl(
    ctx: Context,
    url: str,
    output: str,
    no_vip: bool,
    workers: int,
    timeout: int,
    keep: bool,
) -> None:
    """Download magnet link locally: parse -> get files -> multi-thread download -> cleanup.

    URL: Magnet link (magnet:?xt=urn:btih:...)
    OUTPUT: Output folder path (optional, defaults to current dir)

    This command skips cloud storage - files are downloaded directly to local.
    """
    if not ctx.auth.is_logged_in():
        console.print("[red]Not logged in. Run 'xunlei login' first.[/red]")
        return

    use_vip = not no_vip
    ctx.downloader.max_workers = workers
    output_dir = output or "."
    os.makedirs(output_dir, exist_ok=True)

    task = None
    try:
        # Step 1: Submit offline task to get file list
        console.print(f"[bold]Parsing magnet link...[/bold]")
        console.print(f"URL: {url[:80]}...")

        with console.status("[bold green]Creating task..."):
            task = await ctx.offline.create_task(url)

        console.print(f"[green]Task created:[/green] {task.task_id}")

        # Step 2: Wait for completion
        console.print("\n[bold]Waiting for parse to complete...[/bold]")
        console.print("[dim](Xunlei VIP servers are resolving the magnet link)[/dim]\n")

        start_time = asyncio.get_event_loop().time()
        last_progress = -1

        while True:
            task = await ctx.offline.get_task(task.task_id)
            elapsed = asyncio.get_event_loop().time() - start_time

            if task.progress != last_progress:
                bar = "█" * int(task.progress / 10) + "░" * (10 - int(task.progress / 10))
                console.print(
                    f"\r[bold]{task.name or 'Resolving'}[/bold] "
                    f"[{bar}] {task.progress:.1f}% | {task.status}",
                    end="",
                )
                last_progress = task.progress

            if task.status == "completed":
                console.print()
                console.print(f"\n[bold green]Resolved![/bold green]")
                break

            if task.status == "failed":
                console.print()
                console.print(f"[bold red]Failed:[/bold red] {task.message}")
                return

            if elapsed >= timeout:
                console.print()
                console.print(f"[yellow]Timeout.[/yellow]")
                return

            await asyncio.sleep(5)

        # Step 3: Get file list
        if not task.file_id:
            console.print("[red]No file_id returned[/red]")
            return

        file_info = await ctx.api.get_file_info(task.file_id)
        files_to_download = []

        if file_info.kind == "drive#folder":
            console.print(f"\n[bold]Folder: {file_info.name}[/bold]")
            files, _ = await ctx.api.list_files(parent_id=task.file_id)
            files_to_download = [f for f in files if f.kind == "drive#file"]
        else:
            files_to_download = [file_info]

        if not files_to_download:
            console.print("[yellow]No files to download.[/yellow]")
            return

        console.print(f"Found [bold]{len(files_to_download)}[/bold] file(s)\n")

        # Step 4: Download each file with multi-threading
        success_count = 0
        failed_files = []

        for idx, f in enumerate(files_to_download, 1):
            console.print(
                f"[bold]({idx}/{len(files_to_download)})[/bold] Downloading: {f.name}"
            )

            try:
                # Get download URL
                with console.status("[dim]Getting download link..."):
                    dl_url = await ctx.api.get_download_link(f.file_id)

                # Download with progress
                output_path = os.path.join(output_dir, f.name)
                console.print(f"Size: {format_size(f.size)}")
                console.print(f"VIP: {'On' if use_vip else 'Off'}")

                with Progress(
                    TextColumn(
                        "[bold blue]{task.fields[filename]}", justify="right"
                    ),
                    BarColumn(bar_width=None),
                    "[progress.percentage]{task.percentage:>3.1f}%",
                    "•",
                    DownloadColumn(binary_units=True),
                    "•",
                    TransferSpeedColumn(),
                    "•",
                    TimeRemainingColumn(),
                    console=console,
                ) as progress:
                    dl_task = progress.add_task(
                        "download", filename=f.name, total=f.size
                    )

                    def on_progress(
                        downloaded: int, total: int, speed: float
                    ) -> None:
                        progress.update(dl_task, completed=downloaded, total=total)

                    await ctx.downloader.download_url(
                        dl_url,
                        output_path,
                        total_size=f.size,
                        progress_callback=on_progress,
                        file_id=f.file_id,
                    )

                console.print(f"[green]  Saved:[/green] {output_path}")
                success_count += 1

            except Exception as e:
                console.print(f"[red]  Failed:[/red] {e}")
                failed_files.append(f.name)
                continue

        # Step 5: Cleanup cloud files (unless --keep)
        if not keep and task:
            console.print("\n[dim]Cleaning up cloud files...[/dim]")
            try:
                # Try to delete the offline task
                await ctx.offline.delete_task(task.task_id)
                console.print("[dim]Cloud files deleted.[/dim]")
            except Exception:
                # Fallback: try to delete the file/folder directly
                try:
                    if task.file_id:
                        await ctx.api.delete_file(task.file_id)
                        console.print("[dim]Cloud files deleted.[/dim]")
                except Exception:
                    console.print("[dim]Cleanup skipped (files remain in cloud).[/dim]")

        # Summary
        console.print(f"\n[bold]{'=' * 50}[/bold]")
        console.print(f"[bold green]Downloaded: {success_count}/{len(files_to_download)}[/bold green]")
        if failed_files:
            console.print(f"[bold red]Failed: {', '.join(failed_files)}[/bold red]")
        console.print(f"[bold]Output: {os.path.abspath(output_dir)}[/bold]")
        console.print(f"[bold]{'=' * 50}[/bold]")

    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        sys.exit(1)
    finally:
        await ctx.close()


# ==================== User Commands ====================


@cli.command()
@async_cmd
@pass_context
async def user(ctx: Context) -> None:
    """Show user info and VIP status."""
    if not ctx.auth.is_logged_in():
        console.print("[red]Not logged in. Run 'xunlei login' first.[/red]")
        return

    try:
        with console.status("[bold green]Loading..."):
            info = await ctx.api.get_user_info()

        vip_colors = {"none": "red", "vip": "yellow", "svip": "green"}
        vip_color = vip_colors.get(info.vip_type, "white")

        content = Text()
        content.append("User: ", style="bold")
        content.append(f"{info.nickname}\n")
        content.append("ID: ", style="bold")
        content.append(f"{info.user_id}\n")
        content.append("VIP: ", style="bold")
        content.append(f"{info.vip_type.upper()}\n", style=vip_color)

        if info.vip_expiry:
            content.append("VIP Expiry: ", style="bold")
            content.append(f"{info.vip_expiry}\n")

        if info.total_space > 0:
            used_pct = (info.used_space / info.total_space) * 100
            content.append("\nStorage: ", style="bold")
            content.append(
                f"{format_size(info.used_space)} / {format_size(info.total_space)}"
            )
            content.append(f" ({used_pct:.1f}%)\n")

        panel = Panel(content, title="User Info", border_style="blue")
        console.print(panel)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        await ctx.close()


# ==================== WebUI Commands ====================


@cli.command()
@click.option("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
@click.option("--port", "-p", default=8080, help="Port to bind (default: 8080)")
@click.option("--bg", is_flag=True, help="Run in background")
@async_cmd
@pass_context
async def webui(ctx: Context, host: str, port: int, bg: bool) -> None:
    """Start the Web UI monitoring dashboard.

    Opens a web interface at http://host:port for monitoring
    download tasks, viewing history, and controlling downloads.

    \b
    Examples:
        xunlei webui                    # Start on default port 8080
        xunlei webui --port 3000        # Use custom port
        xunlei webui --bg               # Run in background
    """
    if bg:
        import subprocess
        import sys
        import time

        argv = [a for a in sys.argv[1:] if a != "--bg"]
        cmd = ["nohup", sys.executable, "-m", "xunlei"] + argv
        log_file = os.path.expanduser("~/.config/xunlei-cli/webui.log")
        with open(log_file, "a") as log:
            log.write(f"\n{'=' * 50}\n")
            log.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting webui\n")
            subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        console.print(f"[green]WebUI started in background.[/green]")
        console.print(f"[dim]Log: {log_file}[/dim]")
        console.print(f"[dim]Check: tail -f {log_file}[/dim]")
        return

    console.print(f"[bold blue]Starting WebUI...[/bold blue]")
    console.print(f"[dim]Open http://{host}:{port} in your browser[/dim]\n")

    from .webui import start_server
    try:
        await start_server(host=host, port=port)
    except KeyboardInterrupt:
        console.print("\n[yellow]WebUI stopped.[/yellow]")
