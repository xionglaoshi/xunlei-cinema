# Xunlei CLI - 迅雷命令行下载工具

A command-line tool for Xunlei (迅雷) cloud drive and VIP download acceleration.

---

## Features

- **VIP Download Acceleration** - Use Xunlei VIP high-speed servers
- **Multi-file Concurrent Download** - Download 3 files simultaneously
- **Background Mode** - Run downloads in background (`--bg`)
- **Auto Retry on 503** - Re-fetch expired download links automatically
- **Resume Support** - HTTP Range resume for large files
- **File Integrity Check** - Verify size after download
- **Magnet Link Support** - One-click download via offline + local fetch
- **Process-safe** - Multiple CLI instances can run simultaneously

---

## Installation

```bash
cd xunlei-cli
pip install -e .
```

**Dependencies**: `httpx`, `click`, `rich`, `filelock`

For Web UI dashboard, additional dependencies are installed automatically:
- `fastapi`, `uvicorn`, `jinja2`

---

## Login

Required before any download operation.

```bash
# Interactive login
python3 -m xunlei login

# With username
python3 -m xunlei login -u 18665718082

# With refresh token (skip password)
python3 -m xunlei login --refresh-token <token>
```

**Note**: First login on a new device requires SMS verification. The CLI will
guide you through opening a browser URL and entering the verification code.

**Token persistence**: Login state is saved in `~/.config/xunlei-cli/token.json`.
Token auto-refreshes before expiry. Safe for multiple concurrent processes.

---

## Complete Command Reference

### Authentication

| Command | Args | Description |
|---------|------|-------------|
| `login` | `-u username`, `--refresh-token`, `--creditkey` | Login to Xunlei |
| `logout` | None | Clear credentials |
| `user` | None | Show user info and VIP status |

### Cloud Drive

| Command | Args | Description |
|---------|------|-------------|
| `ls` | `[folder_id]`, `--limit` | List files in folder |
| `mkdir` | `<name> [parent_id]` | Create folder |
| `rm` | `<file_id>`, `--yes` | Delete file/folder |
| `search` | `<keyword>`, `--limit` | Search files |
| `info` | `<file_id>` | Show file details |

### Download (from cloud drive)

| Command | Args | Description |
|---------|------|-------------|
| `download` | `<file_id> [output]`, `--no-vip`, `--workers`, `--all`, `--bg` | Download from cloud |
| `download-url` | `<url> <output>`, `--workers` | Download direct URL |

### Magnet Link Download

| Command | Args | Description |
|---------|------|-------------|
| `offline` | `<url> [folder_id]` | Submit offline task only |
| `offline-list` | `--limit`, `--watch` | List offline tasks |
| `offline-rm` | `<task_id>` | Delete offline task |
| `offline-wait` | `<task_id>`, `--timeout` | Wait for task completion |
| `download-magnet` | `<url> [output]`, `--no-vip`, `--workers`, `--all`, `--bg` | Offline + download |
| **`dl`** | `<url> [output]`, `--no-vip`, `--workers`, `--keep`, `--bg` | **Recommended** |

### Web UI

| Command | Args | Description |
|---------|------|-------------|
| `webui` | `--host`, `--port`, `--bg` | Start Web UI monitoring dashboard |

---

## Recommended Commands for Agent

### 1. `dl` - Best for magnet links

Downloads magnet link to local, auto-cleans cloud files after.

```bash
# Basic usage
python3 -m xunlei dl "magnet:?xt=urn:btih:..." ~/Downloads

# With all options
python3 -m xunlei dl "magnet:?xt=urn:btih:..." ~/Downloads \
    --workers 8 \
    --bg
```

**Workflow**: Submit offline -> Wait parse -> Concurrent download -> Auto cleanup

**Flags**:
- `--workers N` - Download threads per file (default: 8)
- `--keep` - Keep files in cloud (default: auto-delete)
- `--bg` - Run in background

---

### 2. `download-magnet` - Keep in cloud

Same as `dl` but keeps files in cloud drive.

```bash
python3 -m xunlei download-magnet "magnet:?xt=urn:btih:..." ~/Downloads --all --bg
```

**Flags**:
- `--all` / `-a` - Auto download all files without prompt
- `--bg` - Run in background
- `--no-vip` - Disable VIP acceleration

---

### 3. `download` - Download from cloud drive

For downloading files already in cloud drive.

```bash
# Single file
python3 -m xunlei download <file_id> ~/Downloads

# Folder (interactive or --all)
python3 -m xunlei download <folder_id> ~/Downloads --all

# Background
python3 -m xunlei download <file_id> ~/Downloads --bg
```

**Flags**:
- `--all` / `-a` - If folder, download all files without prompt
- `--bg` - Run in background

---

## Background Mode (`--bg`)

Detaches download from terminal using `nohup` + `start_new_session`.

```bash
# Submit and return immediately
python3 -m xunlei download-magnet "magnet:..." ~/DL --all --bg
# Output:
# [green]Download started in background.
# [dim]Log: ~/.config/xunlei-cli/download.log

# Submit multiple tasks in same terminal
python3 -m xunlei download-magnet "magnet:...A" ~/DL --all --bg
python3 -m xunlei download-magnet "magnet:...B" ~/DL --all --bg
python3 -m xunlei download-magnet "magnet:...C" ~/DL --all --bg
```

**Check progress**:
```bash
tail -f ~/.config/xunlei-cli/download.log
```

**Stop background downloads**:
```bash
pkill -f "python3 -m xunlei download"
```

---

## Web UI Dashboard

Start a web-based monitoring dashboard for real-time download tracking.

```bash
# Start Web UI on default port 8080
python3 -m xunlei webui

# Custom port
python3 -m xunlei webui --port 3000

# Background mode
python3 -m xunlei webui --bg
```

**Features**:
- **Active Tasks** - Real-time download progress with speed and ETA (auto-refreshes every 2s)
- **Download History** - Completed/failed downloads with timestamps
- **Add Downloads** - Submit new magnet links from the browser
- **Logs Viewer** - Live download logs with color-coded output

**Access**: Open `http://localhost:8080` (or your `--host`:`--port`)

**Data Storage**: Task history is stored in `~/.config/xunlei-cli/webui.db`

---

## Multi-file Concurrent Download

When downloading a folder with `--all`, files are downloaded concurrently:
- Max 3 files at the same time (semaphore-controlled)
- Each file uses multi-threaded download (8 threads default)
- Total: up to 24 concurrent HTTP connections

```bash
# Download entire folder concurrently
python3 -m xunlei download <folder_id> ~/Downloads --all
```

---

## Typical Agent Workflows

### Workflow A: Download Magnet Link (Full Auto)

```bash
# Step 1: Ensure logged in (check user info)
python3 -m xunlei user

# Step 2: Submit background download (non-interactive)
python3 -m xunlei download-magnet "magnet:?xt=urn:btih:HASH&dn=NAME" ~/Downloads --all --bg

# Step 3: Monitor progress
sleep 30
tail -50 ~/.config/xunlei-cli/download.log
```

### Workflow B: Offline + Selective Download

```bash
# Step 1: Submit offline task
python3 -m xunlei offline "magnet:?xt=urn:btih:HASH"

# Step 2: Check task status
python3 -m xunlei offline-list

# Step 3: Get file_id from completed task, then download
python3 -m xunlei download <file_id> ~/Downloads
```

### Workflow C: Browse Cloud + Download

```bash
# List root folder
python3 -m xunlei ls

# List subfolder
python3 -m xunlei ls <folder_id>

# Download specific file
python3 -m xunlei download <file_id> ~/Downloads

# Or download entire folder
python3 -m xunlei download <folder_id> ~/Downloads --all
```

---

## Error Handling

| Error | Cause | Resolution |
|-------|-------|------------|
| `503 Service Unavailable` | Download link expired | Auto-retry with fresh link (5 retries, 10-22s delay) |
| `captcha_invalid` | Captcha token expired | Auto-refresh captcha token |
| `401 Unauthorized` | Access token expired | Auto-refresh token |
| `review_panel` | New device SMS verification | Follow browser verification flow |
| `Size mismatch` | Incomplete download | File deleted, retry from start |

---

## File Storage

| Path | Content |
|------|---------|
| `~/.config/xunlei-cli/config.json` | Device ID, username |
| `~/.config/xunlei-cli/token.json` | Access/refresh tokens (0600 permissions) |
| `~/.config/xunlei-cli/download.log` | Background download logs |
| `~/.config/xunlei-cli/webui.db` | Web UI task history (SQLite) |
| `~/.config/xunlei-cli/webui.log` | Web UI server logs |

---

## Technical Notes

- **Login flow**: Two-step (CoreLogin v3 -> SignIn v1) with captcha token refresh
- **Download engine**: Multi-thread (4MB chunks) -> fallback single-thread resume on 503
- **Retry strategy**: Exponential backoff, 5 retries max, 10-22s delay for 503
- **Process safety**: FileLock on token.json, atomic writes (.tmp -> rename)
- **Connection**: HTTP/1.1 with keepalive, connection pooling (20 max)
