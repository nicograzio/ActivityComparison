"""Test per core/ai_agent.py (analisi IA locale via Ollama).

Nessuna chiamata di rete nei test: le API HTTP vengono mockate.
Nessuna dipendenza dalla GUI (non importa PyQt6).

Utilizzo:
    python test/test_ai_agent.py
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests as requests_lib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.analyzer import find_common_segments  # noqa: E402
from core.track import Track, TrackPoint  # noqa: E402
from core.ai_agent import (  # noqa: E402
    OllamaUnavailableError,
    build_agent_tools,
    build_comparison_snapshot,
    check_ollama,
    generate_ai_comparison,
    generate_offline_fallback_report,
    list_local_models,
    run_agentic_comparison,
    _build_user_prompt,
)


def _build_synthetic_tracks():
    """Due tracce quasi identiche (offset ~2 m) per un confronto sintetico."""
    from datetime import datetime, timedelta

    def build(offset_lat: float, start_minutes: int) -> Track:
        track = Track(f"track_{start_minutes}")
        for i in range(120):
            track.points.append(
                TrackPoint(
                    latitude=45.0 + offset_lat,
                    longitude=9.0 + i * 0.0001,
                    altitude=100.0 + i * 0.5,
                    timestamp=datetime(2024, 1, 1, 8, start_minutes)
                    + timedelta(seconds=30 * i),
                )
            )
        return track

    return build(0.000000, 0), build(0.000020, 5)


def _build_progress_tracks():
    """Due tracce identiche geometricamente: A veloce nella prima metà, lenta nella seconda.

    13 punti (12 intervalli): A percorre i primi 6 intervalli in 1 s ciascuno e
    gli ultimi 6 in 5 s; B in modo uniforme (3 s per intervallo). A guadagna
    tempo nella prima parte del tratto e lo perde nella seconda.
    """
    from datetime import datetime, timedelta

    base = datetime(2024, 1, 1, 8, 0, 0)
    n = 13
    track_a = Track("prog_a")
    track_b = Track("prog_b")
    t_a = 0.0
    t_b = 0.0
    for i in range(n):
        track_a.points.append(
            TrackPoint(latitude=45.0, longitude=9.0 + i * 1e-4, altitude=100.0,
                       timestamp=base + timedelta(seconds=t_a))
        )
        track_b.points.append(
            TrackPoint(latitude=45.0, longitude=9.0 + i * 1e-4, altitude=100.0,
                       timestamp=base + timedelta(seconds=t_b))
        )
        if i < n - 1:
            t_a += 1.0 if i < 6 else 5.0
            t_b += 3.0
    return track_a, track_b


class TestComparisonSnapshot(unittest.TestCase):
    def setUp(self):
        self.track_a, self.track_b = _build_synthetic_tracks()
        self.segments = find_common_segments(self.track_a, self.track_b)

    def test_common_segments_found(self):
        self.assertGreaterEqual(len(self.segments), 1)
        for key in ("time_a_sec", "avg_speed_a", "length_m"):
            self.assertIn(key, self.segments[0])

    def test_snapshot_deterministic_and_json_safe(self):
        snap1 = build_comparison_snapshot(self.track_a, self.track_b, self.segments)
        snap2 = build_comparison_snapshot(self.track_a, self.track_b, self.segments)
        self.assertEqual(snap1, snap2)
        json.dumps(snap1, ensure_ascii=False)  # non deve sollevare

        self.assertIn("contesto_tracce", snap1)
        self.assertIn("attivita_a", snap1["contesto_tracce"])
        self.assertIn("attivita_b", snap1["contesto_tracce"])
        self.assertEqual(len(snap1["segmenti_comuni"]), len(self.segments))
        # I segmenti comuni devono venire PRIMA del contesto delle tracce:
        # i modelli piccoli danno più peso all'inizio del JSON.
        self.assertEqual(
            list(snap1.keys()), ["tipo", "versione", "segmenti_comuni", "contesto_tracce"]
        )

    def test_snapshot_metrics(self):
        snap = build_comparison_snapshot(self.track_a, self.track_b, self.segments)
        a = snap["contesto_tracce"]["attivita_a"]
        self.assertGreater(a["dist_km"], 0)
        self.assertIsNotNone(a["durata_s"])
        self.assertIsNotNone(a["vel_media_kmh"])
        first = snap["segmenti_comuni"][0]
        self.assertIn("vel_a_kmh", first)
        self.assertIn("delta_tempo_s", first)
        self.assertIn("vincitore", first)
        self.assertIn("esito", first)
        self.assertIn("tempo_a_leggibile", first)

    def test_snapshot_winner_and_readable_times(self):
        segs = [{
            "id": 1,
            "length_m": 800.0,
            "a_start_dist_m": 0.0,
            "b_start_dist_m": 0.0,
            "time_a_sec": 190.0,
            "time_b_sec": 175.0,
            "avg_speed_a": 15.0,
            "avg_speed_b": 16.36,
            "slope_a": 1.0,
            "slope_b": 1.0,
            "avg_hr_a": 140.0,
            "avg_hr_b": 145.0,
        }]
        snap = build_comparison_snapshot(self.track_a, self.track_b, segs)
        entry = snap["segmenti_comuni"][0]
        self.assertEqual(entry["vincitore"], "B")
        self.assertEqual(entry["tempo_a_leggibile"], "3:10")
        self.assertEqual(entry["tempo_b_leggibile"], "2:55")
        self.assertIsNotNone(entry["esito"])
        self.assertIn("B", entry["esito"])

    def test_snapshot_parity(self):
        segs = [{
            "id": 1,
            "length_m": 500.0,
            "a_start_dist_m": 0.0,
            "b_start_dist_m": 0.0,
            "time_a_sec": 180.0,
            "time_b_sec": 180.5,
            "avg_speed_a": 10.0,
            "avg_speed_b": 10.0,
        }]
        snap = build_comparison_snapshot(self.track_a, self.track_b, segs)
        entry = snap["segmenti_comuni"][0]
        self.assertEqual(entry["vincitore"], "pari")
        self.assertIn("parità", entry["esito"])

    def test_snapshot_progress_profile(self):
        track_a, track_b = _build_progress_tracks()
        seg = {
            "id": 1,
            "a_start_idx": 0,
            "a_end_idx": 12,
            "b_start_idx": 0,
            "b_end_idx": 12,
            "a_start_dist_m": 0.0,
            "a_end_dist_m": 86.0,
            "b_start_dist_m": 0.0,
            "b_end_dist_m": 86.0,
            "length_m": 86.0,
            "time_a_sec": 36.0,
            "time_b_sec": 36.0,
            "avg_speed_a": 8.6,
            "avg_speed_b": 8.6,
        }
        snap = build_comparison_snapshot(track_a, track_b, [seg])
        prog = snap["segmenti_comuni"][0]["andamento"]
        self.assertIsNotNone(prog)
        # A guadagna nella prima parte, B nella seconda: due tratti fusi.
        self.assertEqual([st["chi"] for st in prog], ["A", "B"])
        self.assertAlmostEqual(prog[0]["guadagno_s"], 12.0, delta=1.5)
        self.assertAlmostEqual(prog[1]["guadagno_s"], -12.0, delta=1.5)
        self.assertAlmostEqual(
            sum(st["guadagno_s"] for st in prog), 0.0, delta=0.5
        )
        self.assertIn("A guadagna", prog[0]["frase"])
        self.assertIn("B guadagna", prog[1]["frase"])

    def test_snapshot_progress_without_timestamps(self):
        track_a, track_b = _build_progress_tracks()
        for track in (track_a, track_b):
            for p in track.points:
                p.timestamp = None
        seg = {
            "id": 1,
            "a_start_idx": 0,
            "a_end_idx": 12,
            "b_start_idx": 0,
            "b_end_idx": 12,
            "length_m": 86.0,
        }
        snap = build_comparison_snapshot(track_a, track_b, [seg])
        self.assertIsNone(snap["segmenti_comuni"][0]["andamento"])

    def test_user_prompt_lists_segments_exactly(self):
        track_a, track_b = _build_progress_tracks()
        seg = {
            "id": "MoneLLine",
            "a_start_idx": 0,
            "a_end_idx": 12,
            "b_start_idx": 0,
            "b_end_idx": 12,
            "length_m": 86.0,
            "time_a_sec": 36.0,
            "time_b_sec": 36.0,
            "avg_speed_a": 8.6,
            "avg_speed_b": 8.6,
        }
        snap = build_comparison_snapshot(track_a, track_b, [seg])
        entry = snap["segmenti_comuni"][0]
        self.assertEqual(entry["nome"], "MoneLLine")
        prompt = _build_user_prompt(snap, "strava_full", "strava_full")
        self.assertIn("ESATTAMENTE", prompt)
        self.assertIn("MoneLLine", prompt)
        self.assertIn("andamento", prompt)
        # Un solo segmento: il prompt deve dire "questo 1" e vietarne altri.
        self.assertIn("1 (non aggiungerne altri)", prompt)

    def test_snapshot_empty_segments(self):
        snap = build_comparison_snapshot(self.track_a, self.track_b, [])
        self.assertEqual(snap["segmenti_comuni"], [])


class TestAIComparison(unittest.TestCase):
    BASE = "http://127.0.0.1:11434"

    @patch("core.ai_agent.requests.post")
    def test_payload_and_parsing(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message": {"content": "Report IA di prova"}}
        mock_post.return_value = mock_response

        snapshot = {
            "tipo": "confronto_attivita",
            "attivita_a": {"nome": "A", "dist_km": 10.0},
            "attivita_b": {"nome": "B", "dist_km": 10.0},
            "segmenti_comuni": [{"id": 1, "vel_a_kmh": 25.0, "vel_b_kmh": 27.0}],
        }
        result = generate_ai_comparison(
            snapshot,
            "Attività A",
            "Attività B",
            model="qwen2.5:7b",
            base_url=self.BASE,
            timeout=10,
        )
        self.assertEqual(result, "Report IA di prova")

        url = mock_post.call_args.args[0]
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(url, self.BASE + "/api/chat")
        self.assertEqual(payload["model"], "qwen2.5:7b")
        self.assertIs(payload["stream"], False)
        self.assertEqual(mock_post.call_args.kwargs["timeout"], 10)

        self.assertEqual(len(payload["messages"]), 2)
        system, user = payload["messages"]
        self.assertEqual(system["role"], "system")
        self.assertIn("coach", system["content"].lower())
        self.assertEqual(user["role"], "user")
        self.assertIn("segmenti_comuni", user["content"])
        self.assertIn("Attività A", user["content"])

    @patch(
        "core.ai_agent.requests.post",
        side_effect=requests_lib.exceptions.ConnectionError("refused"),
    )
    def test_connection_error_raises_unavailable(self, mock_post):
        with self.assertRaises(OllamaUnavailableError):
            generate_ai_comparison({}, "A", "B", base_url=self.BASE)

    @patch(
        "core.ai_agent.requests.post",
        side_effect=requests_lib.exceptions.Timeout("slow"),
    )
    def test_timeout_raises_unavailable(self, mock_post):
        with self.assertRaises(OllamaUnavailableError):
            generate_ai_comparison({}, "A", "B", base_url=self.BASE)

    @patch("core.ai_agent.requests.post")
    def test_http_error_raises_unavailable(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "model 'x' not found"
        mock_post.return_value = mock_response
        with self.assertRaises(OllamaUnavailableError) as ctx:
            generate_ai_comparison({}, "A", "B", base_url=self.BASE)
        self.assertIn("404", str(ctx.exception))

    @patch("core.ai_agent.requests.get")
    def test_list_local_models(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [{"name": "qwen2.5:7b"}, {"name": "gemma2:9b"}]
        }
        mock_get.return_value = mock_response
        self.assertEqual(list_local_models(self.BASE), ["qwen2.5:7b", "gemma2:9b"])
        self.assertEqual(mock_get.call_args.args[0], self.BASE + "/api/tags")

    @patch("core.ai_agent.requests.get")
    def test_check_ollama(self, mock_get):
        ok_response = MagicMock()
        ok_response.status_code = 200
        mock_get.return_value = ok_response
        self.assertTrue(check_ollama(self.BASE))

        mock_get.side_effect = requests_lib.exceptions.ConnectionError("down")
        self.assertFalse(check_ollama(self.BASE))


class TestOfflineFallback(unittest.TestCase):
    def test_fallback_empty_segments(self):
        report = generate_offline_fallback_report(
            [], "A", "B", "servizio non raggiungibile"
        )
        self.assertIn("servizio non raggiungibile", report)
        self.assertIn("Nessun segmento comune", report)

    def test_fallback_strips_html(self):
        segments = [{
            "id": 1,
            "length_m": 100.0,
            "a_start_dist_m": 0.0,
            "b_start_dist_m": 0.0,
            "time_a_sec": 100.0,
            "time_b_sec": 120.0,
            "avg_speed_a": 30.0,
            "avg_speed_b": 25.0,
            "slope_a": 1.0,
            "slope_b": 1.0,
            "avg_hr_a": 140.0,
            "avg_hr_b": 150.0,
            "avg_alt_a": 100.0,
            "avg_alt_b": 100.0,
        }]
        report = generate_offline_fallback_report(segments, "Attività A", "Attività B")
        self.assertNotIn("<b>", report)
        self.assertNotIn("</b>", report)
        self.assertIn("Attività", report)


class TestAgenticComparison(unittest.TestCase):
    BASE = "http://127.0.0.1:11434"

    @staticmethod
    def _segment():
        points_a = [
            TrackPoint(
                latitude=45.0 + i * 1e-5,
                longitude=9.0,
                altitude=100.0 + i * 2.0,
                speed=4.0 + 0.2 * i,
                heart_rate=140 + i,
            )
            for i in range(5)
        ]
        points_b = [
            TrackPoint(
                latitude=45.0 + i * 1e-5,
                longitude=9.0,
                altitude=100.0 + i * 2.0,
                speed=4.4 + 0.2 * i,
                heart_rate=145 + i,
            )
            for i in range(5)
        ]
        return [{
            "id": 1,
            "length_m": 800.0,
            "a_start_dist_m": 100.0,
            "a_end_dist_m": 900.0,
            "b_start_dist_m": 90.0,
            "b_end_dist_m": 890.0,
            "time_a_sec": 190.0,
            "time_b_sec": 175.0,
            "avg_speed_a": 15.0,
            "avg_speed_b": 16.36,
            "slope_a": 2.0,
            "slope_b": 2.0,
            "avg_hr_a": 142.0,
            "avg_hr_b": 148.0,
            "avg_alt_a": 105.0,
            "avg_alt_b": 105.0,
            "resampled_points_a": points_a,
            "resampled_points_b": points_b,
        }]

    @staticmethod
    def _make_msg(content: str = "", tool_calls=None):
        msg = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return {"message": msg}

    def test_build_agent_tools(self):
        tools = build_agent_tools()
        self.assertEqual(len(tools), 3)
        names = [t["function"]["name"] for t in tools]
        self.assertIn("get_comparison_snapshot", names)
        self.assertIn("get_segment_detail", names)
        self.assertIn("highlight_segment", names)


    def test_loop_with_tool_calls(self):
        segments = self._segment()
        snapshot = {"attivita_a": {}, "attivita_b": {}, "segmenti_comuni": []}
        received = []

        def fake_chat(messages, model, base_url, timeout, tools=None):
            received.append(messages)
            if len(received) == 1:
                calls = [{"function": {"name": "get_comparison_snapshot", "arguments": {}}}]
                return self._make_msg("", calls)
            if len(received) == 2:
                calls = [{"function": {"name": "get_segment_detail", "arguments": "{\"segment_id\": 1, \"side\": \"A\"}"}}]
                return self._make_msg("", calls)
            return self._make_msg("Report finale **ok**.")

        with patch("core.ai_agent._post_chat", side_effect=fake_chat) as mock_chat:
            report, transcript = run_agentic_comparison(
                snapshot, "Attività A", "Attività B", segments,
                model="qwen2.5:7b", base_url=self.BASE, timeout=10,
            )

        self.assertEqual(report, "Report finale **ok**.")
        self.assertEqual(len(transcript), 2)
        self.assertEqual(transcript[0]["tool"], "get_comparison_snapshot")
        self.assertEqual(transcript[1]["tool"], "get_segment_detail")
        self.assertIsInstance(transcript[0]["result"], dict)
        # Velocità massima attesa dal punto più veloce (4.8 m/s * 3.6 = 17.28).
        self.assertEqual(transcript[1]["result"]["vel_max_kmh"], 17.28)
        self.assertEqual(transcript[1]["result"]["fc_max"], 144)

        # La seconda richiesta deve contenere i risultati dei tool (role=tool).
        roles = [m.get("role") for m in received[1]]
        self.assertIn("tool", roles)
        tool_contents = [m.get("content", "") for m in received[1] if m.get("role") == "tool"]
        self.assertTrue(any("segmenti_comuni" in c for c in tool_contents))

        # Gli strumenti vengono forniti in ogni chiamata.
        first_kwargs = mock_chat.call_args_list[0].kwargs
        self.assertEqual(len(first_kwargs["tools"]), 3)

    def test_highlight_callback_invoked(self):
        segments = self._segment()
        highlight = MagicMock()

        def fake_chat(messages, model, base_url, timeout, tools=None):
            if not any(m.get("role") == "tool" for m in messages):
                calls = [{"function": {"name": "highlight_segment", "arguments": {"segment_id": 1, "side": "A"}}}]
                return self._make_msg("", calls)
            return self._make_msg("Report con highlight.")

        with patch("core.ai_agent._post_chat", side_effect=fake_chat):
            report, transcript = run_agentic_comparison(
                {}, "A", "B", segments,
                on_highlight=highlight, base_url=self.BASE, timeout=10,
            )

        highlight.assert_called_once_with(1, "A")
        self.assertEqual(transcript[0]["result"]["status"], "ok")
        self.assertEqual(report, "Report con highlight.")


    def test_unknown_tool_returns_error(self):
        def fake_chat(messages, model, base_url, timeout, tools=None):
            if not any(m.get("role") == "tool" for m in messages):
                calls = [{"function": {"name": "unknown_tool", "arguments": {}}}]
                return self._make_msg("", calls)
            return self._make_msg("Finale.")

        with patch("core.ai_agent._post_chat", side_effect=fake_chat):
            report, transcript = run_agentic_comparison(
                {}, "A", "B", [], base_url=self.BASE, timeout=10,
            )

        self.assertIn("strumento sconosciuto", transcript[0]["result"]["error"])
        self.assertEqual(report, "Finale.")

    def test_max_iterations_stops(self):
        def fake_chat(messages, model, base_url, timeout, tools=None):
            calls = [{"function": {"name": "get_comparison_snapshot", "arguments": {}}}]
            return self._make_msg("", calls)

        with patch("core.ai_agent._post_chat", side_effect=fake_chat):
            report, transcript = run_agentic_comparison(
                {}, "A", "B", [], base_url=self.BASE, timeout=10, max_iterations=2,
            )

        self.assertEqual(len(transcript), 2)
        self.assertEqual(report, "")

    def test_segment_detail_out_of_range(self):
        def fake_chat(messages, model, base_url, timeout, tools=None):
            if not any(m.get("role") == "tool" for m in messages):
                calls = [{"function": {"name": "get_segment_detail", "arguments": {"segment_id": 99, "side": "B"}}}]
                return self._make_msg("", calls)
            return self._make_msg("Finale.")

        with patch("core.ai_agent._post_chat", side_effect=fake_chat):
            _, transcript = run_agentic_comparison(
                {}, "A", "B", self._segment(), base_url=self.BASE, timeout=10,
            )

        self.assertIn("fuori intervallo", transcript[0]["result"]["error"])


if __name__ == "__main__":
    unittest.main()