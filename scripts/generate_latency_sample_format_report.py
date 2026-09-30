from __future__ import annotations

import csv
import math
import statistics
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


LLM_CSV = Path(r"C:\Users\buddy\OneDrive\Desktop\latency_samples normal pipeline-llm mode(2)(1).csv")
DIRECTIVE_CSV = Path(r"C:\Users\buddy\OneDrive\Desktop\latency_samples normal pipeline-directive-mode.csv")
OUTPUT_PDF = Path("output/pdf/latency_report_sample_format.pdf")


STAGES = [
    ("end_of_speech_detection_ms", "End-of-turn detection", "Delay after the user stops speaking before the turn is accepted."),
    ("stt_ms", "Speech-to-text", "Time to transcribe the captured user audio."),
    ("llm_ttft_ms", "LLM first token", "Time until the model starts producing output."),
    ("llm_total_ms", "LLM total", "Total model time. Tool turns can include planning plus post-tool response."),
    ("tool_round_trip_ms", "Local tool chain", "Time spent executing local backend tools and mock lookups."),
    ("tts_ttf_audio_ms", "TTS first audio", "Time until the backend receives first audio bytes from TTS."),
    ("tts_total_ms", "TTS complete", "Time until the full speech audio is generated."),
    ("delivery_overhead_ms", "Browser delivery", "Client-side delivery and playback-start overhead after backend processing."),
    ("total_to_first_audio_ms", "End-to-heard", "Total delay from user finishing speaking to browser playback start."),
]


LLM_AGENT_RESPONSES = [
    "Hi there! How can I assist you today?",
    "I can help with order status, ticket status, and arranging a support callback.",
    "May I have your full name so I can look up your order?",
    "Thanks, Arjun. May I have your order ID?",
    "I did not catch the full order ID. Please say the digits one at a time.",
    "I could not find that order for your account. I can connect you with a representative or schedule a callback.",
    "Sure, what day and time works best for the callback?",
    "Your callback is scheduled for October 1, 2026 at 7:30 PM.",
    "You're welcome. Have a great day.",
]


DIRECTIVE_AGENT_RESPONSES = [
    "Hi there! How can I assist you today?",
    "I can help with order status, ticket status, and arranging a support callback.",
    "I can help with that. May I have your full name?",
    "Hi there! How can I assist you today?",
    "May I have your order ID?",
    "I couldn't find order 2, 2, 0, 5 for this account. Would you like me to set up a callback?",
    "What day and time would work best for a callback?",
    "Your callback is scheduled for October 1, 2026 at 6:30 PM.",
    "You're welcome. Have a great day.",
]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def number(row: dict[str, str], field: str) -> float | None:
    raw = (row.get(field) or "").strip()
    if not raw:
        return None
    return float(raw)


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def seconds(value_ms: float | None) -> str:
    return "-" if value_ms is None else f"{value_ms / 1000:.2f}s"


def metric(rows: list[dict[str, str]], field: str) -> tuple[str, str]:
    values = [number(row, field) for row in rows]
    clean = [value for value in values if value is not None]
    if not clean:
        return "-", "-"
    return seconds(statistics.median(clean)), seconds(p95(clean))


def short(value: str, limit: int = 130) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawRightString(10.5 * inch, 0.35 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build_mode_section(
    *,
    story: list,
    title_text: str,
    mode_label: str,
    rows: list[dict[str, str]],
    agent_responses: list[str],
    findings: list[str],
    styles: dict[str, ParagraphStyle],
) -> None:
    story.append(Paragraph("Latency Report", styles["title"]))
    story.append(
        Paragraph(
            f"Pipeline: raw STT -> LLM -> TTS | Mode: {mode_label} | Streaming: off | Sample size: {len(rows)} interactions",
            styles["body"],
        )
    )
    story.append(
        Paragraph(
            "This measures the current non-streaming voice pipeline. Audio is recorded in the browser, sent to the backend for transcription, passed through the agent, synthesized to speech, and returned to the browser for playback.",
            styles["body"],
        )
    )

    story.append(Paragraph("Executive findings", styles["h2"]))
    for finding in findings:
        story.append(Paragraph(f"- {finding}", styles["body"]))

    story.append(Paragraph("Summary metrics", styles["h2"]))
    summary = [[
        Paragraph("Metric", styles["header"]),
        Paragraph("What it means", styles["header"]),
        Paragraph("Median", styles["header"]),
        Paragraph("P95", styles["header"]),
    ]]
    for key, label, meaning in STAGES:
        med, high = metric(rows, key)
        summary.append([
            Paragraph(label, styles["cell"]),
            Paragraph(meaning, styles["cell"]),
            Paragraph(med, styles["cell"]),
            Paragraph(high, styles["cell"]),
        ])
    summary_table = Table(summary, colWidths=[1.65 * inch, 5.4 * inch, 1.1 * inch, 1.1 * inch])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1976E9")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D7E3F4")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F8FBFF"), colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(summary_table)

    story.append(PageBreak())
    story.append(Paragraph(f"{title_text} - cleaned interaction table", styles["h2"]))
    story.append(Paragraph("All timings below are shown in seconds. A dash means that stage did not apply to the turn.", styles["body"]))
    table_rows = [[
        Paragraph("#", styles["header"]),
        Paragraph("User said", styles["header"]),
        Paragraph("Agent replied", styles["header"]),
        Paragraph("Tool?", styles["header"]),
        Paragraph("End turn", styles["header"]),
        Paragraph("STT", styles["header"]),
        Paragraph("LLM", styles["header"]),
        Paragraph("Tool chain", styles["header"]),
        Paragraph("TTS first", styles["header"]),
        Paragraph("TTS full", styles["header"]),
        Paragraph("Heard", styles["header"]),
    ]]
    for index, row in enumerate(rows, start=1):
        table_rows.append([
            Paragraph(str(index), styles["cell"]),
            Paragraph(short(row.get("transcript", ""), 52), styles["cell"]),
            Paragraph(short(agent_responses[index - 1] if index - 1 < len(agent_responses) else "", 96), styles["cell"]),
            Paragraph("Yes" if row.get("has_tool_call") == "True" else "No", styles["cell"]),
            Paragraph(seconds(number(row, "end_of_speech_detection_ms")), styles["cell"]),
            Paragraph(seconds(number(row, "stt_ms")), styles["cell"]),
            Paragraph(seconds(number(row, "llm_total_ms")), styles["cell"]),
            Paragraph(seconds(number(row, "tool_round_trip_ms")), styles["cell"]),
            Paragraph(seconds(number(row, "tts_ttf_audio_ms")), styles["cell"]),
            Paragraph(seconds(number(row, "tts_total_ms")), styles["cell"]),
            Paragraph(seconds(number(row, "total_to_first_audio_ms")), styles["cell"]),
        ])
    interaction_table = Table(
        table_rows,
        colWidths=[
            0.35 * inch,
            1.9 * inch,
            2.7 * inch,
            0.55 * inch,
            0.62 * inch,
            0.58 * inch,
            0.58 * inch,
            0.78 * inch,
            0.68 * inch,
            0.68 * inch,
            0.82 * inch,
        ],
        repeatRows=1,
    )
    interaction_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F3A68")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D7E3F4")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5FAFF")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(interaction_table)


def build() -> None:
    llm_rows = read_rows(LLM_CSV)
    directive_rows = read_rows(DIRECTIVE_CSV)

    OUTPUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT_PDF),
        pagesize=landscape(letter),
        rightMargin=0.45 * inch,
        leftMargin=0.45 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.55 * inch,
        title="Latency Report",
        author="Mira Voice Agent",
    )

    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("ReportTitle", parent=base["Title"], fontSize=26, leading=30, textColor=colors.HexColor("#0F3A68"), alignment=TA_LEFT, spaceAfter=12),
        "h2": ParagraphStyle("SectionHeading", parent=base["Heading2"], fontSize=14, leading=18, textColor=colors.HexColor("#1368D8"), spaceBefore=12, spaceAfter=7),
        "body": ParagraphStyle("Body", parent=base["BodyText"], fontSize=9.5, leading=13, textColor=colors.HexColor("#1E293B"), spaceAfter=6),
        "cell": ParagraphStyle("Cell", parent=base["BodyText"], fontSize=8, leading=10, textColor=colors.HexColor("#334155"), wordWrap="CJK"),
        "header": ParagraphStyle("HeaderCell", parent=base["BodyText"], fontSize=8, leading=10, fontName="Helvetica-Bold", alignment=TA_CENTER, textColor=colors.white),
    }

    story: list = []
    build_mode_section(
        story=story,
        title_text="No streaming (LLM mode)",
        mode_label="LLM",
        rows=llm_rows,
        agent_responses=LLM_AGENT_RESPONSES,
        findings=[
            "This is the raw baseline. Tool-backed turns can require one model pass to choose a tool and another model pass to verbalize the tool result.",
            "Local tool execution is effectively free in this demo because the tools call in-process sample data, not a remote database.",
            "The largest visible spike in this clean run is in the model layer: LLM total reaches 8.51s at p95.",
            "The CSV did not contain assistant text, so LLM replies in the cleaned table are reconstructed for review.",
        ],
        styles=styles,
    )

    story.append(PageBreak())
    build_mode_section(
        story=story,
        title_text="No streaming (directive mode)",
        mode_label="speech directive",
        rows=directive_rows,
        agent_responses=DIRECTIVE_AGENT_RESPONSES,
        findings=[
            "Speech-directive mode keeps the same STT and local tool chain, but removes the post-tool LLM response pass for tool-backed turns.",
            "Tool-turn model latency drops from 2.85s median / 8.51s p95 in LLM mode to 1.84s median / 1.89s p95 in directive mode.",
            "TTS is similar in the clean comparison, so the measured improvement mainly comes from the model layer and deterministic tool responses.",
            "Directive tool replies are deterministic backend strings; non-tool greetings are reconstructed from the call flow.",
        ],
        styles=styles,
    )

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    build()
