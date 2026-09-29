"""Cloud-only operations for the Xunlei Cinema skill."""

from urllib.parse import urlsplit


def validate_source(url: str) -> str:
    """Reject share pages and unsupported inputs before creating offline tasks."""
    source = url.strip()
    if not source:
        raise ValueError("Empty source URL")
    if source.startswith("magnet:?"):
        return source
    parsed = urlsplit(source)
    if parsed.scheme.lower() not in {"http", "https", "ftp", "thunder"}:
        raise ValueError("Use an HTTP(S), FTP, thunder:// or magnet link")
    if parsed.scheme.lower() != "thunder" and not parsed.netloc:
        raise ValueError("Source URL has no host")
    if parsed.hostname == "pan.xunlei.com" and parsed.path.startswith("/s/"):
        raise ValueError("Xunlei share links require the separate transfer workflow")
    return source


def split_folder_path(path: str) -> list[str]:
    parts = [part.strip() for part in path.strip("/").split("/")]
    if not parts or any(not part or part in {".", ".."} for part in parts):
        raise ValueError("Folder path must contain nonempty names")
    return parts


async def list_all_files(api, parent_id: str):
    """Read every page so existing folders are not missed after 100 items."""
    result = []
    token = ""
    while True:
        files, next_token = await api.list_files(
            parent_id=parent_id, page_token=token, page_size=100
        )
        result.extend(files)
        if not next_token:
            break
        if next_token == token:
            raise RuntimeError("Folder listing did not advance")
        token = next_token
    return result


async def resolve_folder(api, path: str, create: bool = False):
    """Return (folder_id, missing_parts); create only when explicitly requested."""
    parent_id = ""
    parts = split_folder_path(path)
    for index, name in enumerate(parts):
        files = await list_all_files(api, parent_id)
        matches = [f for f in files if f.name == name and f.kind == "drive#folder"]
        if len(matches) > 1:
            raise RuntimeError(f"Duplicate folder name under {parent_id or '/'}: {name}")
        if matches:
            parent_id = matches[0].file_id
        elif create:
            parent_id = (await api.create_folder(name, parent_id)).file_id
        else:
            return parent_id, parts[index:]
    return parent_id, []


async def find_duplicate(api, url: str, folder_id: str, expected_name: str = ""):
    if expected_name and folder_id:
        files = await list_all_files(api, folder_id)
        if any(f.name == expected_name for f in files):
            return f"file named {expected_name} already exists"
    tasks = await api.list_offline_tasks(limit=100)
    if any(task.url == url for task in tasks):
        return "same source URL already has an offline task"
    return ""
