import asyncio
from copy import deepcopy
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'enrichment-worker'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'metadata-worker'))
from metadata_worker.steam_metadata import (
    SteamGenre, SteamMetadataError, SteamMetadataTimeout, SteamMetadataUnavailable,
    lookup_metadata, parse_metadata,
)


def payload(categories=None):
    return {'620': {'success': True, 'data': {'steam_appid': 620,
        'genres': [{'id': '25', 'description': ' Adventure '},
                   {'id': '1', 'description': 'Action'}],
        'categories': categories if categories is not None else [{'id': 2}]}}}


class SteamMetadataParsingTests(unittest.TestCase):
    def test_single_player_mixed_multiplayer_and_empty_categories(self):
        for categories, expected in (([{'id': 2}], True),
                ([{'id': 1}, {'id': 2}, {'id': 9}], True), ([{'id': 1}], False), ([], False)):
            with self.subTest(categories=categories):
                result = parse_metadata(620, payload(categories))
                self.assertIs(result.single_player, expected)
                self.assertEqual(result.genres, (SteamGenre('1', 'Action'), SteamGenre('25', 'Adventure')))

    def test_missing_categories_unknown_and_explicit_empty_genres_valid(self):
        data = payload()
        del data['620']['data']['categories']
        data['620']['data']['genres'] = []
        result = parse_metadata(620, data)
        self.assertIsNone(result.single_player)
        self.assertEqual(result.genres, ())

    def test_genre_deduplication_and_conflicts(self):
        data = payload()
        data['620']['data']['genres'].append({'id': '01', 'description': 'Action'})
        self.assertEqual(len(parse_metadata(620, data).genres), 2)
        data['620']['data']['genres'].append({'id': '1', 'description': 'Other'})
        with self.assertRaises(SteamMetadataError):
            parse_metadata(620, data)

    def test_unavailable_is_not_multiplayer_only(self):
        with self.assertRaises(SteamMetadataUnavailable):
            parse_metadata(620, {'620': {'success': False}})

    def test_invalid_envelope_and_identity(self):
        for data in (None, [], {}, {'620': {'success': 1}},
                     {'620': {'success': True, 'data': {}}}, payload() | {'620': None}):
            with self.subTest(data=data), self.assertRaises(SteamMetadataError):
                parse_metadata(620, data)
        data = payload()
        data['620']['data']['steam_appid'] = 570
        with self.assertRaises(SteamMetadataError):
            parse_metadata(620, data)

    def test_malformed_fields_never_become_negative_classification(self):
        for field, values in (
            ('genres', [None, {}, [None], [{'id': True, 'description': 'Action'}],
                        [{'id': '1', 'description': ' '}]]),
            ('categories', [None, {}, [None], [{'id': True}], [{'id': '2'}]]),
        ):
            for value in values:
                data = deepcopy(payload())
                data['620']['data'][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(SteamMetadataError):
                    parse_metadata(620, data)


class SteamMetadataRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_contract_and_valid_response(self):
        def handler(request):
            self.assertEqual(str(request.url).split('?')[0], 'https://store.steampowered.com/api/appdetails')
            self.assertEqual(request.url.params['appids'], '620')
            self.assertEqual(request.url.params['l'], 'english')
            return httpx.Response(200, json=payload())
        self.assertTrue((await lookup_metadata(620, transport=httpx.MockTransport(handler))).single_player)

    async def test_http_errors_and_redirects_are_not_followed(self):
        for status in (302, 403, 429, 500, 503):
            requests = []
            def handler(request):
                requests.append(request)
                return httpx.Response(status, headers={'Location': 'https://example.com'})
            with self.subTest(status=status), self.assertRaises(SteamMetadataError):
                await lookup_metadata(620, transport=httpx.MockTransport(handler))
            self.assertEqual(len(requests), 1)

    async def test_invalid_json_and_network_error(self):
        def bad_json(request):
            return httpx.Response(200, text='<html>unavailable</html>')
        def network_error(request):
            raise httpx.ConnectError('offline')
        for handler in (bad_json, network_error):
            with self.assertRaises(SteamMetadataError):
                await lookup_metadata(620, transport=httpx.MockTransport(handler))

    async def test_transport_and_overall_timeouts(self):
        def timeout(request):
            raise httpx.ReadTimeout('slow')
        async def slow(request):
            await asyncio.sleep(1)
            return httpx.Response(200, json=payload())
        for handler in (timeout, slow):
            with self.assertRaises(SteamMetadataTimeout):
                await lookup_metadata(620, timeout_seconds=0.01,
                                      transport=httpx.MockTransport(handler))

    async def test_invalid_arguments_make_no_requests(self):
        def unexpected(request):
            self.fail('request should not be sent')
        for app_id in (True, 0, -1, '620'):
            with self.assertRaises(ValueError):
                await lookup_metadata(app_id, transport=httpx.MockTransport(unexpected))
        for timeout in (True, 0, -1, float('nan'), float('inf'), '10'):
            with self.assertRaises(ValueError):
                await lookup_metadata(620, timeout_seconds=timeout,
                                      transport=httpx.MockTransport(unexpected))


class HeaderImageTests(unittest.TestCase):
    def test_optional_header_image_is_validated(self):
        from metadata_worker.steam_metadata import validated_header_image
        good = 'https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/620/hash/header.jpg?t=1'
        self.assertEqual(validated_header_image(good), good)
        for bad in (None, '', 'http://shared.akamai.steamstatic.com/a.jpg',
                    'https://shared.akamai.steamstatic.com.evil.test/a.jpg',
                    'https://user@shared.akamai.steamstatic.com/a.jpg',
                    'https://shared.akamai.steamstatic.com:8080/a.jpg'):
            self.assertIsNone(validated_header_image(bad))
        data = payload()
        data['620']['data']['header_image'] = good
        self.assertEqual(parse_metadata(620, data).header_image, good)

class MultiplayerTests(unittest.TestCase):
    def test_multiplayer_is_independent_of_single_player(self):
        for categories, single, multi in (([{'id': 1}], False, True),
                ([{'id': 1}, {'id': 2}], True, True), ([{'id': 2}], True, False), ([], False, False)):
            result = parse_metadata(620, payload(categories))
            self.assertIs(result.single_player, single)
            self.assertIs(result.multiplayer, multi)
        data = payload()
        del data['620']['data']['categories']
        self.assertIsNone(parse_metadata(620, data).multiplayer)

class FreeToPlayMetadataTests(unittest.TestCase):
    def test_free_is_explicit_boolean_only(self):
        for value,expected in ((True,True),(False,False),(None,None),('true',None),(0,None)):
            data=payload();data['620']['data']['is_free']=value
            self.assertIs(parse_metadata(620,data).is_free,expected)

class TrailerMetadataTests(unittest.TestCase):
    def test_highlight_hls_selection_and_optional_missing_data(self):
        from metadata_worker.steam_metadata import select_trailer
        url='https://video.akamai.steamstatic.com/store_trailers/1/test.m3u8'
        data=payload();data['620']['data']['movies']=[None,{'name':'Trailer','highlight':True,'hls_h264':url}]
        result=parse_metadata(620,data)
        self.assertEqual(result.trailer_url,url)
        self.assertEqual(result.trailer_format,'hls')
        self.assertEqual(result.trailer_title,'Trailer')
        for value in (None,{},[],[{'hls_h264':'https://evil.test/a.m3u8'}],
                      [{'hls_h264':'http://video.akamai.steamstatic.com/a.m3u8'}]):
            self.assertEqual(select_trailer(value),{})

    def test_legacy_mp4_and_unsafe_urls(self):
        from metadata_worker.steam_metadata import select_trailer
        url='https://cdn.akamai.steamstatic.com/steam/apps/1/movie480.mp4'
        self.assertEqual(select_trailer([{'mp4':{'480':url}}])['trailer_format'],'mp4')
        for bad in ('https://video.akamai.steamstatic.com.evil.test/a.m3u8',
                    'https://user@video.akamai.steamstatic.com/a.m3u8',
                    'https://video.akamai.steamstatic.com:123/a.m3u8'):
            self.assertEqual(select_trailer([{'hls_h264':bad}]),{})
