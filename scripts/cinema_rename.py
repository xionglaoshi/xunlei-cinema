#!/usr/bin/env python3
"""Preview or apply exact-ID cloud movie renames from a manifest."""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from xunlei.api import XunleiAPI
from xunlei.auth import AuthManager
from xunlei.config import Config

from cinema_naming import movie_filename
from cinema_ops import list_all, resolve_folder


def load_rows(path: Path, rollback: bool):
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Manifest must contain a nonempty list")
    planned = []
    for row in rows:
        old = row["new_name"] if rollback else row["old_name"]
        new = row["old_name"] if rollback else movie_filename(
            row["old_name"], row["chinese"], row["english"], int(row["year"])
        )
        planned.append({"folder": row["folder"], "id": row["id"],
                        "old_name": old, "new_name": new,
                        "size_bytes": int(row["size_bytes"]),
                        "chinese": row.get("chinese", ""),
                        "english": row.get("english", ""),
                        "year": row.get("year")})
    ids = [row["id"] for row in planned]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate file ID in manifest")
    destinations = [(row["folder"], row["new_name"]) for row in planned]
    if len(destinations) != len(set(destinations)):
        raise ValueError("Two files would have the same destination filename")
    return planned


async def run(args):
    rows = load_rows(args.manifest, args.rollback)
    auth = AuthManager(Config())
    api = XunleiAPI(auth)
    try:
        if not auth.is_logged_in():
            raise RuntimeError("Independent Xunlei login is required")
        folders = {}
        for folder in sorted({row["folder"] for row in rows}):
            fid, missing = await resolve_folder(api, "家庭影院/" + folder)
            if missing:
                raise RuntimeError("Missing cloud folder: " + folder)
            folders[folder] = (fid, await list_all(api, fid))
        pending = []
        already_done = []
        for row in rows:
            existing = folders[row["folder"]][1]
            source = next((x for x in existing if x.file_id == row["id"]), None)
            if not source or source.size != row["size_bytes"]:
                raise RuntimeError("Source changed since inventory: " + row["id"])
            if source.name == row["new_name"] and args.resume:
                already_done.append(row)
                continue
            if source.name != row["old_name"]:
                raise RuntimeError("Source changed since inventory: " + row["id"])
            if any(x.file_id != row["id"] and x.name == row["new_name"] for x in existing):
                raise RuntimeError("Destination name already exists: " + row["new_name"])
            if row["old_name"] != row["new_name"]:
                pending.append(row)
        print(json.dumps({"total": len(rows), "already_done": len(already_done), "to_rename": len(pending),
                          "preview": [{"folder": x["folder"], "old": x["old_name"],
                                       "new": x["new_name"]} for x in pending]}, ensure_ascii=False, indent=2))
        if not args.execute:
            return
        backup = Path(__file__).resolve().parent.parent / "var" / (
            "rename-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        backup.chmod(0o600)
        print("Rollback manifest: " + str(backup), flush=True)
        for index, row in enumerate(pending, 1):
            fid = folders[row["folder"]][0]
            await api.rename_file(row["id"], row["new_name"])
            readback = next((x for x in await list_all(api, fid) if x.file_id == row["id"]), None)
            if not readback or readback.name != row["new_name"] or readback.size != row["size_bytes"]:
                raise RuntimeError("Rename readback failed: " + row["id"])
            print(json.dumps({"done": index, "total": len(pending),
                              "folder": row["folder"], "new": row["new_name"]}, ensure_ascii=False), flush=True)
    finally:
        await api.close()
        await auth.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--rollback", action="store_true", help="Reverse names from an execution backup")
    parser.add_argument("--resume", action="store_true", help="Skip exact-ID files already at the requested name")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
