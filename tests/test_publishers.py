"""SIMULATED integration tests: platform HTTP APIs are mocked. They prove request sequencing, status handling,
dedupe, privacy rules and safety stops. They do NOT prove a real upload; that needs connected accounts.

run: .venv/Scripts/python -m unittest tests.test_publishers -v
"""
import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from autopilot import db, config, tokens, publish  # noqa: E402
from autopilot.publishers import tiktok, instagram, youtube  # noqa: E402

TMP = Path(tempfile.mkdtemp())
db.DB_PATH = TMP / "ops.db"; db.DATA = TMP; publish.MANUAL = TMP / "manual"; config.STOP = TMP / "STOP"; config.CFG = TMP / "config.json"
VID = TMP / "v.mp4"; VID.write_bytes(b"\0" * 2048)
TOK = {"access_token": "SECRET-ACCESS-123456", "refresh_token": "r", "obtained_at": 9e12, "expires_in": 86400, "user_id": "17841"}
APP = {"client_key": "k", "client_secret": "s", "client_id": "i", "redirect_uri": "https://localhost/cb"}


class R:
    def __init__(self, js, code=200):
        self._js, self.status_code, self.text = js, code, json.dumps(js)

    def json(self):
        return self._js


def tt_post(url, json=None, **kw):
    if url.endswith("creator_info/query/"):
        return R({"data": {"privacy_level_options": ["SELF_ONLY"]}, "error": {"code": "ok"}})
    if url.endswith("/post/publish/video/init/"):
        assert json["post_info"]["privacy_level"] == "SELF_ONLY"
        assert json["source_info"]["video_size"] == 2048 and json["source_info"]["total_chunk_count"] == 1
        return R({"data": {"publish_id": "p_1", "upload_url": "https://up/1"}, "error": {"code": "ok"}})
    if url.endswith("/inbox/video/init/"):
        return R({"data": {"publish_id": "p_inbox", "upload_url": "https://up/2"}, "error": {"code": "ok"}})
    if url.endswith("status/fetch/"):
        st = "SEND_TO_USER_INBOX" if json["publish_id"] == "p_inbox" else "PUBLISH_COMPLETE"
        return R({"data": {"status": st}, "error": {"code": "ok"}})
    raise AssertionError(url)


def ig_request(method, url, params=None, **kw):
    if url.endswith("/media") and method == "POST":
        assert params["media_type"] == "REELS" and params["upload_type"] == "resumable"
        return R({"id": "c_9", "uri": "https://rupload.facebook.com/x"})
    if url.endswith("/c_9"):
        return R({"status_code": "FINISHED"})
    if url.endswith("/media_publish"):
        return R({"id": "m_7"})
    if url.endswith("/m_7"):
        return R({"permalink": "https://www.instagram.com/reel/ABC/"})
    if url.endswith("content_publishing_limit"):
        return R({"data": [{"quota_usage": 2, "config": {"quota_total": 50}}]})
    raise AssertionError(url)


def put_tokens(p):
    return dict(TOK)


class T(unittest.TestCase):
    def setUp(self):
        for f in (db.DB_PATH, config.STOP):
            if f.exists():
                f.unlink()
        meta = {"youtube": {"title": "t #shorts", "description": "d Credit: X", "tags": ["GTA6"]},
                "tiktok": {"caption": "c Credit: X"}, "instagram": {"caption": "c Credit: X"}}
        db.execute("INSERT INTO rights (creator, source_platform, status, basis, allowed_platforms) VALUES ('x','own','cleared','own_footage',?)",
                   '["youtube","tiktok","instagram"]')
        self.cid = db.execute("INSERT INTO clips (source_url, creator, rights_id, start, path, sha256, meta, status, created_at) "
                              "VALUES ('s','x',1,0,?,'abc',?,'approved',0)", str(VID), json.dumps(meta))
        self.clip = db.one("SELECT * FROM clips WHERE id=?", self.cid)
        self.p = [mock.patch.object(tokens, "get", side_effect=put_tokens), mock.patch.object(tokens, "app", return_value=APP),
                  mock.patch("time.sleep"), mock.patch.object(publish, "account_guard")]
        [x.start() for x in self.p]

    def tearDown(self):
        [x.stop() for x in self.p]

    def test_tiktok_self_only_test_upload(self):
        with mock.patch("requests.post", side_effect=tt_post), mock.patch("requests.put", return_value=R({}, 201)) as put:
            s = publish.publish_clip(self.clip, "tiktok", "private", config.load(), test=True)
        self.assertEqual(s, "private_test")
        self.assertEqual(put.call_args.kwargs["headers"]["Content-Range"], "bytes 0-2047/2048")

    def test_tiktok_public_unaudited_falls_back_to_inbox(self):
        with mock.patch("requests.post", side_effect=tt_post), mock.patch("requests.put", return_value=R({}, 201)):
            s = publish.publish_clip(self.clip, "tiktok", "public", config.load())
        self.assertEqual(s, "manual_needed")
        self.assertIn("inbox", db.one("SELECT error FROM posts WHERE platform='tiktok'")["error"])

    def test_tiktok_rejects_public_privacy_when_not_allowed(self):
        with mock.patch("requests.post", side_effect=tt_post), self.assertRaises(RuntimeError):
            tiktok.upload(VID, "c", privacy="PUBLIC_TO_EVERYONE")

    def test_instagram_container_only_test_and_publish(self):
        with mock.patch("requests.request", side_effect=ig_request), mock.patch("requests.post", return_value=R({"success": True})) as up:
            self.assertEqual(publish.publish_clip(self.clip, "instagram", "private", config.load(), test=True), "private_test")
            self.assertTrue(up.call_args.args[0].startswith("https://rupload.facebook.com/ig-api-upload/"))
            self.assertEqual(publish.publish_clip(self.clip, "instagram", "public", config.load()), "published")
        self.assertEqual(db.one("SELECT url FROM posts WHERE platform='instagram' AND privacy='public'")["url"], "https://www.instagram.com/reel/ABC/")

    def test_duplicate_prevented(self):
        with mock.patch("requests.request", side_effect=ig_request), mock.patch("requests.post", return_value=R({"success": True})):
            publish.publish_clip(self.clip, "instagram", "public", config.load())
            self.assertEqual(publish.publish_clip(self.clip, "instagram", "public", config.load()), "duplicate")

    def test_youtube_visibility_mismatch_reported(self):
        svc = mock.MagicMock()
        svc.videos().list().execute.return_value = {"items": [{"status": {"uploadStatus": "processed", "privacyStatus": "private"}}]}
        with mock.patch.object(youtube, "_svc", return_value=svc), mock.patch("googleapiclient.http.MediaFileUpload"):
            svc.videos().insert().next_chunk.return_value = (None, {"id": "yt1"})
            s = publish.publish_clip(self.clip, "youtube", "public", config.load())
        self.assertEqual(s, "locked_private")

    def test_copyright_error_engages_emergency_stop_and_redacts_token(self):
        def boom(*a, **k):
            raise RuntimeError("Video blocked: copyright claim SECRET-ACCESS-123456")
        with mock.patch.object(instagram, "quota", return_value=(0, 50)), mock.patch.object(instagram, "upload", side_effect=boom):
            self.assertEqual(publish.publish_clip(self.clip, "instagram", "public", config.load()), "failed")
        self.assertTrue(config.stopped())
        self.assertNotIn("SECRET-ACCESS", db.one("SELECT error FROM posts")["error"])
        self.assertEqual(publish.due(), [])  # nothing publishes while stopped

    def test_wrong_account_is_refused(self):
        publish._ACCOUNT_OK.clear()
        self.p[-1].stop()
        try:
            with mock.patch.object(instagram, "whoami", return_value={"account": "someone_else", "id": "1"}),                  mock.patch.object(instagram, "upload") as up:
                s = publish.publish_clip(self.clip, "instagram", "private", config.load(), test=True)
            self.assertEqual(s, "manual_needed"); up.assert_not_called()
            self.assertIn("not the destination", db.one("SELECT error FROM posts")["error"])
        finally:
            self.p[-1].start()

    def test_uncleared_rights_block_publish(self):
        db.execute("UPDATE rights SET status='revoked'")
        self.assertEqual(publish.publish_clip(self.clip, "instagram", "public", config.load()), "blocked")


if __name__ == "__main__":
    unittest.main()
