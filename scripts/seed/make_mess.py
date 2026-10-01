"""生成 C12 的真实考试材料；使用 --reset 重置，--check 核验生成结果。"""

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[2] / "data" / "demo"
NAMES = {
    "pdf": ["十月预算.pdf", "采购单 003.pdf", "meeting-brief.pdf", "Warehouse_盘点.pdf"],
    "csv": ["销售明细.csv", "clients October.csv", "stock_20261001.csv", "报销记录.csv"],
    "txt": ["电话记录.txt", "todo 10月.txt", "交接说明.txt", "field-notes.txt"],
    "md": ["项目进度.md", "会议纪要.md", "Release Notes.md", "操作指南.md"],
    "none": ["README", "临时便签", "DELIVERY_104", "库存核对"],
}


def generate(reset: bool) -> None:
    inbox = ROOT / "inbox"
    if reset and inbox.exists():
        shutil.rmtree(inbox)
    inbox.mkdir(parents=True, exist_ok=False)
    for kind, names in NAMES.items():
        for index, name in enumerate(names, 1):
            path = inbox / name
            if kind == "pdf":
                document = canvas.Canvas(str(path), pagesize=A4, invariant=1)
                document.setTitle(f"October operations report {index}")
                for page in range(1, index + 1):
                    document.drawString(50, 790, f"AgentCrew C12 | Operations report {index} | Page {page}")
                    document.drawString(50, 760, "Reporting date: 2026-10-01. Generated acceptance material.")
                    for row in range(12 + index * 3):
                        document.drawString(50, 725 - row * 22, f"Item {row + 1:02}: quantity {index * (row + 1)}, unit price 18.50 CNY; verified in warehouse.")
                    document.showPage()
                document.save()
            elif kind == "csv":
                with path.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["日期", "项目", "数量", "单价", "说明"])
                    for row in range(index * 15):
                        writer.writerow(["2026-10-01", f"办公材料{row + 1}", (row + 1) * index, "18.50", "实物已登记，等待归档"])
            else:
                heading = f"# {path.stem}\n\n" if kind == "md" else f"{path.name}\n"
                body = "".join(f"记录 {row + 1}：十月办公材料核对完成，数量 {(row + 1) * index}；负责人需核查交接日期与库存清单。\n" for row in range(index * index * 7))
                path.write_text(heading + body, encoding="utf-8")
    manifest = [{"name": path.name, "size_bytes": path.stat().st_size,
                 "mtime_ns": path.stat().st_mtime_ns,
                 "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in sorted(inbox.iterdir())]
    (ROOT / "source-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"生成 {len(manifest)} 个文件：{inbox}")


def check() -> None:
    from pypdf import PdfReader

    manifest = json.loads((ROOT / "source-manifest.json").read_text(encoding="utf-8"))
    assert len(manifest) == 20
    assert {path.name for path in (ROOT / "inbox").iterdir()} == {name for names in NAMES.values() for name in names}
    for entry in manifest:
        path = ROOT / "inbox" / entry["name"]
        assert path.stat().st_size == entry["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        if path.suffix == ".pdf":
            pages = PdfReader(path).pages
            assert pages and all("Operations report" in page.extract_text() for page in pages)
        elif path.suffix == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            assert rows[0] == ["日期", "项目", "数量", "单价", "说明"] and len(rows) > 1
        else:
            assert "十月办公材料核对完成" in path.read_text(encoding="utf-8")
    print("核验通过：20 个真实文件、五种类型、内容与 SHA-256 一致")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="清除考试 inbox 后重新生成")
    parser.add_argument("--check", action="store_true", help="核验种子材料，保持文件不变")
    arguments = parser.parse_args()
    if arguments.check:
        check()
    else:
        generate(arguments.reset)
