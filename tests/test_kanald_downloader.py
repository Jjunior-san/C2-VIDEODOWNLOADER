from __future__ import annotations

import unittest
from pathlib import Path

import youtube_downloader_app as app
from kanald_downloader import (
    KanalDError,
    is_kanald_collection_url,
    is_kanald_url,
    output_template,
    parse_kanald_collection,
    parse_kanald_page,
)


PAGE_URL = "https://www.kanald.com.tr/uzak-sehir/bolumler/uzak-sehir-22-bolum"
SAMPLE_HTML = r'''
<html>
  <body>
    <div data-id="6805f38384687ed42a4b48a0"></div>
    <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        "name": "Uzak Şehir 22. Bölüm",
        "contentUrl": "https://kanaldvod.duhnet.tv/hls/U/9F/video/playlist.m3u8"
      }
    </script>
  </body>
</html>
'''
COLLECTION_URL = (
    "https://www.kanald.com.tr/uzak-sehir/bolumler?"
    "activeSeasonNumber=1&seasonCount=99"
)
SAMPLE_COLLECTION_HTML = r'''
<html><body>
  <a href="/uzak-sehir/bolumler/uzak-sehir-ilk-bolum">1</a>
  <a href="/uzak-sehir/bolumler/uzak-sehir-2-bolum?ref=list">2</a>
  <a href="/uzak-sehir/bolumler/uzak-sehir-2-bolum">duplicado</a>
  <a href="/uzak-sehir/fragmanlar/tanitim">ignorar</a>
  <a href="https://example.com/uzak-sehir/bolumler/falso">ignorar</a>
</body></html>
'''


class KanalDURLTests(unittest.TestCase):
    def test_recognizes_kanald_page(self) -> None:
        self.assertTrue(is_kanald_url(PAGE_URL))

    def test_rejects_lookalike_domain(self) -> None:
        self.assertFalse(is_kanald_url("https://kanald.com.tr.example/video"))

    def test_recognizes_episode_collection(self) -> None:
        self.assertTrue(is_kanald_collection_url(COLLECTION_URL))
        self.assertFalse(is_kanald_collection_url(PAGE_URL))


class KanalDPageTests(unittest.TestCase):
    def test_extracts_official_hls_and_metadata(self) -> None:
        video = parse_kanald_page(PAGE_URL, SAMPLE_HTML)
        self.assertEqual(video.title, "Uzak Şehir 22. Bölüm")
        self.assertEqual(video.media_id, "6805f38384687ed42a4b48a0")
        self.assertEqual(
            video.content_url,
            "https://kanaldvod.duhnet.tv/hls/U/9F/video/playlist.m3u8",
        )
        self.assertEqual(
            output_template(video),
            "Uzak Şehir 22. Bölüm [6805f38384687ed42a4b48a0].%(ext)s",
        )

    def test_rejects_untrusted_media_host(self) -> None:
        html = SAMPLE_HTML.replace("kanaldvod.duhnet.tv", "example.com")
        with self.assertRaises(KanalDError):
            parse_kanald_page(PAGE_URL, html)

    def test_rejects_page_without_video_object(self) -> None:
        with self.assertRaises(KanalDError):
            parse_kanald_page(PAGE_URL, "<html><body>sem vídeo</body></html>")


class KanalDCollectionTests(unittest.TestCase):
    def test_extracts_ordered_unique_episode_urls(self) -> None:
        collection = parse_kanald_collection(COLLECTION_URL, SAMPLE_COLLECTION_HTML)
        self.assertEqual(
            collection.episode_urls,
            (
                "https://www.kanald.com.tr/uzak-sehir/bolumler/uzak-sehir-ilk-bolum",
                "https://www.kanald.com.tr/uzak-sehir/bolumler/uzak-sehir-2-bolum",
            ),
        )

    def test_rejects_empty_collection(self) -> None:
        with self.assertRaises(KanalDError):
            parse_kanald_collection(COLLECTION_URL, "<html><body></body></html>")


class _Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class KanalDCommandTests(unittest.TestCase):
    def test_can_omit_locked_browser_cookies(self) -> None:
        instance = object.__new__(app.DownloadApp)
        instance.playlist_var = _Value(True)
        instance.cookies_browser_var = _Value("Chrome")
        instance.cookies_file_var = _Value("cookies.txt")

        command = app.DownloadApp._build_command(
            instance,
            Path("yt-dlp.exe"),
            Path("downloads"),
            "Melhor MP4 compatível",
            "https://kanaldvod.duhnet.tv/video/playlist.m3u8",
            include_cookies=False,
        )
        self.assertNotIn("--cookies-from-browser", command)
        self.assertNotIn("--cookies", command)


if __name__ == "__main__":
    unittest.main()
