"""Original cloud-folder and source checks for xunlei-cinema."""

from urllib.parse import urlsplit


def validate_source(value: str) -> str:
    source = value.strip()
    if source.startswith("magnet:?"):
        return source
    parsed = urlsplit(source)
    if parsed.scheme.lower() not in {"http", "https", "ftp", "thunder"}:
        raise ValueError("Expected magnet, thunder, FTP, HTTP or HTTPS link")
    if parsed.scheme.lower() != "thunder" and not parsed.netloc:
        raise ValueError("Link has no host")
    if parsed.hostname == "pan.xunlei.com" and parsed.path.startswith("/s/"):
        raise ValueError("Xunlei shares use the separate transfer workflow")
    return source


def folder_parts(path: str) -> list[str]:
    parts = [part.strip() for part in path.strip("/").split("/")]
    if not parts or any(not part or part in {".", ".."} for part in parts):
        raise ValueError("Folder path contains an empty or invalid name")
    return parts


async def list_all(api, parent_id: str):
    files, page = [], ""
    while True:
        rows, next_page = await api.list_files(parent_id=parent_id, page_token=page, page_size=100)
        files.extend(rows)
        if not next_page:
            return files
        if next_page == page:
            raise RuntimeError("Folder listing did not advance")
        page = next_page


async def resolve_folder(api, path: str, create: bool = False):
    parent_id = ""
    parts = folder_parts(path)
    for index, name in enumerate(parts):
        rows = await list_all(api, parent_id)
        matches = [row for row in rows if row.kind == "drive#folder" and row.name == name]
        if len(matches) > 1:
            raise RuntimeError(f"Ambiguous folder: {name}")
        if matches:
            parent_id = matches[0].file_id
        elif create:
            containing_folder_id = parent_id
            created = await api.create_folder(name, containing_folder_id)
            parent_id = created.file_id
            if not parent_id:
                # Some Xunlei responses acknowledge creation without returning
                # the new ID. Read the parent back before continuing.
                reread = [row for row in await list_all(api, containing_folder_id)
                          if row.kind == "drive#folder" and row.name == name]
                if len(reread) != 1 or not reread[0].file_id:
                    raise RuntimeError(f"Created folder cannot be resolved: {name}")
                parent_id = reread[0].file_id
        else:
            return parent_id, parts[index:]
    return parent_id, []


async def duplicate_reason(api, source: str, folder_id: str, expected_name: str = ""):
    if expected_name and folder_id:
        if any(row.name == expected_name for row in await list_all(api, folder_id)):
            return f"file named {expected_name} already exists"
    tasks = await api.list_offline_tasks(limit=100)
    if any(task.url == source for task in tasks):
        return "same source URL already has an offline task"
    return ""
