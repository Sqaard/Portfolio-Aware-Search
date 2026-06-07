"""SEC filing section extraction utilities.

The parser is intentionally conservative and dependency-free. It turns SEC HTML
into a line-oriented text representation, finds Item headings, then keeps the
longest section for each target Item to avoid table-of-contents matches.
"""

from __future__ import annotations

from dataclasses import dataclass
import html
import re


ITEM_HEADING_RE = re.compile(
    r"(?im)^\s*(?:part\s+[ivx]+\s+)?item\s+"
    r"([0-9]\.[0-9]{2}|[0-9]{1,2}[a-z]?)"
    r"\s*[\.\-:–—]?\s*([^\n]{0,180})$"
)


@dataclass(frozen=True)
class SecSection:
    section_id: str
    item_code: str
    title: str
    body: str
    start_char: int
    end_char: int
    ordinal: int

    @property
    def label(self) -> str:
        return f"Item {self.item_code} {self.title}".strip()


@dataclass(frozen=True)
class _Heading:
    item_code: str
    title: str
    start: int
    end: int
    ordinal: int


def html_to_sec_text(raw: str) -> str:
    """Convert SEC HTML/text into line-oriented text for section parsing."""

    text = re.sub(r"(?is)<(script|style|ix:header).*?</\1>", " ", raw)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(div|p|tr|td|th|table|section|article|h[1-6])>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _normalize_item_code(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().upper())


def _clean_title(value: str) -> str:
    title = re.sub(r"\s+", " ", value).strip(" .:-–—\t")
    title = re.sub(r"\s+\d+$", "", title).strip()
    return title[:160]


def _iter_headings(text: str) -> list[_Heading]:
    headings: list[_Heading] = []
    for match in ITEM_HEADING_RE.finditer(text):
        raw_line = match.group(0).strip()
        if len(raw_line) > 220:
            continue
        title = _clean_title(match.group(2))
        headings.append(
            _Heading(
                item_code=_normalize_item_code(match.group(1)),
                title=title,
                start=match.start(),
                end=match.end(),
                ordinal=len(headings),
            )
        )
    return headings


def _section_id_for_heading(form: str, item_code: str, title: str) -> str:
    form = form.upper()
    title_lower = title.lower()

    if form == "10-K":
        mapping = {
            "1": "item_1_business",
            "1A": "item_1a_risk_factors",
            "7": "item_7_mda",
            "7A": "item_7a_market_risk",
            "8": "item_8_financial_statements",
        }
        return mapping.get(item_code, "")

    if form == "10-Q":
        if item_code == "1" and "financial" in title_lower:
            return "part1_item_1_financial_statements"
        if item_code == "2":
            return "part1_item_2_mda"
        if item_code == "3":
            return "part1_item_3_market_risk"
        if item_code == "4":
            return "part1_item_4_controls"
        if item_code == "1" and "legal" in title_lower:
            return "part2_item_1_legal_proceedings"
        if item_code == "1A":
            return "part2_item_1a_risk_factors"
        return ""

    if form == "8-K" and re.fullmatch(r"\d\.\d{2}", item_code):
        mapping = {
            "1.01": "item_1_01_entry_into_material_definitive_agreement",
            "1.02": "item_1_02_termination_of_material_definitive_agreement",
            "2.01": "item_2_01_acquisition_or_disposition_of_assets",
            "2.02": "item_2_02_results_operations_financial_condition",
            "2.03": "item_2_03_direct_financial_obligation",
            "2.05": "item_2_05_exit_or_disposal_activities",
            "3.02": "item_3_02_unregistered_sales_of_equity",
            "5.02": "item_5_02_director_or_officer_changes",
            "5.03": "item_5_03_articles_bylaws_or_fiscal_year",
            "5.07": "item_5_07_shareholder_vote",
            "7.01": "item_7_01_regulation_fd_disclosure",
            "8.01": "item_8_01_other_events",
            "9.01": "item_9_01_financial_statements_exhibits",
        }
        return mapping.get(item_code, f"item_{item_code.replace('.', '_')}_current_report")

    return ""


def _target_title(form: str, section_id: str, item_code: str, title: str) -> str:
    if title:
        return title
    if section_id == "item_1a_risk_factors" or section_id == "part2_item_1a_risk_factors":
        return "Risk Factors"
    if section_id == "item_7_mda" or section_id == "part1_item_2_mda":
        return "Management Discussion and Analysis"
    if section_id == "item_8_financial_statements" or section_id == "part1_item_1_financial_statements":
        return "Financial Statements"
    if section_id == "item_7a_market_risk" or section_id == "part1_item_3_market_risk":
        return "Quantitative and Qualitative Disclosures About Market Risk"
    if form.upper() == "8-K":
        return f"8-K Item {item_code}"
    return f"Item {item_code}"


def extract_sec_sections(text: str, form: str, *, min_section_chars: int = 250) -> list[SecSection]:
    """Extract target SEC Item sections from full filing text.

    Duplicate headings are common because filings include a table of contents.
    For each normalized section id we keep the longest candidate segment, which
    usually corresponds to the actual section body rather than the TOC row.
    """

    headings = _iter_headings(text)
    if not headings:
        body = text.strip()
        return [
            SecSection(
                section_id="full_filing",
                item_code="FULL",
                title="Full Filing Text",
                body=body,
                start_char=0,
                end_char=len(text),
                ordinal=0,
            )
        ] if body else []

    best_by_section: dict[str, SecSection] = {}
    for index, heading in enumerate(headings):
        end = headings[index + 1].start if index + 1 < len(headings) else len(text)
        body = text[heading.end:end].strip()
        section_id = _section_id_for_heading(form, heading.item_code, heading.title)
        if not section_id:
            continue
        if len(body) < min_section_chars and form.upper() != "8-K":
            continue
        section = SecSection(
            section_id=section_id,
            item_code=heading.item_code,
            title=_target_title(form, section_id, heading.item_code, heading.title),
            body=f"Item {heading.item_code}. {_target_title(form, section_id, heading.item_code, heading.title)}\n{body}".strip(),
            start_char=heading.start,
            end_char=end,
            ordinal=heading.ordinal,
        )
        previous = best_by_section.get(section_id)
        if previous is None or len(section.body) > len(previous.body):
            best_by_section[section_id] = section

    sections = sorted(best_by_section.values(), key=lambda item: item.start_char)
    if sections:
        return sections

    body = text.strip()
    return [
        SecSection(
            section_id="full_filing",
            item_code="FULL",
            title="Full Filing Text",
            body=body,
            start_char=0,
            end_char=len(text),
            ordinal=0,
        )
    ] if body else []
