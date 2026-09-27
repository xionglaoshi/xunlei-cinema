#!/usr/bin/env python3
"""Rank source leads using title signals and movie-duration/file-size plausibility."""

import argparse
import json
import re
import sys

SIZE_RE = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(TB|GB|MB|G|M)\b")


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
        choices = [("4K 压制/WEB", 8, 25), ("4K 原盘/Remux", 35, 80)]
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
        rows = [rows[-1], rows[0]]
    elif source_type in ("bluray",) and resolution in ("1080p", "720p"):
        rows = [rows[-1], rows[0]]
    return rows


def analyze(item, runtime):
    meta = item.get("decoded_metadata") or {}
    text = " ".join(str(item.get(k, "")) for k in ("title", "context", "reported_title", "url"))
    text += " " + str(meta.get("file_name", "")) + " " + str(meta.get("display_name", ""))
    text = text.lower()
    resolution = "2160p" if re.search(r"4k|2160p|uhd|超高清", text) else (
        "1080p" if re.search(r"1080p|1080[pi]|bd1080|full.?hd", text) else (
        "720p" if re.search(r"720p|1280x720|bd1280", text) else None))
    source_type = "remux" if re.search(r"remux|原盘|原盘iso|uhd.?bd", text) else (
        "bluray" if re.search(r"blu.?ray|蓝光|bdrip|bd1080", text) else (
        "web" if re.search(r"web.?dl|web.?rip", text) else "unspecified"))
    got = item.get("file_size_gb") or meta.get("file_size_gb") or size_gb(text)
    estimates = expected_ranges(runtime / 60, resolution, source_type) if runtime and resolution else []
    expected = estimates[0]["range_gb"] if estimates else None
    confidence = 35 if resolution in ("2160p", "1080p") else 20 if resolution == "720p" else 10
    reasons = []
    if resolution:
        reasons.append("名称或说明标注 " + resolution + "（未验证真实画面分辨率）")
    if resolution == "720p":
        reasons.append("低于默认 1080p 兜底清晰度")
    if re.search(r"cam|tc版|ts版|枪版|预告|trailer|sample", text):
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
    selection_score = confidence + (15 if resolution == "2160p" else -10 if resolution == "720p" else 0)
    result["quality"] = {"resolution_claim": resolution, "source_class": source_type,
                         "file_size_gb": got, "expected_size_gb": expected,
                         "expected_size_ranges": estimates,
                         "confidence": tier, "confidence_score": confidence,
                         "selection_score": selection_score,
                         "evidence": reasons}
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="search_sources.py 输出的 JSON")
    ap.add_argument("--runtime", type=int, help="正片时长（分钟）")
    args = ap.parse_args()
    try:
        with open(args.input, encoding="utf-8") as f:
            raw = json.load(f)
        rows = raw if isinstance(raw, list) else raw.get("candidates", [])
        analyzed = [analyze(x, args.runtime) for x in rows]
        analyzed.sort(key=lambda x: -x["quality"]["selection_score"])
        print(json.dumps({"candidate_count": len(analyzed), "runtime_minutes": args.runtime,
                          "candidates": analyzed}, ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError) as exc:
        print(f"分析失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
