#!/usr/bin/env python3
"""Rank source leads using title signals and movie-duration/file-size plausibility."""

import argparse
import json
import re
import sys
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit, parse_qs

SIZE_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(TB|GB|MB|G|M)\b")


def container_claim(item, meta):
    # Selected file metadata takes precedence over webpage text and other versions.
    for value in (item.get("file_name"), item.get("name"), meta.get("file_name"), meta.get("display_name")):
        if isinstance(value, str):
            suffix = PurePosixPath(value.strip()).suffix.lower().lstrip(".")
            if suffix:
                return suffix
    declared = str(item.get("container") or item.get("format") or "").lower().lstrip(".")
    if declared in {"mkv", "matroska", "mp4", "avi", "mov", "m2ts", "ts", "webm"}:
        return "mkv" if declared == "matroska" else declared
    url = urlsplit(str(item.get("url") or ""))
    name = (parse_qs(url.query).get("dn") or [""])[0] if url.scheme == "magnet" else unquote(url.path)
    suffix = PurePosixPath(name).suffix.lower().lstrip(".")
    if suffix in {"mkv", "mp4", "avi", "mov", "m2ts", "ts", "webm", "zip", "rar", "torrent"}:
        return suffix
    hits = set(re.findall(r"(?i)\.(mkv|mp4|avi|mov|m2ts|ts|webm)(?![\w.])", str(item.get("title") or "")))
    return next(iter(hits)).lower() if len(hits) == 1 else None


def size_gb(text):
    values = []
    for n, unit in SIZE_RE.findall(text):
        value = float(n)
        values.append(value * (1000 if unit.upper() == "TB" else 1 / 1000 if unit.upper() == "MB" else 1))
    return max(values) if values else None


def expected_ranges(hours, resolution, source_type):
    # Approximate encoded media size from bitrate x duration; includes a broad
    # audio/container allowance. Ranges overlap intentionally across codecs.
    if resolution == "2160p":
        choices = [("4K 高码率重编码（优选）", 25, 45), ("4K 其他压制/WEB", 8, 25), ("4K 原盘/Remux", 35, 80)]
    elif resolution == "1080p":
        choices = [("1080p 压制/WEB", 3, 10), ("1080p Blu-ray", 8, 22)]
    elif resolution == "720p":
        choices = [("720p 压制/WEB", 1.5, 6), ("720p Blu-ray", 3, 8)]
    else:
        return []
    def size_range(low, high):
        return [round(hours * low * 0.45 + 0.25, 1), round(hours * high * 0.45 + 0.6, 1)]
    rows = [{"release_type": name, "range_gb": size_range(low, high)} for name, low, high in choices]
    if source_type == "remux":
        rows = [rows[-1]] + rows[:-1]
    elif source_type in ("bluray",) and resolution in ("1080p", "720p"):
        rows = [rows[-1]] + rows[:-1]
    return rows


def positive_number(value):
    try:
        value = float(value)
        return value if 0 < value < float("inf") else None
    except (TypeError, ValueError):
        return None


def check_completeness(item):
    """Compare selected-file duration against sourced, edition-specific runtimes.

    A duration match excludes obvious truncation; it does not prove no edits.
    Only measured file duration can clear the automatic-selection gate.
    """
    refs = [r for r in (item.get("runtime_references") or [])
            if isinstance(r, dict) and positive_number(r.get("minutes"))
            and r.get("source") and r.get("edition")]
    edition = item.get("edition")
    wanted = item.get("preferred_edition")
    actual = positive_number(item.get("file_runtime_minutes"))
    if actual is None:
        seconds = positive_number(item.get("file_duration_seconds"))
        actual = seconds / 60 if seconds else None
    evidence = item.get("runtime_evidence")
    actual_source = item.get("runtime_source")
    result = {"status": "pending_runtime", "selection_gate_passed": False,
              "file_runtime_minutes": actual, "edition": edition,
              "preferred_edition": wanted, "references": refs,
              "runtime_evidence": evidence, "runtime_source": actual_source,
              "note": "未取得选定文件的可回查片长；不能用影片介绍片长代替文件片长"}
    matching_refs = [r for r in refs if edition and r["edition"] == edition]
    if matching_refs:
        values = [float(r["minutes"]) for r in matching_refs]
        if max(values) - min(values) <= max(2, min(values) * .015):
            result.update(reference_minutes=float(matching_refs[0]["minutes"]),
                          reference_source=matching_refs[0]["source"])
    parts = item.get("runtime_parts")
    part_name = " ".join(str(item.get(k) or "") for k in ("file_name", "name", "title"))
    split = bool(re.search(r"(?:disc|disk|cd)[ ._-]*[12]\b|分碟|上半部|下半部", part_name, re.I))
    if parts is not None:
        count = item.get("expected_part_count")
        valid = isinstance(count, int) and not isinstance(count, bool) and count > 0 and isinstance(parts, list)
        valid = valid and len(parts) == count and all(isinstance(x, dict) for x in parts)
        valid = valid and sorted(x.get("part_number", -1) for x in parts if isinstance(x.get("part_number", -1), int)) == list(range(1, count + 1))
        valid = valid and item.get("parts_same_edition_confirmed") is True
        valid = valid and all(positive_number(x.get("minutes")) and x.get("source")
                              and x.get("evidence") == "measured" for x in parts)
        if not valid:
            result.update(status="incomplete_parts", note="分碟数、顺序、同版归属或各碟实测片长未核齐；不得将单碟当完整影片")
            return result
        actual = sum(float(x["minutes"]) for x in parts)
        evidence, actual_source = "measured", [x["source"] for x in parts]
        result.update(file_runtime_minutes=actual, runtime_evidence=evidence,
                      runtime_source=actual_source, part_count=count)
    elif split:
        result.update(status="incomplete_parts", note="文件含分碟标记，尚未核对全部分碟及总片长")
        return result
    if not actual or not actual_source or evidence not in ("measured", "claimed"):
        return result
    if not refs:
        result.update(status="pending_reference", note="缺少带来源及版本的公开片长；未核实完整性")
        return result
    matching = [r for r in refs if edition and r["edition"] == edition]
    if not matching:
        shortest = min(float(r["minutes"]) for r in refs)
        if actual < shortest - max(5, shortest * .03):
            result.update(status="suspected_short_or_other_edition",
                          difference_from_shortest_reference_minutes=round(actual-shortest, 3),
                          note="文件短于已查公开版本，剪辑版未确认；需核对缺碟、地区版、帧率或错误文件，不据此断言删减")
        else:
            result.update(status="pending_edition", note="未确认候选剪辑版，或无同版公开片长；不能把院线版与加长版直接相减")
        return result
    values = [float(r["minutes"]) for r in matching]
    if max(values) - min(values) > max(2, min(values) * .015):
        result.update(status="reference_conflict", note="同一版本的公开片长相互冲突，需查发行地区/帧率/版本")
        return result
    reference = matching[0]
    expected = float(reference["minutes"])
    normalized = actual
    speed = positive_number(item.get("playback_speed_ratio"))
    if speed and item.get("playback_speed_source") and (abs(speed - 25 / 24) < .0001 or abs(speed - 25 / 23.976) < .0001):
        normalized *= speed
        result["speed_correction"] = {"ratio": speed, "source": item["playback_speed_source"]}
    delta = normalized - expected
    tolerance = max(2, expected * .015)
    result.update(reference_minutes=expected, reference_source=reference["source"],
                  normalized_minutes=round(normalized, 3), difference_minutes=round(delta, 3),
                  difference_percent=round(delta / expected * 100, 2), tolerance_minutes=round(tolerance, 3))
    if wanted and wanted != edition:
        result.update(status="edition_mismatch", note="候选不是指定剪辑版；不能用片长相近替代版本核对")
    elif delta < -max(5, expected * .03):
        result.update(status="suspected_incomplete", note="比同版公开片长明显偏短，疑似分碟/缺失/删减；暂停优选，不能仅据时长断言被阉割")
    elif abs(delta) > tolerance:
        result.update(status="runtime_mismatch", note="片长超出容差，需查地区版本、片尾、帧率、花絮或元数据错误")
    elif evidence != "measured":
        result.update(status="claimed_consistent", note="发布者片长与公开同版片长接近，尚未取得文件实测片长")
    else:
        result.update(status="runtime_consistent", selection_gate_passed=True,
                      note="实测片长与公开同版片长相符，仅排除明显缺失，不证明每个镜头完整无删减")
    return result


def analyze(item, runtime):
    completeness = check_completeness(item)
    runtime_fallback = runtime
    runtime = completeness.get("file_runtime_minutes") or completeness.get("reference_minutes") or runtime_fallback
    meta = item.get("decoded_metadata") or {}
    text = " ".join(str(item.get(k, "")) for k in ("title", "name", "file_name", "context", "reported_title", "url"))
    text += " " + str(meta.get("file_name", "")) + " " + str(meta.get("display_name", ""))
    text = text.lower()
    resolution = "2160p" if re.search(r"4k|2160p|uhd|超高清", text) else (
        "1080p" if re.search(r"1080p|1080[pi]|bd1080|full.?hd", text) else (
        "720p" if re.search(r"720p|1280x720|bd1280", text) else None))
    source_type = "remux" if re.search(r"remux|原盘|bdmv|\.iso\b", text) else (
        "bluray" if re.search(r"blu.?ray|蓝光|bdrip|bd1080", text) else (
        "web" if re.search(r"web.?dl|web.?rip", text) else "unspecified"))
    got = item.get("file_size_gb") or meta.get("file_size_gb") or (
        int(item["size_bytes"]) / 1_000_000_000 if item.get("size_bytes") is not None else size_gb(text))
    got = float(got) if got is not None else None
    estimates = expected_ranges(runtime / 60, resolution, source_type) if runtime and resolution else []
    expected = estimates[0]["range_gb"] if estimates else None
    confidence = 35 if resolution in ("2160p", "1080p") else 20 if resolution == "720p" else 10
    reasons = []
    if resolution:
        reasons.append("名称或说明标注 " + resolution + "（未验证真实画面分辨率）")
    if resolution == "720p":
        reasons.append("低于默认 1080p 兜底清晰度")
    unsuitable = bool(re.search(r"\bcam\b|tc版|ts版|枪版|预告|\btrailer\b|\bsample\b", text))
    if unsuitable:
        confidence -= 50
        reasons.append("疑似枪版/预告/样片")
    if got is not None and estimates:
        within = [x for x in estimates if x["range_gb"][0] <= got <= x["range_gb"][1]]
        min_size = min(x["range_gb"][0] for x in estimates)
        max_size = max(x["range_gb"][1] for x in estimates)
        if within:
            confidence += 15 if within[0] == estimates[0] else 10
            labels = "、".join(x["release_type"] for x in within)
            reasons.append(f"文件约 {got:g} GB，符合{labels}的粗略估算")
        elif got < min_size * 0.55:
            confidence -= 30
            reasons.append(f"文件约 {got:g} GB，明显低于估算下限 {min_size:g} GB")
        elif got < min_size:
            confidence -= 15
            reasons.append(f"文件约 {got:g} GB，低于该规格估算范围下限 {min_size:g} GB")
        elif got > max_size * 1.5:
            confidence += 5
            reasons.append(f"文件约 {got:g} GB，高于常见估算范围上限 {max_size:g} GB")
        else:
            reasons.append(f"文件约 {got:g} GB，处于估算边界附近")
    elif resolution and runtime is None:
        reasons.append("未提供片长，暂不能计算容量预期")
    if item.get("link_state") == "ok":
        confidence += 10
        reasons.append("分享检测可访问")
    elif item.get("link_state") == "bad":
        confidence -= 60
        reasons.append("分享检测已失效")
    if item.get("source_page"):
        confidence += 5
        reasons.append("有可回查的来源页面")
    if item.get("reported_title") and item.get("title") and item["reported_title"] != item["title"]:
        confidence -= 10
        reasons.append("聚合字段标题不一致，需查链接附近上下文")
    if item.get("type") in ("magnet", "torrent") and not got:
        confidence -= 5
        reasons.append("需读取种子元数据确认文件清单和体积")
    confidence = max(0, min(100, confidence))
    tier = "高" if confidence >= 70 else "中" if confidence >= 40 else "低"
    result = dict(item)
    # Independent feature categories stack; aliases/repeated labels do not.
    dolby_vision = bool(re.search(r"dolby[ ._-]*vision|dovi|\bdv\b|杜比视界", text))
    dolby_audio = bool(re.search(r"atmos|truehd|\bddp(?:\d|\b)|\be[ ._-]?ac[ ._-]?3\b|dd\+|杜比全景声", text))
    dolby = dolby_vision or dolby_audio or bool(re.search(r"dolby|杜比", text))
    bluray = bool(re.search(r"blu[ ._-]?ray|蓝光|bdrip|bd1080|uhd[ ._-]?bd", text))
    container = container_claim(item, meta)
    reencoded = source_type != "remux" and bool(re.search(r"重编码|压制|bdrip|brrip|re[ ._-]?encod|(?:x26[45]|av1)", text)) and (bluray or bool(re.search(r"重编码|压制|bdrip|brrip|re[ ._-]?encod", text)))
    release_priority = 2 if reencoded else 0 if source_type == "remux" else 1
    target_gb = 28 * max(1, runtime / 150) if runtime and runtime > 0 else 28
    estimated_total = round(got * 8000 / (runtime * 60), 2) if got is not None and runtime and runtime > 0 else None
    bitrate = item.get("video_bitrate_mbps")
    bitrate = float(bitrate) if bitrate is not None else None
    bitrate_evidence = item.get("bitrate_evidence")
    bitrate_supported = bool(item.get("bitrate_source")) and bitrate_evidence in ("measured", "claimed")
    bitrate_score = (25 if bitrate_evidence == "measured" else 15) if bitrate_supported and bitrate is not None and 25 <= bitrate <= 45 else 0
    if estimated_total is not None:
        reasons.append(f"估算总码率 {estimated_total:g} Mbps，含音轨与封装，不能当作视频码率")
    if runtime and runtime > 150:
        reasons.append("片长超过150分钟，允许超过30GB，不作体积硬淘汰")
    components = {"4K": 40 if resolution == "2160p" else 0,
                  "杜比": 20 if dolby else 0, "蓝光": 15 if bluray else 0,
                  "MKV": 10 if container == "mkv" else 0,
                  "重编码": 30 if reencoded else -30 if source_type == "remux" else 0,
                  "视频码率25–45Mbps": bitrate_score,
                  "片长适配容量": round(max(0, 20 - abs(got - target_gb)), 2) if got is not None else 0,
                  "4K小体积": -20 if resolution == "2160p" and got is not None and got < 10 else 0}
    preference_score = sum(components.values())
    provisional_eligible = resolution in ("2160p", "1080p") and confidence >= 40 and item.get("link_state") != "bad" and not unsuitable
    eligible = provisional_eligible and completeness["selection_gate_passed"]
    reasons.append(completeness["note"])
    selection_score = confidence + preference_score
    result["quality"] = {"resolution_claim": resolution, "source_class": source_type,
                         "container_claim": container,
                         "reencoded_claim": reencoded, "release_priority": release_priority,
                         "preferred_video_bitrate_mbps": [25, 45],
                         "video_bitrate_mbps": bitrate, "video_bitrate_supported": bitrate_supported,
                         "estimated_total_bitrate_mbps": estimated_total,
                         "size_target_gb": round(target_gb, 2),
                         "preferred_video_only_size_gb": [round(runtime * 60 * x / 8000, 2) for x in (25, 45)] if runtime and runtime > 0 else None,
                         "file_size_gb": got, "expected_size_gb": expected,
                         "expected_size_ranges": estimates,
                         "confidence": tier, "confidence_score": confidence,
                         "selection_score": selection_score,
                         "selection_eligible": eligible,
                         "provisional_eligible": provisional_eligible,
                         "completeness": completeness,
                         "runtime_used_for_size_minutes": runtime,
                         "runtime_is_assumed": completeness.get("file_runtime_minutes") is None,
                         "expected_complete_video_size_gb": [round(completeness["reference_minutes"] * 60 * x / 8000, 2) for x in (25, 45)] if completeness.get("reference_minutes") else None,
                         "preference_score": preference_score,
                         "preference_components": components,
                         "dolby_vision_claim": dolby_vision, "dolby_audio_claim": dolby_audio,
                         "evidence": reasons}
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="search_sources.py 输出的 JSON")
    ap.add_argument("--runtime", type=float, help="仅供容量估算的参考片长（分钟）；不是文件实测片长，不会通过完整性闸口")
    args = ap.parse_args()
    try:
        with open(args.input, encoding="utf-8") as f:
            raw = json.load(f)
        rows = raw if isinstance(raw, list) else raw.get("candidates", [])
        analyzed = [analyze(x, args.runtime) for x in rows]
        # Credible 4K first; 1080p fallback; low-confidence/failed leads last.
        analyzed.sort(key=lambda x: (x["quality"]["selection_eligible"],
                                    x["quality"]["provisional_eligible"],
                                    x["quality"]["resolution_claim"] == "2160p",
                                    x["quality"]["release_priority"],
                                    x["quality"]["selection_score"]), reverse=True)
        print(json.dumps({"candidate_count": len(analyzed), "runtime_minutes": args.runtime,
                          "candidates": analyzed}, ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError) as exc:
        print(f"分析失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
