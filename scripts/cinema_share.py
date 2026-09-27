#!/usr/bin/env python3
"""Inspect a Xunlei share and optionally save one selected video to a folder."""

import argparse
import asyncio
import json
import sys
from urllib.parse import parse_qs, urlsplit

from xunlei.api import XunleiAPI
from xunlei.auth import AuthManager
from xunlei.config import Config

from cinema_ops import list_all, resolve_folder

BASE = "https://api-pan.xunlei.com/drive/v1"
VIDEO_EXT = (".mkv", ".mp4", ".m2ts", ".ts", ".avi", ".mov", ".webm")
MAX_FILES = 500


def parse_link(value, explicit_code=""):
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or parsed.hostname != "pan.xunlei.com":
        raise ValueError("Expected an HTTPS pan.xunlei.com share URL")
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or parts[0] != "s" or not parts[1]:
        raise ValueError("Expected /s/<share-id> URL")
    code = explicit_code or (parse_qs(parsed.query).get("pwd") or [""])[0]
    return parts[1], code


async def read_share(api, share_id, code):
    root = await api._request("GET", BASE + "/share",
                              params={"share_id": share_id, "pass_code": code, "limit": 100})
    if root.get("share_status") != "OK":
        raise RuntimeError("Share unavailable: " + str(root.get("share_status")))
    token = root.get("pass_code_token")
    if not token:
        raise RuntimeError("Share did not return pass_code_token")
    videos = []
    seen_folders = set()

    async def walk(files, prefix=""):
        for entry in files:
            if len(videos) >= MAX_FILES:
                raise RuntimeError("Share exceeds safe video inventory limit")
            name = str(entry.get("name") or "")
            kind = entry.get("kind")
            if kind == "drive#folder":
                folder_id = entry.get("id")
                if not folder_id or folder_id in seen_folders:
                    continue
                seen_folders.add(folder_id)
                page = ""
                while True:
                    data = await api._request("GET", BASE + "/share/detail",
                                              params={"share_id": share_id, "parent_id": folder_id,
                                                      "pass_code_token": token, "limit": 100,
                                                      "page_token": page})
                    await walk(data.get("files", []), prefix + name + "/")
                    nxt = data.get("next_page_token") or ""
                    if not nxt:
                        break
                    if nxt == page:
                        raise RuntimeError("Share pagination did not advance")
                    page = nxt
            elif kind == "drive#file" and name.lower().endswith(VIDEO_EXT):
                videos.append({"id": entry.get("id"), "name": name,
                               "size_bytes": int(entry.get("size") or 0),
                               "size_gb": round(int(entry.get("size") or 0) / 1_000_000_000, 2),
                               "share_path": prefix + name})

    await walk(root.get("files", []))
    return token, videos


async def run(args):
    share_id, code = parse_link(args.share_url, args.pass_code)
    auth = AuthManager(Config())
    api = XunleiAPI(auth)
    try:
        if not auth.is_logged_in():
            raise RuntimeError("Independent Xunlei login is required")
        token, videos = await read_share(api, share_id, code)
        chosen = next((v for v in videos if v["id"] == args.file_id), None)
        if not args.file_id:
            print(json.dumps({"video_count": len(videos), "videos": videos}, ensure_ascii=False, indent=2))
            return 0
        if chosen is None:
            raise ValueError("Selected file ID is absent from this share")
        if args.min_gb and chosen["size_gb"] < args.min_gb:
            raise ValueError(f"Selected file is below {args.min_gb:g} GB")
        if not args.folder:
            raise ValueError("--folder is required when --file-id is set")
        final_name = args.save_as or chosen["name"]
        if ("/" in final_name or "\\" in final_name or final_name in (".", "..")
                or not final_name.lower().endswith(VIDEO_EXT)):
            raise ValueError("--save-as must be a single video filename")
        folder_id, missing = await resolve_folder(api, args.folder)
        if not missing and any(row.name == final_name for row in await list_all(api, folder_id)):
            print("Skipped: exact file name already exists in target folder")
            return 0
        print(json.dumps({"selected": chosen, "target": args.folder,
                          "save_as": final_name,
                          "missing_folders": missing, "execute": args.execute}, ensure_ascii=False))
        if not args.execute:
            return 0
        if missing:
            folder_id, _ = await resolve_folder(api, args.folder, create=True)
        if not folder_id:
            raise RuntimeError("Target folder has no ID")
        payload = {"parent_id": folder_id, "share_id": share_id,
                   "pass_code_token": token, "ancestor_ids": [],
                   "file_ids": [chosen["id"]], "specify_parent_id": True}
        result = await api._request("POST", BASE + "/share/restore", json=payload)
        saved = None
        for _ in range(5):
            files = await list_all(api, folder_id)
            saved = next((f for f in files if f.name == chosen["name"] and f.size == chosen["size_bytes"]), None)
            if saved:
                break
            await asyncio.sleep(1)
        if saved and final_name != chosen["name"]:
            await api.rename_file(saved.file_id, final_name)
            saved = next((f for f in await list_all(api, folder_id)
                          if f.name == final_name and f.size == chosen["size_bytes"]), None)
        print(json.dumps({"restore_status": result.get("restore_status"),
                          "task_created": bool(result.get("restore_task_id")),
                          "file_readback": bool(saved),
                          "readback_size_bytes": saved.size if saved else None}, ensure_ascii=False))
        return 0 if saved and saved.size == chosen["size_bytes"] else 2
    finally:
        await api.close()
        await auth.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("share_url")
    ap.add_argument("--pass-code", default="")
    ap.add_argument("--file-id", default="", help="Exact ID from read-only video inventory")
    ap.add_argument("--folder", default="")
    ap.add_argument("--min-gb", type=float, default=0)
    ap.add_argument("--save-as", default="", help="Canonical video filename after transfer")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
