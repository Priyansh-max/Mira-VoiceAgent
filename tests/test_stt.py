from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend import stt


class _FakeTranscriptions:
    def __init__(self, *, text: str = "hello", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


class SpeechToTextTest(unittest.TestCase):
    def test_preserves_webm_filename_and_normalized_media_type(self) -> None:
        transcriptions = _FakeTranscriptions()
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))

        with patch("backend.stt._client", return_value=client):
            text = stt.transcribe(b"webm-bytes", content_type="audio/webm;codecs=opus")

        self.assertEqual(text, "hello")
        self.assertEqual(
            transcriptions.kwargs["file"],
            ("audio.webm", b"webm-bytes", "audio/webm"),
        )
        self.assertEqual(transcriptions.kwargs["response_format"], "json")
        self.assertEqual(transcriptions.kwargs["language"], "en")
        self.assertNotIn("prompt", transcriptions.kwargs)

    def test_uses_a_narrow_prompt_while_capturing_an_order_id(self) -> None:
        transcriptions = _FakeTranscriptions(text="one two three four")
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))

        with patch("backend.stt._client", return_value=client):
            text = stt.transcribe(
                b"wav-bytes",
                content_type="audio/wav",
                expected_field="order_id",
            )

        self.assertEqual(text, "1234")
        self.assertIn("order ID made of digits", transcriptions.kwargs["prompt"])

    def test_allows_an_explicit_language_override(self) -> None:
        transcriptions = _FakeTranscriptions()
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))

        with (
            patch("backend.stt._client", return_value=client),
            patch.dict("os.environ", {"STT_LANGUAGE": "hi"}),
        ):
            stt.transcribe(b"wav-bytes", content_type="audio/wav")

        self.assertEqual(transcriptions.kwargs["language"], "hi")

    def test_preserves_mp4_upload_format(self) -> None:
        transcriptions = _FakeTranscriptions()
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))

        with patch("backend.stt._client", return_value=client):
            stt.transcribe(b"mp4-bytes", content_type="audio/mp4")

        self.assertEqual(
            transcriptions.kwargs["file"],
            ("audio.mp4", b"mp4-bytes", "audio/mp4"),
        )

    def test_surfaces_provider_errors(self) -> None:
        transcriptions = _FakeTranscriptions(error=RuntimeError("provider rejected audio"))
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))

        with patch("backend.stt._client", return_value=client):
            with self.assertRaisesRegex(stt.SpeechToTextError, "provider rejected audio"):
                stt.transcribe(b"broken", content_type="audio/webm")

    def test_normalizes_spoken_english_digits_for_identifiers(self) -> None:
        transcriptions = _FakeTranscriptions(text="My order ID is one two three four")
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))

        with patch("backend.stt._client", return_value=client):
            text = stt.transcribe(b"wav-bytes", content_type="audio/wav")

        self.assertEqual(text, "My order ID is 1234")

    def test_normalizes_urdu_digit_words_and_non_ascii_digits(self) -> None:
        self.assertEqual(stt.normalize_spoken_identifiers("ون ٹو تھری فور"), "1234")
        self.assertEqual(stt.normalize_spoken_identifiers("۱۲۳۴"), "1234")

    def test_rejects_an_impossible_transcript_for_short_audio(self) -> None:
        transcript = " ".join(["customer"] * 40)

        self.assertTrue(
            stt.transcript_is_implausible(transcript, audio_duration_seconds=1.0)
        )
        self.assertFalse(
            stt.transcript_is_implausible(
                "my order ID is one two three four",
                audio_duration_seconds=1.5,
            )
        )


if __name__ == "__main__":
    unittest.main()
