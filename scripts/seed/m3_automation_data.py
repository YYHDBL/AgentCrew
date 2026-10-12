"""生成 M3 自动化贯穿验收的独立真实材料与 SHA256 清单。"""

import argparse
import csv
import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path


def seed(root: Path) -> dict:
    project = Path(__file__).resolve().parents[2]
    if root == project or root.is_relative_to(project / "data"):
        raise ValueError("验收材料必须使用独立输出目录，不能写入项目根目录或生产 data 目录")
    if any((parent / "agentcrew.db").exists() for parent in (root, *root.parents)):
        raise ValueError("验收材料不能写入已有运行时数据库目录")

    rows = [
        ("M3-001", "办公用品", "纸张", 3, "12.50"),
        ("M3-002", "办公用品", "文件夹", 4, "8.00"),
        ("M3-003", "电子配件", "数据线", 2, "35.00"),
        ("M3-004", "电子配件", "转接头", 1, "48.00"),
    ]
    source = io.StringIO(newline="")
    writer = csv.writer(source)
    writer.writerow(["order_id", "category", "item", "quantity", "unit_price"])
    writer.writerows(rows)
    expected = {
        "order_count": 4,
        "total_quantity": 10,
        "total_amount": "187.50",
        "categories": [
            {"category": "办公用品", "order_count": 2, "quantity": 7, "amount": "69.50"},
            {"category": "电子配件", "order_count": 2, "quantity": 3, "amount": "118.00"},
        ],
    }
    materials = {
        "inputs/orders.csv": source.getvalue(),
        "inputs/context.md": (
            "# M3 订单汇总材料\n\n"
            "根据 orders.csv 汇总订单数量、商品总数量、总金额与各类别金额。\n"
            "金额单位为人民币元，保留两位小数；保留原始订单编号，不修改输入文件。\n"
            "定时执行应将汇总结果写入本次任务明确指定的输出文件。\n"
        ),
        "inputs/notes.txt": (
            "核查规则：订单编号唯一，数量为正整数，单价为人民币元。\n"
            "每笔金额为 quantity × unit_price；总金额包含全部四笔订单。\n"
            "读取任务指定的路径失败时，应记录真实错误与原始事件，保持输入文件内容。\n"
        ),
        "expected/summary.json": json.dumps(expected, ensure_ascii=False, indent=2) + "\n",
    }
    missing_input = root / "inputs" / "absent-for-failure.csv"
    if missing_input.exists() or missing_input.is_symlink():
        raise ValueError("真实失败分析所需的缺失路径已经存在，停止生成")

    # 先核对全部已有材料，防止部分写入后才发现用户修改过输入文件。
    for name, content in materials.items():
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError(f"材料路径不能通过符号链接离开输出目录：{path}")
        if path.exists() and path.read_bytes() != content.encode("utf-8"):
            raise ValueError(f"已有材料内容不一致，停止覆盖：{path}")
    files = []
    for name, content in materials.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open("xb") as output:
                output.write(content.encode("utf-8"))
        value = path.read_bytes()
        files.append({"path": str(path), "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()})

    # 使用 CSV 库读取实际生成文件，独立核查数量与金额。
    with (root / "inputs/orders.csv").open(encoding="utf-8", newline="") as input_file:
        actual = list(csv.DictReader(input_file))
    assert len(actual) == expected["order_count"]
    assert len({row["order_id"] for row in actual}) == len(actual)
    assert sum(int(row["quantity"]) for row in actual) == expected["total_quantity"]
    for category in [None, *expected["categories"]]:
        selected = actual if category is None else [row for row in actual if row["category"] == category["category"]]
        total = sum((Decimal(row["unit_price"]) * int(row["quantity"]) for row in selected), Decimal("0"))
        assert str(total) == (expected["total_amount"] if category is None else category["amount"])
        if category is not None:
            assert len(selected) == category["order_count"]
            assert sum(int(row["quantity"]) for row in selected) == category["quantity"]

    manifest = {
        "version": 1,
        "root": str(root),
        "inputs": {name: str(root / "inputs" / filename) for name, filename in (
            ("orders", "orders.csv"), ("context", "context.md"), ("notes", "notes.txt"))},
        "expected": expected,
        "expected_file": str(root / "expected/summary.json"),
        "missing_input": str(missing_input),
        "outputs": {name: str(root / "outputs" / f"{name}.md") for name in ("at", "every", "cron", "summary")},
        "files": files,
    }
    manifest_path = root / "manifest.json"
    content = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if manifest_path.is_symlink() or not manifest_path.resolve().is_relative_to(root):
        raise ValueError("清单路径不能通过符号链接离开输出目录")
    if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != content:
        raise ValueError("已有清单内容不一致，停止覆盖")
    if not manifest_path.exists():
        with manifest_path.open("x", encoding="utf-8") as output:
            output.write(content)
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest
    return {"manifest": str(manifest_path), "files": len(files), "missing_input": str(missing_input)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="独立验收材料输出目录")
    args = parser.parse_args()
    print(json.dumps(seed(args.output_dir.resolve()), ensure_ascii=False))
