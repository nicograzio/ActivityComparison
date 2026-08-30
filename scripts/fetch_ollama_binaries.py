"""Scarica il binario Ollama per la piattaforma corrente in ``bin/<piattaforma>/``.

Questo binario è quello che l'app avvia automaticamente come processo figlio
(vedi ``core.ollama_embedded``): messo in bundle, l'utente finale non deve
installare né avviare nulla.

Uso:
    python scripts/fetch_ollama_binaries.py                # piattaforma corrente
    python scripts/fetch_ollama_binaries.py --all          # tutte le piattaforme
    python scripts/fetch_ollama_binaries.py --tag v0.33.2  # release specifica

L'archivio ufficiale viene scaricato dalla GitHub Release di ollama/ollama,
verificato con lo SHA-256 pubblicato in ``sha256sum.txt`` e da esso viene
estratto SOLO l'eseguibile ``ollama`` (l'archivio completo contiene anche le
librerie GPU CUDA/ROCm, non necessarie per l'inferenza su CPU).
"""

import argparse
import hashlib
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import requests

REPO = "ollama/ollama"
RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"

# asset ufficiale, nome dell'eseguibile dentro l'archivio, cartella di destinazione
PLATFORMS = {
    "macos": {"asset": "ollama-darwin.tgz", "binary": "ollama", "kind": "targz"},
    "windows": {"asset": "ollama-windows-amd64.zip", "binary": "ollama.exe", "kind": "zip"},
    "linux": {"asset": "ollama-linux-amd64.tar.zst", "binary": "ollama", "kind": "tarzst"},
}


def _current_platform() -> str:
    """Traduce ``platform.system()`` nella chiave di ``PLATFORMS``."""
    return {"Darwin": "macos", "Windows": "windows"}.get(platform.system(), "linux")


def _resolve_tag(tag: str) -> str:
    """Converte ``latest`` nel tag reale dell'ultima release."""
    if tag != "latest":
        return tag
    response = requests.get(RELEASE_API, timeout=30)
    response.raise_for_status()
    return response.json()["tag_name"]


def _download(url: str, dest: Path) -> None:
    """Scarica ``url`` su ``dest`` in streaming con progresso su stdout."""
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        done = 0
        with open(dest, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                handle.write(chunk)
                done += len(chunk)
                if total:
                    percent = done / total * 100.0
                    print(f"\r  {done / 1e6:8.1f} / {total / 1e6:.1f} MB ({percent:4.1f}%)",
                          end="", flush=True)
    print()


def _sha256_of(path: Path) -> str:
    """Calcola lo SHA-256 esadecimale di un file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_sha256(tag: str, asset: str, archive_path: Path, token: str = "") -> None:
    """Confronta lo SHA-256 dell'archivio con quello pubblicato nella release."""
    url = f"https://github.com/{REPO}/releases/download/{tag}/sha256sum.txt"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    expected = ""
    for line in response.text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip("*") == asset:
            expected = parts[0].lower()
            break
    if not expected:
        print(f"  ⚠️  sha256sum.txt non contiene {asset}: verifica saltata")
        return
    actual = _sha256_of(archive_path)
    if actual != expected:
        raise SystemExit(f"  ❌ SHA-256 non corrisponde per {asset}: {actual} != {expected}")
    print(f"  ✅ SHA-256 verificato ({actual[:16]}…)")


def _extract_binary(archive_path: Path, kind: str, binary_name: str, out: Path) -> None:
    """Estrae solo l'eseguibile ``binary_name`` dall'archivio scaricato."""
    out.parent.mkdir(parents=True, exist_ok=True)

    if kind == "targz":
        with tarfile.open(archive_path, "r:gz") as archive:
            members = [m for m in archive.getmembers()
                       if m.isfile() and Path(m.name).name == binary_name]
            if not members:
                raise SystemExit(f"  ❌ {binary_name} non trovato dentro l'archivio")
            # Preferisce il percorso più corto (radice dell'archivio).
            members.sort(key=lambda m: len(m.name))
            src = archive.extractfile(members[0])
            out.write_bytes(src.read())
    elif kind == "zip":
        with zipfile.ZipFile(archive_path) as archive:
            names = [n for n in archive.namelist()
                     if Path(n).name == binary_name]
            if not names:
                raise SystemExit(f"  ❌ {binary_name} non trovato dentro l'archivio")
            names.sort(key=lambda n: len(n))
            out.write_bytes(archive.read(names[0]))
    elif kind == "tarzst":
        # zstd non è gestito da tarfile: serve tar di sistema con supporto zstd.
        tar = shutil.which("tar")
        if tar is None:
            raise SystemExit("  ❌ Serve `tar` per estrarre l'archivio .tar.zst")
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(  # noqa: S603
                [tar, "--zstd", "-xf", str(archive_path), "-C", tmp],
                check=True,
            )
            matches = [p for p in Path(tmp).rglob(binary_name) if p.is_file()]
            if not matches:
                raise SystemExit(f"  ❌ {binary_name} non trovato dentro l'archivio")
            matches.sort(key=lambda p: len(str(p)))
            shutil.copy2(matches[0], out)
    else:  # pragma: no cover
        raise SystemExit(f"  ❌ tipo archivio sconosciuto: {kind}")

    if platform.system() != "Windows":
        out.chmod(0o755)
    print(f"  ✅ Binario estratto: {out} ({out.stat().st_size / 1e6:.1f} MB)")


def fetch(platform_key: str, tag: str) -> None:
    """Scarica, verifica ed estrae il binario per una piattaforma."""
    spec = PLATFORMS[platform_key]
    dest = Path(__file__).resolve().parent.parent / "bin" / platform_key / spec["binary"]

    if dest.is_file():
        print(f"[{platform_key}] binario già presente: {dest}")
        return

    tag = _resolve_tag(tag)
    url = f"https://github.com/{REPO}/releases/download/{tag}/{spec['asset']}"
    print(f"[{platform_key}] release {tag} — {spec['asset']}")

    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / spec["asset"]
        print("  download in corso…")
        _download(url, archive_path)
        _verify_sha256(tag, spec["asset"], archive_path)
        _extract_binary(archive_path, spec["kind"], spec["binary"], dest)


def main() -> None:
    """Punto di ingresso dello script di fetch."""
    parser = argparse.ArgumentParser(description=__doc__)
    choices = [*PLATFORMS, "all"]
    parser.add_argument(
        "--platform",
        choices=choices,
        default=_current_platform(),
        help="piattaforma di destinazione (default: piattaforma corrente)",
    )
    parser.add_argument("--tag", default="latest", help="release Ollama (default: latest)")
    args = parser.parse_args()

    targets = list(PLATFORMS) if args.platform == "all" else [args.platform]
    for platform_key in targets:
        fetch(platform_key, args.tag)
    print("\nFatto. L'app troverà il binario in bin/<piattaforma>/ al prossimo avvio.")


if __name__ == "__main__":
    main()
