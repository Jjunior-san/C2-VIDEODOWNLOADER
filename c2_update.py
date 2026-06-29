from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app_config import (
    APP_NAME,
    APP_VERSION,
    INSTALLER_ASSET_NAME,
    LATEST_INSTALLER_URL,
    LATEST_RELEASE_API,
    MANIFEST_URL,
)

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int], None]

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
USER_AGENT = f"C2VideoDownloader/{APP_VERSION} (+https://github.com/Jjunior-san/C2-VIDEODOWNLOADER)"

DEFAULT_MANIFEST: dict[str, Any] = {
    "schema": 1,
    "app": {
        "release_api": LATEST_RELEASE_API,
        "installer_asset": INSTALLER_ASSET_NAME,
        "installer_url": LATEST_INSTALLER_URL,
    },
    "dependencies": {
        "yt_dlp": {
            "enabled": True,
            "channel": "nightly",
            "bootstrap_url": (
                "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.exe"
            ),
            "update_interval_hours": 24,
        },
        "deno": {
            "enabled": True,
            "release_api": "https://api.github.com/repos/denoland/deno/releases/latest",
            "asset_name": "deno-x86_64-pc-windows-msvc.zip",
            "bootstrap_url": (
                "https://github.com/denoland/deno/releases/latest/download/"
                "deno-x86_64-pc-windows-msvc.zip"
            ),
            "update_interval_hours": 168,
        },
    },
}


def _local_app_data() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root)
    return Path.home() / "AppData" / "Local"


DATA_DIR = _local_app_data() / "C2 Sistemas" / "C2 Video Downloader"
RUNTIME_DIR = DATA_DIR / "runtime"
STATE_FILE = DATA_DIR / "update-state.json"


def install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def version_key(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value or "")
    return tuple(int(item) for item in numbers[:8]) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    left = version_key(candidate)
    right = version_key(current)
    size = max(len(left), len(right))
    return left + (0,) * (size - len(left)) > right + (0,) * (size - len(right))


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json, application/json;q=0.9, */*;q=0.8",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def fetch_json(url: str, timeout: int = 15) -> dict[str, Any]:
    with urllib.request.urlopen(_request(url), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_atomic(
    url: str,
    destination: Path,
    progress: ProgressCallback | None = None,
    timeout: int = 60,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".download")
    temporary.unlink(missing_ok=True)

    try:
        with urllib.request.urlopen(_request(url), timeout=timeout) as response, temporary.open("wb") as output:
            total = int(response.headers.get("Content-Length") or 0)
            received = 0
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                output.write(chunk)
                received += len(chunk)
                if progress:
                    progress(received, total)
        os.replace(temporary, destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, STATE_FILE)


def _check_due(state: dict[str, Any], key: str, interval_hours: int, force: bool) -> bool:
    if force:
        return True
    last_check = float(state.get(key, 0) or 0)
    return time.time() - last_check >= max(1, interval_hours) * 3600


def _safe_manifest(remote: dict[str, Any]) -> dict[str, Any]:
    manifest = json.loads(json.dumps(DEFAULT_MANIFEST))
    if not isinstance(remote, dict):
        return manifest

    app = remote.get("app")
    dependencies = remote.get("dependencies")
    if isinstance(app, dict):
        manifest["app"].update(app)
    if isinstance(dependencies, dict):
        for name, config in dependencies.items():
            if name in manifest["dependencies"] and isinstance(config, dict):
                manifest["dependencies"][name].update(config)
    return manifest


@dataclass(frozen=True)
class DependencyStatus:
    yt_dlp_path: Path
    yt_dlp_version: str
    deno_path: Path | None
    deno_version: str | None


@dataclass(frozen=True)
class AppUpdate:
    version: str
    tag: str
    installer_url: str
    digest: str | None
    release_url: str | None
    notes: str


class DependencyManager:
    def __init__(self) -> None:
        self.runtime_dir = RUNTIME_DIR
        self.yt_dlp_path = self.runtime_dir / "yt-dlp.exe"
        self.deno_path = self.runtime_dir / "deno.exe"
        self._lock = threading.Lock()
        self._manifest: dict[str, Any] | None = None

    def fetch_manifest(self, log: LogCallback | None = None) -> dict[str, Any]:
        try:
            separator = "&" if "?" in MANIFEST_URL else "?"
            remote = fetch_json(f"{MANIFEST_URL}{separator}t={int(time.time())}")
            self._manifest = _safe_manifest(remote)
        except Exception as exc:
            if log:
                log(f"Aviso: manifesto remoto indisponível; usando configuração interna ({exc}).")
            self._manifest = _safe_manifest({})
        return self._manifest

    def ensure(self, log: LogCallback, force: bool = False) -> DependencyStatus:
        with self._lock:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            manifest = self.fetch_manifest(log)
            state = _load_state()

            yt_config = manifest["dependencies"]["yt_dlp"]
            if bool(yt_config.get("enabled", True)):
                self._ensure_yt_dlp(yt_config, state, log, force)

            deno_config = manifest["dependencies"]["deno"]
            if bool(deno_config.get("enabled", True)):
                self._ensure_deno(deno_config, state, log, force)

            _save_state(state)

            yt_version = self._command_version([str(self.yt_dlp_path), "--version"])
            deno_version = None
            if self.deno_path.exists():
                deno_output = self._command_version([str(self.deno_path), "--version"])
                match = re.search(r"deno\s+([^\s]+)", deno_output, flags=re.IGNORECASE)
                deno_version = match.group(1) if match else deno_output.splitlines()[0].strip()

            return DependencyStatus(
                yt_dlp_path=self.yt_dlp_path,
                yt_dlp_version=yt_version.strip(),
                deno_path=self.deno_path if self.deno_path.exists() else None,
                deno_version=deno_version,
            )

    def runtime_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["PATH"] = str(self.runtime_dir) + os.pathsep + environment.get("PATH", "")
        environment["PYTHONUTF8"] = "1"
        return environment

    def _seed_path(self, filename: str) -> Path:
        return install_root() / "runtime" / filename

    def _bootstrap_file(self, filename: str, destination: Path, url: str, log: LogCallback) -> None:
        seed = self._seed_path(filename)
        if seed.exists():
            shutil.copy2(seed, destination)
            log(f"Componente inicial instalado: {filename}")
            return
        if not url.startswith("https://"):
            raise RuntimeError(f"URL de componente inválida: {url}")
        log(f"Baixando componente inicial: {filename}")
        download_atomic(url, destination)

    def _ensure_yt_dlp(
        self,
        config: dict[str, Any],
        state: dict[str, Any],
        log: LogCallback,
        force: bool,
    ) -> None:
        if not self.yt_dlp_path.exists():
            self._bootstrap_file(
                "yt-dlp.exe",
                self.yt_dlp_path,
                str(config.get("bootstrap_url") or ""),
                log,
            )

        interval = int(config.get("update_interval_hours") or 24)
        if _check_due(state, "yt_dlp_last_check", interval, force):
            channel = str(config.get("channel") or "nightly")
            log(f"Verificando atualização do mecanismo yt-dlp ({channel})...")
            command = [str(self.yt_dlp_path), "--update-to", channel]
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                timeout=180,
                env=self.runtime_environment(),
            )
            output = completed.stdout.strip()
            if output:
                for line in output.splitlines():
                    log(f"yt-dlp: {line}")
            if completed.returncode != 0:
                log("Aviso: não foi possível atualizar o yt-dlp; a versão instalada será mantida.")
            state["yt_dlp_last_check"] = time.time()

        if not self._command_version([str(self.yt_dlp_path), "--version"]):
            raise RuntimeError("O mecanismo yt-dlp não pôde ser iniciado.")

    def _ensure_deno(
        self,
        config: dict[str, Any],
        state: dict[str, Any],
        log: LogCallback,
        force: bool,
    ) -> None:
        if not self.deno_path.exists():
            seed = self._seed_path("deno.exe")
            if seed.exists():
                shutil.copy2(seed, self.deno_path)
                log("Componente inicial instalado: deno.exe")
            else:
                self._download_deno(config, log)

        interval = int(config.get("update_interval_hours") or 168)
        if not _check_due(state, "deno_last_check", interval, force):
            return

        try:
            release_api = str(config.get("release_api") or "")
            latest = fetch_json(release_api) if release_api.startswith("https://") else {}
            latest_version = str(latest.get("tag_name") or "").lstrip("v")
            installed_output = self._command_version([str(self.deno_path), "--version"])
            match = re.search(r"deno\s+([^\s]+)", installed_output, flags=re.IGNORECASE)
            installed_version = match.group(1) if match else "0"
            if latest_version and is_newer(latest_version, installed_version):
                log(f"Atualizando Deno {installed_version} para {latest_version}...")
                self._download_deno(config, log, release=latest)
            else:
                log(f"Deno já está atualizado ({installed_version}).")
        except Exception as exc:
            log(f"Aviso: não foi possível atualizar o Deno ({exc}).")
        finally:
            state["deno_last_check"] = time.time()

    def _download_deno(
        self,
        config: dict[str, Any],
        log: LogCallback,
        release: dict[str, Any] | None = None,
    ) -> None:
        asset_name = str(config.get("asset_name") or "deno-x86_64-pc-windows-msvc.zip")
        download_url = str(config.get("bootstrap_url") or "")
        digest = None

        if release:
            for asset in release.get("assets") or []:
                if asset.get("name") == asset_name:
                    download_url = str(asset.get("browser_download_url") or download_url)
                    raw_digest = str(asset.get("digest") or "")
                    if raw_digest.startswith("sha256:"):
                        digest = raw_digest.split(":", 1)[1].lower()
                    break

        if not download_url.startswith("https://"):
            raise RuntimeError("URL de download do Deno inválida.")

        archive = self.runtime_dir / "deno-update.zip"
        extract_dir = self.runtime_dir / "deno-update"
        log("Baixando runtime JavaScript Deno...")
        download_atomic(download_url, archive)
        if digest and sha256_file(archive).lower() != digest:
            archive.unlink(missing_ok=True)
            raise RuntimeError("Falha na validação SHA-256 do Deno.")

        shutil.rmtree(extract_dir, ignore_errors=True)
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as package:
            package.extractall(extract_dir)
        extracted = next(extract_dir.rglob("deno.exe"), None)
        if not extracted:
            raise RuntimeError("deno.exe não encontrado no pacote baixado.")
        temporary = self.deno_path.with_suffix(".exe.new")
        shutil.copy2(extracted, temporary)
        os.replace(temporary, self.deno_path)
        archive.unlink(missing_ok=True)
        shutil.rmtree(extract_dir, ignore_errors=True)
        log("Deno atualizado com sucesso.")

    @staticmethod
    def _command_version(command: list[str]) -> str:
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                timeout=30,
            )
            return completed.stdout.strip() if completed.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""


class ApplicationUpdater:
    def __init__(self, dependency_manager: DependencyManager) -> None:
        self.dependencies = dependency_manager

    def check(self, log: LogCallback | None = None) -> AppUpdate | None:
        manifest = self.dependencies.fetch_manifest(log)
        app_config = manifest.get("app") or {}
        release_api = str(app_config.get("release_api") or LATEST_RELEASE_API)
        installer_asset = str(app_config.get("installer_asset") or INSTALLER_ASSET_NAME)
        installer_url = str(app_config.get("installer_url") or LATEST_INSTALLER_URL)

        release = fetch_json(release_api)
        tag = str(release.get("tag_name") or "")
        latest_version = tag.lstrip("vV")
        if not latest_version or not is_newer(latest_version, APP_VERSION):
            return None

        digest = None
        for asset in release.get("assets") or []:
            if asset.get("name") == installer_asset:
                installer_url = str(asset.get("browser_download_url") or installer_url)
                raw_digest = str(asset.get("digest") or "")
                if raw_digest.startswith("sha256:"):
                    digest = raw_digest.split(":", 1)[1].lower()
                break

        return AppUpdate(
            version=latest_version,
            tag=tag,
            installer_url=installer_url,
            digest=digest,
            release_url=release.get("html_url"),
            notes=str(release.get("body") or ""),
        )

    def download(self, update: AppUpdate, progress: ProgressCallback | None = None) -> Path:
        filename = f"C2VideoDownloaderSetup-{update.version}.exe"
        destination = Path(tempfile.gettempdir()) / filename
        download_atomic(update.installer_url, destination, progress=progress, timeout=180)
        if update.digest and sha256_file(destination).lower() != update.digest:
            destination.unlink(missing_ok=True)
            raise RuntimeError("O instalador baixado não passou na validação SHA-256.")
        return destination

    @staticmethod
    def launch_installer(installer: Path) -> None:
        if os.name != "nt":
            raise RuntimeError("A atualização automática do aplicativo requer Windows.")
        parameters = "/SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS"
        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            str(installer),
            parameters,
            str(installer.parent),
            1,
        )
        if int(result) <= 32:
            raise RuntimeError(f"O Windows não iniciou o instalador (código {result}).")
