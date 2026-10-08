"""Build portable, readable movie filenames without inventing media properties."""

import re
from pathlib import PurePath

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".mov", ".m2ts", ".ts", ".webm"}
INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
UNVERIFIED = re.compile(r"(?:来源标称|source.?claimed).*$", re.I)


def clean_title(value: str) -> str:
    value = INVALID.sub(" ", value).strip(" .-_()[]")
    value = re.sub(r"\s+", " ", value)
    if not value:
        raise ValueError("Movie title is empty")
    return value


def series_chinese_title(series: str, number: int, subtitle: str = "") -> str:
    """Format an actual numbered franchise, not a shared-universe folder."""
    if not 1 <= int(number) <= 99:
        raise ValueError("Series number must be 1 through 99")
    prefix = f"{clean_title(series)}{int(number)}"
    subtitle = clean_title(subtitle) if subtitle.strip() else ""
    return prefix + ("." + subtitle if subtitle else "")


def movie_filename(source_name: str, chinese: str, english: str, year: int) -> str:
    """Keep only source technical suffix after its release year."""
    extension = PurePath(source_name).suffix.lower()
    if extension not in VIDEO_EXT:
        raise ValueError("Source is not a recognized video file")
    if not 1888 <= int(year) <= 2100:
        raise ValueError("Invalid release year")
    stem = source_name[: -len(extension)]
    # Titles may contain a different year (Cold War 1994, released in 2026).
    # Extract technical metadata only after the independently confirmed year.
    year_hit = re.search(rf"(?<!\d){int(year)}(?!\d)", stem)
    tail = stem[year_hit.end():] if year_hit else ""
    # Discard the closing bracket of '(year)', but preserve balanced brackets
    # inside the actual suffix (for example a release group's name).
    tail = re.sub(r"^[)\]]+", "", tail).strip(" .-_")
    tail = UNVERIFIED.sub("", tail)
    tail = INVALID.sub(" ", tail).strip(" .-_")
    # Accept old numbered-series input while emitting the current dot separator.
    chinese = re.sub(r"(?<=[0-9])\s*[：:]\s*", ".", chinese)
    cn, en = clean_title(chinese), clean_title(english)
    title_base = f"{cn}.{year}" if cn == en else f"{cn}.{en}.{year}"
    base = title_base
    if tail and tail.lower() not in {"4k", "source claimed 4k"}:
        base += "." + tail
    result = base + extension
    if len(result.encode("utf-8")) > 240:
        # Long metadata strings break some cloud clients. Preserve titles/year.
        tokens = tail.split(".") if tail else []
        while tokens and len((title_base + "." + ".".join(tokens) + extension).encode("utf-8")) > 240:
            tokens.pop()
        result = title_base + ("." + ".".join(tokens) if tokens else "") + extension
    if len(result.encode("utf-8")) > 240:
        raise ValueError("Titles are too long for a portable filename")
    return result
