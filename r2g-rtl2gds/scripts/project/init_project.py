#!/usr/bin/env python3
from pathlib import Path
import json
import os
import sys

TEMPLATE_DIRS = [
    "input", "rtl", "tb", "constraints", "lint", "sim", "synth",
    "backend", "drc", "lvs", "rcx", "reports", "labels", "features",
]


def resolve_base_dir(argv):
    """确定 design_cases 的父目录，结果与当前工作目录(CWD)无关。

    优先级（从高到低）：
      1. 命令行第二个参数：显式指定的 base-dir（最可靠，推荐由 run_stage.sh 传入）
      2. 环境变量 SKILL_DIR：使用 $SKILL_DIR/design_cases
      3. 兜底：根据脚本位置推导出的 SKILL_DIR 下的 design_cases
         —— 永远确定，不随 CWD 漂移

    关键修复点：绝不再使用 Path("design_cases") 这种相对 CWD 的写法，
    因此即使在任意目录下误调用本脚本，也不会再产生“幽灵”空目录。

    脚本实际位置：$SKILL_DIR/scripts/project/init_project.py
    因此 SKILL_DIR = 脚本目录往上两级（parents[2]）。
    """
    # 1) 显式参数优先
    if len(argv) > 2:
        return Path(argv[2]).expanduser().resolve()

    # 2) 环境变量 SKILL_DIR（与现有 run_stage.sh 的约定一致）
    skill_dir = os.environ.get("SKILL_DIR")
    if skill_dir:
        return (Path(skill_dir).expanduser() / "design_cases").resolve()

    # 3) 由脚本位置反推 SKILL_DIR，再拼 design_cases，而不是相对 CWD
    #    .../r2g-rtl2gds/scripts/project/init_project.py
    #    parents[0]=scripts/project  parents[1]=scripts  parents[2]=r2g-rtl2gds(=SKILL_DIR)
    skill_dir_from_script = Path(__file__).resolve().parents[2]
    return (skill_dir_from_script / "design_cases").resolve()


def main():
    if len(sys.argv) < 2:
        print("usage: init_project.py <design-name> [base-dir]", file=sys.stderr)
        sys.exit(1)

    design_name = sys.argv[1]
    base_dir = resolve_base_dir(sys.argv)
    root = (base_dir / design_name).resolve()

    # 解析后的绝对路径打到 stderr，便于排查“到底创建在哪了”，
    # 而 stdout 仍只输出 root（保持与调用方 PROJECT_DIR=$(...) 的契约不变）。
    print(f"[init_project] base_dir = {base_dir}", file=sys.stderr)
    print(f"[init_project] root     = {root}", file=sys.stderr)

    root.mkdir(parents=True, exist_ok=True)
    for d in TEMPLATE_DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)

    metadata = {
        "design_name": design_name,
        "status": "initialized",
    }
    (root / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(root)


if __name__ == "__main__":
    main()