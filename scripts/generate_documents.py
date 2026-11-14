"""Render the fictitious Caldova Markdown policies into File Search-ready PDFs."""

from pathlib import Path
import re
import unicodedata

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace


DOCUMENTS = Path(__file__).resolve().parents[1] / "data" / "documents"


def latin1(text: str) -> str:
    replacements = {"\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2022": "-", "\u20ac": "EUR"}
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    return unicodedata.normalize("NFKC", text).encode("latin-1", "replace").decode("latin-1")


class PolicyPDF(FPDF):
    def gradient_rule(self, x: float, y: float, width: float, height: float) -> None:
        stops = ((4, 117, 211), (42, 84, 202), (176, 61, 196))
        strips = 240
        for index in range(strips):
            position = index / (strips - 1) * (len(stops) - 1)
            segment = min(int(position), len(stops) - 2)
            blend = position - segment
            color = tuple(round(start + (end - start) * blend) for start, end in zip(stops[segment], stops[segment + 1]))
            self.set_fill_color(*color)
            self.rect(x + width * index / strips, y, width / strips + 0.01, height, style="F")

    def header(self) -> None:
        self.set_font("Helvetica", "B", 19)
        self.set_text_color(42, 65, 102)
        self.set_char_spacing(2.2)
        self.cell(0, 9, "CALDOVA", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_char_spacing(0)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(97, 97, 97)
        self.cell(0, 5, "OPERATIONS POLICY LIBRARY  /  Fictitious demo data", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.gradient_rule(0, self.get_y() + 2.5, self.w, 0.75)
        self.ln(8)

    def footer(self) -> None:
        self.gradient_rule(self.l_margin, self.h - 20, 32, 0.45)
        self.set_y(-15)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(97, 97, 97)
        self.cell(self.epw * 0.75, 8, "Caldova | Fictitious demo data")
        self.cell(0, 8, f"Page {self.page_no()}/{{nb}}", align="R")

    def paragraph(self, text: str, *, style: str = "", size: int = 9) -> None:
        self.set_font("Helvetica", style, size)
        self.set_text_color(*((4, 117, 211) if size >= 16 else (42, 84, 202) if style == "B" else (27, 27, 27)))
        self.multi_cell(0, max(4.05, size * 0.44), latin1(text), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2.5 if size >= 16 else 1)


def render_document(source: Path) -> Path:
    pdf = PolicyPDF()
    pdf.set_margins(17, 12, 17)
    pdf.set_auto_page_break(True, margin=20)
    pdf.alias_nb_pages()
    pdf.set_title(latin1(source.stem.replace("_", " ").title()))
    pdf.set_author("Caldova Control Tower - Fictitious demo data")
    pdf.add_page()
    lines = source.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [latin1(cell.strip()) for cell in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                    rows.append(cells)
                i += 1
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(27, 27, 27)
            pdf.set_draw_color(214, 214, 224)
            pdf.set_line_width(0.18)
            col_widths = tuple(max(1, max(pdf.get_string_width(cell) for cell in column)) ** 0.5 for column in zip(*rows))
            with pdf.table(
                line_height=4.5,
                padding=1.6,
                text_align="LEFT",
                col_widths=col_widths,
                headings_style=FontFace(emphasis="B", color=(42, 84, 202), fill_color=(238, 241, 251)),
                cell_fill_color=(247, 247, 250),
                cell_fill_mode="ROWS",
            ) as table:
                for cells in rows:
                    row = table.row()
                    for cell in cells:
                        row.cell(cell)
            pdf.ln(3)
            continue
        if line.startswith("# "):
            pdf.paragraph(line[2:], style="B", size=18)
        elif line.startswith("## "):
            if pdf.get_y() > pdf.h - 40:
                pdf.add_page()
            pdf.ln(1.5)
            pdf.paragraph(line[3:], style="B", size=11)
        elif line.startswith("- ") or re.match(r"\d+\.\s", line):
            pdf.paragraph(line)
        else:
            paragraph = [line]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"^(#|\||- |\d+\.\s)", lines[i + 1].strip()):
                i += 1
                paragraph.append(lines[i].strip())
            pdf.paragraph(" ".join(paragraph))
        i += 1
    target = source.with_suffix(".pdf")
    pdf.output(target)
    return target


def main() -> None:
    sources = sorted(DOCUMENTS.glob("*.md"))
    if not sources:
        raise SystemExit(f"No Markdown policies found in {DOCUMENTS}")
    for source in sources:
        target = render_document(source)
        print(f"{target.name}: {target.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
