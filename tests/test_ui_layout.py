import time
from tkinter import TclError, Tk, Toplevel

import pytest

import c2_launcher  # Apply the same entry-point configuration as the installer.
import youtube_downloader_app as app
from ui_layout import window_dimensions


@pytest.mark.parametrize("area", [
    (0, 0, 1920, 1040), (0, 0, 1366, 728), (0, 0, 1024, 728),
    (0, 0, 800, 560), (0, 0, 640, 440), (-1280, 0, 0, 680),
])
def test_geometry_fits_work_area_including_native_frame(area):
    width, height, x, y = window_dimensions(area)
    assert area[0] <= x < x + width <= area[2]
    assert area[1] <= y < y + height + 40 <= area[3]


@pytest.fixture(scope="module")
def desktop():
    try:
        root = Tk()
    except TclError as exc:
        pytest.skip(f"Tk desktop unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture(params=[1.0, 1.5, 2.0])
def window(monkeypatch, request, tmp_path, desktop):
    monkeypatch.setattr(app, "load_user_settings", lambda: {})
    monkeypatch.setattr(app.DownloadApp, "start_maintenance", lambda *args: None)
    monkeypatch.setattr(app, "QUEUE_FILE", tmp_path / "downloads.sqlite3")
    monkeypatch.setattr(app, "ACTIVITY_LOG_FILE", tmp_path / "activity.log")
    root = Toplevel(desktop)
    root.withdraw()
    root.tk.call("tk", "scaling", request.param * 96 / 72)
    instance = app.DownloadApp(root)
    yield root, instance
    for timer in root.tk.call("after", "info"):
        root.after_cancel(timer)
    root.destroy()  # No writes to the user's settings or background downloads.


@pytest.mark.parametrize("geometry", ["820x680", "780x520", "560x420"])
def test_compact_window_keeps_controls_reachable(window, geometry):
    root, instance = window
    root.geometry(geometry)
    instance.download_item_var.set("Uzak Şehir 38. Bölüm — " * 12)
    instance.download_metrics_var.set("35,8% • 1,0 GB / 3,0 GB • Atual: 5,0 MB/s • Média: 4,0 MB/s\nRestante: 8min • Trabalho: 12,0%")
    root.deiconify()
    root.update()
    assert root.winfo_width() == int(geometry.split("x")[0])
    assert root.winfo_height() == int(geometry.split("x")[1])
    assert len(instance.tabs.tabs()) == 5
    for page in (
        instance.music_page,
        instance.video_page,
        instance.queue_page,
        instance.completed_page,
        instance.activity_page,
    ):
        instance.tabs.select(page)
        root.update()
        assert page.winfo_width() <= root.winfo_width()
        assert page.winfo_height() <= root.winfo_height()

    instance._open_settings()
    instance.settings_dialog.update()
    page = instance.settings_page
    canvas = page.canvas
    bounds = canvas.bbox(page.window)
    assert bounds[2] >= page.body.winfo_reqwidth()
    assert bounds[3] >= page.body.winfo_reqheight()
    if page.body.winfo_reqheight() > canvas.winfo_height():
        assert page.vertical.winfo_ismapped()
        canvas.yview_moveto(1)
        instance.settings_dialog.update()
        assert canvas.yview()[1] == 1
    instance._close_settings(False)

    instance.tabs.select(instance.activity_page)
    root.update()
    instance.log.focus_force()
    root.update()
    assert instance.log.winfo_rooty() >= instance.activity_page.winfo_rooty()


def test_completion_distinguishes_partial_empty_and_failed_jobs(window):
    _, instance = window
    instance.download_job_started_at = instance._download_clock()
    for failures, completed, expected in [
        (1, 2, "Trabalho finalizado com avisos"),
        (1, 0, "Trabalho finalizado com falhas"),
        (0, 0, "Nenhum arquivo disponível para baixar"),
        (0, 2, "Trabalho finalizado com sucesso"),
    ]:
        instance._finish_download({"failures": failures, "completed": completed})
        assert instance.download_item_var.get() == expected
        assert f"Arquivos concluídos: {completed}" in instance.download_metrics_var.get()


def test_activity_is_persisted_and_can_be_cleared(window):
    _, instance = window
    instance.queue_log("diagnostic line")
    assert "diagnostic line" in app.ACTIVITY_LOG_FILE.read_text(encoding="utf-8")
    instance.clear_log()
    assert app.ACTIVITY_LOG_FILE.read_text(encoding="utf-8") == ""


def test_audio_format_enables_bitrate_and_original_disables_conversion_controls(window):
    root, instance = window
    instance.resolution_var.set("Apenas áudio (MP3)")
    instance.audio_bitrate_mode_var.set("Personalizada")
    root.update()
    assert str(instance.audio_bitrate_combo["state"]) == "readonly"
    assert str(instance.audio_custom_bitrate["state"]) == "normal"

    instance.resolution_var.set("Áudio original (sem conversão)")
    root.update()
    assert instance.audio_bitrate_mode_var.get() == "Original / automática"
    assert str(instance.audio_bitrate_combo["state"]) == "disabled"
    assert str(instance.audio_custom_bitrate["state"]) == "disabled"


def test_switching_music_and_video_tabs_keeps_folders_and_formats_independent(window):
    root, instance = window
    video_folder = r"C:\Media\Videos"
    music_folder = r"D:\Media\Musicas"
    instance.video_folder_var.set(video_folder)
    instance.music_folder_var.set(music_folder)
    instance.video_format_var.set("720p")
    instance.music_format_var.set("Apenas áudio (Opus)")
    instance._apply_work_mode("video", initial=True)

    instance.tabs.select(instance.music_page)
    root.update()
    assert instance.work_mode == "music"
    assert instance.folder_var.get() == music_folder
    assert instance.resolution_var.get() == "Apenas áudio (Opus)"
    assert instance.video_folder_var.get() == video_folder
    assert instance.video_format_var.get() == "720p"
    music_options = instance._capture_options()
    assert music_options["folder"] == music_folder
    assert music_options["music_folder"] == music_folder
    assert music_options["video_folder"] == video_folder

    instance.tabs.select(instance.video_page)
    root.update()
    assert instance.work_mode == "video"
    assert instance.folder_var.get() == video_folder
    assert instance.resolution_var.get() == "720p"
    assert instance.music_folder_var.get() == music_folder
    assert instance.music_format_var.get() == "Apenas áudio (Opus)"
    video_options = instance._capture_options()
    assert video_options["folder"] == video_folder
    assert video_options["music_folder"] == music_folder
    assert video_options["video_folder"] == video_folder


def test_choosing_inactive_music_folder_does_not_change_video_folder(window, monkeypatch):
    _, instance = window
    instance._apply_work_mode("video", initial=True)
    instance.video_folder_var.set(r"C:\Media\Videos")
    instance.folder_var.set(r"C:\Media\Videos")
    monkeypatch.setattr(app.filedialog, "askdirectory", lambda **kwargs: r"D:\Media\Musicas")

    instance.choose_music_folder()

    assert instance.music_folder_var.get() == r"D:\Media\Musicas"
    assert instance.video_folder_var.get() == r"C:\Media\Videos"
    assert instance.folder_var.get() == r"C:\Media\Videos"


def test_restoring_music_queue_does_not_revert_video_preferences(window):
    from download_queue import queue_item

    root, instance = window
    instance.video_folder_var.set(r"D:\Current\Videos")
    instance.video_format_var.set("720p")
    item = queue_item("deezer: example", "Music result", kind="deezer_preview")
    options = instance._capture_options()
    options.update({
        "work_mode": "music",
        "folder": r"C:\Queue\Music",
        "music_folder": r"C:\Queue\Music",
        "music_format": "Apenas áudio (MP3)",
        "format": "Apenas áudio (MP3)",
        "video_folder": r"C:\Old\Videos",
        "video_format": "1080p",
    })
    instance.queue_repository.replace([item], options, ["deezer: example"])

    instance._restore_queue()
    root.update()

    assert instance.work_mode == "music"
    assert instance.music_folder_var.get() == r"C:\Queue\Music"
    assert instance.video_folder_var.get() == r"D:\Current\Videos"
    assert instance.video_format_var.get() == "720p"
    assert instance.folder_var.get() == r"C:\Queue\Music"


def test_queue_table_selections_retry_and_stop_race(window):
    from download_queue import queue_item
    _, instance = window
    items = [queue_item(f"https://example.com/{i}", str(i)) for i in range(3)]
    items[1]["status"] = "failed"
    instance.queue_repository.replace(items, instance._capture_options(), [])
    instance._refresh_queue()
    instance._toggle_items([items[0]["id"]])
    assert not instance.queue_repository.snapshot()["items"][0]["enabled"]
    instance.episode_tree.selection_set(items[1]["id"])
    instance.retry_failed()
    assert instance.queue_repository.snapshot()["items"][1]["status"] == "pending"
    instance.episode_tree.selection_set(items[2]["id"])
    instance.cancel_selected()
    assert instance.queue_repository.snapshot()["items"][2]["status"] == "cancelled"
    instance.download_control.cancel()
    instance._start_saved_queue = lambda: pytest.fail("Stopping discovery must prevent automatic download")
    instance._queue_prepared(True)


def test_completed_tab_and_queue_cleanup_preserve_downloaded_files(window, tmp_path, monkeypatch):
    from download_queue import queue_item
    import queue_ui

    _, instance = window
    files = [tmp_path / "one.mp4", tmp_path / "two.mp4"]
    for path in files:
        path.write_bytes(path.name.encode())
    completed = []
    for index, path in enumerate(files):
        item = queue_item(f"https://example.com/done-{index}", f"Done {index}")
        item.update(status="completed", files=[str(path)])
        completed.append(item)
    pending = queue_item("https://example.com/pending", "Pending")
    instance.queue_repository.replace(completed + [pending], instance._capture_options(), [])
    instance._refresh_queue()

    assert instance.episode_tree.get_children() == (pending["id"],)
    assert set(instance.completed_tree.get_children()) == {item["id"] for item in completed}
    instance.episode_tree.selection_set(pending["id"])
    instance.remove_queue_selected()
    assert pending["id"] not in {item["id"] for item in instance.queue_repository.snapshot()["items"]}
    instance.completed_tree.selection_set(completed[0]["id"])
    instance.remove_completed_selected()
    assert files[0].is_file()
    assert completed[0]["id"] not in {item["id"] for item in instance.queue_repository.snapshot()["items"]}

    monkeypatch.setattr(queue_ui.messagebox, "askyesno", lambda *args, **kwargs: True)
    instance.clear_completed()
    assert files[1].is_file()
    assert instance.completed_tree.get_children() == ()
    pending = queue_item("https://example.com/pending-again", "Pending again")
    instance.queue_repository.replace([pending], instance._capture_options(), [])
    instance._refresh_queue()
    instance.clear_queue()
    assert instance.queue_repository.snapshot()["items"] == []
    assert pending["id"] not in instance.episode_tree.get_children()


def test_queue_progress_uses_episode_count_and_preserves_fraction_during_conversion(window):
    from download_queue import queue_item
    _, instance = window
    items = [queue_item(f"https://example.com/{i}", str(i)) for i in range(4)]
    items[0]["status"] = "completed"
    items[1]["status"] = "downloading"
    items[3]["enabled"] = False
    instance.queue_repository.replace(items, instance._capture_options(), [])
    instance._refresh_queue()
    instance.queue_running = True
    instance.queue_initial_done = 1
    instance.download_job_started_at = instance._download_clock() - 10
    instance._handle_media_context({"index": 1, "total": 1, "queue_id": items[1]["id"], "label": "Episode 2"})
    assert "2/3" in instance.download_item_var.get()
    instance._handle_media_progress({"index": 1, "item_total": 1, "queue_id": items[1]["id"], "percent": 50})
    assert float(instance.progress["value"]) == 50
    instance._handle_conversion_progress({"percent": 100})
    assert float(instance.progress["value"]) == 50


def test_context_queues_show_only_their_media_type_and_share_progress(window):
    from download_queue import queue_item

    root, instance = window
    music = queue_item("https://www.deezer.com/track/1", "Music", kind="deezer_preview")
    video = queue_item("https://example.com/video", "Video", kind="video")
    instance.queue_repository.replace([music, video], instance._capture_options(), [])
    instance._refresh_queue()
    instance.progress_value_var.set(37.5)
    root.update()

    assert instance.context_queue_trees["music"].get_children() == (music["id"],)
    assert instance.context_queue_trees["video"].get_children() == (video["id"],)
    assert float(instance.progress["value"]) == 37.5


def test_player_uses_compact_icons_and_settings_cancel_restores_values(window):
    _, instance = window
    assert instance.catalog_play_button.cget("text") == "▶"
    assert instance.catalog_stop_button.cget("text") == "■"
    assert instance.music_play_button.cget("text") == "▶"
    assert hasattr(instance.catalog_play_button, "_c2_tooltip")

    original = instance.fragments_var.get()
    instance._open_settings()
    instance.fragments_var.set("1" if original != "1" else "2")
    instance._close_settings(False)
    assert instance.fragments_var.get() == original


def test_recent_deezer_search_uses_memory_cache(window, monkeypatch):
    from deezer_catalog import DeezerSearchResult

    root, instance = window
    result = DeezerSearchResult(
        kind="track",
        item_id="1",
        title="Cached",
        subtitle="Artist",
        cover_url=None,
        page_url="https://www.deezer.com/track/1",
    )
    instance.music_search_var.set("Cached Query")
    if instance._music_search_after is not None:
        root.after_cancel(instance._music_search_after)
        instance._music_search_after = None
    instance._music_search_cache[("track", "cached query")] = (time.monotonic(), (result,))
    monkeypatch.setattr(app, "search_deezer_catalog", lambda *args, **kwargs: pytest.fail("network called"))

    instance._start_music_search()
    event, payload = instance.event_queue.get_nowait()

    assert event == "music_search_results"
    assert payload["cached"] is True
    assert payload["results"] == (result,)


def test_prepared_queue_stays_on_current_work_tab(window):
    from download_queue import queue_item

    root, instance = window
    item = queue_item("https://www.deezer.com/track/1", "Music", kind="deezer_preview")
    instance.queue_repository.replace([item], instance._capture_options(), [item["source"]])
    instance.tabs.select(instance.music_page)
    root.update()

    instance._queue_prepared(False)

    assert instance.tabs.select() == str(instance.music_page)
