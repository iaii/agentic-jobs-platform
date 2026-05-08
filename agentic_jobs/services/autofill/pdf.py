from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from markdown_it import MarkdownIt

from agentic_jobs.services.documents.style import DocumentStyle, get_document_style


_MD = MarkdownIt("commonmark").enable("strikethrough")
_WORD_SPLIT_RE = re.compile(r"(\s+)")


@dataclass(slots=True)
class TextSegment:
    text: str
    bold: bool = False
    italic: bool = False
    monospace: bool = False
    size: int = 12

    def with_text(self, text: str) -> "TextSegment":
        return TextSegment(
            text=text,
            bold=self.bold,
            italic=self.italic,
            monospace=self.monospace,
            size=self.size,
        )


def render_cover_letter_pdf(text: str, output_path: Path) -> Path:
    """Render markdown content into a PDF following the configured formatting rules."""

    style = get_document_style()
    raw_lines = _markdown_to_lines(text, style)
    lines = _wrap_lines(raw_lines, style)
    content_stream = _build_text_stream(lines, style)
    _write_pdf(content_stream, style, output_path)
    return output_path


def _markdown_to_lines(text: str, style: DocumentStyle) -> list[list[TextSegment]]:
    tokens = _MD.parse(text or "")
    lines: list[list[TextSegment]] = []
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token.type == "heading_open":
            level = int(token.tag[1:])
            inline = tokens[idx + 1]
            font_size = 16 if level == 1 else 14 if level == 2 else 13 if level == 3 else 12
            segments = _render_inline(inline.children or [], font_size=font_size, emphasize_heading=True)
            lines.extend(_split_segments(segments))
            lines.append([])
            idx += 3
            continue
        if token.type == "paragraph_open":
            inline = tokens[idx + 1]
            segments = _render_inline(inline.children or [])
            lines.extend(_split_segments(segments))
            lines.append([])
            idx += 3
            continue
        if token.type == "bullet_list_open":
            idx = _consume_list(tokens, idx + 1, lines, style, ordered=False)
            lines.append([])
            continue
        if token.type == "ordered_list_open":
            start = int(token.attrGet("start") or 1)
            idx = _consume_list(tokens, idx + 1, lines, style, ordered=True, start_index=start)
            lines.append([])
            continue
        idx += 1
    if lines and not lines[-1]:
        lines.pop()
    return lines or [[]]


def _consume_list(
    tokens: list,
    idx: int,
    lines: list[list[TextSegment]],
    style: DocumentStyle,
    *,
    ordered: bool,
    start_index: int = 1,
) -> int:
    item_index = 0
    closing_type = "ordered_list_close" if ordered else "bullet_list_close"
    while idx < len(tokens):
        token = tokens[idx]
        if token.type == "list_item_open":
            idx += 1
            item_segments: list[TextSegment] = []
            while idx < len(tokens) and tokens[idx].type != "list_item_close":
                if tokens[idx].type == "paragraph_open":
                    inline = tokens[idx + 1]
                    item_segments.extend(_render_inline(inline.children or []))
                    idx += 3
                else:
                    idx += 1
            idx += 1  # consume list_item_close
            formatted = _format_list_item(item_segments, style, ordered, start_index + item_index)
            lines.extend(formatted)
            lines.append([])
            item_index += 1
            continue
        if token.type == closing_type:
            return idx + 1
        idx += 1
    return idx


def _format_list_item(segments: list[TextSegment], style: DocumentStyle, ordered: bool, item_number: int) -> list[list[TextSegment]]:
    wrapped = _split_segments(segments)
    if not wrapped:
        return []
    bullet = style.bullet_symbol or "-"
    prefix = f"{item_number}. " if ordered else f"{bullet} "
    indent = " " * len(prefix)
    wrapped[0].insert(0, TextSegment(prefix, bold=False))
    for line in wrapped[1:]:
        line.insert(0, TextSegment(indent, bold=False))
    return wrapped


def _split_segments(segments: list[TextSegment]) -> list[list[TextSegment]]:
    lines: list[list[TextSegment]] = []
    current: list[TextSegment] = []
    for segment in segments:
        parts = segment.text.split("\n")
        for index, part in enumerate(parts):
            if part:
                current.append(segment.with_text(part))
            if index < len(parts) - 1:
                lines.append(current)
                current = []
    if current:
        lines.append(current)
    return lines


def _wrap_lines(lines: list[list[TextSegment]], style: DocumentStyle) -> list[list[TextSegment]]:
    wrapped: list[list[TextSegment]] = []
    for line in lines:
        wrapped.extend(_wrap_line(line, style))
    return wrapped


def _wrap_line(segments: list[TextSegment], style: DocumentStyle) -> list[list[TextSegment]]:
    if not segments:
        return [[]]
    max_width = style.content_width
    wrapped: list[list[TextSegment]] = []
    current: list[TextSegment] = []
    current_width = 0.0
    for segment in segments:
        text = segment.text
        parts = _WORD_SPLIT_RE.split(text)
        for part in parts:
            if not part:
                continue
            if part == "\n":
                wrapped.append(current)
                current = []
                current_width = 0.0
                continue
            part_width = _estimate_width(segment, part, style)
            if part.strip() and current and current_width + part_width > max_width:
                wrapped.append(current)
                current = []
                current_width = 0.0
                if part.strip() == "":
                    continue
            if not current and part.strip() == "":
                continue
            current.append(segment.with_text(part))
            current_width += part_width
    if current or not wrapped:
        wrapped.append(current)
    return wrapped


def _estimate_width(segment: TextSegment, text: str, style: DocumentStyle) -> float:
    factor = style.monospace_width_factor if segment.monospace else style.char_width_factor
    return len(text) * segment.size * factor


def _build_text_stream(lines: list[list[TextSegment]], style: DocumentStyle) -> str:
    start_y = style.page_height - style.margin_top_pt
    content_lines: list[str] = [
        "BT",
        f"{style.line_height:.2f} TL",
        f"{style.margin_left_pt:.2f} {start_y:.2f} Td",
    ]
    active_font = None
    active_size = None
    for line in lines:
        if not line:
            content_lines.append("T*")
            continue
        for segment in line:
            font_name = _font_name(segment)
            if font_name != active_font or segment.size != active_size:
                content_lines.append(f"/{font_name} {segment.size:.2f} Tf")
                active_font = font_name
                active_size = segment.size
            escaped = _escape_pdf_text(segment.text)
            if not escaped:
                continue
            content_lines.append(f"({escaped}) Tj")
        content_lines.append("T*")
    content_lines.append("ET")
    return "\n".join(content_lines)


def _font_name(segment: TextSegment) -> str:
    if segment.monospace:
        return "F5"
    if segment.bold and segment.italic:
        return "F4"
    if segment.bold:
        return "F2"
    if segment.italic:
        return "F3"
    return "F1"


def _write_pdf(content_stream: str, style: DocumentStyle, output_path: Path) -> None:
    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    offsets: list[int] = []

    def _add_object(obj_number: int, body: bytes) -> None:
        offsets.append(buffer.tell())
        buffer.write(f"{obj_number} 0 obj\n".encode("ascii"))
        buffer.write(body)
        buffer.write(b"\nendobj\n")

    _add_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    _add_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    page_dict = (
        f"<< /Type /Page /Parent 2 0 R "
        f"/MediaBox [0 0 {style.page_width:.2f} {style.page_height:.2f}] "
        f"/Resources << /Font << /F1 5 0 R /F2 6 0 R /F3 7 0 R /F4 8 0 R /F5 9 0 R >> >> "
        f"/Contents 4 0 R >>"
    ).encode("ascii")
    _add_object(3, page_dict)
    stream_body = content_stream.encode("latin-1", "replace")
    content_stream_obj = b"<< /Length %d >>\nstream\n" % len(stream_body)
    content_stream_obj += stream_body + b"\nendstream"
    _add_object(4, content_stream_obj)
    _add_object(5, f"<< /Type /Font /Subtype /Type1 /BaseFont /{style.pdf_font_family} >>".encode("ascii"))
    _add_object(6, f"<< /Type /Font /Subtype /Type1 /BaseFont /{style.pdf_bold_font} >>".encode("ascii"))
    _add_object(7, f"<< /Type /Font /Subtype /Type1 /BaseFont /{style.pdf_italic_font} >>".encode("ascii"))
    _add_object(8, f"<< /Type /Font /Subtype /Type1 /BaseFont /{style.pdf_bold_italic_font} >>".encode("ascii"))
    _add_object(9, f"<< /Type /Font /Subtype /Type1 /BaseFont /{style.pdf_monospace_font} >>".encode("ascii"))

    xref_offset = buffer.tell()
    obj_count = 10
    buffer.write(f"xref\n0 {obj_count}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets:
        buffer.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buffer.write(f"trailer\n<< /Size {obj_count} /Root 1 0 R >>\nstartxref\n".encode("ascii"))
    buffer.write(f"{xref_offset}\n".encode("ascii"))
    buffer.write(b"%%EOF")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        handle.write(buffer.getvalue())


def _render_inline(
    tokens: Iterable,
    *,
    font_size: int = 12,
    emphasize_heading: bool = False,
) -> list[TextSegment]:
    segments: list[TextSegment] = []
    bold_depth = 0
    italic_depth = 0
    for token in tokens:
        ttype = token.type
        if ttype == "text":
            content = token.content
            if emphasize_heading:
                content = content.strip()
            segments.append(
                TextSegment(
                    text=content,
                    bold=bold_depth > 0 or emphasize_heading,
                    italic=italic_depth > 0,
                    size=font_size,
                )
            )
        elif ttype == "code_inline":
            segments.append(TextSegment(token.content, monospace=True, size=font_size))
        elif ttype in ("softbreak", "hardbreak"):
            segments.append(TextSegment("\n", size=font_size))
        elif ttype == "strong_open":
            bold_depth += 1
        elif ttype == "strong_close":
            bold_depth = max(0, bold_depth - 1)
        elif ttype == "em_open":
            italic_depth += 1
        elif ttype == "em_close":
            italic_depth = max(0, italic_depth - 1)
        elif ttype == "link_open":
            continue
        elif ttype == "link_close":
            continue
        elif ttype == "html_inline":
            segments.append(TextSegment(token.content, size=font_size))
        elif ttype == "image":
            alt = token.attrGet("alt") or ""
            if alt:
                segments.append(TextSegment(f"[{alt}]", size=font_size))
    return segments


def _escape_pdf_text(value: str) -> str:
    normalized = _normalize_text(value)
    safe = normalized.encode("latin-1", "ignore").decode("latin-1")
    return (
        safe.replace("\\", r"\\\\")
        .replace("(", r"\(")
        .replace(")", r"\)")
        .replace("\r", " ")
    )


def _normalize_text(value: str) -> str:
    replacements = {
        "•": "-",
        "–": "-",
        "—": "-",
    }
    for target, repl in replacements.items():
        value = value.replace(target, repl)
    return unicodedata.normalize("NFKD", value)
