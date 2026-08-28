"""Offline fixture: a private video and a removed file between valid videos."""
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import ExtractorError


class C2PlaylistFixtureIE(InfoExtractor):
    _VALID_URL = r'c2fixture:(?P<id>playlist|first|private|deleted|last):(?P<port>\d+)'

    def _real_extract(self, url):
        match = self._match_valid_url(url)
        video_id, port = match.group('id', 'port')
        if video_id == 'playlist':
            return self.playlist_result([
                self.url_result(f'c2fixture:{item}:{port}', ie=self.ie_key())
                for item in ('first', 'private', 'deleted', 'last')
            ], playlist_id='fixture', playlist_title='Offline playlist')
        if video_id == 'private':
            raise ExtractorError('Private video. This video is private.', expected=True)
        return {
            'id': video_id, 'title': video_id, 'ext': 'mp4',
            'url': f'http://127.0.0.1:{port}/{video_id}.mp4',
        }
