from __future__ import annotations

import csv
import math
import statistics
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)


CSV_PATH = Path(r"C:\Users\buddy\OneDrive\Desktop\latency_samples-normal_piipeline_llm mode.csv")
OUTPUT_PATH = Path("output/pdf/latency_report_pipeline_llm_baseline.pdf")


METRIC_FIELDS = [
    "end_of_speech_detection_ms",
    "stt_ms",
    "llm_ttft_ms",
    "llm_total_ms",
    "tool_round_trip_ms",
    "tts_ttf_audio_ms",
    "tts_total_ms",
    "delivery_overhead_ms",
    "total_to_first_audio_ms",
]


def safe_float(row: dict[str, str], key: str) -> float | None:
    value = (row.get(key) or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def seconds(value_ms: float | None) -> str:
    if value_ms is None:
        return "-"
    return f"{value_ms / 1000:.2f}s"


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def metric_summary(rows: list[dict[str, str]], key: str) -> tuple[float | None, float | None]:
    values = [safe_float(row, key) for row in rows]
    clean = [value for value in values if value is not None]
    if not clean:
        return None, None
    return statistics.median(clean), percentile(clean, 0.95)


def truncate(text: str, limit: int = 44) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def page_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawRightString(10.5 * inch, 0.35 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build_report() -> None:
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"CSV not found: {CSV_PATH}")

    with CSV_PATH.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=landscape(letter),
        rightMargin=0.45 * inch,
        leftMargin=0.45 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.55 * inch,
        title="Latency Report",
        author="Mira Voice Agent",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=26,
        leading=30,
        textColor=colors.HexColor("#0F3A68"),
        alignment=TA_LEFT,
        spaceAfter=12,
    )
    h2 = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#1368D8"),
        spaceBefore=12,
        spaceAfter=7,
    )
    body = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=6,
    )
    small = ParagraphStyle(
        "Small",
        parent=body,
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#475569"),
    )
    cell = ParagraphStyle(
        "Cell",
        parent=small,
        alignment=TA_LEFT,
        wordWrap="CJK",
    )
    header_cell = ParagraphStyle(
        "HeaderCell",
        parent=small,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
        textColor=colors.white,
    )

    story = []
    story.append(Paragraph("Latency Report", title_style))
    story.append(
        Paragraph(
            "Pipeline: raw STT -> LLM -> TTS | Mode: LLM | Streaming: off | "
            f"Sample size: {len(rows)} interactions",
            body,
        )
    )
    story.append(
        Paragraph(
            "This baseline measures the current non-streaming pipeline. Audio is recorded in the browser, "
            "sent to the backend for transcription, passed to the LLM, converted to speech, and then sent "
            "back to the browser for playback.",
            body,
        )
    )

    story.append(Paragraph("Executive findings", h2))
    findings = [
        "The largest user-visible delay is not the local tool chain. The local tool functions are almost free because they run in-process over local sample data.",
        "Tool turns in LLM mode pay for two model steps: one model pass to decide the tool call, and a second model pass to turn the tool result into a spoken answer. The dashboard currently reports those together as model response time.",
        "The biggest raw bottleneck in this sample is TTS completion. The backend receives the first audio chunk earlier, but the current app waits for the full MP3 before the browser can play it.",
        "End-of-turn detection is stable at about 0.68s per turn. That is expected for a conservative VAD/turn-detection setting, but it is still meaningful latency before STT even starts.",
        "Because this sample has only seven interactions, p95 should be treated as a directional worst-case signal rather than a statistically stable production number.",
    ]
    for item in findings:
        story.append(Paragraph(f"- {item}", body))

    story.append(Paragraph("Summary metrics", h2))
    summary_rows = [
        [
            Paragraph("Metric", header_cell),
            Paragraph("What it means", header_cell),
            Paragraph("Median", header_cell),
            Paragraph("P95", header_cell),
        ]
    ]
    metric_labels = [
        ("end_of_speech_detection_ms", "End-of-turn detection", "Delay after the user stops speaking before the turn is accepted."),
        ("stt_ms", "Speech-to-text", "Time to transcribe the captured user audio."),
        ("llm_ttft_ms", "LLM first token", "Time until the model starts producing output."),
        ("llm_total_ms", "LLM total", "Total model time. On tool turns this combines planning plus post-tool response."),
        ("tool_round_trip_ms", "Local tool chain", "Time spent executing local backend tools and mock lookups."),
        ("tts_ttf_audio_ms", "TTS first audio", "Time until the backend receives the first audio bytes from TTS."),
        ("tts_total_ms", "TTS complete", "Time until the full speech audio is generated."),
        ("delivery_overhead_ms", "Browser delivery", "Client-side delivery and playback-start overhead after backend processing."),
        ("total_to_first_audio_ms", "End-to-heard", "Total delay from user finishing speaking to the browser starting the response."),
    ]
    for key, label, meaning in metric_labels:
        median, p95 = metric_summary(rows, key)
        summary_rows.append(
            [
                Paragraph(label, cell),
                Paragraph(meaning, cell),
                Paragraph(seconds(median), cell),
                Paragraph(seconds(p95), cell),
            ]
        )

    summary_table = Table(summary_rows, colWidths=[1.65 * inch, 5.4 * inch, 1.1 * inch, 1.1 * inch])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1976E9")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D7E3F4")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8FBFF")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(summary_table)

    story.append(PageBreak())
    story.append(Paragraph("Cleaned interaction table", h2))
    story.append(
        Paragraph(
            "All timings below are shown in seconds. A dash means that stage did not apply to the turn.",
            body,
        )
    )
    turn_rows = [
        [
            Paragraph("#", header_cell),
            Paragraph("User said", header_cell),
            Paragraph("Tool?", header_cell),
            Paragraph("End turn", header_cell),
            Paragraph("STT", header_cell),
            Paragraph("LLM", header_cell),
            Paragraph("Tool chain", header_cell),
            Paragraph("TTS first", header_cell),
            Paragraph("TTS full", header_cell),
            Paragraph("End-to-heard", header_cell),
        ]
    ]
    for idx, row in enumerate(rows, start=1):
        turn_rows.append(
            [
                Paragraph(str(idx), cell),
                Paragraph(truncate(row.get("transcript", "")), cell),
                Paragraph("Yes" if row.get("has_tool_call") == "True" else "No", cell),
                Paragraph(seconds(safe_float(row, "end_of_speech_detection_ms")), cell),
                Paragraph(seconds(safe_float(row, "stt_ms")), cell),
                Paragraph(seconds(safe_float(row, "llm_total_ms")), cell),
                Paragraph(seconds(safe_float(row, "tool_round_trip_ms")), cell),
                Paragraph(seconds(safe_float(row, "tts_ttf_audio_ms")), cell),
                Paragraph(seconds(safe_float(row, "tts_total_ms")), cell),
                Paragraph(seconds(safe_float(row, "total_to_first_audio_ms")), cell),
            ]
        )

    turn_table = Table(
        turn_rows,
        colWidths=[
            0.35 * inch,
            2.35 * inch,
            0.55 * inch,
            0.72 * inch,
            0.72 * inch,
            0.72 * inch,
            0.85 * inch,
            0.8 * inch,
            0.8 * inch,
            0.95 * inch,
        ],
        repeatRows=1,
    )
    turn_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F3A68")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D7E3F4")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5FAFF")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(turn_table)

    story.append(Paragraph("How to read the baseline", h2))
    story.append(
        Paragraph(
            "In this LLM-mode baseline, tool execution itself is not the latency problem. The model response "
            "column includes every LLM pass required by the turn, so tool turns include both tool planning and "
            "post-tool verbalization. The TTS columns reveal the current architectural limitation: the backend "
            "can see first audio earlier than the browser, but playback still waits for complete synthesis. The "
            "next optimization should therefore stream audio to the browser as soon as usable TTS chunks arrive.",
            body,
        )
    )

    doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)


if __name__ == "__main__":
    build_report()
