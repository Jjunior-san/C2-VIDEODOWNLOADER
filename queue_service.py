"""Discovery and per-video execution, independent of Tk variables and widgets."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlparse

from c2_update import CREATE_NO_WINDOW
from download_control import DownloadCancelled, DownloadSkipped
from download_queue import RUNNABLE, queue_item
from jw_org_downloader import is_jw_category_url, resolve_category_items, download_item, convert_to_m4a
from kanald_downloader import is_kanald_collection_url, is_kanald_url, resolve_kanald_collection, resolve_kanald_video


def cookie_arguments(options):
    args = []
    browser = options.get("cookies_browser", "Nenhum").strip().lower()
    if browser and browser != "nenhum":
        args += ["--cookies-from-browser", browser]
    if options.get("cookies_file"):
        args += ["--cookies", options["cookies_file"]]
    return args


def read_metadata(engine, source, options, control, environment, log):
    command = [str(engine), "--ignore-config", "--no-abort-on-error", "--flat-playlist",
               "--dump-single-json", "--skip-download", "--no-color", "--encoding", "utf-8",
               "--socket-timeout", "20", "--extractor-retries", "2",
               "--yes-playlist" if options["playlist"] else "--no-playlist",
               *cookie_arguments(options), "--", source]
    process = control.popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=CREATE_NO_WINDOW, env=environment)
    result = None
    errors = []
    try:
        for line in process.stdout:
            control.checkpoint()
            if line.startswith("{"):
                result = json.loads(line)
            else:
                log(line.rstrip())
                if "ERROR:" in line:
                    errors.append(line.strip())
        control.checkpoint()
        process.wait()
        if not isinstance(result, dict):
            raise RuntimeError(errors[-1] if errors else "Não foi possível listar os vídeos deste link.")
        return result
    finally:
        if process.poll() is None:
            control.terminate_active()
        process.wait()
        process.stdout.close()
        control.release(process)


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
                          duration=entry.get("duration"), quality="A definir")
        if not url or entry.get("availability") == "private" or title in {"[Private video]", "[Deleted video]"}:
            item.update(status="skipped", enabled=False, error="Item privado ou indisponível na playlist.")
        result.append(item)

    visit(info, source)
    return result


def discover(sources, options, engine, control, environment, log):
    items = []
    for source in sources:
        control.checkpoint()
        try:
            if is_jw_category_url(source):
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
                if item["kind"] == "jw":
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
                    if options["format"] == "Apenas áudio (M4A)":
                        output = convert_to_m4a(output, owner.ffmpeg_path, logger=owner.queue_log, control=owner.download_control,
                                                progress=lambda payload: owner.event_queue.put(("conversion_progress", payload)))
                    else:
                        output = owner._ensure_player_compatibility(output)
                    files = [str(output)]
                else:
                    url = item["source"]
                    outputs = [Path(path) for path in item.get("downloaded_files", [])]
                    if outputs and all(path.is_file() and path.stat().st_size > 0 for path in outputs):
                        code = 0
                        owner.queue_log(f"Finalizando arquivo já recebido: {item['title']}")
                    else:
                        if item["kind"] == "kanald":
                            video = resolve_kanald_video(url)  # Refresh expiring media URLs after reopening.
                            owner.download_control.checkpoint()
                            url = video.content_url
                            item.update(title=video.title, media_id=video.media_id)
                            repository.update(item_id, title=video.title, media_id=video.media_id)
                        template = item.get("output_template") or filename_template(item, ordinal)
                        repository.update(item_id, output_template=template)
                        command = owner._build_command(engine, folder, options["format"], url,
                                                        output_template=template,
                                                        include_cookies=item["kind"] != "kanald")
                        code, outputs = owner._run_downloader(command)
                    repository.update(item_id, status="finalizing", downloaded_files=[str(path) for path in outputs] if code == 0 else [])
                    owner.event_queue.put(("queue_changed", None))
                    before = owner.download_completed_files
                    ok = owner._finalize_downloaded_files(code, outputs, options["format"])
                    if not ok or not outputs:
                        raise RuntimeError("O vídeo não foi concluído. Consulte a atividade para detalhes.")
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
        owner.event_queue.put(("download_finished", {
            "failures": sum(item["enabled"] and item["status"] in {"failed", "skipped"} for item in items),
            "completed": sum(item["enabled"] and item["status"] == "completed" for item in items),
            "stopped": stopped,
            "cancelled": sum(item["enabled"] and item["status"] == "cancelled" for item in items),
        }))
