#!/usr/bin/env python3
"""Inspect link syntax and metadata only; never fetch movie payloads."""

import argparse
import base64
import ftplib
import ipaddress
import json
import os
import re
import socket
import sys
import urllib.parse
import urllib.request


def decode_thunder(url):
    token = url.split("://", 1)[1].strip()
    token += "=" * ((4 - len(token) % 4) % 4)
    decoded = base64.b64decode(token, altchars=b"-_", validate=False).decode("utf-8", "replace")
    # Thunder adds an "AA" wrapper to encoded ed2k links.
    if decoded.startswith("AAed2k://"):
        decoded = decoded[2:]
    out = {"decoded_scheme": decoded.split("://", 1)[0] if "://" in decoded else "unknown",
           "decoded": decoded}
    parts = decoded.split("|")
    if len(parts) >= 5 and parts[1].lower() == "file":
        name = urllib.parse.unquote(parts[2])
        size = int(parts[3]) if parts[3].isdigit() else None
        out.update({"file_name": name, "file_size_bytes": size,
                    "file_size_gb": round(size / 1_000_000_000, 2) if size else None,
                    "file_size_gib": round(size / (1024 ** 3), 2) if size else None,
                    "hash": parts[4]})
    return out


def inspect_magnet(url):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    return {"type": "magnet", "info_hash": (q.get("xt") or [None])[0],
            "display_name": (q.get("dn") or [None])[0],
            "trackers": len(q.get("tr", [])),
            "metadata_available": False,
            "note": "磁力链接本身通常不含文件清单/大小；本工具不连接 DHT 或下载内容。"}


def inspect_torrent_file(path):
    data = open(path, "rb").read(12 * 1024 * 1024 + 1)
    if len(data) > 12 * 1024 * 1024:
        raise ValueError("种子元数据超过 12 MiB 限额")
    value, end = bdecode(data, 0)
    if end != len(data) or not isinstance(value, dict) or not isinstance(value.get(b"info"), dict):
        raise ValueError("无效的 .torrent 文件")
    info = value[b"info"]
    files = info.get(b"files")
    if files:
        rows = [{"path": b"/".join(x.get(b"path", [])).decode("utf-8", "replace"),
                 "size_bytes": x.get(b"length")} for x in files]
    else:
        rows = [{"path": info.get(b"name", b"").decode("utf-8", "replace"),
                 "size_bytes": info.get(b"length")}]
    total = sum(x["size_bytes"] or 0 for x in rows)
    return {"type": "torrent", "name": info.get(b"name", b"").decode("utf-8", "replace"),
            "files": rows, "total_size_bytes": total, "total_size_gb": round(total / 1_000_000_000, 2),
            "total_size_gib": round(total / (1024 ** 3), 2),
            "piece_length": info.get(b"piece length"), "metadata_only": True}


def bdecode(buf, pos):
    token = buf[pos:pos + 1]
    if token == b"i":
        end = buf.index(b"e", pos)
        return int(buf[pos + 1:end]), end + 1
    if token == b"l":
        out, pos = [], pos + 1
        while buf[pos:pos + 1] != b"e":
            item, pos = bdecode(buf, pos)
            out.append(item)
        return out, pos + 1
    if token == b"d":
        out, pos = {}, pos + 1
        while buf[pos:pos + 1] != b"e":
            key, pos = bdecode(buf, pos)
            out[key], pos = bdecode(buf, pos)
        return out, pos + 1
    match = re.match(rb"(\d+):", buf[pos:])
    if match:
        start = pos + len(match.group(0))
        end = start + int(match.group(1))
        return buf[start:end], end
    raise ValueError(f"无法解析种子元数据位置 {pos}")


def inspect_ftp(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.username or parsed.password:
        raise ValueError("出于凭据保护，不接受带用户名或密码的 FTP URL")
    if not parsed.hostname:
        raise ValueError("FTP URL 缺少主机")
    if parsed.hostname.lower().endswith((".local", ".localhost", ".internal")):
        raise ValueError("仅允许公开 FTP 主机")
    try:
        addresses = {ipaddress.ip_address(row[4][0]) for row in
                     socket.getaddrinfo(parsed.hostname, parsed.port or 21, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError(f"无法解析 FTP 主机：{exc}") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("仅允许解析到公网 IP 的 FTP 主机")
    ftp = ftplib.FTP(timeout=7)
    ftp.connect(parsed.hostname, parsed.port or 21)
    ftp.login("anonymous", "anonymous@")
    ftp.cwd(urllib.parse.unquote(parsed.path or "/"))
    rows = []
    try:
        for name, facts in ftp.mlsd():
            rows.append({"name": name, "type": facts.get("type"), "size_bytes": facts.get("size")})
    except ftplib.all_errors:
        for name in ftp.nlst():
            try:
                size = ftp.size(name)
            except ftplib.all_errors:
                size = None
            rows.append({"name": name, "type": None, "size_bytes": size})
    ftp.quit()
    return {"type": "ftp", "host": parsed.hostname, "path": parsed.path,
            "anonymous_listing": rows, "metadata_only": True}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("link", help="magnet:, thunder://, ftp://, or local .torrent path")
    ap.add_argument("--torrent-file", action="store_true", help="parse a local .torrent metadata file")
    args = ap.parse_args()
    try:
        if args.torrent_file:
            result = inspect_torrent_file(args.link)
        elif args.link.lower().startswith("thunder://"):
            result = {"type": "thunder", **decode_thunder(args.link)}
        elif args.link.lower().startswith("magnet:"):
            result = inspect_magnet(args.link)
        elif args.link.lower().startswith("ftp://"):
            result = inspect_ftp(args.link)
        elif args.link.lower().split("?", 1)[0].endswith(".torrent"):
            result = {"type": "torrent_url", "url": args.link,
                      "note": "不会自动获取种子文件；如需读取清单，先由用户提供 .torrent 文件。"}
        else:
            raise ValueError("不支持的链接类型")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"解析失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
