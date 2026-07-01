from __future__ import annotations

import unittest

from jw_org_downloader import (
    JWOrgError,
    _select_file,
    parse_jw_category_url,
)


class ParseJWCategoryURLTests(unittest.TestCase):
    def test_parses_portuguese_category(self) -> None:
        result = parse_jw_category_url(
            "https://www.jw.org/pt/biblioteca/videos/"
            "#pt/categories/StudioMonthlyPrograms"
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.language, "T")
        self.assertEqual(result.category, "StudioMonthlyPrograms")

    def test_ignores_non_jw_url(self) -> None:
        self.assertIsNone(
            parse_jw_category_url(
                "https://example.com/pt/biblioteca/videos/"
                "#pt/categories/StudioMonthlyPrograms"
            )
        )

    def test_rejects_unconfigured_language(self) -> None:
        with self.assertRaises(JWOrgError):
            parse_jw_category_url(
                "https://www.jw.org/de/bibliothek/videos/#de/categories/Foo"
            )


class SelectFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.media = {
            "files": [
                {
                    "progressiveDownloadURL": "https://cdn.example/video-360.mp4",
                    "height": 360,
                    "mimetype": "video/mp4",
                    "filesize": 100,
                },
                {
                    "progressiveDownloadURL": "https://cdn.example/video-720.mp4",
                    "height": 720,
                    "mimetype": "video/mp4",
                    "filesize": 200,
                },
                {
                    "progressiveDownloadURL": "https://cdn.example/video-1080.mp4",
                    "height": 1080,
                    "mimetype": "video/mp4",
                    "filesize": 300,
                },
            ]
        }

    def test_selects_requested_ceiling(self) -> None:
        selected = _select_file(self.media, "720p")
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["_height"], 720)

    def test_selects_best_quality(self) -> None:
        selected = _select_file(self.media, "Melhor qualidade")
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["_height"], 1080)

    def test_uses_smallest_above_when_no_file_is_under_limit(self) -> None:
        media = {
            "files": [
                {
                    "progressiveDownloadURL": "https://cdn.example/video-720.mp4",
                    "height": 720,
                    "mimetype": "video/mp4",
                },
                {
                    "progressiveDownloadURL": "https://cdn.example/video-1080.mp4",
                    "height": 1080,
                    "mimetype": "video/mp4",
                },
            ]
        }
        selected = _select_file(media, "480p")
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["_height"], 720)

    def test_prefers_direct_audio_for_audio_mode(self) -> None:
        media = {
            "files": [
                {
                    "progressiveDownloadURL": "https://cdn.example/video.mp4",
                    "height": 720,
                    "mimetype": "video/mp4",
                },
                {
                    "progressiveDownloadURL": "https://cdn.example/audio.m4a",
                    "mimetype": "audio/mp4",
                    "bitRate": 128,
                },
            ]
        }
        selected = _select_file(media, "Apenas áudio (M4A)")
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["_kind"], "audio")


if __name__ == "__main__":
    unittest.main()
