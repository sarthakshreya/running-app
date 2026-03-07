"""Tests for src/extract.py."""

import json
from unittest.mock import MagicMock, patch

import pytest

from extract import _load_images, _parse_json, extract

# Minimal valid PNG header bytes
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
_JPG = b"\xff\xd8\xff" + b"\x00" * 100


class TestParseJson:
    def test_plain_json(self):
        raw = '{"distance_km": 10.5, "splits": []}'
        assert _parse_json(raw)["distance_km"] == 10.5

    def test_fenced_json_with_lang(self):
        raw = '```json\n{"distance_km": 10.5}\n```'
        assert _parse_json(raw)["distance_km"] == 10.5

    def test_fenced_json_no_lang(self):
        raw = '```\n{"date": "2026-02-28"}\n```'
        assert _parse_json(raw)["date"] == "2026-02-28"

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json("not json at all")


class TestLoadImages:
    def test_raises_when_dir_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _load_images(tmp_path / "missing")

    def test_raises_when_dir_empty(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError, match="No screenshots found"):
            _load_images(empty)

    def test_loads_png(self, tmp_path):
        (tmp_path / "screen.png").write_bytes(_PNG)
        result = _load_images(tmp_path)
        assert len(result) == 1
        assert result[0]["media_type"] == "image/png"
        assert result[0]["data"]  # non-empty base64

    def test_loads_jpg(self, tmp_path):
        (tmp_path / "screen.jpg").write_bytes(_JPG)
        result = _load_images(tmp_path)
        assert len(result) == 1
        assert result[0]["media_type"] == "image/jpeg"

    def test_loads_multiple_images(self, tmp_path):
        (tmp_path / "overview.png").write_bytes(_PNG)
        (tmp_path / "splits.png").write_bytes(_PNG)
        (tmp_path / "heart.jpg").write_bytes(_JPG)
        result = _load_images(tmp_path)
        assert len(result) == 3

    def test_ignores_non_image_files(self, tmp_path):
        (tmp_path / "screen.png").write_bytes(_PNG)
        (tmp_path / "notes.txt").write_text("ignore me")
        result = _load_images(tmp_path)
        assert len(result) == 1


class TestExtract:
    def _make_run_dir(self, tmp_path, date="2026-02-28"):
        run_dir = tmp_path / "data" / "runs" / date
        run_dir.mkdir(parents=True)
        (run_dir / "screen.png").write_bytes(_PNG)
        return run_dir

    def _make_prompts_dir(self, tmp_path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "extract.md").write_text("Extract metrics.")
        return prompts_dir

    def _mock_client(self, payload: dict):
        fake_response = MagicMock()
        fake_response.content = [MagicMock(text=json.dumps(payload))]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response
        return mock_client

    def test_raises_when_run_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("extract.DATA_DIR", tmp_path / "data")
        with pytest.raises(FileNotFoundError, match="Run directory not found"):
            extract("2026-02-28")

    def test_returns_full_metrics(self, tmp_path, monkeypatch):
        self._make_run_dir(tmp_path)
        prompts_dir = self._make_prompts_dir(tmp_path)
        monkeypatch.setattr("extract.DATA_DIR", tmp_path / "data")
        monkeypatch.setattr("extract.PROMPTS_DIR", prompts_dir)

        payload = {
            "date": "2026-02-28",
            "distance_km": 10.5,
            "duration_hms": "55:23",
            "moving_time_hms": "54:10",
            "avg_pace_per_km": "5:16",
            "avg_hr_bpm": 152,
            "max_hr_bpm": 178,
            "elevation_gain_m": 45,
            "calories_kcal": 720,
            "avg_cadence_spm": 172,
            "title": "Morning Run",
            "location": "Golden Gate Park",
            "splits": [{"km": 1, "pace": "5:10", "hr_bpm": 148, "elev_m": 5}],
        }

        with patch("extract.Anthropic", return_value=self._mock_client(payload)):
            result = extract("2026-02-28")

        assert result["distance_km"] == 10.5
        assert result["avg_hr_bpm"] == 152
        assert len(result["splits"]) == 1
        assert result["date"] == "2026-02-28"

    def test_backfills_date_when_null(self, tmp_path, monkeypatch):
        self._make_run_dir(tmp_path)
        prompts_dir = self._make_prompts_dir(tmp_path)
        monkeypatch.setattr("extract.DATA_DIR", tmp_path / "data")
        monkeypatch.setattr("extract.PROMPTS_DIR", prompts_dir)

        payload = {"date": None, "distance_km": 5.0, "splits": []}

        with patch("extract.Anthropic", return_value=self._mock_client(payload)):
            result = extract("2026-02-28")

        assert result["date"] == "2026-02-28"

    def test_passes_all_images_to_api(self, tmp_path, monkeypatch):
        run_dir = self._make_run_dir(tmp_path)
        (run_dir / "splits.png").write_bytes(_PNG)  # second screenshot
        prompts_dir = self._make_prompts_dir(tmp_path)
        monkeypatch.setattr("extract.DATA_DIR", tmp_path / "data")
        monkeypatch.setattr("extract.PROMPTS_DIR", prompts_dir)

        payload = {"date": "2026-02-28", "distance_km": 10.0, "splits": []}
        mock_client = self._mock_client(payload)

        with patch("extract.Anthropic", return_value=mock_client):
            extract("2026-02-28")

        call_args = mock_client.messages.create.call_args
        content = call_args.kwargs["messages"][0]["content"]
        image_blocks = [c for c in content if c["type"] == "image"]
        assert len(image_blocks) == 2

    def test_uses_opus_model(self, tmp_path, monkeypatch):
        self._make_run_dir(tmp_path)
        prompts_dir = self._make_prompts_dir(tmp_path)
        monkeypatch.setattr("extract.DATA_DIR", tmp_path / "data")
        monkeypatch.setattr("extract.PROMPTS_DIR", prompts_dir)

        payload = {"date": "2026-02-28", "distance_km": 5.0, "splits": []}
        mock_client = self._mock_client(payload)

        with patch("extract.Anthropic", return_value=mock_client):
            extract("2026-02-28")

        call_args = mock_client.messages.create.call_args
        assert call_args.kwargs["model"] == "claude-opus-4-6"
