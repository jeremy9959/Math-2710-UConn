#!/usr/bin/env python3
"""Build a "Recommended Problems" worksheet from the curated problem bank.

Usage:
    scripts/mkhw.py list
    scripts/mkhw.py build <date> <problem-id> [<problem-id> ...]

<date> is YYYY-MM-DD or YYYYMMDD.
<problem-id> looks like "1.1.A.1" (section.group.number), matching the
book's own numbering, and must already exist in homework/problems.yaml.

Adding a new problem to the bank is manual: edit homework/problems.yaml
(or ask Claude to transcribe one from a page/screenshot) before it can be
picked here.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString as DQ

REPO_ROOT = Path(__file__).resolve().parent.parent
HOMEWORK_DIR = REPO_ROOT / "homework"
BANK_PATH = HOMEWORK_DIR / "problems.yaml"
QUARTO_YML = REPO_ROOT / "_quarto.yml"

ID_RE = re.compile(r"^(?P<section>\d+(?:\.\d+)*)\.(?P<group>[A-Za-z])\.(?P<number>\d+)(?P<part>[a-z])?$")


def load_bank():
    with open(BANK_PATH) as f:
        data = yaml.safe_load(f)
    return data.get("sections", {})


def normalize_date(raw):
    raw = raw.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", raw)
    if m:
        year, month, day = m.groups()
    else:
        m = re.match(r"^(\d{4})(\d{2})(\d{2})$", raw)
        if not m:
            sys.exit(f"error: date '{raw}' is not YYYY-MM-DD or YYYYMMDD")
        year, month, day = m.groups()
    return {
        "iso": f"{year}-{month}-{day}",
        "compact": f"{year}{month}{day}",
        "label": f"{month}{day}{year}",
    }


def cmd_list(_args):
    sections = load_bank()
    for sec_id in sorted(sections, key=lambda s: [int(p) for p in s.split(".")]):
        sec = sections[sec_id]
        print(f"{sec_id}  ({sec.get('source', '').strip()})")
        groups = sec.get("groups", {})
        for group_id in sorted(groups):
            group = groups[group_id]
            problems = group.get("problems", {})
            print(f"  {group_id}. {group.get('instructions', '').strip()}")
            ids = []
            for number in sorted(problems, key=int):
                entry = problems[number]
                if isinstance(entry, dict):
                    for letter in sorted(entry.get("parts", {})):
                        ids.append(f"{sec_id}.{group_id}.{number}{letter}")
                else:
                    ids.append(f"{sec_id}.{group_id}.{number}")
            print(f"     {', '.join(ids)}")


def parse_id(problem_id):
    m = ID_RE.match(problem_id)
    if not m:
        sys.exit(f"error: '{problem_id}' doesn't look like section.group.number[part], e.g. 1.1.A.1 or 1.2.A.1a")
    return m.group("section"), m.group("group"), m.group("number"), m.group("part")


def resolve_ids(sections, problem_ids):
    """Return list of (section, group, number, part, latex, stem), preserving input
    order, and erroring out (listing everything missing) before rendering anything."""
    resolved = []
    missing = []
    for pid in problem_ids:
        sec_id, group_id, number, part = parse_id(pid)
        sec = sections.get(sec_id)
        group = sec.get("groups", {}).get(group_id) if sec else None
        problems = group.get("problems", {}) if group else {}
        entry = problems.get(int(number))

        if entry is None:
            missing.append(pid)
            continue
        if isinstance(entry, dict):
            if part is None:
                sys.exit(f"error: '{pid}' has multiple parts; pick one, e.g. {pid}a")
            latex = entry.get("parts", {}).get(part)
            if latex is None:
                missing.append(pid)
                continue
            resolved.append((sec_id, group_id, number, part, latex, entry.get("stem", "")))
        else:
            if part is not None:
                missing.append(pid)
                continue
            resolved.append((sec_id, group_id, number, None, entry, None))

    if missing:
        sys.exit(
            "error: these problem IDs aren't in the bank yet: "
            + ", ".join(missing)
            + f"\n(edit {BANK_PATH.relative_to(REPO_ROOT)} to add them, then retry)"
        )
    return resolved


def build_markdown(date_info, resolved, sections):
    lines = ["---", f"title: {date_info['iso']}", "---", ""]

    # Preserve first-encountered order of sections and groups within them.
    seen_sections = []
    grouped = {}
    for sec_id, group_id, number, part, latex, stem in resolved:
        if sec_id not in grouped:
            grouped[sec_id] = {}
            seen_sections.append(sec_id)
        grouped[sec_id].setdefault(group_id, {}).setdefault(number, {"stem": stem, "parts": {}})
        if part is None:
            grouped[sec_id][group_id][number]["latex"] = latex
        else:
            grouped[sec_id][group_id][number]["parts"][part] = latex

    for sec_id in seen_sections:
        sec = sections[sec_id]
        lines.append(f"## Exercises for Section {sec_id}")
        lines.append("")
        if sec.get("source"):
            lines.append(f"Source: {sec['source'].strip()}")
            lines.append("")
        for group_id in sorted(grouped[sec_id]):
            group = sec["groups"][group_id]
            lines.append(f"**{group_id}.** {group.get('instructions', '').strip()}")
            lines.append("")
            numbers = sorted(grouped[sec_id][group_id], key=int)
            for number in numbers:
                item = grouped[sec_id][group_id][number]
                if item["parts"]:
                    lines.append(f"**{number}.** {item['stem']}")
                    lines.append("")
                    for letter in sorted(item["parts"]):
                        lines.append(f"**({letter})** {item['parts'][letter]}")
                        lines.append("")
                else:
                    lines.append(f"**{number}.** {item['latex']}")
                    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_pdf(md_path, pdf_path):
    result = subprocess.run(
        ["pandoc", str(md_path), "--pdf-engine=xelatex", "-o", str(pdf_path)],
        cwd=md_path.parent,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.exit(f"error: pandoc failed:\n{result.stderr}")


def patch_quarto_yml(rel_md_path, sidebar_label):
    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.width = 4096
    yaml_rt.indent(mapping=2, sequence=4, offset=2)
    with open(QUARTO_YML) as f:
        doc = yaml_rt.load(f)

    render_list = doc["project"]["render"]
    if rel_md_path not in render_list:
        render_list.append(rel_md_path)

    sidebar_contents = doc["website"]["sidebar"]["contents"]
    section = next(
        (e for e in sidebar_contents if isinstance(e, dict) and e.get("section") == "Recommended Problems"),
        None,
    )
    if section is None:
        sys.exit("error: no 'Recommended Problems' section found in _quarto.yml sidebar")

    already = any(
        isinstance(e, dict) and e.get("href") == rel_md_path for e in section["contents"]
    )
    if not already:
        section["contents"].append({"text": DQ(sidebar_label), "href": rel_md_path})

    with open(QUARTO_YML, "w") as f:
        yaml_rt.dump(doc, f)

    return not already  # True if we actually changed something


def cmd_build(args):
    date_info = normalize_date(args.date)
    sections = load_bank()
    resolved = resolve_ids(sections, args.problem_ids)

    md_path = HOMEWORK_DIR / f"problems_{date_info['compact']}.md"
    pdf_path = HOMEWORK_DIR / f"problems_{date_info['compact']}.pdf"

    markdown = build_markdown(date_info, resolved, sections)
    md_path.write_text(markdown)
    print(f"wrote {md_path.relative_to(REPO_ROOT)}")

    render_pdf(md_path, pdf_path)
    print(f"wrote {pdf_path.relative_to(REPO_ROOT)}")

    rel_md_path = f"homework/{md_path.name}"
    changed = patch_quarto_yml(rel_md_path, date_info["label"])
    if changed:
        print(f"updated {QUARTO_YML.relative_to(REPO_ROOT)} (render list + sidebar entry)")
    else:
        print(f"{QUARTO_YML.relative_to(REPO_ROOT)} already had an entry for {rel_md_path}, left as-is")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list problems available in the bank").set_defaults(func=cmd_list)

    build_parser = sub.add_parser("build", help="build a worksheet from problem IDs")
    build_parser.add_argument("date", help="YYYY-MM-DD or YYYYMMDD")
    build_parser.add_argument("problem_ids", nargs="+", help="e.g. 1.1.A.1 1.1.B.24")
    build_parser.set_defaults(func=cmd_build)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
