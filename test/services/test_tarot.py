import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from app.models.schema import VideoParams
from app.services import tarot_assets as assets, tarot_video


class TarotLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patcher = patch.object(assets, "library_root", return_value=self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def save(self, cid, color="red"):
        output = io.BytesIO()
        Image.new("RGB", (180, 300), color).save(output, "PNG")
        assets.save_card(cid, output.getvalue())

    def test_complete_unique_catalog(self):
        cards = assets.cards()
        self.assertEqual(len(cards), 78)
        self.assertEqual(len({c["id"] for c in cards}), 78)
        self.assertEqual(sum(c["category"] == "major" for c in cards), 22)
        for suit in assets.SUITS:
            self.assertEqual(sum(c["suit"] == suit for c in cards), 14)
        self.assertEqual(next(c for c in cards if c["id"] == "pentacles-01")["commons_file"], "Pents01.jpg")
        self.assertTrue(all(c["name_vi"] and c["image_path"] for c in cards))

    def test_exact_multilingual_matching_and_order(self):
        found = assets.mentions("The Moon, Nữ Hoàng Kiếm, The Sun, 10 Tiền, Át Ly, Judgment; The Moon.")
        self.assertEqual([m["id"] for m in found], ["major-18", "swords-13", "major-19", "pentacles-10", "cups-01", "major-20", "major-18"])
        self.assertEqual(assets.mentions("sunflower, swordsman, moonlight"), [])

    def test_selection_never_substitutes_missing_or_unmentioned_cards(self):
        self.save("major-19")
        self.assertEqual(assets.select_cards("Không nêu tên bài", ["major-19"])[0]["id"], "major-19")
        with self.assertRaisesRegex(ValueError, "Missing image"):
            assets.select_cards("The Moon", ["major-19"])
        with self.assertRaisesRegex(ValueError, "No Tarot"):
            assets.select_cards("Không có tên bài")

    def test_path_traversal_and_invalid_uploads(self):
        with self.assertRaises(ValueError):
            assets.asset_path("../../outside")
        with self.assertRaises(ValueError):
            assets.save_card("../../outside", b"image")
        with self.assertRaises(OSError):
            assets.save_card("major-00", b"not an image")
        with self.assertRaises(ValueError):
            assets.save_effect("not-a-category", b"mp4")

    def test_import_rejects_non_public_domain(self):
        response = Mock()
        response.json.return_value = {"query": {"pages": {"1": {"imageinfo": [{
            "url": "https://upload.wikimedia.org/fake.jpg", "extmetadata": {
                "LicenseShortName": {"value": "CC BY-SA"}, "Copyrighted": {"value": "True"}
            }}]}}}}
        with patch.object(assets.requests, "Session") as session, patch.object(assets, "cards", return_value=[assets.cards()[0]]):
            session.return_value.get.return_value = response
            result = assets.import_public_domain()
            self.assertIn("major-00", result["errors"])
            self.assertEqual(session.return_value.get.call_count, 1)
            self.assertFalse(assets.asset_path("cards/major-00.png").exists())

    def test_tarot_pipeline_bypasses_llm_terms_and_stock(self):
        from app.services import task
        self.save("major-19")
        params = VideoParams(video_subject="tarot", video_source="tarot", video_script="The Sun")
        with patch.object(task.llm, "generate_terms", side_effect=AssertionError("LLM called")), patch.object(task.material, "download_videos", side_effect=AssertionError("stock called")):
            self.assertEqual(task.generate_terms("unused", params, params.video_script), ["major-19"])
            paths = task.get_video_materials("unused", params, ["major-19"], 2)
            self.assertEqual(paths, [str(assets.asset_path("cards/major-19.png"))])

    def test_template_is_scoped_and_cta_is_validated(self):
        ordinary = VideoParams(video_subject="test", video_source="local", voice_name="custom")
        original = ordinary.model_dump()
        tarot_video.apply_template(ordinary)
        self.assertEqual(original, ordinary.model_dump())
        tarot = VideoParams(video_subject="tarot", video_source="tarot")
        tarot_video.apply_template(tarot)
        self.assertEqual(tarot.video_aspect, "9:16")
        self.assertTrue(tarot.subtitle_enabled)
        self.assertIn("vi-VN", tarot.voice_name)
        for value in ("javascript:alert(1)", "https://user:secret@example.org", "https://example.org\n"):
            with self.assertRaises(ValueError):
                tarot_video.validate_cta(value)

    def test_subtitle_timeline_uses_voice_cues(self):
        srt = self.root / "sub.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nThe Sun\n\n2\n00:00:02,000 --> 00:00:03,000\nThe Moon\n", encoding="utf-8")
        timeline = tarot_video.card_timeline("The Sun. The Moon.", [], 4, str(srt))
        self.assertEqual(timeline, [(0, "major-19"), (2, "major-18")])

    def test_animation_keeps_original_face_and_appends_cta(self):
        self.save("major-19", (219, 35, 41))
        selected = assets.select_cards("The Sun")
        params = VideoParams(video_subject="tarot", video_source="tarot", tarot_cta_duration=1)
        frames = {}
        def inspect(clip, **kwargs):
            self.assertEqual(clip.duration, 3)
            for t in (0, .4, 1, 2.5):
                frames[t] = clip.get_frame(t)
        with patch("app.services.video._write_videofile_with_codec_fallback", side_effect=inspect):
            tarot_video.render(self.root/"video.mp4", selected, "The Sun", 2, params, resolution=(270,480), fps=10)
        np.testing.assert_array_equal(frames[1][190,135], [219,35,41])
        self.assertFalse(np.array_equal(frames[0], frames[.4]))
        self.assertFalse(np.array_equal(frames[1], frames[2.5]))

    def test_webui_asset_manager_and_manual_selection(self):
        from streamlit.testing.v1 import AppTest
        self.save("major-19")
        app = AppTest.from_string('''
from app.models.schema import VideoParams
from webui.tarot_library import render_asset_manager, render_template
p = VideoParams(video_subject="Tarot", video_source="tarot")
render_asset_manager()
render_template(p)
''', default_timeout=30).run()
        self.assertFalse(app.exception)
        app.multiselect(key="tarot_card_ids").set_value(["major-19"]).run()
        self.assertFalse(app.exception)
        app.selectbox(key="tarot_category").select("Ẩn phụ · Minor Arcana").run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.selectbox(key="tarot_inspect").options), 56)

    def test_real_mp4_import_and_overlay_keeps_card_face(self):
        from moviepy import ColorClip
        source = self.root / "effect.mp4"
        with ColorClip((64,64), color=(35,0,65), duration=.3) as clip:
            clip.write_videofile(str(source), fps=10, codec="libx264", audio=False, logger=None)
        effect_id = assets.save_effect("purple_smoke", source.read_bytes())
        self.assertEqual(assets.effects()[0]["id"], effect_id)
        self.save("major-19", (219,35,41))
        params = VideoParams(video_subject="Tarot", tarot_effect_ids=[effect_id])
        def inspect(clip, **kwargs):
            np.testing.assert_array_equal(clip.get_frame(1)[190,135], [219,35,41])
        with patch("app.services.video._write_videofile_with_codec_fallback", side_effect=inspect):
            tarot_video.render(self.root/"out.mp4", assets.select_cards("The Sun"), "The Sun", 2, params, resolution=(270,480))
        with self.assertRaises(ValueError):
            assets.save_effect("particles", b"not a video")
        self.assertEqual(len(assets.effects()), 1)


if __name__ == "__main__":
    unittest.main()
