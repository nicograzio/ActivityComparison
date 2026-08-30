"""Motore Ollama "embedded": avvio e gestione del server locale da parte dell'app.

L'obiettivo è che l'utente **non debba installare né avviare nulla**: l'app
trova il binario Ollama (in bundle nella cartella ``bin/<piattaforma>/`` o nel
pacchetto PyInstaller), lancia ``ollama serve`` come processo figlio su una
porta privata e scarica automaticamente i modelli richiesti (``POST /api/pull``).

Il server usa una cartella modelli dedicata (``OLLAMA_MODELS``) dentro la
cartella dati dell'app, così non interferisce con un'eventuale installazione
Ollama dell'utente (che viene comunque riusata se già attiva su 11434).

Consumed by:
    - ``core.ai_agent.resolve_base_url`` (URL del server da usare)
    - ``ui.insight_dialog`` (avvio motore + download modello con progress bar)
    - ``ui.main_window`` (chiusura pulita del processo)

Uses:
    - solo ``requests`` + stdlib: nessuna dipendenza da Qt (testabile in CI)
"""

import atexit
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

log = logging.getLogger(__name__)

DEFAULT_PORT = 11434
TAGS_ENDPOINT = "/api/tags"
PULL_ENDPOINT = "/api/pull"

DEFAULT_START_TIMEOUT = 30.0

# Callback di progresso: progress_cb(status: str, percent: Optional[float])
ProgressCallback = Callable[[str, Optional[float]], None]


class OllamaEmbeddedError(RuntimeError):
    """Il motore Ollama embedded non è stato avviato o non ha risposto."""


def app_data_dir() -> Path:
    """Cartella dati dell'app (modelli, log) per la piattaforma corrente."""
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    elif system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "DuoTrack"


def _binary_name() -> str:
    """Nome del file eseguibile Ollama per la piattaforma corrente."""
    return "ollama.exe" if platform.system() == "Windows" else "ollama"


def _binary_subdir() -> str:
    """Sottocartella di ``bin/`` per la piattaforma corrente."""
    return {"Darwin": "macos", "Windows": "windows"}.get(platform.system(), "linux")


def _bundled_binary_candidates() -> List[Path]:
    """Percorsi candidati del binario Ollama in bundle con l'app."""
    name = _binary_name()
    subdir = _binary_subdir()
    candidates: List[Path] = []

    # Pacchetto PyInstaller (onefile): cartella di estrazione temporanea.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(meipass)
        candidates += [base / "bin" / subdir / name, base / "bin" / name]

    # Layout del repository / pacchetto onedir: <root>/bin/<piattaforma>/ollama
    root = Path(__file__).resolve().parent.parent
    candidates += [root / "bin" / subdir / name, root / "bin" / name]
    return candidates


def find_ollama_binary() -> Optional[Path]:
    """Trova il binario Ollama da usare.

    Ordine di ricerca:
        1. variabile d'ambiente ``DUOTRACK_OLLAMA_BIN``;
        2. binario in bundle (cartella ``bin/`` dell'app o pacchetto PyInstaller);
        3. binario nel PATH di sistema (utente che ha già installato Ollama).

    Returns:
        Il Path del binario, oppure None se non trovato.
    """
    env_path = os.environ.get("DUOTRACK_OLLAMA_BIN")
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            return candidate
        log.warning("DUOTRACK_OLLAMA_BIN impostato ma non esiste: %s", env_path)

    for candidate in _bundled_binary_candidates():
        if candidate.is_file():
            return candidate

    which = shutil.which("ollama")
    if which:
        return Path(which)
    return None


def default_base_url() -> str:
    """Base URL da sondare per prima (env dell'app o porta standard)."""
    return os.environ.get("DUOTRACK_OLLAMA_URL", f"http://127.0.0.1:{DEFAULT_PORT}")


def _probe(base_url: str, timeout: float = 1.0) -> bool:
    """True se su ``base_url`` risponde un server Ollama (GET /api/tags)."""
    try:
        response = requests.get(base_url.rstrip("/") + TAGS_ENDPOINT, timeout=timeout)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


def _port_free(port: int) -> bool:
    """True se la porta TCP su localhost è libera."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _free_port() -> int:
    """Richiede al kernel una porta libera e la restituisce."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def human_size(num_bytes: Optional[int]) -> Optional[str]:
    """Formatta una dimensione in bytes in una stringa leggibile (KB/MB/GB).

    Returns:
        Es. ``"398 MB"``, ``"4.7 GB"``, oppure None se il valore non è valido.
    """
    if not isinstance(num_bytes, (int, float)) or num_bytes <= 0:
        return None
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if value >= 100:
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return None  # pragma: no cover - irraggiungibile


class EmbeddedOllamaManager:
    """Gestisce il ciclo di vita del server Ollama usato dall'app.

    Il manager può trovarsi in tre situazioni:
        - nessun server attivo (``base_url`` è None);
        - server esterno riusato (Ollama dell'utente già attivo);
        - processo figlio avviato dall'app (``owns_process`` True).
    """

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._base_url: Optional[str] = None
        self._owns_process = False
        self._log_handle = None

    @property
    def models_dir(self) -> Path:
        """Cartella dove vengono salvati i modelli scaricati dall'app."""
        return app_data_dir() / "models"

    @property
    def owns_process(self) -> bool:
        """True se il server attivo è un processo figlio avviato dall'app."""
        return self._owns_process and self._proc is not None and self._proc.poll() is None

    @property
    def is_running(self) -> bool:
        """True se un server Ollama (embedded o esterno) è raggiungibile."""
        if self._base_url is None:
            return False
        if self.owns_process:
            return True
        return _probe(self._base_url)

    @property
    def base_url(self) -> Optional[str]:
        """Base URL del server attivo, oppure None se non avviato."""
        return self._base_url if self.is_running else None

    @property
    def is_embedded_binary_available(self) -> bool:
        """True se esiste un binario Ollama utilizzabile (bundle o PATH)."""
        return find_ollama_binary() is not None

    def start(self, timeout: float = DEFAULT_START_TIMEOUT) -> str:
        """Avvia il motore (se necessario) e restituisce la base URL.

        Ordine:
            1. se un server gestito da noi è già attivo, riusalo;
            2. se su ``default_base_url()`` risponde già un Ollama (dell'utente
               o di un avvio precedente), riusalo senza lanciare processi;
            3. altrimenti lancia ``ollama serve`` come processo figlio su una
               porta privata, con modelli isolati nella cartella dati dell'app.

        Raises:
            OllamaEmbeddedError: se il binario non esiste o il server non
                risponde entro ``timeout`` secondi.
        """
        if self.is_running and self._base_url:
            return self._base_url

        # Un'Ollama già attiva (es. installata dall'utente)? Riusala.
        external = default_base_url()
        if _probe(external):
            log.info("Riuso istanza Ollama già attiva su %s", external)
            self._base_url = external.rstrip("/")
            self._owns_process = False
            self._proc = None
            return self._base_url

        binary = find_ollama_binary()
        if binary is None:
            raise OllamaEmbeddedError(
                "Binario Ollama non trovato. Esegui "
                "`python scripts/fetch_ollama_binaries.py` per scaricarlo "
                "in bin/, oppure installa Ollama da https://ollama.com"
            )

        port = DEFAULT_PORT if _port_free(DEFAULT_PORT) else _free_port()
        self._base_url = f"http://127.0.0.1:{port}"

        env = os.environ.copy()
        env["OLLAMA_HOST"] = f"127.0.0.1:{port}"
        env["OLLAMA_MODELS"] = str(self.models_dir)
        env.setdefault("OLLAMA_CONTEXT_LENGTH", "8192")

        self.models_dir.mkdir(parents=True, exist_ok=True)
        log_path = app_data_dir() / "ollama.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = open(log_path, "ab")

        log.info("Avvio motore Ollama embedded: %s serve (porta %d)", binary, port)
        try:
            self._proc = subprocess.Popen(  # noqa: S603
                [str(binary), "serve"],
                env=env,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            self._cleanup()
            raise OllamaEmbeddedError(f"Impossibile avviare {binary}: {exc}") from exc

        self._owns_process = True

        if not self.wait_ready(timeout):
            log_path_str = str(log_path)
            self.stop()
            raise OllamaEmbeddedError(
                f"Il server Ollama non ha risposto entro {timeout:.0f} s. "
                f"Log: {log_path_str}"
            )
        log.info(
            "Motore Ollama pronto su %s (modelli: %s)", self._base_url, self.models_dir
        )
        return self._base_url

    def wait_ready(self, timeout: float = DEFAULT_START_TIMEOUT) -> bool:
        """Attende (senza bloccare per più di ``timeout``) che /api/tags risponda."""
        if self._base_url is None:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                return False  # il processo è terminato prematuramente
            if _probe(self._base_url, timeout=1.0):
                return True
            time.sleep(0.25)
        return False

    def stop(self) -> None:
        """Termina il processo figlio (se avviato da noi) e rilascia le risorse."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            log.info("Arresto del motore Ollama embedded (pid %s)", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.warning("Il processo Ollama non termina: kill forzato")
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        self._cleanup()

    def _cleanup(self) -> None:
        self._proc = None
        self._owns_process = False
        self._base_url = None
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

    # -------------------------------------------------------------- modelli

    def available_models(self, timeout: float = 5.0) -> List[str]:
        """Elenco dei modelli installati sul server attivo (GET /api/tags)."""
        if self._base_url is None:
            return []
        response = requests.get(self._base_url + TAGS_ENDPOINT, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        return [m.get("name") for m in data.get("models", []) if m.get("name")]

    def available_models_info(self, timeout: float = 5.0) -> List[Dict[str, Any]]:
        """Informazioni ricche dei modelli installati (GET /api/tags).

        Ogni elemento contiene ``name``, ``size_bytes`` (dimensione su disco),
        ``parameter_size`` e ``quantization`` (dagli eventuali ``details``).
        """
        if self._base_url is None:
            return []
        response = requests.get(self._base_url + TAGS_ENDPOINT, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        info: List[Dict[str, Any]] = []
        for entry in data.get("models", []):
            if not entry.get("name"):
                continue
            details = entry.get("details") or {}
            info.append({
                "name": entry.get("name"),
                "size_bytes": entry.get("size"),
                "parameter_size": details.get("parameter_size"),
                "quantization": details.get("quantization_level"),
            })
        return info

    def model_status(self, model: str, timeout: float = 5.0) -> Dict[str, Any]:
        """Stato di un modello sul server attivo.

        Returns:
            Dict con ``installed`` (bool), ``size_bytes``, ``size_human``,
            ``parameter_size`` e ``quantization`` (None se non installato o
            se il server non è raggiungibile).
        """
        try:
            models = self.available_models_info(timeout=timeout)
        except (requests.exceptions.RequestException, ValueError):
            models = []
        for entry in models:
            name = entry.get("name", "")
            if name == model or name.split(":")[0] == model.split(":")[0]:
                size_bytes = entry.get("size_bytes")
                return {
                    "installed": True,
                    "size_bytes": size_bytes,
                    "size_human": human_size(size_bytes),
                    "parameter_size": entry.get("parameter_size"),
                    "quantization": entry.get("quantization"),
                }
        return {
            "installed": False,
            "size_bytes": None,
            "size_human": None,
            "parameter_size": None,
            "quantization": None,
        }

    def has_model(self, model: str, timeout: float = 5.0) -> bool:
        """True se ``model`` è installato (confronto anche senza tag esplicito)."""
        try:
            names = self.available_models(timeout=timeout)
        except (requests.exceptions.RequestException, ValueError):
            return False
        for name in names:
            if name == model or name.split(":")[0] == model.split(":")[0]:
                return True
        return False

    def ensure_model(
        self,
        model: str,
        progress_cb: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
        timeout: float = 3600.0,
    ) -> None:
        """Assicura che ``model`` sia installato, scaricandolo se necessario.

        Il download usa ``POST /api/pull`` in streaming: ogni riga JSON contiene
        ``status`` e, durante il download, ``completed``/``total`` da cui si
        ricava la percentuale da mostrare nella UI.

        Raises:
            OllamaEmbeddedError: se il pull fallisce o viene annullato.
        """
        if self._base_url is None:
            raise OllamaEmbeddedError("Il motore non è avviato: chiama prima start().")
        if self.has_model(model):
            if progress_cb:
                progress_cb(f"{model} già installato", 100.0)
            return

        if progress_cb:
            progress_cb(f"Download di {model}…", 0.0)

        try:
            response = requests.post(
                self._base_url + PULL_ENDPOINT,
                json={"model": model, "stream": True},
                stream=True,
                timeout=timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise OllamaEmbeddedError(f"Download di {model} fallito: {exc}") from exc

        if response.status_code != 200:
            detail = response.text[:200]
            raise OllamaEmbeddedError(
                f"Download di {model}: HTTP {response.status_code} {detail}"
            )

        try:
            for raw_line in response.iter_lines():
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaEmbeddedError(f"Download di {model} annullato.")
                if not raw_line:
                    continue
                try:
                    payload: Dict = json.loads(raw_line.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                self._handle_pull_event(model, payload, progress_cb)
        except requests.exceptions.RequestException as exc:
            raise OllamaEmbeddedError(f"Download di {model} interrotto: {exc}") from exc

        if not self.has_model(model):
            raise OllamaEmbeddedError(
                f"Download di {model} terminato ma il modello non risulta installato."
            )

    def _handle_pull_event(
        self,
        model: str,
        payload: Dict,
        progress_cb: Optional[ProgressCallback],
    ) -> None:
        """Processa un evento JSON dello stream di pull e notifica la UI."""
        if payload.get("error"):
            message = str(payload["error"])
            if "file does not exist" in message or "not found" in message:
                raise OllamaEmbeddedError(
                    f"Il modello '{model}' non è stato trovato su ollama.com. "
                    "Controlla il nome: deve essere esattamente come compare "
                    "nella libreria (es. qwen2.5:0.5b)."
                )
            raise OllamaEmbeddedError(f"Download di {model} fallito: {message}")
        status = str(payload.get("status", ""))
        total = payload.get("total")
        completed = payload.get("completed")
        percent: Optional[float] = None
        if isinstance(total, int) and isinstance(completed, int) and total > 0:
            percent = round(completed / total * 100.0, 1)
        log.debug("pull %s: %s (%s%%)", model, status, percent)
        if progress_cb:
            progress_cb(status or f"Download di {model}…", percent)


_manager: Optional[EmbeddedOllamaManager] = None
_atexit_registered = False


def get_ollama_manager() -> EmbeddedOllamaManager:
    """Restituisce il manager singleton, registrando la chiusura pulita."""
    global _manager, _atexit_registered
    if _manager is None:
        _manager = EmbeddedOllamaManager()
    if not _atexit_registered:
        atexit.register(_manager.stop)
        _atexit_registered = True
    return _manager
