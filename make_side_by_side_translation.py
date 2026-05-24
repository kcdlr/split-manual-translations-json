from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import fitz
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont


SOURCE_PDF = "01_Intro_Interface.pdf"
TRANSLATIONS_JSON = "translations_01_intro_interface.json"
OUTPUT_PDF = "01_Intro_Interface_ja_side_by_side.pdf"
MISSING_JSON = "translations_01_intro_interface.missing.json"
GENERATED_FONT_DIR = "generated_fonts"
FONT_WEIGHTS = {
    "regular": 400,
    "semibold": 600,
    "bold": 700,
}
FONT_STYLE_NAMES = {
    "regular": "Regular",
    "semibold": "SemiBold",
    "bold": "Bold",
}
FONT_NAMES = {
    "regular": "NotoSansJPRegular",
    "semibold": "NotoSansJPSemiBold",
    "bold": "NotoSansJPBold",
}


def default_japanese_font() -> str:
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [
        Path(windir) / "Fonts" / "NotoSansJP-VF.ttf",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    raise FileNotFoundError("No NotoSansJP variable font found in Windows Fonts.")


def ensure_static_font_instances(source_font: Path, output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    font_paths: dict[str, str] = {}
    for weight_name, weight_value in FONT_WEIGHTS.items():
        output_path = output_dir / f"NotoSansJP-{weight_name}-{weight_value}.ttf"
        style_name = FONT_STYLE_NAMES[weight_name]
        needs_write = True
        if output_path.exists():
            existing = TTFont(str(output_path))
            full_name = existing["name"].getDebugName(4)
            typographic_subfamily = existing["name"].getDebugName(17)
            needs_write = (
                existing["OS/2"].usWeightClass != weight_value
                or full_name != f"Noto Sans JP {style_name}"
                or typographic_subfamily != style_name
            )
        if needs_write:
            font = TTFont(str(source_font))
            static_font = instantiateVariableFont(font, {"wght": weight_value}, inplace=False)
            static_font["OS/2"].usWeightClass = weight_value
            set_font_names(static_font, style_name)
            static_font.save(str(output_path))
        font_paths[weight_name] = str(output_path)
    return font_paths


def set_font_names(font: TTFont, style_name: str) -> None:
    names = font["name"]
    values = {
        1: "Noto Sans JP",
        2: style_name,
        3: f"2.004;ADBO;NotoSansJP-{style_name};ADOBE",
        4: f"Noto Sans JP {style_name}",
        6: f"NotoSansJP-{style_name}",
        16: "Noto Sans JP",
        17: style_name,
    }
    for name_id, value in values.items():
        for platform_id, encoding_id, language_id in (
            (1, 0, 0),
            (3, 1, 0x409),
            (3, 1, 0x411),
        ):
            names.setName(value, name_id, platform_id, encoding_id, language_id)


def make_font_assets(source_font: Path, output_dir: Path) -> dict[str, dict[str, Any]]:
    font_paths = ensure_static_font_instances(source_font, output_dir)
    return {
        weight_name: {
            "path": font_path,
            "name": FONT_NAMES[weight_name],
            "font": fitz.Font(fontfile=font_path),
        }
        for weight_name, font_path in font_paths.items()
    }


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
            "spans": [
                {
                    "text": span.get("text", ""),
                    "font": span["font"],
                    "size": round(span["size"], 1),
                    "color": span.get("color"),
                    "bbox": list(span["bbox"]),
                }
                for span in spans
            ],
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


def int_color_to_rgb(color: int | None) -> tuple[float, float, float]:
    if color is None:
        return (0.12, 0.12, 0.12)
    return (
        ((color >> 16) & 255) / 255,
        ((color >> 8) & 255) / 255,
        (color & 255) / 255,
    )


def source_color(features: dict[str, Any], fallback: tuple[float, float, float] = (0.12, 0.12, 0.12)) -> tuple[float, float, float]:
    weighted_colors: dict[int, int] = {}
    color_order: list[int] = []
    for span in features.get("spans", []):
        color = span.get("color")
        if color is None:
            continue
        if color not in weighted_colors:
            weighted_colors[color] = 0
            color_order.append(color)
        weighted_colors[color] += max(1, len(span.get("text", "")))
    if not weighted_colors:
        return fallback
    color = max(color_order, key=lambda item: weighted_colors[item])
    return int_color_to_rgb(color)


def source_font_size(features: dict[str, Any], fallback: float = 9.5) -> float:
    sizes = sorted(features.get("sizes", []), reverse=True)
    return float(sizes[0]) if sizes else fallback


def span_weight(font_name: str) -> str:
    if "Semibold" in font_name:
        return "semibold"
    if "Bold" in font_name or "Black" in font_name:
        return "bold"
    return "regular"


def source_font_weight(features: dict[str, Any]) -> str:
    weighted: dict[str, int] = {}
    order: list[str] = []
    for span in features.get("spans", []):
        weight = span_weight(span.get("font", ""))
        if weight not in weighted:
            weighted[weight] = 0
            order.append(weight)
        weighted[weight] += max(1, len(span.get("text", "")))
    if not weighted:
        return "regular"
    return max(order, key=lambda item: weighted[item])


def font_asset(font_assets: dict[str, dict[str, Any]], weight: str) -> dict[str, Any]:
    return font_assets.get(weight, font_assets["regular"])


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
    font_assets: dict[str, dict[str, Any]],
    fontsize: float,
    color: tuple[float, float, float],
    weight: str = "regular",
    line_factor: float = 1.32,
) -> float:
    asset = font_asset(font_assets, weight)
    font = asset["font"]
    line_height = fontsize * line_factor
    max_width = max(1, rect.width)
    lines = wrap_text(font, text, fontsize, max_width)
    y = rect.y0 + fontsize
    for line in lines:
        page.insert_text(
            fitz.Point(rect.x0, y),
            line,
            fontname=asset["name"],
            fontfile=asset["path"],
            fontsize=fontsize,
            color=color,
            fill=color,
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


def overlaps_vertically(a: fitz.Rect, b: fitz.Rect, padding: float = 20) -> bool:
    return (a.y0 - padding) < b.y1 and (b.y0 - padding) < a.y1


def classify_block(block: dict[str, Any], features: dict[str, Any], toc_page: bool) -> str:
    rect = fitz.Rect(block["bbox"])
    fonts = features.get("fonts", set())
    sizes = features.get("sizes", set())
    colors = features.get("colors", set())
    text = features.get("text", block.get("source", ""))

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
    if (
        "ProximaNova-Bold" in fonts
        and "ProximaNova-Light" in fonts
        and 16099584 in colors
        and re.match(r"^\d+\s+", text)
    ):
        return "numbered_step"
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
    font_assets: dict[str, dict[str, Any]],
    color: tuple[float, float, float],
    fontsize: float,
) -> None:
    asset = font_asset(font_assets, "regular")
    target = fitz.Rect(rect.x0, rect.y0 - 1, rect.x1, rect.y1 + 4)
    size = min(fontsize, 7.2)
    while size >= 6.0:
        result = page.insert_textbox(
            target,
            text,
            fontname=asset["name"],
            fontfile=asset["path"],
            fontsize=size,
            color=color,
            fill=color,
            align=fitz.TEXT_ALIGN_LEFT,
            overlay=True,
        )
        if result >= 0:
            return
        size = round(size - 0.2, 2)


def draw_numbered_step(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    font_assets: dict[str, dict[str, Any]],
    fontsize: float,
    line_advance: float,
) -> float:
    match = re.match(r"^(\d+)\s+(.*)$", text)
    if not match:
        return draw_wrapped_text(
            page,
            rect,
            text,
            font_assets,
            fontsize=fontsize,
            color=(0.12, 0.12, 0.12),
            weight="regular",
            line_factor=line_advance / fontsize,
        )

    number, body = match.groups()
    number_asset = font_asset(font_assets, "bold")
    body_asset = font_asset(font_assets, "regular")
    y = rect.y0 + fontsize
    number_color = int_color_to_rgb(16099584)
    page.insert_text(
        fitz.Point(rect.x0, y),
        number,
        fontname=number_asset["name"],
        fontfile=number_asset["path"],
        fontsize=fontsize,
        color=number_color,
        fill=number_color,
        overlay=True,
    )

    body_x0 = min(rect.x1 - 20, rect.x0 + 17)
    body_rect = fitz.Rect(body_x0, rect.y0, rect.x1, rect.y1)
    lines = wrap_text(body_asset["font"], body, fontsize, max(1, body_rect.width))
    body_y = y
    for line in lines:
        page.insert_text(
            fitz.Point(body_rect.x0, body_y),
            line,
            fontname=body_asset["name"],
            fontfile=body_asset["path"],
            fontsize=fontsize,
            color=(0.12, 0.12, 0.12),
            fill=(0.12, 0.12, 0.12),
            overlay=True,
        )
        body_y += line_advance
    return max(y + line_advance, body_y)


def layout_translated_blocks(
    page: fitz.Page,
    blocks: list[dict[str, Any]],
    block_features: dict[str, dict[str, Any]],
    image_rects: list[fitz.Rect],
    translations: dict[str, str],
    page_width: float,
    font_assets: dict[str, dict[str, Any]],
    body_font_size: float,
    body_line_advance: float,
    paragraph_spacing: float,
) -> None:
    flow_blocks: list[tuple[dict[str, Any], fitz.Rect, str]] = []
    fixed_anchors: list[fitz.Rect] = [rect for rect in image_rects if rect.x0 < page_width - 80]
    toc_page = is_toc_page(blocks)

    for block in blocks:
        translated = translations.get(block["id"])
        if not translated:
            continue

        bbox = fitz.Rect(block["bbox"])
        features = block_features.get(block["id"], {})
        block_type = classify_block(block, features, toc_page)
        target = fitz.Rect(
            bbox.x0 + page_width,
            bbox.y0,
            bbox.x1 + page_width,
            bbox.y1,
        )

        if block_type in {"side_label", "footer"}:
            continue

        if block_type in {"chapter_title", "toc_entry"} and bbox.x0 < page_width - 80:
            fixed_anchors.append(bbox)

        if block_type == "chapter_title":
            title_font_size = 28 if bbox.height > 80 else 22
            title_target = fitz.Rect(target)
            title_target.x1 = min(page.rect.x1 - 50, max(title_target.x1, page_width + 540))
            draw_wrapped_text(
                page,
                title_target,
                translated,
                font_assets,
                fontsize=title_font_size,
                color=source_color(features),
                weight=source_font_weight(features),
                line_factor=1.15,
            )
        elif block_type == "toc_entry":
            insert_toc_line(
                page,
                target,
                translated,
                font_assets,
                source_color(features),
                fontsize=source_font_size(features, 9.0),
            )
        else:
            flow_blocks.append((block, bbox, block_type))

    flow_blocks.sort(key=lambda item: (round(item[1].x0 / 20) * 20, item[1].y0))
    column_rights: dict[int, float] = {}
    for _, bbox, _ in flow_blocks:
        column_key = round(bbox.x0 / 20) * 20
        column_rights[column_key] = max(column_rights.get(column_key, bbox.x1), bbox.x1)

    cursors: dict[int, float] = {}
    base_line_ratio = body_line_advance / body_font_size
    for block, bbox, block_type in flow_blocks:
        translated = translations[block["id"]]
        features = block_features.get(block["id"], {})
        column_key = round(bbox.x0 / 20) * 20
        x0 = bbox.x0 + page_width
        source_x1 = max(bbox.x1, column_rights.get(column_key, bbox.x1))
        has_right_neighbor = False
        for _, other_bbox, _ in flow_blocks:
            if other_bbox.x0 > bbox.x0 + 40 and overlaps_vertically(bbox, other_bbox, padding=40):
                source_x1 = min(source_x1, other_bbox.x0 - 12)
                has_right_neighbor = True
        if has_right_neighbor:
            source_x1 = max(source_x1, bbox.x0 + 80)
        else:
            source_x1 = max(source_x1, bbox.x1)
        x1 = source_x1 + page_width
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
        fontsize = source_font_size(features, body_font_size)
        line_advance = fontsize * base_line_ratio
        weight = source_font_weight(features)
        measuring_font = font_asset(font_assets, "regular")["font"]
        while fontsize > 6.0:
            measured_end = measure_wrapped_text_end(measuring_font, target, translated, fontsize, line_advance)
            if measured_end <= flow_limit:
                break
            fontsize = round(fontsize - 0.25, 2)
            line_advance = fontsize * base_line_ratio
        if block_type == "numbered_step":
            next_y = draw_numbered_step(
                page,
                target,
                translated,
                font_assets,
                fontsize=fontsize,
                line_advance=line_advance,
            )
        else:
            next_y = draw_wrapped_text(
                page,
                target,
                translated,
                font_assets,
                fontsize=fontsize,
                color=source_color(features),
                weight=weight,
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
    font_assets: dict[str, dict[str, Any]],
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
        block_features = extract_block_features(src_page, page_index)
        toc_page = is_toc_page(blocks)
        out_page = out.new_page(width=output_rect.width, height=output_rect.height)

        left_rect = fitz.Rect(0, 0, first.width, first.height)
        right_rect = fitz.Rect(first.width, 0, first.width * 2, first.height)

        out_page.show_pdf_page(left_rect, src, page_index)

        translated_blocks = [
            block
            for block in blocks
            if block["id"] in translations
            and classify_block(block, block_features.get(block["id"], {}), toc_page)
            not in {"side_label", "footer"}
        ]
        redacted = make_page_copy_without_text(src, page_index, translated_blocks)
        out_page.show_pdf_page(right_rect, redacted, 0)
        redacted.close()

        image_rects = extract_image_rects(src_page)
        layout_translated_blocks(
            out_page,
            blocks,
            block_features,
            image_rects,
            translations,
            first.width,
            font_assets,
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
    parser.add_argument("--generated-font-dir", default=GENERATED_FONT_DIR)
    parser.add_argument("--body-font-size", type=float, default=9.5)
    parser.add_argument("--body-line-advance", type=float, default=11.59)
    parser.add_argument("--paragraph-spacing", type=float, default=5.67)
    args = parser.parse_args()

    font_assets = make_font_assets(Path(args.font), Path(args.generated_font_dir))
    build_pdf(
        Path(args.input),
        Path(args.translations),
        Path(args.output),
        Path(args.missing),
        font_assets,
        args.body_font_size,
        args.body_line_advance,
        args.paragraph_spacing,
    )


if __name__ == "__main__":
    main()
