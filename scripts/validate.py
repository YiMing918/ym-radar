#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
雷达站提交前门禁（只用 Python 标准库）。

断言：
  - data.json 能解析，13 个栏目齐全
  - 每条条目 name / url / summary 非空
  - description 长度：人工条目 >= 30；自动收录的新条目（auto=true，深挖补全后）
    >= 40 且含用途类信息（带「适合」字样，模板的人群段）
  - cracked 分组（破解类）条目 warning 非空且含风险类词（风险/病毒/后门等）
  - overview 里的 counts 与实际条数一致
不过 → 打印原因并以非零码退出（GitHub Actions 里这一步失败就不会提交）。

用法：python3 scripts/validate.py [data.json 路径]
"""
import json
import os
import sys

EXPECTED_SLUGS = ["ai", "apple", "video", "music", "book", "live",
                  "service", "domain", "cfip", "node", "app", "adult", "proxy"]
MAX_PER_CAT = 500  # 每栏总量上限（FIX17 二期：300→500，与 collect.py 同步）
VERSION_REQUIRED_SLUGS = ("app", "apple", "proxy")

DOMAIN_Q = ["免费时长", "能否续费", "续费价格", "能否进 CF"]
HOST_Q = ["免费时长", "机器配置", "申请要求", "能干什么"]
EMAIL_Q = ["免费时长", "容量与发信量", "申请要求", "能干什么"]
OTHER_Q = ["免费时长", "额度", "申请要求", "能干什么"]


def expected_labels(slug, item):
    """service/domain 两栏的固定四问（与 collect.py 同一套规则）。"""
    if slug == "domain":
        return DOMAIN_Q
    sub = ""
    for dd in item.get("details") or []:
        if isinstance(dd, dict) and dd.get("label") == "类别":
            sub = str(dd.get("value") or "")
    if "域名" in sub:
        return DOMAIN_Q
    if "主机" in sub or "托管" in sub:
        return HOST_Q
    if "邮箱" in sub:
        return EMAIL_Q
    return OTHER_Q


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_path = os.path.join(os.path.dirname(script_dir), "data.json")
    path = sys.argv[1] if len(sys.argv) > 1 else default_path

    problems = []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"校验失败：data.json 解析不了：{e}")
        return 1

    categories = data.get("categories")
    if not isinstance(categories, dict):
        print("校验失败：缺少 categories")
        return 1

    for slug in EXPECTED_SLUGS:
        if slug not in categories:
            problems.append(f"缺栏目：{slug}")
    for slug in categories:
        if slug not in EXPECTED_SLUGS:
            problems.append(f"多出未知栏目：{slug}")

    total = 0
    for slug in EXPECTED_SLUGS:
        cat = categories.get(slug) or {}
        items = cat.get("items")
        if not isinstance(items, list):
            problems.append(f"{slug}：items 不是列表")
            continue
        total += len(items)
        if len(items) > MAX_PER_CAT:
            problems.append(f"{slug}：条目数 {len(items)} 超过单栏上限 {MAX_PER_CAT}")
        for idx, item in enumerate(items):
            name = (item.get("name") or "").strip()
            tag = f"{slug}[{idx}] {name or '(无名)'}"
            if not name:
                problems.append(f"{tag}：name 为空")
            if not (item.get("url") or "").strip():
                problems.append(f"{tag}：url 为空")
            if not (item.get("summary") or "").strip():
                problems.append(f"{tag}：summary 为空")
            desc = item.get("description") or ""
            if item.get("auto"):
                if len(desc) < 40:
                    problems.append(
                        f"{tag}：新条目 description 只有 {len(desc)} 字（要求 >= 40）")
                if "适合" not in desc:
                    problems.append(f"{tag}：新条目 description 缺用途/适合人群信息")
            elif len(desc) < 30:
                problems.append(f"{tag}：description 只有 {len(desc)} 字（要求 >= 30）")
            if "cracked" in (item.get("section") or ""):
                w = item.get("warning") or ""
                risk_words = ("风险", "病毒", "后门", "不明", "别装", "谨慎", "中毒")
                if not w.strip():
                    problems.append(f"{tag}：破解类条目缺风险警告")
                elif not any(k in w for k in risk_words):
                    problems.append(f"{tag}：破解类条目警告里没有风险类词")
            detail_labels = {dd.get("label") for dd in item.get("details") or []
                             if isinstance(dd, dict)}
            if slug in VERSION_REQUIRED_SLUGS:
                for lb in ("最新版本", "版本日期"):
                    if lb not in detail_labels:
                        problems.append(f"{tag}：缺版本字段「{lb}」（查不到应填未公布）")
            if slug == "proxy":
                for lb in ("规则格式", "模块支持", "分流方式", "配套规则", "可加的东西"):
                    if lb not in detail_labels:
                        problems.append(f"{tag}：proxy 条目缺必备说明「{lb}」")
            if slug in ("apple", "app") and "工具类" in (item.get("tags") or []):
                for lb in ("规则格式", "模块支持", "分流方式", "配套规则", "可加的东西"):
                    if lb not in detail_labels:
                        problems.append(f"{tag}：工具类条目缺必备说明「{lb}」")
            if slug == "apple":
                if "现价" not in detail_labels:
                    problems.append(f"{tag}：缺「现价」（苹果条目必带现价）")
                if (item.get("section") or "") == "discounts" \
                        and "原价" not in detail_labels:
                    problems.append(f"{tag}：降价条目缺「原价」")
                # FIX17 二期：iapfree 分区已废除，门禁不再检查它。
            if slug in ("service", "domain"):
                have = {dd.get("label") for dd in item.get("details") or []
                        if isinstance(dd, dict)}
                for lb in expected_labels(slug, item):
                    if lb not in have:
                        problems.append(f"{tag}：缺固定字段「{lb}」（查不到应填未公布）")
            if slug == "apple" and (item.get("section") or "") == "ipasign":
                typ = ""
                for dd in item.get("details") or []:
                    if isinstance(dd, dict) and dd.get("label") == "类型":
                        typ = str(dd.get("value") or "")
                if typ not in ("正版包", "破解包"):
                    problems.append(f"{tag}：ipasign 条目缺「类型：正版包/破解包」标注")
                elif typ == "破解包" and not (item.get("warning") or "").strip():
                    problems.append(f"{tag}：ipasign 破解包缺风险警告")

    overview = data.get("overview") or {}
    ov_counts = {}
    for oc in overview.get("categories") or []:
        if isinstance(oc, dict) and oc.get("slug"):
            ov_counts[oc["slug"]] = oc.get("count")
    for slug in EXPECTED_SLUGS:
        actual = len((categories.get(slug) or {}).get("items") or [])
        if ov_counts.get(slug) != actual:
            problems.append(
                f"{slug}：overview count 是 {ov_counts.get(slug)}，实际 {actual} 条")

    if problems:
        print(f"校验失败，共 {len(problems)} 个问题：")
        for p in problems:
            print(" -", p)
        return 1
    print(f"校验通过：13 个栏目齐全，共 {total} 条，counts 一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
