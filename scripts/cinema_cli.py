#!/usr/bin/env python3
"""Save one direct source into a Xunlei cloud folder; preview by default."""

import argparse
import asyncio
import sys

from xunlei.api import XunleiAPI
from xunlei.auth import AuthManager
from xunlei.config import Config

from cinema_ops import duplicate_reason, resolve_folder, validate_source


async def save(args):
    source = validate_source(args.url)
    auth = AuthManager(Config())
    api = XunleiAPI(auth)
    try:
        if not auth.is_logged_in():
            raise RuntimeError("Independent Xunlei login is required")
        folder_id, missing = await resolve_folder(api, args.folder)
        if not missing:
            reason = await duplicate_reason(api, source, folder_id, args.expect_name)
            if reason:
                print(f"Skipped: {reason}")
                return 0
        print(f"Target: {args.folder}")
        print(f"Missing folders: {' / '.join(missing) if missing else 'none'}")
        if not args.execute:
            print("Preview only. Add --execute to submit.")
            return 0
        if missing:
            folder_id, _ = await resolve_folder(api, args.folder, create=True)
        task = await api.create_offline_task(source, folder_id)
        print(f"Task created: {task.task_id}")
        print(f"Target folder ID: {folder_id}")
        print("Read task status and target folder before reporting success.")
        return 0
    finally:
        await api.close()
        await auth.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--folder", required=True)
    parser.add_argument("--expect-name", default="")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        return asyncio.run(save(args))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
