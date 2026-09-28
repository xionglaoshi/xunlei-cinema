#!/usr/bin/env python3
"""Read the cloud catalogue; preview by default, --execute writes private local files only."""
import argparse
import asyncio
import copy
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / 'private'
STATE = PRIVATE / '影片库.json'
DOCUMENT = ROOT / '影片库.md'
VIDEO = {'.mkv', '.mp4', '.avi', '.mov', '.m2ts', '.ts', '.webm', '.wmv', '.flv', '.iso'}


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def safe_url(value):
    if not value:
        return ''
    p = urlsplit(value)
    if p.scheme not in {'https', 'http', 'ftp', 'magnet', 'thunder', 'ed2k'}:
        raise ValueError('Unsupported source scheme')
    secret_keys = {'access_token', 'refresh_token', 'captcha_token', 'authorization', 'signature', 'x-amz-signature'}
    if p.username or p.password or secret_keys.intersection(k.lower() for k in parse_qs(p.query)):
        raise ValueError('Do not store credentials or signed playback URLs as sources')
    return value


def merge(previous, snapshot, timestamp):
    """Keep each cloud file ID and its source/history across rename, move, deletion and return."""
    records = copy.deepcopy(previous)
    for fid, item in snapshot.items():
        row = records.setdefault(fid, {'file_id': fid, 'first_seen': timestamp,
                                      'sources': [], 'history': []})
        row.setdefault('first_seen', timestamp)
        changes = {k: {'before': row.get(k), 'after': v} for k, v in item.items()
                   if k in {'name', 'folder', 'size_bytes', 'status'} and row.get(k) != v}
        if changes:
            row['history'].append({'at': timestamp, 'changes': changes})
        if item['status'] == '已删除' and row.get('status') != '已删除':
            row['deletion_detected_at'] = timestamp
        row.update(item)
        row['last_checked'] = timestamp
        if item['status'] in {'在库', '已移出家庭影院'}:
            row['last_seen'] = timestamp
    return records


def cell(value):
    return html.escape(str(value or '待补录')).replace('|', '&#124;').replace('\n', '<br>')


def render(state):
    rows = list(state['records'].values())
    active = [r for r in rows if r['status'] == '在库']
    total = sum(r.get('size_bytes', 0) for r in active)
    lines = ['# 影片库', '', '> 私人资料：禁止上传任何 Git 仓库、公开发布或随技能打包。',
             '> 仅在用户明确要求“更新影片库”时刷新；允许按用户指令写入私有 WIKI，须同时排除 WIKI 的 Git 同步。', '',
             '更新时间：' + state['updated_at'], '',
             f'家庭影院在库视频文件：{len(active)} 个；文件大小合计：{total / 10**12:.3f} TB（十进制）。',
             '这是文件大小合计，不代表整个账号的实际配额或剩余空间。记录以文件为单位，剧集每集、同片不同版本分别保留。',
             '删除时间表示首次检测到删除的时间；原始来源缺失时标为待补录，不使用临时播放地址替代。', '',
             '## 更新提示', '']
    lines += ['- ' + cell(w) for w in state.get('warnings', [])] or ['本次无读取异常。']
    if state.get('source_check_at'):
        lines += ['', '来源链接验证时间：' + state['source_check_at'],
                  '验证仅限资源预解析／分享可访问性，未提交下载或转存；不能证明离线一定完成。']
    for status in ['在库', '已删除', '已移出家庭影院', '待核实']:
        group = sorted((r for r in rows if r['status'] == status), key=lambda r: (r.get('folder', ''), r.get('name', '')))
        lines += ['', f'## {status}（{len(group)}）', '',
                  '| 自定义文件名 | 所在／最后目录 | 大小 GB | 原始链接与来源证据 | 原始文件名 | 文件 ID | 最近确认／删除检测 |',
                  '|---|---|---:|---|---|---|---|']
        for r in group:
            source = '<br>'.join(cell(s.get('url')) + '；' + cell(s.get('evidence')) +
                                ('；来源页：' + cell(s['page']) if s.get('page') else '') +
                                ('；提取码：' + cell(s['pass_code']) if s.get('pass_code') else '')
                                + ('；验证：' + cell(s['verification'].get('status')) + '；' +
                                   cell(s['verification'].get('target_match')) if s.get('verification') else '')
                                for s in r.get('sources', [])) or '待补录'
            lines.append('| ' + ' | '.join([cell(r.get('name')), cell(r.get('folder')),
                         f"{r.get('size_bytes', 0) / 10**9:.2f}", source, cell(r.get('original_name')),
                         cell(r['file_id']), cell(r.get('deletion_detected_at') if status == '已删除' else r.get('last_seen'))]) + ' |')
    lines += ['', '## 名称、目录与状态历史', '']
    for r in sorted(rows, key=lambda x: x.get('name', '')):
        events = r.get('history', [])
        if events:
            lines += [f"- **{cell(r.get('name'))}**（{cell(r['file_id'])}）"]
            for e in events:
                changes = '；'.join(f"{cell(k)}：{cell(v['before'])} → {cell(v['after'])}" for k, v in e['changes'].items())
                lines.append(f"  - {e['at']}：{changes}")
    return '\n'.join(lines) + '\n'


async def scan(old):
    from xunlei.api import XunleiAPI
    from xunlei.auth import AuthManager, DRIVE_API_URL
    from xunlei.config import Config
    auth = AuthManager(Config())
    api = XunleiAPI(auth)
    try:
        if not auth.is_logged_in() or not auth.user_id:
            raise RuntimeError('Independent Xunlei login required')
        account = hashlib.sha256(str(auth.user_id).encode()).hexdigest()
        if old.get('account') and old['account'] != account:
            raise RuntimeError('Account changed; refusing to overwrite this library')

        async def pages(endpoint, key, params):
            token, seen = '', set()
            result = []
            while True:
                data = await api._request('GET', DRIVE_API_URL + endpoint,
                                          params={**params, 'page_token': token, 'limit': '100'})
                if not isinstance(data.get(key), list):
                    raise RuntimeError('Incomplete listing response: ' + key)
                result.extend(data[key])
                token = data.get('next_page_token') or ''
                if not token:
                    return result
                if token in seen:
                    raise RuntimeError('Repeated pagination token')
                seen.add(token)

        async def listing(parent):
            return await pages('/files', 'files', {'parent_id': parent, 'space': '', '__type': 'drive',
                               'refresh': 'true', 'filters': '{"trashed":{"eq":false}}'})

        roots = [f for f in await listing('') if f.get('name') == '家庭影院' and f.get('kind') == 'drive#folder']
        if len(roots) != 1:
            raise RuntimeError('家庭影院 missing or ambiguous; existing catalogue preserved')
        root_id = roots[0]['id']
        if old.get('root_id') and old['root_id'] != root_id:
            raise RuntimeError('家庭影院 folder ID changed; manual reconciliation required')
        snapshot, seen_folders, seen_files = {}, set(), set()
        sources, warnings = {}, []

        def fields(f, folder, status):
            if not f.get('id') or not isinstance(f.get('name'), str) or 'size' not in f:
                raise RuntimeError('Incomplete file metadata')
            params = f.get('params') or {}
            for value, evidence in [(f.get('original_url'), '迅雷文件 original_url 字段'),
                                    (params.get('url'), '迅雷文件 params.url 字段；可能指向多文件种子，召回需按原名核对')]:
                if isinstance(value, str) and value:
                    try:
                        sources.setdefault(f['id'], []).append({'url': safe_url(value), 'evidence': evidence})
                    except ValueError:
                        warnings.append('忽略非持久或含凭证的来源字段：' + f['id'])
            share_id = params.get('share_id')
            if isinstance(share_id, str) and share_id and all(c.isalnum() or c in '-_' for c in share_id):
                sources.setdefault(f['id'], []).append({'url': 'https://pan.xunlei.com/s/' + share_id,
                    'evidence': '迅雷文件 params.share_id 字段；提取码未由接口提供，链接未重验'})
            return {'name': f['name'], 'folder': folder, 'parent_id': f.get('parent_id', ''),
                    'size_bytes': int(f.get('size') or 0), 'status': status,
                    'created_time': f.get('created_time', ''), 'phase': f.get('phase', ''),
                    'content_hash': f.get('hash', ''), 'original_file_index': f.get('original_file_index')}

        async def walk(fid, path):
            if fid in seen_folders:
                raise RuntimeError('Directory traversal cycle')
            seen_folders.add(fid)
            for f in await listing(fid):
                if not f.get('id') or f['id'] in seen_files:
                    raise RuntimeError('Missing or duplicate cloud ID')
                seen_files.add(f['id'])
                if f.get('trashed'):
                    raise RuntimeError('Unexpected trashed file in active listing')
                if f.get('kind') == 'drive#folder':
                    await walk(f['id'], path + '/' + f['name'])
                elif Path(f.get('name', '')).suffix.lower() in VIDEO or f['id'] in old.get('records', {}):
                    snapshot[f['id']] = fields(f, path, '在库')
            print(f'已读取目录 {len(seen_folders)}，视频文件 {len(snapshot)}', flush=True)

        await walk(root_id, '家庭影院')
        for fid, r in old.get('records', {}).items():
            if fid in snapshot:
                continue
            try:
                f = await api.get_file_info_raw(fid)
                if f.get('id') != fid:
                    raise RuntimeError('File lookup returned unexpected ID')
                if f.get('trashed') is True:
                    snapshot[fid] = {**{k: r.get(k) for k in ('name', 'folder', 'size_bytes')}, 'status': '已删除'}
                else:
                    parent, chain, visited = f.get('parent_id'), [], set()
                    while parent:
                        if parent in visited:
                            raise RuntimeError('Parent directory cycle')
                        visited.add(parent)
                        p = await api.get_file_info_raw(parent)
                        if p.get('id') != parent or p.get('trashed'):
                            raise RuntimeError('Parent unavailable')
                        chain.insert(0, p['name'])
                        parent = p.get('parent_id')
                    if root_id in visited:
                        raise RuntimeError('File appeared after scan; run again')
                    snapshot[fid] = fields(f, '/'.join(chain) or '/', '已移出家庭影院')
            except Exception as exc:
                response = getattr(exc, 'response', None)
                try:
                    code = response.json().get('error') if response is not None else ''
                except Exception:
                    code = ''
                confirmed = code in {'file_not_found', 'file_not_exist'}
                # A generic 404, authentication problem or permission error is not deletion proof.
                status = '已删除' if confirmed else ('已删除' if r.get('status') == '已删除' else '待核实')
                snapshot[fid] = {**{k: r.get(k) for k in ('name', 'folder', 'size_bytes')}, 'status': status}
                if not confirmed:
                    warnings.append('未能核实文件状态：' + fid + '（' + type(exc).__name__ + '）')
        try:
            tasks = await pages('/tasks', 'tasks', {'type': 'offline'})
            for task in tasks:
                parsed = api._parse_task(task)
                task_url = parsed.url or (task.get('params') or {}).get('url')
                if parsed.file_id and task_url:
                    sources.setdefault(parsed.file_id, []).append({'url': safe_url(task_url),
                        'evidence': '迅雷离线任务与目标文件 ID 对应：' + parsed.task_id})
        except Exception as exc:
            warnings.append('离线任务来源读取不完整，保留旧来源：' + type(exc).__name__)
        return account, root_id, snapshot, sources, warnings
    finally:
        await api.close()
        await auth.close()


async def run(args):
    old = json.loads(STATE.read_text()) if STATE.exists() else {'schema': 1, 'records': {}}
    if old.get('schema') != 1:
        raise ValueError('Unsupported library schema')
    if args.history:
        for r in json.loads(args.history.read_text()):
            fid = r['id']
            old['records'].setdefault(fid, {'file_id': fid, 'name': r['name'],
                'original_name': r['name'], 'folder': '家庭影院/' + r['folder'],
                'size_bytes': int(r['size']), 'status': '待核实', 'history': [], 'sources': [],
                'historical_evidence': str(args.history.relative_to(ROOT))})
    account, root_id, snapshot, sources, warnings = await scan(old)
    timestamp = now()
    records = merge(old['records'], snapshot, timestamp)
    if args.sources:
        for s in json.loads(args.sources.read_text()):
            if s['file_id'] not in records or not s.get('evidence'):
                raise ValueError('Source needs known file_id and explicit evidence')
            entry = {'url': safe_url(s['url']), 'evidence': s['evidence']}
            for k in ['page', 'pass_code', 'original_name']:
                if s.get(k):
                    entry[k] = safe_url(s[k]) if k == 'page' else s[k]
            sources.setdefault(s['file_id'], []).append(entry)
    for fid, entries in sources.items():
        if fid not in records:
            continue
        for entry in entries:
            if not any(all(existing.get(k) == v for k, v in entry.items()) for existing in records[fid]['sources']):
                records[fid]['sources'].append(entry)
            if entry.get('original_name'):
                records[fid].setdefault('original_name', entry['original_name'])
    state = {'schema': 1, 'account': account, 'root_id': root_id, 'updated_at': timestamp,
             'warnings': warnings, 'records': records}
    if old.get('source_check_at'):
        state['source_check_at'] = old['source_check_at']
    counts = {s: sum(r['status'] == s for r in records.values()) for s in ['在库', '已删除', '已移出家庭影院', '待核实']}
    counts['有来源记录'] = sum(bool(r.get('sources')) for r in records.values())
    print(json.dumps({'execute': args.execute, 'counts': counts, 'warnings': warnings}, ensure_ascii=False))
    if not args.execute:
        return
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    PRIVATE.chmod(0o700)
    # State is authoritative. If rendering is interrupted, rerunning regenerates Markdown from it.
    backup = PRIVATE / 'backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True, mode=0o700)
    for p in (STATE, DOCUMENT):
        if p.exists():
            atomic_write(backup / p.name, p.read_text())
    atomic_write(STATE, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    atomic_write(DOCUMENT, render(state))
    print('已更新私人影片库：' + str(DOCUMENT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Write local private catalogue, never mutate cloud')
    parser.add_argument('--dry-run', action='store_true', help='Read-only preview (default)')
    parser.add_argument('--history', type=Path, help='Import a previous confirmed cloud inventory')
    parser.add_argument('--sources', type=Path, help='Import explicit per-file provenance JSON')
    args = parser.parse_args()
    if args.execute and args.dry_run:
        parser.error('Choose --execute or --dry-run')
    # Advisory lock without creating files during dry-run; protects against concurrent writers.
    with open(__file__, 'r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(run(args))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('更新停止，未完成；错误类型：' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
