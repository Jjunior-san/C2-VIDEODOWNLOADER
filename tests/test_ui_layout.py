from tkinter import TclError, Tk

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


@pytest.fixture(params=[1.0, 1.5, 2.0])
def window(monkeypatch, request):
    monkeypatch.setattr(app, "load_user_settings", lambda: {})
    monkeypatch.setattr(app.DownloadApp, "start_maintenance", lambda *args: None)
    try:
        root = Tk()
    except TclError as exc:
        pytest.skip(f"Tk desktop unavailable: {exc}")
    root.withdraw()
    root.tk.call("tk", "scaling", request.param * 96 / 72)
    instance = app.DownloadApp(root)
    yield root, instance
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
    assert len(instance.tabs.tabs()) == 2
    for page in (instance.download_page, instance.settings_page):
        instance.tabs.select(page)
        root.update()
        assert page.winfo_width() <= root.winfo_width()
        assert page.winfo_height() <= root.winfo_height()
        canvas = page.canvas
        bounds = canvas.bbox(page.window)
        assert bounds[2] >= page.body.winfo_reqwidth()
        assert bounds[3] >= page.body.winfo_reqheight()
        if page.body.winfo_reqheight() > canvas.winfo_height():
            assert page.vertical.winfo_ismapped()
            canvas.yview_moveto(1)
            root.update()
            assert canvas.yview()[1] == 1
    instance.tabs.select(instance.download_page)
    root.update()
    # Keyboard navigation also scrolls offscreen controls into view.
    instance.log.focus_force()
    root.update()
    assert instance.log.winfo_rooty() >= instance.download_page.canvas.winfo_rooty()


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
