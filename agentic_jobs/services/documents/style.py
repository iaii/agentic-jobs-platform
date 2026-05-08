from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = Path("config/document_formatting.yaml")
POINTS_PER_INCH = 72.0
LETTER_WIDTH = 8.5 * POINTS_PER_INCH
LETTER_HEIGHT = 11 * POINTS_PER_INCH


@dataclass(slots=True)
class DocumentStyle:
    pdf_font_family: str
    pdf_bold_font: str
    pdf_italic_font: str
    pdf_bold_italic_font: str
    pdf_monospace_font: str
    docx_font_name: str
    docx_monospace_font: str
    font_size: float
    line_spacing: float
    margin_top_in: float
    margin_bottom_in: float
    margin_left_in: float
    margin_right_in: float
    bullet_symbol: str
    page_width: float = LETTER_WIDTH
    page_height: float = LETTER_HEIGHT
    char_width_factor: float = 0.52
    monospace_width_factor: float = 0.6
    downloads_path: Path | None = None

    @property
    def margin_left_pt(self) -> float:
        return self.margin_left_in * POINTS_PER_INCH

    @property
    def margin_right_pt(self) -> float:
        return self.margin_right_in * POINTS_PER_INCH

    @property
    def margin_top_pt(self) -> float:
        return self.margin_top_in * POINTS_PER_INCH

    @property
    def margin_bottom_pt(self) -> float:
        return self.margin_bottom_in * POINTS_PER_INCH

    @property
    def content_width(self) -> float:
        return self.page_width - self.margin_left_pt - self.margin_right_pt

    @property
    def content_height(self) -> float:
        return self.page_height - self.margin_top_pt - self.margin_bottom_pt

    @property
    def line_height(self) -> float:
        return self.font_size * self.line_spacing

    @property
    def downloads_dir(self) -> Path:
        if self.downloads_path:
            return self.downloads_path
        return Path.home() / "Downloads"


DEFAULTS: dict[str, Any] = {
    "font": {
        "pdf_family": "Times-Roman",
        "pdf_bold": "Times-Bold",
        "pdf_italic": "Times-Italic",
        "pdf_bold_italic": "Times-BoldItalic",
        "pdf_monospace": "Courier",
        "docx_name": "Times New Roman",
        "docx_monospace": "Courier New",
        "size": 12,
        "line_spacing": 1.25,
    },
    "margins": {
        "top_inches": 1.0,
        "bottom_inches": 1.0,
        "left_inches": 0.8,
        "right_inches": 0.8,
    },
    "bullets": {"symbol": "-"},
    "downloads_path": str(Path.home() / "Downloads"),
    "char_width_factor": 0.52,
    "monospace_width_factor": 0.6,
}


def _merge_dict(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            target[key] = _merge_dict(dict(target[key]), value)
        else:
            target[key] = value
    return target


def _load_raw_config() -> dict[str, Any]:
    config = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
            config = _merge_dict(config, loaded)
        except Exception:
            pass
    return config


def _normalize_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value).expanduser().resolve()


def _build_style(data: dict[str, Any]) -> DocumentStyle:
    font = data.get("font", {})
    margins = data.get("margins", {})
    bullets = data.get("bullets", {})
    return DocumentStyle(
        pdf_font_family=font.get("pdf_family", DEFAULTS["font"]["pdf_family"]),
        pdf_bold_font=font.get("pdf_bold", DEFAULTS["font"]["pdf_bold"]),
        pdf_italic_font=font.get("pdf_italic", DEFAULTS["font"]["pdf_italic"]),
        pdf_bold_italic_font=font.get("pdf_bold_italic", DEFAULTS["font"]["pdf_bold_italic"]),
        pdf_monospace_font=font.get("pdf_monospace", DEFAULTS["font"]["pdf_monospace"]),
        docx_font_name=font.get("docx_name", DEFAULTS["font"]["docx_name"]),
        docx_monospace_font=font.get("docx_monospace", DEFAULTS["font"]["docx_monospace"]),
        font_size=float(font.get("size", DEFAULTS["font"]["size"])),
        line_spacing=float(font.get("line_spacing", DEFAULTS["font"]["line_spacing"])),
        margin_top_in=float(margins.get("top_inches", DEFAULTS["margins"]["top_inches"])),
        margin_bottom_in=float(margins.get("bottom_inches", DEFAULTS["margins"]["bottom_inches"])),
        margin_left_in=float(margins.get("left_inches", DEFAULTS["margins"]["left_inches"])),
        margin_right_in=float(margins.get("right_inches", DEFAULTS["margins"]["right_inches"])),
        bullet_symbol=str(bullets.get("symbol", DEFAULTS["bullets"]["symbol"])),
        char_width_factor=float(data.get("char_width_factor", DEFAULTS["char_width_factor"])),
        monospace_width_factor=float(data.get("monospace_width_factor", DEFAULTS["monospace_width_factor"])),
        downloads_path=_normalize_path(data.get("downloads_path")),
    )


@lru_cache(maxsize=1)
def get_document_style() -> DocumentStyle:
    return _build_style(_load_raw_config())
