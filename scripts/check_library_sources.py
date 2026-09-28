#!/usr/bin/env python3
"""Preview library source checks; --execute runs metadata-only checks and saves a private report."""
import argparse
import asyncio
from collections import Counter
import fcntl
import hashlib
import json
import re
from urllib.parse import urlsplit, parse_qs

from cinema_library import STATE, PRIVATE, DOCUMENT, atomic_write, now, render
from xunlei.api import XunleiAPI
from xunlei.auth import AuthManager, DRIVE_API_URL
from xunlei.config import Config


class MetadataOnlyAPI(XunleiAPI):
    async def _request(self, method, url, **kwargs):
        allowed = (method, url) in {
            ('POST', DRIVE_API_URL + '/resource/list'),
            ('GET', DRIVE_API_URL + '/share'),
        }
        if not allowed:
            raise RuntimeError('Non-preview endpoint blocked')
        return await super()._request(method, url, **kwargs)


def flatten(data):
    files, incomplete = [], False
    def walk(branch):
        nonlocal incomplete
        incomplete = incomplete or bool(branch.get('next_page_token'))
        for node in branch.get('resources', []):
            if node.get('is_dir'):
                if not isinstance(node.get('dir'), dict):
                    incomplete = True
                else:
                    walk(node['dir'])
            else:
                files.append({'name': node.get('name', ''), 'size_bytes': int(node.get('file_size') or 0),
                              'index': node.get('file_index')})
    walk(data)
    return files, incomplete


def match(row, files):
    sized = [f for f in files if f['size_bytes'] == row['size_bytes']]
    names = {n for n in [row.get('original_name'), row.get('name')] if n}
    if any(f['name'] in names for f in sized):
        return '原名与字节大小匹配'
    if len(sized) == 1:
        return '仅字节大小唯一匹配，片名待核实'
    return '未匹配到库中目标文件'


async def run(args):
    old_bytes = STATE.read_bytes()
    library = json.loads(old_bytes)
    if args.recover_codes:
        # Use existing local source evidence only; never guess a share password.
        codes = {}
        folder = STATE.parents[1] / 'var'
        paths = set()
        for pattern in ['*-live.json', '*inventory*.json', '*test.json', '*analyzed.json', '*analysis.json', '*.html']:
            paths.update(folder.glob(pattern))
        for path in paths:
            for hit in re.finditer(r'https://pan\.xunlei\.com/s/([A-Za-z0-9_-]+)\?pwd=([A-Za-z0-9]{4,8})(?![A-Za-z0-9])', path.read_text(errors='replace')):
                codes.setdefault(hit[1], set()).add(hit[2])
        for row in library['records'].values():
            for source in row.get('sources', []):
                p = urlsplit(source['url'])
                if p.hostname != 'pan.xunlei.com' or not p.path.startswith('/s/'):
                    continue
                choices = codes.get(p.path.split('/')[2], set())
                if len(choices) == 1 and not source.get('pass_code') and not parse_qs(p.query).get('pwd'):
                    source['pass_code'] = next(iter(choices))
                    source['pass_code_evidence'] = '本机历史来源记录中相同分享 ID，唯一提取码'
                    if source.get('verification'):
                        source.setdefault('verification_history', []).append(source.pop('verification'))
    links = {}
    for fid, row in library['records'].items():
        for s in row.get('sources', []):
            if args.only_unchecked and s.get('verification'):
                continue
            code = s.get('pass_code') or (parse_qs(urlsplit(s['url']).query).get('pwd') or [''])[0]
            links.setdefault((s['url'], code), set()).add(fid)
    print(json.dumps({'unique_checks': len(links), 'execute_metadata_checks': args.execute,
                      'cloud_task_creation': False}, ensure_ascii=False), flush=True)
    if not args.execute:
        return
    auth = AuthManager(Config())
    api = MetadataOnlyAPI(auth)
    results = []
    try:
        if not auth.is_logged_in():
            raise RuntimeError('Independent login required')
        if hashlib.sha256(str(auth.user_id).encode()).hexdigest() != library['account']:
            raise RuntimeError('Account mismatch')
        for (url, code), ids in links.items():
            p = urlsplit(url)
            entry = {'url': url, 'pass_code': code, 'checked_at': now(), 'file_ids': sorted(ids),
                     'submitted': False, 'download_completed_tested': False}
            try:
                if p.hostname == 'pan.xunlei.com' and p.path.startswith('/s/'):
                    entry['route'] = '分享转存；不适用添加链接下载'
                    response = await api._request('GET', DRIVE_API_URL + '/share',
                        params={'share_id': p.path.split('/')[2], 'pass_code': code, 'limit': '100'})
                    status = response.get('share_status', 'UNKNOWN')
                    entry['status'] = '分享可访问，需转存' if status == 'OK' else '分享受限：' + str(status)
                    entry['target_file_checked'] = False
                elif p.scheme in {'magnet', 'thunder', 'ed2k', 'http', 'https', 'ftp'}:
                    entry['route'] = '添加链接的资源预解析'
                    response = await api._request('POST', DRIVE_API_URL + '/resource/list',
                                                  json={'urls': url, 'with': ['file_category']})
                    branch = response.get('list')
                    if not isinstance(branch, dict) or not isinstance(branch.get('resources'), list):
                        raise ValueError('Missing resource list')
                    files, incomplete = flatten(branch)
                    entry.update({'status': '已解析文件清单' if files else '未解析出文件清单',
                                  'files': files, 'incomplete': incomplete,
                                  'matches': {fid: match(library['records'][fid], files) for fid in ids}})
                else:
                    entry['status'] = '未支持的链接类型'
            except Exception as exc:
                response = getattr(exc, 'response', None)
                entry['status'] = '检查失败'
                entry['error_type'] = type(exc).__name__
                if response is not None:
                    entry['http_status'] = response.status_code
                    try:
                        payload = response.json()
                        entry['error_code'] = str(payload.get('error', payload.get('error_code', '')))[:100]
                    except ValueError:
                        pass
                # Do not print API exception bodies, headers, credentials or captcha details.
            results.append(entry)
            if len(results) % 5 == 0 or len(results) == len(links):
                print(json.dumps({'checked': len(results), 'total': len(links),
                                  'status_counts': dict(Counter(r['status'] for r in results))}, ensure_ascii=False), flush=True)
            if entry.get('http_status') in {401, 403, 429}:
                print('认证或限流阻塞；余下链接保留未测试状态', flush=True)
                break
            await asyncio.sleep(.15)
    finally:
        await api.close()
        await auth.close()
    report = {'checked_at': now(), 'total': len(links), 'checked': len(results),
              'no_task_submitted': True, 'results': results}
    stamp = report['checked_at'].replace(':', '-').replace('+', '_')
    path = PRIVATE / ('链接验证-' + stamp + '.json')
    atomic_write(path, json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    with open(STATE.parents[1] / 'scripts/cinema_library.py') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if STATE.read_bytes() != old_bytes:
            raise RuntimeError('Library changed concurrently; report saved, catalogue not overwritten')
        backup = PRIVATE / 'backups' / ('link-check-' + stamp)
        for f in [STATE, DOCUMENT]:
            atomic_write(backup / f.name, f.read_text())
        lookup = {(e['url'], e['pass_code']): e for e in results}
        for fid, row in library['records'].items():
            for s in row.get('sources', []):
                code = s.get('pass_code') or (parse_qs(urlsplit(s['url']).query).get('pwd') or [''])[0]
                e = lookup.get((s['url'], code))
                if e:
                    s['verification'] = {k: e[k] for k in ['checked_at', 'status', 'route', 'submitted', 'incomplete', 'http_status', 'error_code'] if k in e}
                    s['verification']['target_match'] = e.get('matches', {}).get(fid, '未核对目标文件')
        library['source_check_at'] = report['checked_at']
        atomic_write(STATE, json.dumps(library, ensure_ascii=False, indent=2) + '\n')
        atomic_write(DOCUMENT, render(library))
    print('已保存私人报告并标注影片库；没有提交下载或转存任务', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--execute', action='store_true', help='Metadata-only network checks plus private local report')
    ap.add_argument('--dry-run', action='store_true', help='Only show number of checks (default)')
    ap.add_argument('--recover-codes', action='store_true', help='Recover unique share codes from local historical source records')
    ap.add_argument('--only-unchecked', action='store_true', help='Check only unverified or newly enriched source records')
    args = ap.parse_args()
    if args.execute and args.dry_run:
        ap.error('Choose one mode')
    asyncio.run(run(args))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('验证停止：' + type(exc).__name__)
        raise SystemExit(1)
