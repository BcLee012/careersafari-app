#!/usr/bin/env python3
"""
把 README.md 中「能力 Taxonomy / 培养方案映射 / 岗位族」三张数据表
从 careersafari/curriculum.py 重新生成，写回 README。

为什么需要这个脚本：这三张表是领域知识，手工维护必然与代码漂移。
漂移的后果很实际——有人照着 README 实现，得到的 Taxonomy 和真实运行的不一样。

用法：
    python app/tools/sync_readme.py           # 重写 README 中标记区的内容
    python app/tools/sync_readme.py --check   # 只比对不写回，不一致则退出码 1

README 中用注释标记可重写区域：
    <!-- BEGIN:GEN -->
    ...
    <!-- END:GEN -->
标记区之外的文字（原则、算法、验收标准等）由人维护，脚本绝不改动。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
BEGIN = "<!-- BEGIN:GEN -->"
END = "<!-- END:GEN -->"

sys.path.insert(0, str(ROOT / "app"))

from careersafari.curriculum import (  # noqa: E402
    CAPABILITIES, CURRICULUM, JOB_FAMILIES, PROGRAM_MILESTONES, SOFT_CAPABILITIES,
)


def build_block() -> str:
    L: list[str] = []
    A = L.append

    A("### 4. 能力 Taxonomy\n")
    n_alias = sum(len(c["aliases"]) for c in CAPABILITIES)
    A(f"共 {len(CAPABILITIES)} 项能力、{n_alias} 个别名。\n")
    A("匹配在 **canonical capability id** 上做，不做模糊字符串匹配。")
    A("「熟悉 SQL」和「能独立写多表 JOIN」必须落到同一个 id，否则比对结果是假的。\n")
    A("| id | 能力名 | 别名（命中任一即归入该项） | 软能力 |")
    A("|---|---|---|---|")
    for c in CAPABILITIES:
        aliases = "、".join(f"`{a}`" for a in c["aliases"])
        soft = "**是**" if c["id"] in SOFT_CAPABILITIES else ""
        A(f"| `{c['id']}` | {c['name']} | {aliases} | {soft} |")
    A("")
    A("**一条文本可能涉及多项能力**（「熟练使用 Excel 与 SQL」同时要求 Excel 和 SQL），")
    A("匹配引擎必须返回能力**集合**而非单项。只归一类会让用户已验证的能力不计入匹配。")
    A("")
    A("### 5. 培养方案课程映射\n")
    A("数据来源：工程管理学院 1201 管理科学与工程学科学术学位硕士研究生培养方案（2026级）。")
    A("这是本产品相对通用 AI **唯一的结构性优势**——通用模型不知道你们学校开什么课、")
    A("哪个学期开、几个学分。\n")
    A("| 能力 | 课程 | 课程代码 | 学期 | 教师 | 学分 | 必修 |")
    A("|---|---|---|---|---|---|---|")
    for cid, courses in CURRICULUM.items():
        if not courses:
            A(f"| `{cid}` | **培养方案内无对应课程** | — | — | — | — | — |")
            continue
        for i, c in enumerate(courses):
            name = f"`{cid}`" if i == 0 else ""
            mand = "是" if c.get("mandatory") else ""
            A(f"| {name} | 《{c['course']}》 | {c['code']} | {c['semester']} "
              f"| {c['teacher']} | {c['credits']} | {mand} |")
    A("")
    A("**诚实空缺**：以下能力在培养方案内没有对应课程，必须明确说「无对应课程」，")
    A("不许硬凑一门课——软能力无法从一门课获得，只能靠课程项目/竞赛/实习中的真实协作。\n")
    A("培养方案硬节点（来自培养方案第六、七节）：\n")
    A("| 学期 | 节点 | 说明 |")
    A("|---|---|---|")
    for m in PROGRAM_MILESTONES:
        A(f"| {m['semester']} | {m['event']} | {m['note']} |")
    A("")
    A("### 6. 岗位族归一化\n")
    A(f"共 {len(JOB_FAMILIES)} 个受控岗位族。\n")
    A("LLM 对同一类岗位会给出不同名字（商业分析师 / 战略商业分析师 / 商业分析Leader），")
    A("直接当独立岗位会让统计虚高、结果列表碎成一地。用受控词表归并：\n")
    A("| 岗位族 | 命中关键词 |")
    A("|---|---|")
    for f in JOB_FAMILIES:
        kws = "、".join(f"`{k}`" for k in f["keywords"])
        A(f"| {f['family']} | {kws} |")
    A("")
    A("无命中归入「其他（未归入受控岗位族）」。同族只保留证据最强的代表参与排名，")
    A("但展示族内样本数。")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对不写回")
    a = ap.parse_args()

    text = README.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        print(f"错误：README.md 缺少 {BEGIN} / {END} 标记", file=sys.stderr)
        return 2

    head = text.split(BEGIN, 1)[0] + BEGIN + "\n"
    tail = END + text.split(END, 1)[1]
    new = head + build_block() + "\n" + tail

    if a.check:
        if new != text:
            print("README 数据表与 curriculum.py 不一致。")
            print("请运行：python app/tools/sync_readme.py")
            return 1
        print("README 数据表与 curriculum.py 一致 ✓")
        return 0

    README.write_text(new, encoding="utf-8")
    print(f"已重写 README.md 的标记区（{BEGIN}…{END}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
