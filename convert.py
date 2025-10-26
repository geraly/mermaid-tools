"""Convert a Mermaid Gantt chart into a draw.io (mxGraph) XML file.

This is a minimal implementation that supports:
- mermaid gantt with date ranges: "YYYY-MM-DD" or "YYYY/MM/DD"
- tasks lines like: "    A :a1, 2020-01-01, 10d" or "    B :a2, 2020-01-05, 5d"
- simple parsing of sections and task ids

It produces a basic mxGraphModel XML representing each task as a rectangle
positioned on a timeline. Coordinates and scaling are configurable.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List

DATE_RE = re.compile(r"(\d{4}[-/]\d{2}[-/]\d{2})")
TASK_RE = re.compile(
    r"^\s*(?P<name>[^:\n]+)\s*:\s*(?P<id>[^,\s]+)\s*,\s*(?P<start>[^,]+)\s*,"
    r"\s*(?P<len>[^\n]+)",
    re.IGNORECASE,
)

# Configuration constants (change here to adjust output appearance)
DAY_WIDTH = 20  # pixels per day
TASK_HEIGHT = 20  # task rectangle height in pixels
MARGIN = 40  # left/top margin in pixels
ROW_GAP = 0  # vertical gap between tasks
FILL_COLOR = "#CDEBFF"  # default fill color for tasks
# timeline configuration
TICK_INTERVAL = 7  # days between labeled ticks (default weekly)
LABEL_MIN_GAP = 48  # minimal horizontal pixel gap between labels to avoid overlap
SECTION_COL_WIDTH = 120  # width reserved at left for section labels
SECTION_BG_COLORS = ["#FBF7F3", "#F3F7FB"]


class Task:
    def __init__(
        self,
        name: str,
        id_: str,
        start: datetime,
        length_days: int,
        section: str | None = None,
    ):
        self.name = name.strip()
        self.id = id_.strip()
        self.start = start
        self.length_days = length_days
        self.section = section

    def end(self) -> datetime:
        return self.start + timedelta(days=self.length_days)


def parse_mermaid(text: str) -> List[Task]:
    """Parse a mermaid gantt block text and return list of Task objects."""
    tasks: List[Task] = []
    in_gantt = False

    raw_tasks: list[dict[str, str | None]] = []
    current_section: str | None = None

    for line in text.splitlines():
        line = line.rstrip()
        if not in_gantt:
            if line.strip().lower().startswith("gantt"):
                in_gantt = True
            continue

        # skip empty or comment lines
        if not line.strip() or line.strip().startswith("%%"):
            continue

        m = TASK_RE.match(line)
        if not m:
            # check for section lines
            s = line.strip()
            if s.lower().startswith("section"):
                # rest after 'section' is the name
                current_section = s[len("section") :].strip()
            continue

        name = m.group("name").strip()
        id_ = m.group("id").strip()
        start_s = m.group("start").strip()
        len_s = m.group("len").strip()

        # keep raw start (may be a date or 'after <id>')
        # and raw length for later resolution
        raw_tasks.append(
            {
                "name": name,
                "id": id_,
                "start_raw": start_s,
                "len_raw": len_s,
                "section": current_section,
            }
        )

    # Resolve raw tasks into Task objects, handling 'after <id>' starts
    id_to_task: dict[str, Task] = {}
    unresolved = raw_tasks.copy()

    def parse_length(len_raw: str, start_dt: datetime) -> int:
        lr = len_raw.strip()
        if lr.endswith("d"):
            return int(lr[:-1])
        if lr.endswith("w"):
            return int(lr[:-1]) * 7
        m = DATE_RE.search(lr)
        if m:
            end_dt = datetime.fromisoformat(m.group(1).replace("/", "-"))
            return (end_dt - start_dt).days
        try:
            return int(lr)
        except Exception:
            return 1

    # multiple passes to resolve 'after' chains
    max_passes = 10
    for _ in range(max_passes):
        progressed = False
        remaining: list[dict[str, str | None]] = []
        for r in unresolved:
            start_raw = r.get("start_raw") or ""
            # date start
            ds = DATE_RE.search(start_raw)
            if ds:
                start_dt = datetime.fromisoformat(ds.group(1).replace("/", "-"))
            else:
                # maybe 'after id'
                s_lower = start_raw.lower()
                if s_lower.startswith("after"):
                    parts = start_raw.split()
                    if len(parts) >= 2:
                        ref_id = parts[1].strip().strip(",")
                        ref_task = id_to_task.get(ref_id)
                        if ref_task is None:
                            # cannot resolve yet
                            remaining.append(r)
                            continue
                        start_dt = ref_task.end()
                    else:
                        # malformed, skip
                        remaining.append(r)
                        continue
                else:
                    # unknown start format; default to today (or skip)
                    start_dt = datetime.now()

            length_days = parse_length(r.get("len_raw") or "", start_dt)
            task = Task(
                r.get("name") or "",
                r.get("id") or "",
                start_dt,
                max(1, length_days),
                r.get("section"),
            )
            id_to_task[task.id] = task
            tasks.append(task)
            progressed = True

        if not progressed:
            break
        unresolved = remaining

    # Any still-unresolved (circular or missing refs) -> place at min start
    if unresolved:
        # choose earliest existing start or today
        fallback = min((t.start for t in tasks), default=datetime.now())
        for r in unresolved:
            start_dt = fallback
            length_days = parse_length(r.get("len_raw") or "", start_dt)
            task = Task(
                r.get("name") or "",
                r.get("id") or "",
                start_dt,
                max(1, length_days),
                r.get("section"),
            )
            tasks.append(task)

    return tasks


def build_drawio_xml(
    tasks: List[Task],
    day_width: int = DAY_WIDTH,
    task_height: int = TASK_HEIGHT,
    margin: int = MARGIN,
) -> str:
    """Build a draw.io compatible XML string from tasks.

    Layout: vertical stacking by task, x-position calculated from earliest date.
    Each task is represented as a mxCell with a mxGeometry rectangle.
    """
    if not tasks:
        raise ValueError("no tasks provided")

    min_date = min(t.start for t in tasks)
    max_date = max(t.end() for t in tasks)

    # XML root
    mxfile = ET.Element("mxfile", attrib={"host": "mermaid2drawio"})
    diagram = ET.SubElement(mxfile, "diagram", attrib={"name": "Gantt", "id": "gantt1"})
    mxGraphModel = ET.SubElement(diagram, "mxGraphModel")
    root = ET.SubElement(mxGraphModel, "root")
    # add default cells
    ET.SubElement(root, "mxCell", attrib={"id": "0"})
    ET.SubElement(root, "mxCell", attrib={"id": "1", "parent": "0"})

    parent_cell = "1"

    # Group tasks by section while preserving insertion order of sections
    grouped: dict[str | None, list[Task]] = {}
    section_order: list[str | None] = []
    for t in tasks:
        if t.section not in grouped:
            section_order.append(t.section)
            grouped[t.section] = []
        grouped[t.section].append(t)

    # Flatten rows in section order
    rows: list[tuple[Task, str | None]] = []
    for section in section_order:
        for t in grouped.get(section, []):
            rows.append((t, section))

    total_rows = len(rows)
    row_height = task_height + ROW_GAP
    rows_height = total_rows * row_height - (ROW_GAP if total_rows > 0 else 0)
    y_start = margin

    total_days = (max_date - min_date).days + 1

    # draw section backgrounds and single centered label per section block
    cell_counter = 0
    row_index = 0
    for si, section in enumerate(section_order):
        tlist = grouped.get(section, [])
        if not tlist:
            continue
        block_start_y = y_start + row_index * row_height
        block_height = len(tlist) * row_height - (ROW_GAP if len(tlist) > 0 else 0)

        # background striping
        bg_id = f"bg{si + 1}"
        bg_color = SECTION_BG_COLORS[si % len(SECTION_BG_COLORS)]
        bg_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": bg_id,
                "value": "",
                "style": f"rounded=0;fillColor={bg_color};strokeColor=none;",
                "vertex": "1",
                "parent": parent_cell,
            },
        )

        # expand background to include the left section column so the
        # colored strip spans from the left edge of the section column
        # through the timeline area
        ET.SubElement(
            bg_cell,
            "mxGeometry",
            attrib={
                "x": str(margin),
                "y": str(block_start_y),
                "width": str(SECTION_COL_WIDTH + total_days * day_width),
                "height": str(block_height),
                "as": "geometry",
            },
        )

        # single centered label for this section block
        label_y = block_start_y + (block_height - task_height) / 2
        hid = f"sec_{si + 1}"
        # section label placed inside the left column; vertically centered
        # using verticalAlign=middle and centered horizontally
        header = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": hid,
                "value": section or "",
                "style": "text;verticalAlign=middle;align=center;whiteSpace=wrap;",
                "vertex": "1",
                "parent": parent_cell,
            },
        )
        ET.SubElement(
            header,
            "mxGeometry",
            attrib={
                "x": str(margin),
                "y": str(int(label_y)),
                "width": str(SECTION_COL_WIDTH - 8),
                "height": str(task_height),
                "as": "geometry",
            },
        )

        row_index += len(tlist)

    # (Ticks will be drawn after tasks so they appear on top)

    # place tasks row by row
    row_index = 0
    cell_counter = 0
    for t, section in rows:
        cell_counter += 1
        x = margin + SECTION_COL_WIDTH + (t.start - min_date).days * day_width
        w = max(4, t.length_days * day_width)
        h = task_height
        cid = f"task{cell_counter}"
        style_str = f"rounded=0;whiteSpace=wrap;fillColor={FILL_COLOR}"
        cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": cid,
                "value": t.name,
                "style": style_str,
                "vertex": "1",
                "parent": parent_cell,
            },
        )
        y = y_start + row_index * row_height
        ET.SubElement(
            cell,
            "mxGeometry",
            attrib={
                "x": str(x),
                "y": str(y),
                "width": str(w),
                "height": str(h),
                "as": "geometry",
            },
        )
        row_index += 1

    # pretty print
    # draw weekly ticks as edges (mxCell with edge="1") using two mxPoint children
    last_label_x = -1_000_000
    # iterate by week (TICK_INTERVAL days)
    for d in range(0, total_days, TICK_INTERVAL):
        day = min_date + timedelta(days=d)
        x = margin + SECTION_COL_WIDTH + d * day_width
        tick_id = f"tick{d + 1}"
        # create an edge cell so the line is drawn above vertex shapes
        tick_cell = ET.SubElement(
            root,
            "mxCell",
            attrib={
                "id": tick_id,
                "value": "",
                "edge": "1",
                "style": "endArrow=none;strokeColor=#000000;dashed=1;",
                "parent": parent_cell,
                # endArrow=classic;html=1;rounded=0;opacity=50;endFill=1;
            },
        )
        # geometry for edge (absolute points as source/target)
        geom = ET.SubElement(
            tick_cell,
            "mxGeometry",
            attrib={"as": "geometry"},
        )
        # source point (top)
        ET.SubElement(
            geom,
            "mxPoint",
            attrib={"x": str(x), "y": str(y_start), "as": "sourcePoint"},
        )
        # target point (bottom)
        ET.SubElement(
            geom,
            "mxPoint",
            attrib={
                "x": str(x),
                "y": str(y_start + (rows_height if rows_height > 0 else task_height)),
                "as": "targetPoint",
            },
        )

        # label for this weekly tick (avoid overlap)
        label_text = day.strftime("%m/%d")
        if x - last_label_x >= LABEL_MIN_GAP:
            label_id = f"lbl{d + 1}"
            label_cell = ET.SubElement(
                root,
                "mxCell",
                attrib={
                    "id": label_id,
                    "value": label_text,
                    "style": "text;verticalAlign=middle;align=center;whiteSpace=wrap;",
                    "vertex": "1",
                    "parent": parent_cell,
                },
            )
            lbl_w = max(40, LABEL_MIN_GAP)
            lbl_x = int(x - lbl_w // 2)
            ET.SubElement(
                label_cell,
                "mxGeometry",
                attrib={
                    "x": str(lbl_x),
                    "y": str(margin - 40),
                    "width": str(lbl_w),
                    "height": str(task_height),
                    "as": "geometry",
                },
            )
            last_label_x = x

    xml_str = ET.tostring(mxfile, encoding="utf-8")
    return xml_str.decode("utf-8")


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    p = argparse.ArgumentParser(description="Convert Mermaid Gantt to draw.io XML")
    p.add_argument("infile", help="input mermaid file (gantt)")
    p.add_argument("outfile", help="output draw.io xml file")
    args = p.parse_args()

    inp = Path(args.infile)
    if not inp.exists():
        raise SystemExit(f"Input file not found: {args.infile}")

    text = inp.read_text(encoding="utf-8")
    tasks = parse_mermaid(text)
    xml = build_drawio_xml(tasks)
    outp = Path(args.outfile)
    outp.write_text(xml, encoding="utf-8")
    print(f"Wrote {args.outfile}")
