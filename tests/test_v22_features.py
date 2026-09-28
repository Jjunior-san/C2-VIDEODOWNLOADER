from pathlib import Path
from tkinter import Tk, StringVar, END, Text
from unittest.mock import MagicMock

import pytest

import youtube_downloader_app as app
from download_queue import queue_item, QueueRepository
from queue_ui import QueueUI


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = Tk()
        root.withdraw()
    except Exception:
        pytest.skip("Tk desktop unavailable")
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def test_presets_structure_and_choices():
    assert "Personalizado" in app.PRESETS
    assert "🌟 Máxima Qualidade (4K / 2K / HD)" in app.PRESETS
    assert "📱 Celular & WhatsApp (720p H.264 Leve)" in app.PRESETS
    assert "🎧 Áudio Hi-Fi Lossless (FLAC)" in app.PRESETS
    assert "🎵 Música Universal (MP3 320 kbps)" in app.PRESETS
    assert "🎙️ Podcast & Audiolivro (MP3 128 kbps)" in app.PRESETS
    assert len(app.PRESET_CHOICES) == len(app.PRESETS)


def test_post_download_actions_defined():
    assert "Nenhuma ação" in app.POST_DOWNLOAD_ACTIONS
    assert "Tocar som de alerta" in app.POST_DOWNLOAD_ACTIONS
    assert "Abrir pasta de downloads" in app.POST_DOWNLOAD_ACTIONS
    assert "Suspender computador" in app.POST_DOWNLOAD_ACTIONS
    assert "Desligar o computador (30s)" in app.POST_DOWNLOAD_ACTIONS


def test_applying_preset_configures_format_and_bitrate(tk_root):
    instance = object.__new__(app.DownloadApp)
    instance.work_mode = "video"
    instance.video_format_var = StringVar(master=tk_root, value="480p")
    instance.resolution_var = StringVar(master=tk_root, value="480p")
    instance.audio_bitrate_mode_var = StringVar(master=tk_root, value="128 kbps")
    instance.embed_chapters_var = StringVar(master=tk_root, value=False)
    instance.subtitles_embed_var = StringVar(master=tk_root, value=False)
    instance.preset_var = StringVar(master=tk_root, value="🎧 Áudio Hi-Fi Lossless (FLAC)")
    instance._on_video_format_selected = MagicMock()
    instance._update_audio_controls = MagicMock()
    instance._save_preferences = MagicMock()
    instance.queue_log = MagicMock()

    instance._on_preset_selected()

    assert instance.video_format_var.get() == "Apenas áudio (FLAC)"
    assert instance.resolution_var.get() == "Apenas áudio (FLAC)"
    assert instance.audio_bitrate_mode_var.get() == "Original / automática"
    instance._save_preferences.assert_called_once()


def test_detected_links_counter(tk_root):
    instance = object.__new__(app.DownloadApp)
    instance.root = tk_root
    instance.video_url_text = Text(tk_root)
    instance.detected_links_var = StringVar(master=tk_root)
    instance._schedule_video_preview_check = MagicMock()

    instance.video_url_text.insert("1.0", "https://youtube.com/watch?v=abc\nhttps://youtube.com/watch?v=def\n# comentario\n")
    instance._update_detected_links_count()
    assert instance.detected_links_var.get() == "2 links detectados"

    instance._clear_url_text()
    assert instance.detected_links_var.get() == "Nenhum link inserido"
    assert instance.video_url_text.get("1.0", END).strip() == ""


def test_clear_completed_from_queue_preserves_pending(tmp_path):
    repo = QueueRepository(tmp_path / "queue.sqlite3")
    item1 = queue_item("https://example.com/1", "Item 1")
    item1["status"] = "completed"
    item2 = queue_item("https://example.com/2", "Item 2")
    item2["status"] = "pending"
    item3 = queue_item("https://example.com/3", "Item 3")
    item3["status"] = "failed"
    repo.replace([item1, item2, item3], {}, [])

    instance = object.__new__(app.DownloadApp)
    instance.queue_repository = repo
    instance.busy = False
    instance.queue_items = []
    instance._refresh_queue = MagicMock()
    instance.queue_log = MagicMock()

    instance.clear_completed_from_queue()

    snapshot = repo.snapshot()["items"]
    assert len(snapshot) == 2
    assert {i["status"] for i in snapshot} == {"pending", "failed"}
    instance._refresh_queue.assert_called_once()


def test_user_settings_saves_preset_and_post_download(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(app, "SETTINGS_FILE", settings_file)

    settings = {
        "download_folder": r"C:\Downloads",
        "preset": "🌟 Máxima Qualidade (4K / 2K / HD)",
        "post_download_action": "Tocar som de alerta",
    }
    app.save_user_settings(settings)
    loaded = app.load_user_settings()
    assert loaded["preset"] == "🌟 Máxima Qualidade (4K / 2K / HD)"
    assert loaded["post_download_action"] == "Tocar som de alerta"
