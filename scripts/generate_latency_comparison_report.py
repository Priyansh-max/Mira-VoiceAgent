from __future__ import annotations

import csv
import math
import statistics
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


LLM_CSV = Path(r"C:\Users\buddy\OneDrive\Desktop\latency_samples normal pipeline-llm mode(2)(1).csv")
DIRECTIVE_CSV = Path(r"C:\Users\buddy\OneDrive\Desktop\latency_samples normal pipeline-directive-mode.csv")
OUTPUT_PDF = Path("output/pdf/latency_report_pipeline_comparison.pdf")


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def number(row: dict[str, str], field: str) -> float | None:
    value = (row.get(field) or "").strip()
    if not value:
        return None
    return float(value)


def sec(ms: float | None) -> str:
    return "-" if ms is None else f"{ms / 1000:.2f}s"


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def metric(rows: list[dict[str, str]], field: str) -> tuple[str, str]:
    values = [number(row, field) for row in rows]
    clean = [value for value in values if value is not None]
    return sec(median(clean)), sec(p95(clean))


def summarize(rows: list[dict[str, str]]) -> dict[str, str]:
    tool_rows = [row for row in rows if row.get("has_tool_call") == "True"]
    all_end_med, all_end_p95 = metric(rows, "total_to_first_audio_ms")
    tool_end_med, tool_end_p95 = metric(tool_rows, "total_to_first_audio_ms")
    tool_llm_med, tool_llm_p95 = metric(tool_rows, "llm_total_ms")
    tool_tts_med, tool_tts_p95 = metric(tool_rows, "tts_total_ms")
    vad_med, vad_p95 = metric(rows, "end_of_speech_detection_ms")
    stt_med, stt_p95 = metric(rows, "stt_ms")
    return {
        "turns": str(len(rows)),
        "tool_turns": str(len(tool_rows)),
        "end_med": all_end_med,
        "end_p95": all_end_p95,
        "tool_end_med": tool_end_med,
        "tool_end_p95": tool_end_p95,
        "tool_llm_med": tool_llm_med,
        "tool_llm_p95": tool_llm_p95,
        "tool_tts_med": tool_tts_med,
        "tool_tts_p95": tool_tts_p95,
        "vad": f"{vad_med} / {vad_p95}",
        "stt": f"{stt_med} / {stt_p95}",
    }


def build() -> None:
    llm_rows = load_rows(LLM_CSV)
    directive_rows = load_rows(DIRECTIVE_CSV)
    llm = summarize(llm_rows)
    directive = summarize(directive_rows)

    OUTPUT_PDF.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(OUTPUT_PDF),
        pagesize=letter,
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.4 * inch,
        bottomMargin=0.35 * inch,
        title="Latency Report",
        author="Mira Voice Agent",
    )

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "Title",
        parent=styles["Title"],
        fontSize=24,
        leading=28,
        textColor=colors.HexColor("#0F3A68"),
        alignment=TA_LEFT,
        spaceAfter=4,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontSize=11.5,
        leading=14,
        textColor=colors.HexColor("#1368D8"),
        spaceBefore=8,
        spaceAfter=4,
    )
    body = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontSize=8.8,
        leading=11,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=4,
    )
    small = ParagraphStyle(
        "Small",
        parent=body,
        fontSize=7.4,
        leading=9,
    )
    header = ParagraphStyle(
        "Header",
        parent=small,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    cell = ParagraphStyle("Cell", parent=small)

    story = [
        Paragraph("Latency Report", title),
        Paragraph(
            "Pipeline: raw STT -> LLM -> TTS | Transport: non-streaming | Comparison: clean LLM mode vs speech directive mode",
            body,
        ),
        Paragraph("Result", h2),
        Paragraph(
            "Directive mode reduced the median tool-turn end-to-heard latency from "
            f"{llm['tool_end_med']} to {directive['tool_end_med']}. The clearest cause is the model layer: "
            f"tool-turn LLM time dropped from {llm['tool_llm_med']} median / {llm['tool_llm_p95']} p95 to "
            f"{directive['tool_llm_med']} median / {directive['tool_llm_p95']} p95 because the post-tool response "
            "LLM pass is skipped.",
            body,
        ),
    ]

    table_data = [
        [
            Paragraph("Mode", header),
            Paragraph("Turns", header),
            Paragraph("Tool turns", header),
            Paragraph("All turns end-to-heard<br/>median / p95", header),
            Paragraph("Tool turns end-to-heard<br/>median / p95", header),
            Paragraph("Tool-turn LLM<br/>median / p95", header),
            Paragraph("Tool-turn TTS full<br/>median / p95", header),
        ],
        [
            Paragraph("LLM mode", cell),
            Paragraph(llm["turns"], cell),
            Paragraph(llm["tool_turns"], cell),
            Paragraph(f"{llm['end_med']} / {llm['end_p95']}", cell),
            Paragraph(f"{llm['tool_end_med']} / {llm['tool_end_p95']}", cell),
            Paragraph(f"{llm['tool_llm_med']} / {llm['tool_llm_p95']}", cell),
            Paragraph(f"{llm['tool_tts_med']} / {llm['tool_tts_p95']}", cell),
        ],
        [
            Paragraph("Directive mode", cell),
            Paragraph(directive["turns"], cell),
            Paragraph(directive["tool_turns"], cell),
            Paragraph(f"{directive['end_med']} / {directive['end_p95']}", cell),
            Paragraph(f"{directive['tool_end_med']} / {directive['tool_end_p95']}", cell),
            Paragraph(f"{directive['tool_llm_med']} / {directive['tool_llm_p95']}", cell),
            Paragraph(f"{directive['tool_tts_med']} / {directive['tool_tts_p95']}", cell),
        ],
    ]
    table = Table(
        table_data,
        colWidths=[0.95 * inch, 0.45 * inch, 0.65 * inch, 1.25 * inch, 1.25 * inch, 1.15 * inch, 1.15 * inch],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1368D8")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CFE0F5")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F9FF")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([table, Spacer(1, 0.08 * inch)])

    story.extend(
        [
            Paragraph("Findings", h2),
            Paragraph(
                "- End-of-turn detection did not explain the difference: both runs stayed around 0.68s median.",
                body,
            ),
            Paragraph(
                f"- STT was similar enough for this comparison: LLM {llm['stt']} vs directive {directive['stt']} median / p95.",
                body,
            ),
            Paragraph(
                "- Local tool execution was effectively zero in both modes because tools call local in-process functions over sample data.",
                body,
            ),
            Paragraph(
                "- TTS was not the main differentiator in the clean pass: tool-turn TTS full was "
                f"{llm['tool_tts_med']} vs {directive['tool_tts_med']} median.",
                body,
            ),
            Paragraph(
                "- Determinism improved in directive mode: instead of the model paraphrasing the tool outcome, the agent speaks the exact directive text returned by the backend.",
                body,
            ),
            Paragraph("Conclusion", h2),
            Paragraph(
                "The meaningful optimization is speech-directive mode for tool-backed turns. It keeps local tool latency unchanged, "
                "removes the second model generation step, and makes tool outcomes deterministic. The next optimization should target "
                "streaming STT/LLM/TTS so the browser can hear first audio before full synthesis completes.",
                body,
            ),
        ]
    )

    doc.build(story)


if __name__ == "__main__":
    build()
