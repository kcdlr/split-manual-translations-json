from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import fitz


SOURCE_PDF = "01_Intro_Interface.pdf"
TRANSLATIONS_JSON = "translations_01_intro_interface.json"
OUTPUT_PDF = "01_Intro_Interface_ja_side_by_side.pdf"
MISSING_JSON = "translations_01_intro_interface.missing.json"


def default_japanese_font() -> str:
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [
        Path(windir) / "Fonts" / "NotoSansJP-VF.ttf",
        Path(windir) / "Fonts" / "YuGothR.ttc",
        Path(windir) / "Fonts" / "meiryo.ttc",
        Path(windir) / "Fonts" / "msgothic.ttc",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    raise FileNotFoundError("No Japanese font found in Windows Fonts.")


def block_id(page_index: int, block_index: int) -> str:
    return f"p{page_index + 1:03d}_b{block_index:03d}"


def clean_text(text: str) -> str:
    return " ".join(text.replace("\x08", " ").split())


def extract_text_blocks(page: fitz.Page, page_index: int) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block_index, raw in enumerate(page.get_text("blocks")):
        text = clean_text(raw[4])
        if not text:
            continue
        x0, y0, x1, y1 = raw[:4]
        blocks.append(
            {
                "id": block_id(page_index, block_index),
                "bbox": [x0, y0, x1, y1],
                "source": text,
            }
        )
    return blocks


def extract_block_features(page: fitz.Page, page_index: int) -> dict[str, dict[str, Any]]:
    features: dict[str, dict[str, Any]] = {}
    dict_blocks: list[dict[str, Any]] = []
    for raw in page.get_text("dict")["blocks"]:
        lines = raw.get("lines", [])
        spans = [
            span
            for line in lines
            for span in line.get("spans", [])
            if span.get("text", "").strip()
        ]
        if not spans:
            continue
        text = clean_text(" ".join(span["text"] for span in spans))
        if not text:
            continue
        line_origins: list[float] = []
        for line in lines:
            line_spans = [span for span in line.get("spans", []) if span.get("text", "").strip()]
            if line_spans:
                line_origins.append(line_spans[0]["origin"][1])
        dict_blocks.append(
            {
                "rect": fitz.Rect(raw["bbox"]),
                "spans": spans,
                "line_origins": line_origins,
                "text": text,
            }
        )

    for block in extract_text_blocks(page, page_index):
        block_rect = fitz.Rect(block["bbox"]) + (-0.75, -0.75, 0.75, 0.75)
        matched = []
        for entry in dict_blocks:
            entry_rect = entry["rect"]
            area = max(1.0, entry_rect.get_area())
            overlap = block_rect & entry_rect
            if overlap.get_area() / area > 0.75:
                matched.append(entry)
        spans = [span for entry in matched for span in entry["spans"]]
        if not spans:
            continue
        line_origins = sorted(
            {
                round(origin, 2)
                for entry in matched
                for origin in entry["line_origins"]
            }
        )
        text = clean_text(" ".join(entry["text"] for entry in matched)) or block["source"]
        features[block["id"]] = {
            "fonts": {span["font"] for span in spans},
            "sizes": {round(span["size"], 1) for span in spans},
            "colors": {span.get("color") for span in spans},
            "line_count": len(line_origins),
            "line_gaps": [
                round(line_origins[index + 1] - line_origins[index], 2)
                for index in range(len(line_origins) - 1)
            ],
            "text": text,
        }
    return features


def extract_image_rects(page: fitz.Page) -> list[fitz.Rect]:
    return [
        fitz.Rect(block["bbox"])
        for block in page.get_text("dict")["blocks"]
        if block.get("type") == 1
    ]


def load_translations(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    translations: dict[str, str] = {}
    for page in data.get("pages", []):
        for block in page.get("blocks", []):
            text = block.get("translation", "")
            if text:
                translations[block["id"]] = text
    return translations


def add_text_redactions(page: fitz.Page, blocks: list[dict[str, Any]]) -> None:
    for block in blocks:
        rect = fitz.Rect(block["bbox"])
        page.add_redact_annot(rect, fill=None, cross_out=False)
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_NONE,
        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )


def choose_font_size(rect: fitz.Rect, text: str) -> float:
    height = rect.height
    if rect.y0 < 150 and height >= 80:
        return 30
    if height >= 120:
        return 8.2
    if height >= 70:
        return 7.8
    if height >= 40:
        return 7.2
    if height >= 20:
        return 8.5
    return 7


def translated_color(rect: fitz.Rect, block_type: str = "") -> tuple[float, float, float]:
    # Keep heading-like blocks close to the source manual's orange accent.
    if block_type in {"chapter_title", "section_heading"}:
        return (0.82, 0.22, 0.04)
    if block_type == "caption":
        return (0.43, 0.43, 0.43)
    return (0.12, 0.12, 0.12)


def insert_fitted_text(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    font_path: str,
    color: tuple[float, float, float],
) -> None:
    base_size = choose_font_size(rect, text)
    min_size = 4.2
    inset = min(2, max(0, rect.width * 0.01))
    target = fitz.Rect(rect.x0 + inset, rect.y0 + 1, rect.x1 - inset, rect.y1 - 1)

    size = base_size
    while size >= min_size:
        result = page.insert_textbox(
            target,
            text,
            fontname="NotoSansJP",
            fontfile=font_path,
            fontsize=size,
            color=color,
            fill=color,
            align=fitz.TEXT_ALIGN_LEFT,
            render_mode=2,
            border_width=0.025,
            overlay=True,
        )
        if result >= 0:
            return
        size -= 0.5

    page.insert_textbox(
        target,
        text,
        fontname="NotoSansJP",
        fontfile=font_path,
        fontsize=min_size,
        color=color,
        fill=color,
        align=fitz.TEXT_ALIGN_LEFT,
        render_mode=2,
        border_width=0.025,
        overlay=True,
    )


def wrap_text(font: fitz.Font, text: str, fontsize: float, max_width: float) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and font.text_length(candidate, fontsize=fontsize) > max_width:
                lines.append(current.rstrip())
                current = char.lstrip()
            else:
                current = candidate
        if current:
            lines.append(current.rstrip())
    return lines


def draw_wrapped_text(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    font_path: str,
    font: fitz.Font,
    fontsize: float,
    color: tuple[float, float, float],
    line_factor: float = 1.32,
) -> float:
    line_height = fontsize * line_factor
    max_width = max(1, rect.width)
    lines = wrap_text(font, text, fontsize, max_width)
    y = rect.y0 + fontsize
    for line in lines:
        page.insert_text(
            fitz.Point(rect.x0, y),
            line,
            fontname="NotoSansJP",
            fontfile=font_path,
            fontsize=fontsize,
            color=color,
            fill=color,
            render_mode=2,
            border_width=0.025,
            overlay=True,
        )
        y += line_height
    return y


def measure_wrapped_text_end(
    font: fitz.Font,
    rect: fitz.Rect,
    text: str,
    fontsize: float,
    line_advance: float,
) -> float:
    lines = wrap_text(font, text, fontsize, max(1, rect.width))
    return rect.y0 + fontsize + (len(lines) * line_advance)


def is_side_label(rect: fitz.Rect) -> bool:
    return rect.x0 > 520


def is_footer(rect: fitz.Rect) -> bool:
    return rect.y0 > 760


def is_title(rect: fitz.Rect) -> bool:
    return rect.x0 < 95 and rect.y0 < 115 and rect.height > 20


def classify_block(block: dict[str, Any], features: dict[str, Any], toc_page: bool) -> str:
    rect = fitz.Rect(block["bbox"])
    fonts = features.get("fonts", set())
    sizes = features.get("sizes", set())
    colors = features.get("colors", set())
    text = features.get("text", block.get("source", ""))
    line_count = features.get("line_count", 1)

    if rect.x0 > 520 or "BrandonGrotesque-Black" in fonts:
        return "side_label"
    if rect.y0 > 760:
        return "footer"
    if toc_page and 110 < rect.y0 < 710:
        return "toc_entry"
    if (
        sizes == {9.0}
        and rect.x0 < 520
        and rect.y0 > 430
        and ("ProximaNovaT-Thin" in fonts or text.rstrip().split(" ")[-1].isdigit())
    ):
        return "toc_entry"
    if 50.0 in sizes or (22.0 in sizes and rect.y0 < 120 and rect.x0 < 95):
        return "chapter_title"
    if 22.0 in sizes:
        return "chapter_title"
    if 15.0 in sizes and "ProximaNova-Semibold" in fonts:
        return "section_heading"
    if (
        sizes == {11.5}
        and "ProximaNova-Regular" in fonts
        and line_count == 1
        and colors == {0}
    ):
        return "minor_heading"
    if min(sizes or {99}) <= 8.5 and 7303022 in colors and rect.y0 < 760 and rect.x0 < 520:
        return "caption"
    if text.startswith(("NOTE:", "TIP:")) or (
        rect.x0 >= 100
        and rect.width > 300
        and rect.height < 55
        and ("For more information" in text or "see Chapter" in text)
    ) or (
        rect.x0 > 250
        and rect.y0 > 630
        and rect.width < 230
        and rect.height > 45
        and sizes == {9.5}
        and "ProximaNova-Light" in fonts
    ) or (rect.x0 >= 100 and {"ProximaNova-Bold", "ProximaNova-Light"} <= fonts):
        return "note"
    if (
        sizes == {9.5}
        and "ProximaNova-Semibold" in fonts
        and line_count == 1
        and colors == {0}
        and rect.width < 280
    ):
        return "subheading"
    return "body"


def is_toc_page(blocks: list[dict[str, Any]]) -> bool:
    small_rows = 0
    for block in blocks:
        rect = fitz.Rect(block["bbox"])
        if 110 < rect.y0 < 710 and rect.height < 18:
            small_rows += 1
    return small_rows > 30


def insert_toc_line(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    font_path: str,
    color: tuple[float, float, float],
) -> None:
    target = fitz.Rect(rect.x0, rect.y0 - 1, rect.x1, rect.y1 + 4)
    page.insert_textbox(
        target,
        text,
        fontname="NotoSansJP",
        fontfile=font_path,
        fontsize=7.2,
        color=color,
        fill=color,
        render_mode=2,
        border_width=0.025,
        align=fitz.TEXT_ALIGN_LEFT,
        overlay=True,
    )


def draw_one_line_label(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    font_path: str,
    font: fitz.Font,
    fontsize: float,
    color: tuple[float, float, float],
    page_width: float,
) -> None:
    width = font.text_length(text, fontsize=fontsize)
    target = fitz.Rect(rect)
    target.x1 = min(page_width * 2 - 34, max(target.x1, target.x0 + width + 8))
    page.insert_text(
        fitz.Point(target.x0, target.y0 + fontsize),
        text,
        fontname="NotoSansJP",
        fontfile=font_path,
        fontsize=fontsize,
        color=color,
        fill=color,
        render_mode=2,
        border_width=0.025,
        overlay=True,
    )


def layout_translated_blocks(
    page: fitz.Page,
    blocks: list[dict[str, Any]],
    block_features: dict[str, dict[str, Any]],
    image_rects: list[fitz.Rect],
    translations: dict[str, str],
    page_width: float,
    font_path: str,
    body_font_size: float,
    body_line_advance: float,
    paragraph_spacing: float,
) -> None:
    font = fitz.Font(fontfile=font_path)
    flow_blocks: list[tuple[dict[str, Any], fitz.Rect]] = []
    fixed_anchors: list[fitz.Rect] = [rect for rect in image_rects if rect.x0 < page_width - 80]
    toc_page = is_toc_page(blocks)

    for block in blocks:
        translated = translations.get(block["id"])
        if not translated:
            continue

        bbox = fitz.Rect(block["bbox"])
        block_type = classify_block(block, block_features.get(block["id"], {}), toc_page)
        target = fitz.Rect(
            bbox.x0 + page_width,
            bbox.y0,
            bbox.x1 + page_width,
            bbox.y1,
        )

        if (
            block_type
            in {"chapter_title", "section_heading", "minor_heading", "subheading", "caption", "toc_entry"}
            and bbox.x0 < page_width - 80
        ):
            fixed_anchors.append(bbox)

        if block_type == "chapter_title":
            title_font_size = 28 if bbox.height > 80 else 22
            title_target = fitz.Rect(target)
            title_target.x1 = min(page.rect.x1 - 50, max(title_target.x1, page_width + 540))
            draw_wrapped_text(
                page,
                title_target,
                translated,
                font_path,
                font,
                fontsize=title_font_size,
                color=translated_color(bbox, block_type),
                line_factor=1.15,
            )
        elif block_type == "toc_entry":
            insert_toc_line(
                page,
                target,
                translated,
                font_path,
                translated_color(bbox, block_type),
            )
        elif block_type == "section_heading":
            draw_one_line_label(
                page,
                target,
                translated,
                font_path,
                font,
                fontsize=15.0,
                color=translated_color(bbox, block_type),
                page_width=page_width,
            )
        elif block_type == "minor_heading":
            draw_one_line_label(
                page,
                target,
                translated,
                font_path,
                font,
                fontsize=11.5,
                color=translated_color(bbox, block_type),
                page_width=page_width,
            )
        elif block_type == "subheading":
            draw_one_line_label(
                page,
                target,
                translated,
                font_path,
                font,
                fontsize=body_font_size,
                color=translated_color(bbox, block_type),
                page_width=page_width,
            )
        elif block_type == "caption":
            caption_target = fitz.Rect(target)
            caption_target.y1 = min(caption_target.y1 + 16, page.rect.y1 - 44)
            draw_wrapped_text(
                page,
                caption_target,
                translated,
                font_path,
                font,
                fontsize=8.0,
                color=translated_color(bbox, block_type),
                line_factor=1.22,
            )
        elif block_type == "note":
            insert_fitted_text(
                page,
                target,
                translated,
                font_path,
                translated_color(bbox, block_type),
            )
        elif block_type in {"side_label", "footer"}:
            insert_fitted_text(
                page,
                target,
                translated,
                font_path,
                translated_color(bbox, block_type),
            )
        else:
            flow_blocks.append((block, bbox))

    flow_blocks.sort(key=lambda item: (round(item[1].x0 / 20) * 20, item[1].y0))
    cursors: dict[int, float] = {}
    for block, bbox in flow_blocks:
        translated = translations[block["id"]]
        column_key = round(bbox.x0 / 20) * 20
        x0 = bbox.x0 + page_width
        x1 = bbox.x1 + page_width
        y0 = max(bbox.y0, cursors.get(column_key, bbox.y0))
        next_fixed_y = min(
            (
                anchor.y0
                for anchor in fixed_anchors
                if abs(anchor.x0 - bbox.x0) < 40 and anchor.y0 > bbox.y0 + 10
            ),
            default=page.rect.y1 - 44,
        )
        flow_limit = min(page.rect.y1 - 44, next_fixed_y - 6)
        y1 = max(y0 + 1, min(flow_limit, max(y0 + 40, bbox.y1 + 80)))
        target = fitz.Rect(x0, y0, x1, y1)
        fontsize = body_font_size
        line_advance = body_line_advance
        while fontsize > 8.0:
            measured_end = measure_wrapped_text_end(font, target, translated, fontsize, line_advance)
            if measured_end <= flow_limit:
                break
            fontsize = round(fontsize - 0.25, 2)
            line_advance = body_line_advance * (fontsize / body_font_size)
        next_y = draw_wrapped_text(
            page,
            target,
            translated,
            font_path,
            font,
            fontsize=fontsize,
            color=translated_color(bbox, "body"),
            line_factor=line_advance / fontsize,
        )
        cursors[column_key] = next_y + paragraph_spacing - fontsize


def make_page_copy_without_text(src: fitz.Document, page_index: int, blocks: list[dict[str, Any]]) -> fitz.Document:
    redacted = fitz.open()
    redacted.insert_pdf(src, from_page=page_index, to_page=page_index)
    if blocks:
        add_text_redactions(redacted[0], blocks)
    return redacted


def write_missing_report(
    source: fitz.Document,
    translations: dict[str, str],
    output_path: Path,
) -> int:
    missing_pages: list[dict[str, Any]] = []
    count = 0
    for page_index, page in enumerate(source):
        missing_blocks = []
        for block in extract_text_blocks(page, page_index):
            if block["id"] not in translations:
                missing_blocks.append(block)
                count += 1
        if missing_blocks:
            missing_pages.append({"page": page_index + 1, "blocks": missing_blocks})

    output_path.write_text(
        json.dumps({"pages": missing_pages}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return count


def build_pdf(
    input_pdf: Path,
    translations_json: Path,
    output_pdf: Path,
    missing_json: Path,
    font_path: str,
    body_font_size: float,
    body_line_advance: float,
    paragraph_spacing: float,
) -> None:
    src = fitz.open(input_pdf)
    translations = load_translations(translations_json)

    missing_count = write_missing_report(src, translations, missing_json)
    if missing_count:
        print(f"Missing translations: {missing_count} blocks -> {missing_json}")

    first = src[0].rect
    out = fitz.open()
    output_rect = fitz.Rect(0, 0, first.width * 2, first.height)

    for page_index, src_page in enumerate(src):
        blocks = extract_text_blocks(src_page, page_index)
        out_page = out.new_page(width=output_rect.width, height=output_rect.height)

        left_rect = fitz.Rect(0, 0, first.width, first.height)
        right_rect = fitz.Rect(first.width, 0, first.width * 2, first.height)

        out_page.show_pdf_page(left_rect, src, page_index)

        translated_blocks = [block for block in blocks if block["id"] in translations]
        redacted = make_page_copy_without_text(src, page_index, translated_blocks)
        out_page.show_pdf_page(right_rect, redacted, 0)
        redacted.close()

        block_features = extract_block_features(src_page, page_index)
        image_rects = extract_image_rects(src_page)
        layout_translated_blocks(
            out_page,
            blocks,
            block_features,
            image_rects,
            translations,
            first.width,
            font_path,
            body_font_size,
            body_line_advance,
            paragraph_spacing,
        )

    out.save(output_pdf, garbage=4, deflate=True)
    out.close()
    src.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an A3-landscape side-by-side original/Japanese PDF."
    )
    parser.add_argument("--input", default=SOURCE_PDF)
    parser.add_argument("--translations", default=TRANSLATIONS_JSON)
    parser.add_argument("--output", default=OUTPUT_PDF)
    parser.add_argument("--missing", default=MISSING_JSON)
    parser.add_argument("--font", default=default_japanese_font())
    parser.add_argument("--body-font-size", type=float, default=9.5)
    parser.add_argument("--body-line-advance", type=float, default=11.59)
    parser.add_argument("--paragraph-spacing", type=float, default=5.67)
    args = parser.parse_args()

    build_pdf(
        Path(args.input),
        Path(args.translations),
        Path(args.output),
        Path(args.missing),
        args.font,
        args.body_font_size,
        args.body_line_advance,
        args.paragraph_spacing,
    )


if __name__ == "__main__":
    main()
