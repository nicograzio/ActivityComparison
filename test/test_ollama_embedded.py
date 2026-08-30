"""Test per core/ollama_embedded.py (motore Ollama gestito dall'app).

Nessuna dipendenza dalla GUI e nessun processo reale: il server Ollama viene
simulato con un server HTTP locale che emula ``GET /api/tags`` e lo streaming
NDJSON di ``POST /api/pull``.

Utilizzo:
    python test/test_ollama_embedded.py
"""

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.ai_agent import (  # noqa: E402
    KNOWN_DOWNLOADABLE_MODELS,
    get_model_suggestions,
    is_valid_model_name,
    list_local_models_info,
    resolve_base_url,
)
from core.ollama_embedded import (  # noqa: E402
    EmbeddedOllamaManager,
    OllamaEmbeddedError,
    _free_port,
    _port_free,
    default_base_url,
    find_ollama_binary,
    get_ollama_manager,
    human_size,
)

TEST_MODEL = "qwen2.5:0.5b"


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    """Handler che emula gli endpoint Ollama usati dal manager."""

    def log_message(self, *args):  # silenzia i log del server di test
        pass

    def do_GET(self):
        if self.path.startswith("/api/tags"):
            entries = []
            for model in self.server.models:
                if isinstance(model, dict):
                    entries.append(model)
                else:
                    # Normalizza i nomi in entry complete come fa Ollama.
                    entries.append({
                        "name": model,
                        "size": 417_000_000,
                        "details": {
                            "parameter_size": "494.03M",
                            "quantization_level": "Q4_K_M",
                        },
                    })
            body = json.dumps({"models": entries}).encode("utf-8")
            self._send(200, body)
        else:
            self._send(404, b"{}")

    def do_POST(self):
        if self.path.startswith("/api/pull"):
            self.server.pull_requests += 1
            if getattr(self.server, "pull_error", None):
                payload = json.dumps({"error": self.server.pull_error}) + "\n"
                self._send(200, payload.encode("utf-8"))
                return
            events = [
                {"status": "pulling manifest"},
                {"status": "downloading sha256:abc", "total": 200, "completed": 100},
                {"status": "downloading sha256:abc", "total": 200, "completed": 200},
                {"status": "verifying sha256 digest"},
                {"status": "success"},
            ]
            payload = b"".join((json.dumps(e) + "\n").encode("utf-8") for e in events)
            self._send(200, payload)
            # Il pull è andato a buon fine: il modello risulta installato.
            self.server.models.append(TEST_MODEL)
        else:
            self._send(404, b"{}")

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _FakeOllama:
    """Server HTTP di test che emula un'istanza Ollama su una porta libera."""

    def __init__(self, models=None):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllamaHandler)
        self.httpd.models = list(models or [])
        self.httpd.pull_requests = 0
        self.httpd.pull_error = None
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc_info):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False


def _fresh_manager() -> EmbeddedOllamaManager:
    """Manager nuovo (non singleton) senza processo collegato."""
    manager = EmbeddedOllamaManager()
    manager._base_url = None
    manager._owns_process = False
    manager._proc = None
    return manager


class TestHelpers(unittest.TestCase):
    def test_default_base_url_from_env(self):
        with patch.dict(os.environ, {"DUOTRACK_OLLAMA_URL": "http://127.0.0.1:12345"}):
            self.assertEqual(default_base_url(), "http://127.0.0.1:12345")

    def test_default_base_url_fallback(self):
        env = {k: v for k, v in os.environ.items() if k != "DUOTRACK_OLLAMA_URL"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(default_base_url(), "http://127.0.0.1:11434")

    def test_find_binary_from_env(self):
        fake_bin = Path(self.id() + "_fake_ollama")
        fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        try:
            with patch.dict(os.environ, {"DUOTRACK_OLLAMA_BIN": str(fake_bin)}):
                found = find_ollama_binary()
            self.assertIsNotNone(found)
            self.assertEqual(found, fake_bin)
        finally:
            fake_bin.unlink(missing_ok=True)

    def test_port_free_roundtrip(self):
        port = _free_port()
        self.assertIsInstance(port, int)
        # Il risultato di _free_port non è garantito restare libero, ma
        # una porta > 0 è un valore valido e _port_free deve rispondere.
        self.assertIsInstance(_port_free(port), bool)


class TestManagerAgainstFakeServer(unittest.TestCase):
    def test_start_reuses_existing_server(self):
        with _FakeOllama(models=[TEST_MODEL]) as fake:
            with patch.dict(os.environ, {"DUOTRACK_OLLAMA_URL": fake.url}):
                manager = _fresh_manager()
                base_url = manager.start(timeout=5)
                self.assertEqual(base_url, fake.url)
                self.assertFalse(manager.owns_process)
                self.assertTrue(manager.is_running)
                self.assertEqual(manager.base_url, fake.url)
                self.assertIn(TEST_MODEL, manager.available_models())
                self.assertTrue(manager.has_model(TEST_MODEL))
                self.assertFalse(manager.has_model("llama3.2:3b"))

    def test_wait_ready_without_url(self):
        manager = _fresh_manager()
        self.assertFalse(manager.wait_ready(timeout=0.1))

    def test_ensure_model_already_installed(self):
        with _FakeOllama(models=[TEST_MODEL]) as fake:
            manager = _fresh_manager()
            manager._base_url = fake.url
            events = []
            manager.ensure_model(TEST_MODEL, progress_cb=lambda s, p: events.append((s, p)))
            self.assertEqual(fake.httpd.pull_requests, 0)
            self.assertEqual(events[-1][1], 100.0)

    def test_ensure_model_pulls_with_progress(self):
        with _FakeOllama(models=[]) as fake:
            manager = _fresh_manager()
            manager._base_url = fake.url
            self.assertFalse(manager.has_model(TEST_MODEL))

            events = []
            manager.ensure_model(TEST_MODEL, progress_cb=lambda s, p: events.append((s, p)))

            self.assertEqual(fake.httpd.pull_requests, 1)
            percents = [p for _, p in events if p is not None]
            self.assertIn(50.0, percents)
            self.assertIn(100.0, percents)
            self.assertTrue(manager.has_model(TEST_MODEL))

    def test_ensure_model_cancelled(self):
        with _FakeOllama(models=[]) as fake:
            manager = _fresh_manager()
            manager._base_url = fake.url
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(OllamaEmbeddedError) as ctx:
                manager.ensure_model(TEST_MODEL, cancel_event=cancel)
            self.assertIn("annullato", str(ctx.exception))

    def test_ensure_model_http_error(self):
        manager = _fresh_manager()
        # URL su cui nessuno ascolta → il POST deve fallire in modo controllato.
        manager._base_url = "http://127.0.0.1:1"
        with self.assertRaises(OllamaEmbeddedError):
            manager.ensure_model(TEST_MODEL)

    def test_resolve_base_url_prefers_embedded(self):
        with _FakeOllama(models=[TEST_MODEL]) as fake:
            # resolve_base_url usa il singleton: configuriamo quello.
            manager = get_ollama_manager()
            manager._proc = None
            manager._owns_process = False
            manager._base_url = fake.url
            try:
                self.assertEqual(resolve_base_url(), fake.url)
            finally:
                manager._base_url = None
                manager._owns_process = False
                manager._proc = None


class TestModelInfo(unittest.TestCase):
    def test_human_size(self):
        self.assertEqual(human_size(417_000_000), "398 MB")
        self.assertEqual(human_size(4_700_000_000), "4.4 GB")
        self.assertEqual(human_size(512), "512 B")
        self.assertIsNone(human_size(None))
        self.assertIsNone(human_size(0))
        self.assertIsNone(human_size("non un numero"))  # type: ignore[arg-type]

    def test_available_models_info_includes_size(self):
        with _FakeOllama(models=[TEST_MODEL]) as fake:
            manager = _fresh_manager()
            manager._base_url = fake.url
            info = manager.available_models_info()
            self.assertEqual(len(info), 1)
            entry = info[0]
            self.assertEqual(entry["name"], TEST_MODEL)
            self.assertEqual(entry["size_bytes"], 417_000_000)
            self.assertEqual(entry["parameter_size"], "494.03M")
            self.assertEqual(entry["quantization"], "Q4_K_M")

    def test_model_status_installed_and_missing(self):
        with _FakeOllama(models=[TEST_MODEL]) as fake:
            manager = _fresh_manager()
            manager._base_url = fake.url
            status = manager.model_status(TEST_MODEL)
            self.assertTrue(status["installed"])
            self.assertEqual(status["size_bytes"], 417_000_000)
            self.assertEqual(status["size_human"], "398 MB")
            self.assertEqual(status["quantization"], "Q4_K_M")

            missing = manager.model_status("llama3.2:3b")
            self.assertFalse(missing["installed"])
            self.assertIsNone(missing["size_human"])

    def test_model_status_without_server(self):
        manager = _fresh_manager()
        status = manager.model_status(TEST_MODEL)
        self.assertFalse(status["installed"])
        self.assertIsNone(status["size_human"])

    def test_list_local_models_info(self):
        with _FakeOllama(models=[TEST_MODEL]) as fake:
            manager = get_ollama_manager()
            manager._proc = None
            manager._owns_process = False
            manager._base_url = None
            env = {k: v for k, v in os.environ.items() if k != "DUOTRACK_OLLAMA_URL"}
            env["DUOTRACK_OLLAMA_URL"] = fake.url
            with patch.dict(os.environ, env, clear=True):
                info = list_local_models_info()
            self.assertEqual(len(info), 1)
            self.assertEqual(info[0]["name"], TEST_MODEL)
            self.assertEqual(info[0]["size_bytes"], 417_000_000)


class TestCustomModelName(unittest.TestCase):
    def test_valid_model_names(self):
        valid = (
            "qwen2.5:0.5b",
            "deepseek-r1:1.5b",
            "llama3.2",          # senza tag → :latest
            "mistral:7b-instruct",
            "Qwen2.5:0.5B",      # maiuscole ammesse
        )
        for name in valid:
            self.assertTrue(is_valid_model_name(name), name)

    def test_invalid_model_names(self):
        invalid = ("", "   ", "modello con spazi", "modello!!", "a b:c", "nome:tag:extra")
        for name in invalid:
            self.assertFalse(is_valid_model_name(name), name)

    def test_pull_model_not_found_message(self):
        with _FakeOllama(models=[]) as fake:
            fake.httpd.pull_error = "pull model manifest: file does not exist"
            manager = _fresh_manager()
            manager._base_url = fake.url
            with self.assertRaises(OllamaEmbeddedError) as ctx:
                manager.ensure_model("modello-inesistente:latest")
            self.assertIn("non è stato trovato", str(ctx.exception))
            self.assertIn("ollama.com", str(ctx.exception))


class TestModelSuggestions(unittest.TestCase):
    def test_suggestions_valid_and_deduplicated(self):
        suggestions = get_model_suggestions()
        self.assertGreaterEqual(len(suggestions), 10)
        self.assertEqual(len(suggestions), len(set(suggestions)))
        for name in suggestions:
            self.assertTrue(is_valid_model_name(name), name)
        # Il catalogo scaricabile è incluso nei suggerimenti.
        for entry in KNOWN_DOWNLOADABLE_MODELS:
            self.assertIn(entry["name"], suggestions)

    def test_suggestions_merge_installed(self):
        suggestions = get_model_suggestions(["qwen2.5:0.5b", "mio-modello:custom", "spazio non valido"])
        self.assertIn("mio-modello:custom", suggestions)
        self.assertNotIn("spazio non valido", suggestions)
        # Nessun duplicato con la lista statica.
        self.assertEqual(suggestions.count("qwen2.5:0.5b"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
