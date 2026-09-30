from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from pathlib import Path
from statistics import median
from threading import Lock
from typing import Deque, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class LatencySample(BaseModel):
    turn_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    response_mode: Literal["llm", "speech_directive"]
    has_tool_call: bool = False
    transcript: Optional[str] = None
    measurement_source: Literal["output_energy", "first_audio_event"] = "first_audio_event"
    end_of_speech_detection_ms: float = Field(ge=0)
    stt_ms: Optional[float] = Field(default=None, ge=0)
    llm_ttft_ms: Optional[float] = Field(default=None, ge=0)
    llm_total_ms: Optional[float] = Field(default=None, ge=0)
    tts_ttf_audio_ms: Optional[float] = Field(default=None, ge=0)
    tts_total_ms: Optional[float] = Field(default=None, ge=0)
    backend_processing_ms: Optional[float] = Field(default=None, ge=0)
    backend_other_ms: Optional[float] = Field(default=None, ge=0)
    audio_encoding_ms: Optional[float] = Field(default=None, ge=0)
    client_pipeline_round_trip_ms: Optional[float] = Field(default=None, ge=0)
    transport_serialization_ms: Optional[float] = Field(default=None, ge=0)
    response_prepare_ms: Optional[float] = Field(default=None, ge=0)
    playback_start_ms: Optional[float] = Field(default=None, ge=0)
    delivery_overhead_ms: Optional[float] = Field(default=None, ge=0)
    audio_duration_ms: Optional[float] = Field(default=None, ge=0)
    voiced_ms: Optional[float] = Field(default=None, ge=0)
    voiced_ratio: Optional[float] = Field(default=None, ge=0, le=1)
    peak_rms: Optional[float] = Field(default=None, ge=0)
    vad_threshold: Optional[float] = Field(default=None, ge=0)
    total_to_first_audio_ms: float = Field(ge=0)
    tool_round_trip_ms: Optional[float] = Field(default=None, ge=0)
    recorded_at: float = Field(default_factory=time.time)


METRIC_FIELDS = (
    "end_of_speech_detection_ms",
    "stt_ms",
    "llm_ttft_ms",
    "llm_total_ms",
    "tts_ttf_audio_ms",
    "tts_total_ms",
    "backend_processing_ms",
    "backend_other_ms",
    "audio_encoding_ms",
    "client_pipeline_round_trip_ms",
    "transport_serialization_ms",
    "response_prepare_ms",
    "playback_start_ms",
    "delivery_overhead_ms",
    "total_to_first_audio_ms",
    "tool_round_trip_ms",
)


def _percentile(values: List[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[rank], 1)


class LatencyStore:
    def __init__(self, *, sample_limit: int = 1000, log_path: Optional[Path] = None) -> None:
        self._samples: Deque[LatencySample] = deque(maxlen=sample_limit)
        self._turn_ids: set[str] = set()
        self._lock = Lock()
        self._log_path = log_path
        self._load_existing()

    def _load_existing(self) -> None:
        if not self._log_path or not self._log_path.exists():
            return
        for line in self._log_path.read_text(encoding="utf-8").splitlines():
            try:
                sample = LatencySample.model_validate_json(line)
            except (ValueError, TypeError):
                continue
            self._samples.append(sample)
            self._turn_ids.add(sample.turn_id)

    def add(self, sample: LatencySample) -> bool:
        with self._lock:
            if sample.turn_id in self._turn_ids:
                return False
            self._samples.append(sample)
            self._turn_ids.add(sample.turn_id)
            if self._log_path:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._log_path.open("a", encoding="utf-8") as handle:
                    handle.write(sample.model_dump_json() + "\n")
            return True

    def samples(self) -> List[LatencySample]:
        with self._lock:
            return list(self._samples)

    def summary(self) -> Dict[str, object]:
        groups: Dict[str, List[LatencySample]] = defaultdict(list)
        for sample in self.samples():
            groups[sample.response_mode].append(sample)

        by_mode: Dict[str, object] = {}
        for mode in ("llm", "speech_directive"):
            mode_samples = groups.get(mode, [])
            tool_samples = [sample for sample in mode_samples if sample.has_tool_call]
            by_mode[mode] = {
                "count": len(mode_samples),
                "tool_count": len(tool_samples),
                "metrics": self._metric_summary(mode_samples),
                "tool_metrics": self._metric_summary(tool_samples),
            }

        return {"total_samples": sum(len(samples) for samples in groups.values()), "by_mode": by_mode}

    def _metric_summary(self, samples: List[LatencySample]) -> Dict[str, object]:
        metrics: Dict[str, object] = {}
        for field in METRIC_FIELDS:
            values = [float(value) for sample in samples if (value := getattr(sample, field)) is not None]
            metrics[field] = {
                "count": len(values),
                "median_ms": round(median(values), 1) if values else None,
                "p95_ms": _percentile(values, 0.95),
            }
        return metrics
