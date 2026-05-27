"""Web UI for xunlei-cli download monitoring.

Provides a web dashboard for:
- Real-time download task status
- Download history
- Download control (start/stop)
- File browsing
"""

import asyncio
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api import XunleiAPI
from .auth import AuthManager
from .config import Config
from .downloader import DownloadEngine
from .offline import OfflineManager

# ============ Database ============

DB_PATH = Path.home() / ".config" / "xunlei-cli" / "webui.db"


def init_db():
    """Initialize SQLite database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS download_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT NOT NULL,
            file_id TEXT,
            magnet TEXT,
            size INTEGER DEFAULT 0,
            downloaded INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            speed REAL DEFAULT 0,
            error TEXT,
            output_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS active_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            history_id INTEGER,
            file_name TEXT NOT NULL,
            file_id TEXT,
            magnet TEXT,
            size INTEGER DEFAULT 0,
            downloaded INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            speed REAL DEFAULT 0,
            progress REAL DEFAULT 0,
            error TEXT,
            output_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.commit()
    conn.close()


class TaskDB:
    """Database operations for tasks."""

    def __init__(self):
        self.db_path = str(DB_PATH)
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        init_db()  # Ensure tables exist

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def add_active_task(
        self, file_name: str, file_id: str = "", magnet: str = "",
        size: int = 0, output_path: str = ""
    ) -> int:
        conn = self._conn()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO active_tasks (file_name, file_id, magnet, size, status, output_path)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (file_name, file_id, magnet, size, output_path),
        )
        task_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return task_id

    def update_task_progress(
        self, task_id: int, downloaded: int, size: int, speed: float,
        status: str = "downloading"
    ):
        progress = (downloaded / size * 100) if size > 0 else 0
        conn = self._conn()
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE active_tasks SET downloaded=?, size=?, speed=?,
               status=?, progress=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (downloaded, size, speed, status, progress, task_id),
        )
        conn.commit()
        conn.close()

    def complete_task(self, task_id: int, status: str = "completed"):
        conn = self._conn()
        cursor = conn.cursor()

        # Move to history
        cursor.execute(
            """INSERT INTO download_history (file_name, file_id, magnet, size,
               downloaded, status, output_path, created_at, completed_at)
               SELECT file_name, file_id, magnet, size, downloaded, ?,
               output_path, created_at, CURRENT_TIMESTAMP
               FROM active_tasks WHERE id=?""",
            (status, task_id),
        )

        # Remove from active
        cursor.execute("DELETE FROM active_tasks WHERE id=?", (task_id,))
        conn.commit()
        conn.close()

    def fail_task(self, task_id: int, error: str):
        conn = self._conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE active_tasks SET status='failed', error=? WHERE id=?",
            (error, task_id),
        )
        conn.commit()
        conn.close()
        self.complete_task(task_id, "failed")

    def get_active_tasks(self) -> List[Dict]:
        conn = self._conn()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, file_name, file_id, magnet, size, downloaded,
               status, speed, progress, error, output_path, created_at
               FROM active_tasks ORDER BY created_at DESC"""
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "file_name": r[1],
                "file_id": r[2],
                "magnet": r[3],
                "size": r[4],
                "downloaded": r[5],
                "status": r[6],
                "speed": r[7],
                "progress": r[8],
                "error": r[9],
                "output_path": r[10],
                "created_at": r[11],
            }
            for r in rows
        ]

    def get_history(
        self, limit: int = 100, offset: int = 0
    ) -> List[Dict]:
        conn = self._conn()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, file_name, file_id, magnet, size, downloaded,
               status, error, output_path, created_at, completed_at
               FROM download_history ORDER BY completed_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "file_name": r[1],
                "file_id": r[2],
                "magnet": r[3],
                "size": r[4],
                "downloaded": r[5],
                "status": r[6],
                "error": r[7],
                "output_path": r[8],
                "created_at": r[9],
                "completed_at": r[10],
            }
            for r in rows
        ]

    def delete_task(self, task_id: int):
        conn = self._conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM active_tasks WHERE id=?", (task_id,))
        conn.commit()
        conn.close()

    def clear_completed(self):
        conn = self._conn()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM active_tasks WHERE status IN ('completed', 'failed')"
        )
        conn.commit()
        conn.close()


# ============ Global State ============

db = TaskDB()


def get_ctx():
    """Create a fresh context for background operations."""
    config = Config()
    auth = AuthManager(config)
    api = XunleiAPI(auth)
    offline = OfflineManager(api)
    downloader = DownloadEngine(api)
    return config, auth, api, offline, downloader


# ============ FastAPI App ============

app = FastAPI(title="Xunlei CLI WebUI", version="0.1.0")

# HTML templates directory
TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


# ============ HTML Template ============

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Xunlei CLI - Download Monitor</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d1117;
            color: #c9d1d9;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 {
            color: #58a6ff;
            font-size: 28px;
            margin-bottom: 5px;
        }
        .subtitle { color: #8b949e; font-size: 14px; margin-bottom: 25px; }
        .stats-bar {
            display: flex;
            gap: 15px;
            margin-bottom: 25px;
            flex-wrap: wrap;
        }
        .stat-card {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 15px 20px;
            min-width: 150px;
            flex: 1;
        }
        .stat-label { font-size: 12px; color: #8b949e; margin-bottom: 5px; }
        .stat-value { font-size: 24px; font-weight: 600; }
        .stat-value.blue { color: #58a6ff; }
        .stat-value.green { color: #3fb950; }
        .stat-value.yellow { color: #d29922; }
        .stat-value.red { color: #f85149; }

        .tabs {
            display: flex;
            gap: 0;
            margin-bottom: 20px;
            border-bottom: 1px solid #30363d;
        }
        .tab {
            padding: 12px 24px;
            cursor: pointer;
            color: #8b949e;
            border-bottom: 2px solid transparent;
            transition: all 0.2s;
        }
        .tab:hover { color: #c9d1d9; }
        .tab.active {
            color: #58a6ff;
            border-bottom-color: #58a6ff;
        }

        .panel { display: none; }
        .panel.active { display: block; }

        .toolbar {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
            align-items: center;
            flex-wrap: wrap;
        }
        .btn {
            padding: 8px 16px;
            border: 1px solid #30363d;
            background: #21262d;
            color: #c9d1d9;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            transition: all 0.2s;
        }
        .btn:hover { background: #30363d; }
        .btn.primary { background: #1f6feb; border-color: #1f6feb; color: white; }
        .btn.primary:hover { background: #388bfd; }
        .btn.danger { background: #da3633; border-color: #da3633; color: white; }
        .btn.danger:hover { background: #f85149; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }

        input, select {
            padding: 8px 12px;
            background: #21262d;
            border: 1px solid #30363d;
            color: #c9d1d9;
            border-radius: 6px;
            font-size: 13px;
        }
        input:focus, select:focus { outline: none; border-color: #58a6ff; }

        .task-list { display: flex; flex-direction: column; gap: 10px; }
        .task-card {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 15px;
            transition: border-color 0.2s;
        }
        .task-card:hover { border-color: #484f58; }
        .task-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }
        .task-name {
            font-weight: 600;
            font-size: 14px;
            word-break: break-all;
        }
        .task-status {
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
        }
        .status-pending { background: #bb800926; color: #d29922; }
        .status-downloading { background: #1f6feb26; color: #58a6ff; }
        .status-completed { background: #23863626; color: #3fb950; }
        .status-failed { background: #da363326; color: #f85149; }
        .status-retrying { background: #8957e526; color: #bc8cff; }

        .task-meta {
            display: flex;
            gap: 20px;
            font-size: 12px;
            color: #8b949e;
            margin-bottom: 10px;
            flex-wrap: wrap;
        }
        .task-meta span { display: flex; align-items: center; gap: 5px; }

        .progress-bar {
            width: 100%;
            height: 6px;
            background: #21262d;
            border-radius: 3px;
            overflow: hidden;
            margin-bottom: 8px;
        }
        .progress-fill {
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s;
        }
        .progress-fill.blue { background: #1f6feb; }
        .progress-fill.green { background: #238636; }
        .progress-fill.red { background: #da3633; }

        .task-footer {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 12px;
        }
        .task-actions { display: flex; gap: 5px; }

        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #8b949e;
        }
        .empty-state svg {
            width: 48px;
            height: 48px;
            margin-bottom: 15px;
            opacity: 0.5;
        }

        .toast {
            position: fixed;
            bottom: 20px;
            right: 20px;
            padding: 12px 20px;
            border-radius: 8px;
            color: white;
            font-size: 13px;
            z-index: 1000;
            animation: slideIn 0.3s ease;
        }
        .toast.success { background: #238636; }
        .toast.error { background: #da3633; }
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }

        .modal-overlay {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.7);
            display: none;
            justify-content: center;
            align-items: center;
            z-index: 1000;
        }
        .modal-overlay.active { display: flex; }
        .modal {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 12px;
            padding: 25px;
            min-width: 450px;
            max-width: 90vw;
        }
        .modal h3 { margin-bottom: 15px; color: #c9d1d9; }
        .modal-body { margin-bottom: 20px; }
        .modal-body input, .modal-body select {
            width: 100%;
            margin-bottom: 10px;
        }
        .modal-footer { display: flex; justify-content: flex-end; gap: 10px; }

        .log-container {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 15px;
            max-height: 400px;
            overflow-y: auto;
            font-family: 'SF Mono', Monaco, monospace;
            font-size: 12px;
            line-height: 1.6;
        }
        .log-line { color: #8b949e; }
        .log-line.error { color: #f85149; }
        .log-line.success { color: #3fb950; }
        .log-line.info { color: #58a6ff; }

        @media (max-width: 768px) {
            .stats-bar { flex-direction: column; }
            .task-header { flex-direction: column; align-items: flex-start; gap: 10px; }
            .toolbar { flex-direction: column; align-items: stretch; }
            .toolbar input, .toolbar select { width: 100%; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ Xunlei CLI Monitor</h1>
        <p class="subtitle">Download Task Management Dashboard</p>

        <div class="stats-bar">
            <div class="stat-card">
                <div class="stat-label">Active Downloads</div>
                <div class="stat-value blue" id="stat-active">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Completed Today</div>
                <div class="stat-value green" id="stat-completed">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Failed</div>
                <div class="stat-value red" id="stat-failed">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Total Downloaded</div>
                <div class="stat-value yellow" id="stat-total">0 GB</div>
            </div>
        </div>

        <div class="tabs">
            <div class="tab active" onclick="switchTab('active')">Active Tasks</div>
            <div class="tab" onclick="switchTab('history')">History</div>
            <div class="tab" onclick="switchTab('logs')">Logs</div>
        </div>

        <!-- Active Tasks Panel -->
        <div class="panel active" id="panel-active">
            <div class="toolbar">
                <button class="btn primary" onclick="showAddModal()">+ Add Download</button>
                <button class="btn" onclick="refreshTasks()">Refresh</button>
                <button class="btn danger" onclick="clearCompleted()">Clear Completed</button>
                <input type="text" id="search-active" placeholder="Search tasks..." oninput="filterTasks()">
            </div>
            <div class="task-list" id="active-tasks">
                <div class="empty-state">
                    <p>No active download tasks</p>
                </div>
            </div>
        </div>

        <!-- History Panel -->
        <div class="panel" id="panel-history">
            <div class="toolbar">
                <button class="btn" onclick="refreshHistory()">Refresh</button>
                <input type="text" id="search-history" placeholder="Search history..." oninput="filterHistory()">
            </div>
            <div class="task-list" id="history-tasks">
                <div class="empty-state">
                    <p>No download history</p>
                </div>
            </div>
        </div>

        <!-- Logs Panel -->
        <div class="panel" id="panel-logs">
            <div class="toolbar">
                <button class="btn" onclick="refreshLogs()">Refresh</button>
                <button class="btn" onclick="clearLogs()">Clear</button>
            </div>
            <div class="log-container" id="log-container">
                <div class="empty-state">
                    <p>No logs available</p>
                </div>
            </div>
        </div>
    </div>

    <!-- Add Download Modal -->
    <div class="modal-overlay" id="add-modal">
        <div class="modal">
            <h3>Add Download Task</h3>
            <div class="modal-body">
                <input type="text" id="dl-magnet" placeholder="magnet:?xt=urn:btih:...">
                <input type="text" id="dl-output" placeholder="~/Downloads (optional)">
            </div>
            <div class="modal-footer">
                <button class="btn" onclick="hideAddModal()">Cancel</button>
                <button class="btn primary" onclick="addDownload()">Start Download</button>
            </div>
        </div>
    </div>

    <div id="toast-container"></div>

    <script>
        let refreshInterval = null;
        let currentTab = 'active';

        function switchTab(tab) {
            currentTab = tab;
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
            event.target.classList.add('active');
            document.getElementById('panel-' + tab).classList.add('active');

            if (tab === 'active') refreshTasks();
            else if (tab === 'history') refreshHistory();
            else if (tab === 'logs') refreshLogs();
        }

        function formatSize(bytes) {
            if (bytes === 0) return '0 B';
            const units = ['B', 'KB', 'MB', 'GB', 'TB'];
            let i = 0;
            while (bytes >= 1024 && i < units.length - 1) {
                bytes /= 1024;
                i++;
            }
            return bytes.toFixed(1) + ' ' + units[i];
        }

        function formatSpeed(bps) {
            if (!bps || bps <= 0) return '-';
            return formatSize(bps) + '/s';
        }

        function formatTime(iso) {
            if (!iso) return '-';
            const d = new Date(iso);
            return d.toLocaleString();
        }

        async function refreshTasks() {
            try {
                const res = await fetch('/api/tasks');
                const tasks = await res.json();
                renderTasks(tasks);
                updateStats(tasks);
            } catch (e) {
                showToast('Failed to load tasks: ' + e.message, 'error');
            }
        }

        function renderTasks(tasks) {
            const container = document.getElementById('active-tasks');
            if (tasks.length === 0) {
                container.innerHTML = '<div class="empty-state"><p>No active download tasks</p></div>';
                return;
            }

            container.innerHTML = tasks.map(t => `
                <div class="task-card" data-name="${t.file_name.toLowerCase()}">
                    <div class="task-header">
                        <div class="task-name">${escapeHtml(t.file_name)}</div>
                        <span class="task-status status-${t.status}">${t.status}</span>
                    </div>
                    <div class="task-meta">
                        <span>Size: ${formatSize(t.size)}</span>
                        <span>Downloaded: ${formatSize(t.downloaded)}</span>
                        <span>Speed: ${formatSpeed(t.speed)}</span>
                        ${t.error ? '<span style="color:#f85149">Error: ' + escapeHtml(t.error) + '</span>' : ''}
                    </div>
                    <div class="progress-bar">
                        <div class="progress-fill ${t.status === 'failed' ? 'red' : t.status === 'completed' ? 'green' : 'blue'}"
                             style="width: ${t.progress || 0}%"></div>
                    </div>
                    <div class="task-footer">
                        <span>${(t.progress || 0).toFixed(1)}% | ${formatTime(t.created_at)}</span>
                        <div class="task-actions">
                            ${t.file_id ? `<button class="btn" onclick="browseFile('${t.file_id}')">Info</button>` : ''}
                            ${t.status === 'failed' ? `<button class="btn danger" onclick="deleteTask(${t.id})">Remove</button>` : ''}
                        </div>
                    </div>
                </div>
            `).join('');
        }

        function updateStats(tasks) {
            const active = tasks.filter(t => ['pending', 'downloading', 'retrying'].includes(t.status)).length;
            const completed = tasks.filter(t => t.status === 'completed').length;
            const failed = tasks.filter(t => t.status === 'failed').length;
            const total = tasks.reduce((sum, t) => sum + (t.downloaded || 0), 0);

            document.getElementById('stat-active').textContent = active;
            document.getElementById('stat-completed').textContent = completed;
            document.getElementById('stat-failed').textContent = failed;
            document.getElementById('stat-total').textContent = formatSize(total);
        }

        async function refreshHistory() {
            try {
                const res = await fetch('/api/history?limit=50');
                const items = await res.json();
                renderHistory(items);
            } catch (e) {
                showToast('Failed to load history: ' + e.message, 'error');
            }
        }

        function renderHistory(items) {
            const container = document.getElementById('history-tasks');
            if (items.length === 0) {
                container.innerHTML = '<div class="empty-state"><p>No download history</p></div>';
                return;
            }

            container.innerHTML = items.map(t => `
                <div class="task-card" data-name="${t.file_name.toLowerCase()}">
                    <div class="task-header">
                        <div class="task-name">${escapeHtml(t.file_name)}</div>
                        <span class="task-status status-${t.status}">${t.status}</span>
                    </div>
                    <div class="task-meta">
                        <span>Size: ${formatSize(t.size)}</span>
                        <span>Downloaded: ${formatSize(t.downloaded)}</span>
                        <span>Completed: ${formatTime(t.completed_at)}</span>
                        ${t.error ? '<span style="color:#f85149">' + escapeHtml(t.error) + '</span>' : ''}
                    </div>
                    ${t.output_path ? `<div class="task-meta"><span>Path: ${escapeHtml(t.output_path)}</span></div>` : ''}
                </div>
            `).join('');
        }

        async function refreshLogs() {
            try {
                const res = await fetch('/api/logs');
                const log = await res.text();
                const container = document.getElementById('log-container');
                if (!log || log.trim() === '') {
                    container.innerHTML = '<div class="empty-state"><p>No logs available</p></div>';
                    return;
                }
                const lines = log.split('\\n').filter(l => l.trim());
                container.innerHTML = lines.map(line => {
                    let cls = 'log-line';
                    if (line.includes('Error') || line.includes('FAIL')) cls += ' error';
                    else if (line.includes('Success') || line.includes('Complete')) cls += ' success';
                    else if (line.includes('Starting') || line.includes('Created')) cls += ' info';
                    return `<div class="${cls}">${escapeHtml(line)}</div>`;
                }).join('');
                container.scrollTop = container.scrollHeight;
            } catch (e) {
                showToast('Failed to load logs: ' + e.message, 'error');
            }
        }

        function clearLogs() {
            document.getElementById('log-container').innerHTML = '<div class="empty-state"><p>No logs available</p></div>';
        }

        function showAddModal() {
            document.getElementById('add-modal').classList.add('active');
        }
        function hideAddModal() {
            document.getElementById('add-modal').classList.remove('active');
        }

        async function addDownload() {
            const magnet = document.getElementById('dl-magnet').value.trim();
            const output = document.getElementById('dl-output').value.trim() || '~/Downloads';
            if (!magnet) {
                showToast('Please enter a magnet link', 'error');
                return;
            }
            hideAddModal();
            try {
                const res = await fetch('/api/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ magnet, output })
                });
                const result = await res.json();
                if (result.success) {
                    showToast('Download task started!', 'success');
                    refreshTasks();
                } else {
                    showToast('Failed: ' + (result.error || 'Unknown error'), 'error');
                }
            } catch (e) {
                showToast('Failed to start download: ' + e.message, 'error');
            }
        }

        async function deleteTask(taskId) {
            try {
                const res = await fetch(`/api/tasks/${taskId}`, { method: 'DELETE' });
                if (res.ok) {
                    showToast('Task removed', 'success');
                    refreshTasks();
                }
            } catch (e) {
                showToast('Failed to remove task: ' + e.message, 'error');
            }
        }

        async function clearCompleted() {
            try {
                const res = await fetch('/api/tasks/clear', { method: 'POST' });
                if (res.ok) {
                    showToast('Completed tasks cleared', 'success');
                    refreshTasks();
                }
            } catch (e) {
                showToast('Failed to clear: ' + e.message, 'error');
            }
        }

        function browseFile(fileId) {
            window.open(`/api/files/${fileId}/info`, '_blank');
        }

        function filterTasks() {
            const query = document.getElementById('search-active').value.toLowerCase();
            document.querySelectorAll('#active-tasks .task-card').forEach(card => {
                card.style.display = card.dataset.name.includes(query) ? '' : 'none';
            });
        }

        function filterHistory() {
            const query = document.getElementById('search-history').value.toLowerCase();
            document.querySelectorAll('#history-tasks .task-card').forEach(card => {
                card.style.display = card.dataset.name.includes(query) ? '' : 'none';
            });
        }

        function showToast(message, type) {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            toast.textContent = message;
            container.appendChild(toast);
            setTimeout(() => toast.remove(), 3000);
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        // Auto refresh
        refreshInterval = setInterval(() => {
            if (currentTab === 'active') refreshTasks();
        }, 2000);

        // Initial load
        refreshTasks();
    </script>
</body>
</html>
"""


# Write template file
def write_template():
    template_file = TEMPLATE_DIR / "index.html"
    template_file.write_text(INDEX_HTML, encoding="utf-8")


# ============ API Routes ============


@app.get("/", response_class=HTMLResponse)
async def index():
    write_template()
    return INDEX_HTML


@app.get("/api/tasks")
async def get_tasks():
    """Get all active download tasks."""
    return db.get_active_tasks()


@app.get("/api/history")
async def get_history(limit: int = Query(100), offset: int = Query(0)):
    """Get download history."""
    return db.get_history(limit, offset)


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: int):
    """Delete a task from active list."""
    db.delete_task(task_id)
    return {"success": True}


@app.post("/api/tasks/clear")
async def clear_completed():
    """Clear all completed/failed tasks from active list."""
    db.clear_completed()
    return {"success": True}


@app.post("/api/download")
async def start_download(request: Request):
    """Start a new download task."""
    data = await request.json()
    magnet = data.get("magnet", "").strip()
    output = data.get("output", "~/Downloads").strip() or "~/Downloads"

    if not magnet:
        return JSONResponse({"success": False, "error": "Magnet link is required"}, 400)

    # Validate
    from .utils import validate_magnet
    is_valid, err = validate_magnet(magnet)
    if not is_valid:
        return JSONResponse({"success": False, "error": err}, 400)

    # Start in background
    import subprocess
    import sys

    cmd = [sys.executable, "-m", "xunlei", "download-magnet", magnet, output, "--all", "--bg"]
    log_file = str(Path.home() / ".config" / "xunlei-cli" / "webui.log")
    with open(log_file, "a") as log:
        subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    # Track in DB
    task_id = db.add_active_task(
        file_name="Starting...",
        magnet=magnet,
        output_path=os.path.expanduser(output),
    )

    return {"success": True, "task_id": task_id}


@app.get("/api/files/{file_id}/info")
async def file_info(file_id: str):
    """Get file info from cloud drive."""
    try:
        _, auth, api, _, _ = get_ctx()
        if not auth.is_logged_in():
            return {"error": "Not logged in"}
        info = await api.get_file_info(file_id)
        await auth.close()
        return {
            "id": info.file_id,
            "name": info.name,
            "size": info.size,
            "kind": info.kind,
            "vip_link": info.vip_download_link[:100] + "..." if info.vip_download_link else None,
            "web_link": info.web_content_link[:100] + "..." if info.web_content_link else None,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/logs")
async def get_logs():
    """Get download logs."""
    log_file = Path.home() / ".config" / "xunlei-cli" / "download.log"
    if not log_file.exists():
        return ""
    try:
        # Return last 200 lines
        with open(log_file, "r") as f:
            lines = f.readlines()
        return "".join(lines[-200:])
    except Exception:
        return ""


# ============ Web UI Server ============


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    init_db()
    write_template()
    return app


async def start_server(host: str = "0.0.0.0", port: int = 8080):
    """Start the Web UI server with uvicorn."""
    import uvicorn
    app_instance = create_app()
    config = uvicorn.Config(app_instance, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


def main():
    """Entry point for `python -m xunlei.webui`."""
    import argparse
    parser = argparse.ArgumentParser(description="Xunlei CLI WebUI")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", "-p", type=int, default=8080, help="Port to bind (default: 8080)")
    args = parser.parse_args()

    print(f"Starting Xunlei WebUI on http://{args.host}:{args.port}")
    asyncio.run(start_server(args.host, args.port))


if __name__ == "__main__":
    main()
