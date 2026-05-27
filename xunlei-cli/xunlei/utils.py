"""Utility functions for xunlei-cli."""

import re


def validate_magnet(magnet_url: str) -> tuple[bool, str]:
    """Validate magnet link format.

    Returns:
        (is_valid, error_message)
    """
    if not magnet_url or not magnet_url.startswith("magnet:?"):
        return False, "Invalid magnet URL: must start with 'magnet:?'"

    # Extract xt parameter
    xt_match = re.search(r'xt=urn:btih:([0-9a-fA-F]{40})', magnet_url)
    if not xt_match:
        return False, "Invalid magnet: missing or malformed xt (btih hash)"

    # Hash should be exactly 40 hex chars
    btih = xt_match.group(1).lower()
    if not re.match(r'^[0-9a-f]{40}$', btih):
        return False, "Invalid magnet: btih hash must be 40 hex characters"

    # Check for all-zero hash (dead magnet)
    if btih == "0" * 40:
        return False, "Invalid magnet: btih hash is all zeros (dead magnet)"

    return True, ""


def extract_magnet_info(magnet_url: str) -> dict:
    """Extract info from magnet link.

    Returns:
        dict with keys: btih, dn (display name), xl (exact length), tr (trackers)
    """
    info = {"btih": "", "dn": "", "xl": "", "tr": []}

    # btih
    m = re.search(r'xt=urn:btih:([0-9a-fA-F]{40})', magnet_url)
    if m:
        info["btih"] = m.group(1).lower()

    # display name
    m = re.search(r'&dn=([^&]+)', magnet_url)
    if m:
        from urllib.parse import unquote
        info["dn"] = unquote(m.group(1))

    # exact length
    m = re.search(r'&xl=(\d+)', magnet_url)
    if m:
        info["xl"] = m.group(1)

    # trackers
    info["tr"] = re.findall(r'&tr=([^&]+)', magnet_url)

    return info
