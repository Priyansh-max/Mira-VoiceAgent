from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.latency import LatencySample, LatencyStore


def sample(turn_id: str, mode: str, total_ms: float, *, has_tool_call: bool = False) -> LatencySample:
    return LatencySample(
        turn_id=turn_id,
        session_id="session-1",
        response_mode=mode,
        has_tool_call=has_tool_call,
        end_of_speech_detection_ms=500,
        stt_ms=120,
        llm_ttft_ms=220,
        llm_total_ms=400,
        tts_ttf_audio_ms=180,
        total_to_first_audio_ms=total_ms,
    )


def sample_with_source(turn_id: str, source: str) -> LatencySample:
    return LatencySample(
        turn_id=turn_id,
        session_id="session-1",
        response_mode="speech_directive",
        measurement_source=source,
        end_of_speech_detection_ms=500,
        total_to_first_audio_ms=700,
    )


class LatencyStoreTest(unittest.TestCase):
    def test_summarizes_median_and_nearest_rank_p95_by_mode(self) -> None:
        store = LatencyStore()
        for index, value in enumerate((100, 200, 300, 400)):
            store.add(sample(f"llm-{index}", "llm", value))
        for index, value in enumerate((50, 100, 150)):
            store.add(sample(f"directive-{index}", "speech_directive", value))

        summary = store.summary()
        llm = summary["by_mode"]["llm"]
        directive = summary["by_mode"]["speech_directive"]

        self.assertEqual(llm["count"], 4)
        self.assertEqual(llm["metrics"]["total_to_first_audio_ms"]["median_ms"], 250)
        self.assertEqual(llm["metrics"]["total_to_first_audio_ms"]["p95_ms"], 400)
        self.assertEqual(directive["count"], 3)
        self.assertEqual(directive["metrics"]["total_to_first_audio_ms"]["median_ms"], 100)

    def test_persists_samples_and_deduplicates_turn_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latency.jsonl"
            store = LatencyStore(log_path=path)
            self.assertTrue(store.add(sample("turn-1", "llm", 800)))
            self.assertFalse(store.add(sample("turn-1", "llm", 800)))

            restored = LatencyStore(log_path=path)
            self.assertEqual(len(restored.samples()), 1)
            self.assertEqual(restored.samples()[0].turn_id, "turn-1")

    def test_reports_tool_turns_separately(self) -> None:
        store = LatencyStore()
        store.add(sample("general", "llm", 100, has_tool_call=False))
        store.add(sample("tool", "llm", 900, has_tool_call=True))

        llm = store.summary()["by_mode"]["llm"]
        self.assertEqual(llm["count"], 2)
        self.assertEqual(llm["tool_count"], 1)
        self.assertEqual(llm["metrics"]["total_to_first_audio_ms"]["median_ms"], 500)
        self.assertEqual(llm["tool_metrics"]["total_to_first_audio_ms"]["median_ms"], 900)

    def test_accepts_pipeline_streaming_measurement_sources(self) -> None:
        store = LatencyStore()
        self.assertTrue(store.add(sample_with_source("streamed", "streamed_first_audio")))
        self.assertTrue(store.add(sample_with_source("buffered", "buffered_complete_audio")))


if __name__ == "__main__":
    unittest.main()
