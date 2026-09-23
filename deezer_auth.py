"""Deezer authentication, stream acquisition, and Blowfish decryption.

Handles session initialization using the user's ARL cookie, querying
internal stream endpoints, and decrypting BF_CBC_STRIPE audio chunks
into full FLAC or MP3 files.
"""
from __future__ import annotations

import json
import re
import time
from binascii import a2b_hex, b2a_hex
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests
from Crypto.Cipher import Blowfish
from Crypto.Hash import MD5

from app_config import APP_VERSION

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

STATIC_BLOWFISH_KEY = b"g4el58wc0zvf9na1"
BLOWFISH_IV = a2b_hex("0001020304050607")
CHUNK_SIZE = 2048


class DeezerAuthError(RuntimeError):
    pass


class DeezerStreamError(RuntimeError):
    pass


def calc_blowfish_key(song_id: str | int) -> str:
    """Calculate the Blowfish decryption key for a given song ID."""
    sid_bytes = str(song_id).encode("ascii")
    h = MD5.new()
    h.update(sid_bytes)
    sid_md5 = b2a_hex(h.digest())

    xor_op = lambda i: chr(sid_md5[i] ^ sid_md5[i + 16] ^ STATIC_BLOWFISH_KEY[i])
    return "".join(xor_op(i) for i in range(16))


def blowfish_decrypt_block(data: bytes, key: str) -> bytes:
    """Decrypt a 2048-byte audio block using Blowfish CBC."""
    cipher = Blowfish.new(key.encode("ascii"), Blowfish.MODE_CBC, BLOWFISH_IV)
    return cipher.decrypt(data)


def create_deezer_session(arl: str | None = None) -> requests.Session:
    """Create a configured requests Session for Deezer API calls."""
    session = requests.Session()
    session.headers.update({
        "Origin": "https://www.deezer.com",
        "Referer": "https://www.deezer.com/",
        "User-Agent": USER_AGENT,
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    if arl and arl.strip():
        session.cookies.update({
            "arl": arl.strip(),
            "comeback": "1",
        })
    return session


def validate_deezer_arl(arl: str, session: requests.Session | None = None) -> dict:
    """Validate a Deezer ARL cookie and return account information.

    Returns dict with keys:
        valid (bool), user_id (int), user_name (str), plan (str),
        lossless (bool), high_quality (bool), license_token (str), error (str | None)
    """
    clean_arl = str(arl or "").strip()
    if not clean_arl:
        return {
            "valid": False,
            "user_id": 0,
            "user_name": "",
            "plan": "Não autenticado",
            "lossless": False,
            "high_quality": False,
            "license_token": "",
            "error": "Nenhum cookie ARL fornecido.",
        }

    sess = session or create_deezer_session(clean_arl)
    url = "https://www.deezer.com/ajax/gw-light.php?method=deezer.getUserData&input=3&api_version=1.0&api_token="
    try:
        response = sess.get(url, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return {
            "valid": False,
            "user_id": 0,
            "user_name": "",
            "plan": "Erro de conexão",
            "lossless": False,
            "high_quality": False,
            "license_token": "",
            "error": f"Falha de conexão com a Deezer: {exc}",
        }

    results = payload.get("results") or {}
    user = results.get("USER") or {}
    user_id = int(user.get("USER_ID") or 0)
    user_name = str(user.get("BLOG_NAME") or user.get("nickname") or f"Usuário {user_id}").strip()
    options = user.get("OPTIONS") or {}
    license_token = str(options.get("license_token") or "")
    web_sound_quality = options.get("web_sound_quality") or {}
    lossless = bool(web_sound_quality.get("lossless"))
    high_quality = bool(web_sound_quality.get("high") or options.get("can_stream_hq"))

    if user_id > 0 and license_token:
        plan_name = "HiFi / Lossless (FLAC)" if lossless else ("Premium (MP3 320k)" if high_quality else "Gratuito (MP3 128k)")
        return {
            "valid": True,
            "user_id": user_id,
            "user_name": user_name,
            "plan": plan_name,
            "lossless": lossless,
            "high_quality": high_quality,
            "license_token": license_token,
            "error": None,
        }

    return {
        "valid": False,
        "user_id": 0,
        "user_name": "",
        "plan": "Cookie inválido",
        "lossless": False,
        "high_quality": False,
        "license_token": "",
        "error": "O cookie ARL informado é inválido ou expirou.",
    }


def fetch_track_token(track_id: str | int, session: requests.Session) -> dict:
    """Fetch track token and metadata from the Deezer track web page."""
    url = f"https://www.deezer.com/us/track/{track_id}"
    response = session.get(url, timeout=20)
    response.raise_for_status()

    match = re.search(
        r"window\.__DZR_APP_STATE__\s*=\s*({.*?})\s*(?:;|</script>)",
        response.text,
        re.DOTALL,
    )
    if not match:
        raise DeezerStreamError(f"Não foi possível extrair os dados da faixa {track_id}.")

    try:
        app_state = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise DeezerStreamError(f"Resposta inválida para a faixa {track_id}.") from exc

    data = app_state.get("DATA") or {}
    track_token = str(data.get("TRACK_TOKEN") or "").strip()
    sng_id = str(data.get("SNG_ID") or track_id).strip()

    if not track_token:
        raise DeezerStreamError(
            f"A faixa {track_id} não possui token de reprodução disponível nesta região/conta."
        )

    return {
        "track_token": track_token,
        "song_id": sng_id,
        "media_version": data.get("MEDIA_VERSION"),
        "filesize_flac": data.get("FILESIZE_FLAC"),
        "filesize_mp3_320": data.get("FILESIZE_MP3_320"),
        "filesize_mp3_128": data.get("FILESIZE_MP3_128"),
        "raw_data": data,
    }


def resolve_sound_format(user_info: dict, quality_preference: str = "auto") -> tuple[str, str]:
    """Return (sound_format, file_extension) based on account tier and preference."""
    pref = str(quality_preference or "auto").strip().lower()
    lossless_allowed = bool(user_info.get("lossless"))
    high_allowed = bool(user_info.get("high_quality") or lossless_allowed)

    if pref in {"flac", "lossless"}:
        if lossless_allowed:
            return "FLAC", "flac"
        if high_allowed:
            return "MP3_320", "mp3"
        return "MP3_128", "mp3"

    if pref in {"mp3_320", "320", "320 kbps"}:
        if high_allowed:
            return "MP3_320", "mp3"
        return "MP3_128", "mp3"

    if pref in {"mp3_128", "128", "128 kbps"}:
        return "MP3_128", "mp3"

    # "auto" (default): highest available for user's subscription
    if lossless_allowed:
        return "FLAC", "flac"
    if high_allowed:
        return "MP3_320", "mp3"
    return "MP3_128", "mp3"


def request_media_url(
    track_token: str,
    license_token: str,
    sound_format: str,
    session: requests.Session,
) -> str:
    """Request the direct CDN stream URL from Deezer media API."""
    url = "https://media.deezer.com/v1/get_url"
    payload = {
        "license_token": license_token,
        "media": [{
            "type": "FULL",
            "formats": [{"cipher": "BF_CBC_STRIPE", "format": sound_format}],
        }],
        "track_tokens": [track_token],
    }

    response = session.post(url, json=payload, timeout=20)
    response.raise_for_status()
    data = response.json()

    items = data.get("data") or []
    if not items:
        raise DeezerStreamError("A Deezer não retornou dados de mídia para a faixa.")

    first = items[0]
    errors = first.get("errors")
    if errors:
        msg = errors[0].get("message") if isinstance(errors, list) and errors else str(errors)
        raise DeezerStreamError(f"Erro da API de mídia Deezer: {msg}")

    media_list = first.get("media") or []
    if not media_list:
        raise DeezerStreamError("Nenhuma fonte de mídia retornada para este formato.")

    sources = media_list[0].get("sources") or []
    if not sources or not sources[0].get("url"):
        raise DeezerStreamError("Endereço de download não encontrado na resposta da Deezer.")

    return sources[0]["url"]


def download_and_decrypt_track(
    track_id: str | int,
    output_path: Path,
    arl: str,
    *,
    quality_preference: str = "auto",
    session: requests.Session | None = None,
    progress_callback: Callable[[int, int | None, float | None], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> Path:
    """Download a complete Deezer track, decrypting Blowfish blocks on the fly."""
    output_path = Path(output_path)
    sess = session or create_deezer_session(arl)

    # 1. Validate / retrieve account license
    account_info = validate_deezer_arl(arl, session=sess)
    if not account_info.get("valid"):
        raise DeezerAuthError(account_info.get("error") or "ARL inválido.")

    license_token = account_info["license_token"]
    sound_format, ext = resolve_sound_format(account_info, quality_preference)

    # 2. Get track token and metadata
    token_info = fetch_track_token(track_id, sess)
    track_token = token_info["track_token"]
    sng_id = token_info["song_id"]

    # 3. Get stream URL (with fallback to lower format if FLAC is unavailable for this specific track)
    try:
        stream_url = request_media_url(track_token, license_token, sound_format, sess)
    except DeezerStreamError as exc:
        if sound_format == "FLAC":
            # Fallback to MP3_320 or MP3_128 if FLAC isn't available for this track
            sound_format, ext = "MP3_320", "mp3"
            stream_url = request_media_url(track_token, license_token, sound_format, sess)
        else:
            raise

    # Ensure output filename matches correct extension (.flac or .mp3)
    if output_path.suffix.lower() != f".{ext}":
        output_path = output_path.with_suffix(f".{ext}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.c2part")

    # 4. Decrypt key calculation
    bf_key = calc_blowfish_key(sng_id)

    # 5. Stream, decrypt, and write
    start_time = time.monotonic()
    downloaded_bytes = 0

    with sess.get(stream_url, stream=True, timeout=30) as response:
        response.raise_for_status()
        total_header = response.headers.get("content-length")
        total_bytes = int(total_header) if total_header and total_header.isdigit() else None

        with open(temp_path, "wb") as output_file:
            block_index = 0
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if check_cancelled:
                    check_cancelled()

                if not chunk:
                    break

                # Deezer BF_CBC_STRIPE: every 3rd 2048-byte block is Blowfish encrypted
                is_encrypted = (block_index % 3 == 0) and len(chunk) == CHUNK_SIZE
                if is_encrypted:
                    chunk = blowfish_decrypt_block(chunk, bf_key)

                output_file.write(chunk)
                downloaded_bytes += len(chunk)
                block_index += 1

                if progress_callback:
                    elapsed = max(0.001, time.monotonic() - start_time)
                    speed = downloaded_bytes / elapsed
                    progress_callback(downloaded_bytes, total_bytes, speed)

    # Replace atomic
    temp_path.replace(output_path)
    return output_path
