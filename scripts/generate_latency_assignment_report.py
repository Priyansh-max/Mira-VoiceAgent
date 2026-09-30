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
OUTPUT_PDF = Path("output/pdf/latency_report.pdf")


STAGES = [
    ("end_of_speech_detection_ms", "End-of-speech detection", "VAD/turn detection delay after the user stops speaking."),
    ("stt_ms", "Speech-to-text", "Time to produce the transcript from captured audio."),
    ("llm_ttft_ms", "LLM first token", "Time until model output begins."),
    ("llm_total_ms", "LLM total response", "Total model time for the turn. Tool turns may include planning and post-tool response."),
    ("tool_round_trip_ms", "Local tool chain", "Local tool execution and mock-data lookup time."),
    ("tts_ttf_audio_ms", "TTS first audio", "Time until backend receives first audio bytes from TTS."),
    ("tts_total_ms", "TTS complete synthesis", "Time until the full response audio is generated."),
    ("delivery_overhead_ms", "Browser delivery", "Browser-side response handling and playback-start overhead."),
    ("total_to_first_audio_ms", "Total end-to-heard", "Total time from user finishing speech to the browser starting response playback."),
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


def as_float(row: dict[str, str], key: str) -> float | None:
    raw = (row.get(key) or "").strip()
    if not raw:
        return None
    return float(raw)


def nearest_rank_p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def seconds(value_ms: float | None) -> str:
    return "-" if value_ms is None else f"{value_ms / 1000:.2f}s"


def stats(rows: list[dict[str, str]], key: str) -> tuple[str, str]:
    values = [as_float(row, key) for row in rows]
    clean = [value for value in values if value is not None]
    if not clean:
        return "-", "-"
    return seconds(statistics.median(clean)), seconds(nearest_rank_p95(clean))


def mode_summary(rows: list[dict[str, str]]) -> dict[str, str]:
    tool_rows = [row for row in rows if row.get("has_tool_call") == "True"]
    end_med, end_p95 = stats(rows, "total_to_first_audio_ms")
    tool_end_med, tool_end_p95 = stats(tool_rows, "total_to_first_audio_ms")
    llm_tool_med, llm_tool_p95 = stats(tool_rows, "llm_total_ms")
    tts_tool_med, tts_tool_p95 = stats(tool_rows, "tts_total_ms")
    return {
        "turns": str(len(rows)),
        "tool_turns": str(len(tool_rows)),
        "end": f"{end_med} / {end_p95}",
        "tool_end": f"{tool_end_med} / {tool_end_p95}",
        "tool_llm": f"{llm_tool_med} / {llm_tool_p95}",
        "tool_tts": f"{tts_tool_med} / {tts_tool_p95}",
    }


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawRightString(10.5 * inch, 0.35 * inch, f"Page {doc.page}")
    canvas.restoreState()


def truncate(value: str, limit: int = 140) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def build() -> None:
    llm_rows = read_rows(LLM_CSV)
    directive_rows = read_rows(DIRECTIVE_CSV)
    llm_summary = mode_summary(llm_rows)
    directive_summary = mode_summary(directive_rows)

    OUTPUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT_PDF),
        pagesize=landscape(letter),
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.55 * inch,
        title="Latency Report",
        author="Mira Voice Agent",
    )

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "Title",
        parent=styles["Title"],
        fontSize=26,
        leading=30,
        textColor=colors.HexColor("#0F3A68"),
        alignment=TA_LEFT,
        spaceAfter=8,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontSize=14,
        leading=17,
        textColor=colors.HexColor("#1368D8"),
        spaceBefore=10,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=5,
    )
    small = ParagraphStyle(
        "Small",
        parent=body,
        fontSize=7.8,
        leading=9.5,
    )
    cell = ParagraphStyle(
        "Cell",
        parent=small,
        alignment=TA_LEFT,
        wordWrap="CJK",
    )
    header = ParagraphStyle(
        "Header",
        parent=small,
        fontName="Helvetica-Bold",
        textColor=colors.white,
        alignment=TA_CENTER,
    )

    story: list = [
        Paragraph("Latency Report", title),
        Paragraph(
            "Goal: measure the raw STT -> LLM -> TTS voice pipeline, identify where latency comes from, then compare a deterministic speech-directive optimization.",
            body,
        ),
        Paragraph("Run overview", h2),
    ]

    overview = [
        [
            Paragraph("Run", header),
            Paragraph("Streaming", header),
            Paragraph("Turns", header),
            Paragraph("Tool turns", header),
            Paragraph("End-to-heard<br/>median / p95", header),
            Paragraph("Tool end-to-heard<br/>median / p95", header),
            Paragraph("Tool LLM total<br/>median / p95", header),
            Paragraph("Tool TTS complete<br/>median / p95", header),
        ],
        [
            Paragraph("No streaming - LLM mode", cell),
            Paragraph("Off", cell),
            Paragraph(llm_summary["turns"], cell),
            Paragraph(llm_summary["tool_turns"], cell),
            Paragraph(llm_summary["end"], cell),
            Paragraph(llm_summary["tool_end"], cell),
            Paragraph(llm_summary["tool_llm"], cell),
            Paragraph(llm_summary["tool_tts"], cell),
        ],
        [
            Paragraph("No streaming - Directive mode", cell),
            Paragraph("Off", cell),
            Paragraph(directive_summary["turns"], cell),
            Paragraph(directive_summary["tool_turns"], cell),
            Paragraph(directive_summary["end"], cell),
            Paragraph(directive_summary["tool_end"], cell),
            Paragraph(directive_summary["tool_llm"], cell),
            Paragraph(directive_summary["tool_tts"], cell),
        ],
        [
            Paragraph("Streaming - Directive mode", cell),
            Paragraph("Pending", cell),
            Paragraph("-", cell),
            Paragraph("-", cell),
            Paragraph("-", cell),
            Paragraph("-", cell),
            Paragraph("-", cell),
            Paragraph("-", cell),
        ],
    ]
    overview_table = Table(
        overview,
        colWidths=[
            1.7 * inch,
            0.8 * inch,
            0.55 * inch,
            0.75 * inch,
            1.25 * inch,
            1.35 * inch,
            1.25 * inch,
            1.35 * inch,
        ],
    )
    overview_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1976E9")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D7E3F4")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5FAFF")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([overview_table, Spacer(1, 0.08 * inch)])

    story.extend(
        [
            Paragraph("Key findings", h2),
            Paragraph(
                "- End-of-turn detection was stable in both non-streaming runs at about 0.68s median, so it did not explain the mode difference.",
                body,
            ),
            Paragraph(
                "- Local tool execution was effectively zero because tools run as local backend functions over sample data.",
                body,
            ),
            Paragraph(
                "- In the clean comparison, TTS was not the main differentiator: tool-turn complete synthesis was 2.14s in LLM mode vs 1.99s in directive mode.",
                body,
            ),
            Paragraph(
                "- The model layer was the main optimized step: directive mode removed the post-tool LLM response pass, reducing tool-turn LLM total from 2.85s median / 8.51s p95 to 1.84s median / 1.89s p95.",
                body,
            ),
            Paragraph(
                "- Determinism improved: in LLM mode the model can paraphrase or choose a different support phrasing; in directive mode tool-backed turns speak the backend directive exactly.",
                body,
            ),
        ]
    )

    for title_text, rows, notes, agent_responses, response_note in [
        (
            "No streaming (LLM mode)",
            llm_rows,
            [
                "Baseline raw pipeline. Tool-backed turns can require one model call to choose the tool and a second model call to verbalize the tool result.",
                "This run showed one high-latency callback turn, visible in the LLM p95.",
            ],
            LLM_AGENT_RESPONSES,
            "Agent replies are reconstructed from the observed conversation flow because the CSV records user transcripts and latency fields but not assistant text.",
        ),
        (
            "No streaming (directive mode)",
            directive_rows,
            [
                "Optimization run. Tool-backed turns still use the LLM to choose tools, but final speech comes directly from the backend directive.",
                "This removed the post-tool model generation pass and made tool outcomes deterministic.",
            ],
            DIRECTIVE_AGENT_RESPONSES,
            "Tool-backed agent replies use deterministic backend directive strings; non-tool greetings are reconstructed from the call flow.",
        ),
    ]:
        story.append(PageBreak())
        story.append(Paragraph(title_text, title))
        for note in notes:
            story.append(Paragraph(f"- {note}", body))
        table_rows = [
            [
                Paragraph("Pipeline stage", header),
                Paragraph("What it measures", header),
                Paragraph("Median", header),
                Paragraph("P95", header),
            ]
        ]
        for key, label, meaning in STAGES:
            med, p95 = stats(rows, key)
            table_rows.append(
                [
                    Paragraph(label, cell),
                    Paragraph(meaning, cell),
                    Paragraph(med, cell),
                    Paragraph(p95, cell),
                ]
            )
        stage_table = Table(
            table_rows,
            colWidths=[1.8 * inch, 5.1 * inch, 1.1 * inch, 1.1 * inch],
        )
        stage_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F3A68")),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D7E3F4")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5FAFF")]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(stage_table)

        story.append(PageBreak())
        story.append(Paragraph(f"{title_text} - interaction transcript", title))
        story.append(Paragraph(response_note, body))
        interaction_rows = [
            [
                Paragraph("#", header),
                Paragraph("User said", header),
                Paragraph("Agent replied", header),
                Paragraph("Tool?", header),
                Paragraph("End-to-heard", header),
            ]
        ]
        for index, row in enumerate(rows, start=1):
            agent_text = agent_responses[index - 1] if index - 1 < len(agent_responses) else ""
            interaction_rows.append(
                [
                    Paragraph(str(index), cell),
                    Paragraph(truncate(row.get("transcript", ""), 95), cell),
                    Paragraph(truncate(agent_text, 170), cell),
                    Paragraph("Yes" if row.get("has_tool_call") == "True" else "No", cell),
                    Paragraph(seconds(as_float(row, "total_to_first_audio_ms")), cell),
                ]
            )

        interaction_table = Table(
            interaction_rows,
            colWidths=[0.35 * inch, 2.45 * inch, 4.85 * inch, 0.55 * inch, 0.95 * inch],
            repeatRows=1,
        )
        interaction_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F3A68")),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D7E3F4")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5FAFF")]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(interaction_table)

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    build()
