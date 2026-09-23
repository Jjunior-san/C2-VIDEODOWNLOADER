"""Discovery and per-video execution, independent of Tk variables and widgets."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlparse

from c2_update import CREATE_NO_WINDOW
from audio_library import (
    apply_deezer_metadata,
    bitrate_from_options,
    create_collection_zip,
    create_deezer_playlists,
    is_audio_format,
    music_output_template,
    music_target_folder,
)
from deezer_auth import DeezerAuthError, DeezerStreamError, download_and_decrypt_track
from deezer_catalog import is_deezer_url, resolve_deezer_track, resolve_deezer_url, search_deezer_tracks
from download_control import DownloadCancelled, DownloadSkipped
from download_queue import RUNNABLE, queue_item
from jw_org_downloader import is_jw_category_url, resolve_category_items, download_item, convert_to_audio
from kanald_downloader import is_kanald_collection_url, is_kanald_url, resolve_kanald_collection, resolve_kanald_video
from process_monitor import ProcessInactivityError, close_process, monitored_lines


METADATA_INACTIVITY_SECONDS = 60
METADATA_FIELDS = "%(.{id,title,webpage_url,original_url,url,availability,_type,duration,playlist_title,entries})j"


def cookie_arguments(options):
    args = []
    browser = options.get("cookies_browser", "Nenhum").strip().lower()
    if browser and browser != "nenhum":
        args += ["--cookies-from-browser", browser]
    if options.get("cookies_file"):
        args += ["--cookies", options["cookies_file"]]
    return args


def _read_metadata_once(engine, source, options, control, environment, log):
    command = [str(engine), "--ignore-config", "--no-abort-on-error", "--flat-playlist",
               "--skip-download", "--no-color", "--encoding", "utf-8", "--no-quiet",
               "--socket-timeout", "20", "--extractor-retries", "2",
               "--remote-components", "ejs:github", "--print", METADATA_FIELDS,
               "--yes-playlist" if options["playlist"] else "--no-playlist",
               *cookie_arguments(options), "--", source]
    deno = Path(engine).with_name("deno.exe")
    if deno.is_file():
        option_boundary = command.index("--")
        command[option_boundary:option_boundary] = ["--js-runtimes", f"deno:{deno}"]
    process = control.popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=CREATE_NO_WINDOW, env=environment)
    results = []
    errors = []
    try:
        for line in monitored_lines(process, control, METADATA_INACTIVITY_SECONDS):
            cleaned = line.rstrip()
            if cleaned.startswith("{"):
                result = json.loads(cleaned)
                if isinstance(result, dict):
                    results.append(result)
            else:
                log(cleaned)
                if "ERROR:" in line:
                    errors.append(line.strip())
        control.checkpoint()
        process.wait(timeout=10)
        if not results:
            raise RuntimeError(errors[-1] if errors else "Não foi possível listar os vídeos deste link.")
        return results[0] if len(results) == 1 else {"_type": "playlist", "entries": results}
    finally:
        close_process(process, control)


def read_metadata(engine, source, options, control, environment, log):
    for attempt in range(2):
        try:
            return _read_metadata_once(engine, source, options, control, environment, log)
        except ProcessInactivityError:
            if attempt:
                raise RuntimeError(
                    "O YouTube não respondeu após duas tentativas. Tente novamente em alguns minutos.",
                )
            log("YouTube sem resposta; reiniciando a análise automaticamente (1/1)...")


def metadata_items(info: dict, source: str) -> list[dict]:
    result = []

    def visit(entry, fallback):
        if not isinstance(entry, dict):
            return
        if entry.get("_type") in {"playlist", "multi_video"} or "entries" in entry:
            for child in entry.get("entries") or []:
                visit(child, "")
            return
        url = entry.get("webpage_url") or entry.get("url") or fallback
        title = entry.get("title") or entry.get("id") or url or "Vídeo indisponível"
        # Only retain stable page links. Never persist formats, cookies or headers.
        item = queue_item(str(url), str(title), media_id=entry.get("id"),
                          duration=entry.get("duration"), playlist_title=entry.get("playlist_title"),
                          quality="A definir")
        if not url or entry.get("availability") == "private" or title in {"[Private video]", "[Deleted video]"}:
            item.update(status="skipped", enabled=False, error="Item privado ou indisponível na playlist.")
        result.append(item)

    visit(info, source)
    return result



def _deezer_queue_items(tracks, collection_title: str, options) -> list[dict]:
    items = []
    has_arl = bool(str(options.get("deezer_arl") or "").strip())
    quality_pref = str(options.get("deezer_quality") or "auto").strip().lower()

    for position, track in enumerate(tracks, 1):
        if has_arl:
            kind = "deezer_full"
            display_title = track.display_title
            if quality_pref in {"flac", "lossless"}:
                quality_label = "Deezer HiFi (FLAC)"
            elif quality_pref in {"mp3_320", "320"}:
                quality_label = "Deezer 320 kbps"
            elif quality_pref in {"mp3_128", "128"}:
                quality_label = "Deezer 128 kbps"
            else:
                quality_label = "Deezer Completo"
        else:
            kind = "deezer_preview"
            display_title = f"{track.display_title} (prévia Deezer)"
            quality_label = (
                f"Prévia oficial • {options['format']}"
                if is_audio_format(options["format"])
                else "Prévia oficial MP3"
            )

        item = queue_item(
            track.page_url,
            display_title,
            kind=kind,
            media_id=track.track_id,
            track_title=track.title,
            artist=track.artist,
            album=track.album,
            track_number=track.track_number or position,
            disc_number=track.disc_number,
            release_year=track.release_year,
            duration=track.duration,
            cover_url=track.cover_url,
            collection_title=collection_title,
            quality=quality_label,
        )
        if not has_arl and not track.preview_url:
            item.update(
                status="skipped", enabled=False,
                error="A Deezer não disponibilizou uma prévia pública para esta faixa.",
            )
        items.append(item)
    return items

def discover(sources, options, engine, control, environment, log):
    items = []
    for source in sources:
        control.checkpoint()
        try:
            music_mode = str(options.get("work_mode") or "").lower() == "music"
            parsed_source = urlparse(source.strip())
            plain_music_search = (
                music_mode
                and not parsed_source.scheme
                and not parsed_source.netloc
                and not source.lower().startswith("deezer:")
            )

            if is_deezer_url(source):
                if bool(str(options.get("deezer_arl") or "").strip()):
                    log("Deezer: autenticado com ARL; faixas completas serão incluídas na fila.")
                else:
                    log("Deezer: consultando o catálogo público; somente prévias oficiais serão incluídas.")
                collection = resolve_deezer_url(source)
                tracks = collection.tracks if options["playlist"] else collection.tracks[:1]
                items.extend(_deezer_queue_items(tracks, collection.title, options))
            elif source.lower().startswith("deezer:") or plain_music_search:
                query = source.split(":", 1)[1].strip() if source.lower().startswith("deezer:") else source.strip()
                if len(query) < 2:
                    raise RuntimeError("Digite ao menos dois caracteres para pesquisar artista ou música.")
                log(f"Deezer: pesquisando no catálogo por '{query}'.")
                limit = 25 if options["playlist"] else 1
                tracks = search_deezer_tracks(query, limit=limit)
                if not tracks:
                    raise RuntimeError(f"Nenhum resultado foi encontrado para '{query}' na Deezer.")
                items.extend(_deezer_queue_items(tracks, f"Pesquisa Deezer - {query}", options))
            elif is_jw_category_url(source):
                for media in resolve_category_items(source, options["format"], include_subcategories=options["playlist"], logger=log):
                    items.append(queue_item(source, media.title, kind="jw", media_id=media.media_id,
                                            quality=f"{media.height}p" if media.height else media.source_kind))
            elif is_kanald_collection_url(source):
                episodes = resolve_kanald_collection(source).episode_urls
                if not options["playlist"]:
                    episodes = episodes[:1]
                for episode in episodes:
                    title = unquote(urlparse(episode).path.rsplit("/", 1)[-1]).replace("-", " ").title()
                    items.append(queue_item(episode, title, kind="kanald", quality="A definir"))
            elif is_kanald_url(source):
                video = resolve_kanald_video(source)
                items.append(queue_item(source, video.title, kind="kanald", media_id=video.media_id, quality="A definir"))
            else:
                items.extend(metadata_items(read_metadata(engine, source, options, control, environment, log), source))
        except DownloadCancelled:
            raise
        except Exception as exc:
            log(f"Não foi possível analisar {source}: {exc}")
            item = queue_item(source, source, kind="unresolved", quality="—")
            item.update(status="failed", enabled=False, error=f"Analise este link novamente: {exc}")
            items.append(item)
    # De-duplicate stable source/ID combinations, without changing playlist order.
    unique = {}
    for item in items:
        if item.get("quality") == "A definir":
            item["quality"] = options["format"]
        unique.setdefault((item["kind"], item["source"], item.get("media_id")), item)
    return list(unique.values())


def filename_template(item, index):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", item["title"]).strip(" .")[:140] or "Vídeo"
    identifier = re.sub(r'[^\w.-]', "_", str(item.get("media_id") or item["id"][:12]))[:60]
    return f"{index:03d} - {title} [{identifier}].%(ext)s".replace("%", "%%").replace("%%(ext)s", "%(ext)s")


def run_queue(owner, repository, options, engine):
    """One engine invocation per item makes failures, cancellation and ETA explicit."""
    owner.download_completed_files = 0
    selected_bitrate = bitrate_from_options(options) if is_audio_format(options["format"]) else None
    jw_cache = {}
    stopped = False
    try:
        ids = [item["id"] for item in repository.snapshot()["items"]]
        for ordinal, item_id in enumerate(ids, 1):
            owner.download_control.checkpoint()
            with repository.lock:
                item = next(item for item in repository.snapshot()["items"] if item["id"] == item_id)
                if not item["enabled"] or item["status"] not in RUNNABLE:
                    continue
                owner.active_queue_id = item_id
                repository.update(item_id, status="downloading", error="")
            owner.event_queue.put(("queue_changed", None))
            try:
                owner._begin_download_item(ordinal, len(ids), item["title"])
                folder = Path(options["folder"])
                if item["kind"] in {"deezer_preview", "deezer_full"}:
                    folder = music_target_folder(
                        folder,
                        item,
                        str(options.get("music_structure") or "Pasta raiz"),
                    )
                    folder.mkdir(parents=True, exist_ok=True)
                if item["kind"] == "deezer_full":
                    arl = str(options.get("deezer_arl") or "").strip()
                    pref_quality = str(options.get("deezer_quality") or "auto")
                    template = item.get("output_template") or music_output_template(
                        item,
                        ordinal,
                        str(options.get("music_filename_template") or "{faixa:02} - {titulo}"),
                    )
                    clean_filename = template.replace(".%(ext)s", "")
                    initial_file = folder / f"{clean_filename}.audio"

                    possible_existing = [
                        folder / f"{clean_filename}.flac",
                        folder / f"{clean_filename}.mp3",
                    ]
                    existing_done = next((p for p in possible_existing if p.is_file() and p.stat().st_size > 0), None)
                    if existing_done:
                        owner.queue_log(f"Deezer: arquivo já concluído: {existing_done.name}")
                        output = existing_done
                    else:
                        owner.queue_log(f"Deezer: baixando faixa completa autenticada ({item['title']})...")

                        def _deezer_progress(received, total, speed):
                            pct = (received / total * 100) if total else 0.0
                            owner._report_direct_progress(
                                current_received=received,
                                current_total=total or received,
                                speed_bps=speed or 0.0,
                                current_percent=pct,
                                current_eta=((total - received) / speed) if (total and speed) else 0.0,
                                overall_percent=pct,
                                status_text="Baixando e decifrando Deezer...",
                            )

                        output = download_and_decrypt_track(
                            track_id=str(item.get("media_id") or ""),
                            output_path=initial_file,
                            arl=arl,
                            quality_preference=pref_quality,
                            progress_callback=_deezer_progress,
                            check_cancelled=owner.download_control.checkpoint,
                        )

                    repository.update(item_id, status="finalizing")
                    owner.event_queue.put(("queue_changed", None))
                    apply_deezer_metadata(output, item, owner.queue_log)
                    files = [str(output)]
                elif item["kind"] == "jw":
                    key = item["source"]
                    if key not in jw_cache:
                        jw_cache[key] = resolve_category_items(key, options["format"], include_subcategories=options["playlist"], logger=owner.queue_log)
                    owner.download_control.checkpoint()
                    media = next((media for media in jw_cache[key] if media.media_id == item.get("media_id")), None)
                    if media is None:
                        repository.update(item_id, status="skipped", error="Vídeo não está mais disponível na categoria.")
                        continue
                    media = replace(media, title=item["title"])
                    output = download_item(media, folder, ordinal, len(ids), logger=owner.queue_log, progress=owner._report_direct_progress)
                    repository.update(item_id, status="finalizing")
                    owner.event_queue.put(("queue_changed", None))
                    if is_audio_format(options["format"]):
                        output = convert_to_audio(
                            output, options["format"], owner.ffmpeg_path,
                            bitrate_kbps=selected_bitrate,
                            logger=owner.queue_log, control=owner.download_control,
                            progress=lambda payload: owner.event_queue.put(("conversion_progress", payload)),
                        )
                    else:
                        output = owner._ensure_player_compatibility(output)
                    files = [str(output)]
                else:
                    url = item["source"]
                    effective_format = (
                        options["format"]
                        if item["kind"] != "deezer_preview" or is_audio_format(options["format"])
                        else "Apenas áudio (MP3)"
                    )
                    outputs = [Path(path) for path in item.get("downloaded_files", [])]
                    if outputs and all(path.is_file() and path.stat().st_size > 0 for path in outputs):
                        code = 0
                        owner.queue_log(f"Finalizando arquivo já recebido: {item['title']}")
                    else:
                        if item["kind"] == "deezer_preview":
                            owner.queue_log(
                                "Deezer: baixando somente a prévia pública oficial, sem usar credenciais de conta.",
                            )
                            track = resolve_deezer_track(str(item.get("media_id") or ""))
                            if not track.preview_url:
                                raise RuntimeError("A prévia pública desta faixa não está mais disponível.")
                            url = track.preview_url
                            refreshed = {
                                "track_title": track.title, "artist": track.artist,
                                "album": track.album, "track_number": track.track_number or item.get("track_number"),
                                "disc_number": track.disc_number, "release_year": track.release_year,
                                "duration": track.duration, "cover_url": track.cover_url,
                                "title": f"{track.display_title} (prévia Deezer)",
                            }
                            item.update(refreshed)
                            repository.update(item_id, **refreshed)
                        elif item["kind"] == "kanald":
                            video = resolve_kanald_video(url)  # Refresh expiring media URLs after reopening.
                            owner.download_control.checkpoint()
                            url = video.content_url
                            item.update(title=video.title, media_id=video.media_id)
                            repository.update(item_id, title=video.title, media_id=video.media_id)
                        if item["kind"] == "deezer_preview":
                            template = item.get("output_template") or music_output_template(
                                item,
                                ordinal,
                                str(options.get("music_filename_template") or "{faixa:02} - {titulo}"),
                            )
                        else:
                            template = item.get("output_template") or filename_template(item, ordinal)
                        repository.update(item_id, output_template=template)
                        command = owner._build_command(engine, folder, effective_format, url,
                                                        output_template=template,
                                                        include_cookies=item["kind"] not in {"kanald", "deezer_preview"},
                                                        audio_bitrate=selected_bitrate)
                        code, outputs = owner._run_downloader(command)
                    repository.update(item_id, status="finalizing", downloaded_files=[str(path) for path in outputs] if code == 0 else [])
                    owner.event_queue.put(("queue_changed", None))
                    before = owner.download_completed_files
                    final_format = effective_format if item["kind"] == "deezer_preview" else options["format"]
                    ok = owner._finalize_downloaded_files(code, outputs, final_format)
                    if not ok or not outputs:
                        raise RuntimeError("O vídeo não foi concluído. Consulte a atividade para detalhes.")
                    if item["kind"] == "deezer_preview":
                        for output in owner.finalized_files:
                            try:
                                apply_deezer_metadata(output, item, owner.queue_log)
                            except Exception as exc:
                                owner.queue_log(
                                    f"Aviso: a prévia foi salva, mas os metadados não puderam ser aplicados ({exc}).",
                                )
                    files = [str(path) for path in owner.finalized_files]
                    owner.download_completed_files = before
                with repository.lock:
                    owner.download_control.check_cancelled()
                    repository.update(item_id, status="completed", files=files, error="")
                owner.download_completed_files += 1
            except DownloadSkipped:
                repository.update(item_id, status="cancelled", error="Cancelado pelo usuário; arquivos parciais preservados.")
            except DownloadCancelled:
                repository.update(item_id, status="interrupted", error="Pronto para continuar na próxima sessão.")
                raise
            except Exception as exc:
                repository.update(item_id, status="failed", error=str(exc))
                owner.queue_log(f"Não foi possível concluir {item['title']}: {exc}. Continuando a fila.")
            finally:
                with repository.lock:
                    owner.active_queue_id = None
                    owner.download_control.finish_item()
                owner.event_queue.put(("queue_changed", None))
    except DownloadCancelled:
        stopped = True
    except Exception as exc:
        stopped = True
        owner.queue_log(f"Fila interrompida: {exc}")
    finally:
        items = repository.snapshot()["items"]
        try:
            created_playlists = create_deezer_playlists(items, Path(options["folder"]), owner.queue_log)
            if options.get("create_collection_zip"):
                create_collection_zip(items, Path(options["folder"]), playlists=created_playlists, logger=owner.queue_log)
        except OSError as exc:
            owner.queue_log(f"Aviso: não foi possível criar a playlist local ou arquivo ZIP: {exc}")
        owner.event_queue.put(("download_finished", {
            "failures": sum(item["enabled"] and item["status"] in {"failed", "skipped"} for item in items),
            "completed": sum(item["enabled"] and item["status"] == "completed" for item in items),
            "stopped": stopped,
            "cancelled": sum(item["enabled"] and item["status"] == "cancelled" for item in items),
        }))
