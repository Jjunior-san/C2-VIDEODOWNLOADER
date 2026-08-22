from __future__ import annotations

import unittest

from kanald_downloader import (
    KanalDError,
    is_kanald_url,
    output_template,
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


class KanalDURLTests(unittest.TestCase):
    def test_recognizes_kanald_page(self) -> None:
        self.assertTrue(is_kanald_url(PAGE_URL))

    def test_rejects_lookalike_domain(self) -> None:
        self.assertFalse(is_kanald_url("https://kanald.com.tr.example/video"))


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


if __name__ == "__main__":
    unittest.main()
