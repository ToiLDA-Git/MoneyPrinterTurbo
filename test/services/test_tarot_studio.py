import tempfile
import json
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from PIL import Image

from app.services import tarot_registry as registry
from app.services.tarot_batch import BatchStore, StudioSettings


class TarotStudioTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.assets = self.root / "assets"
        (self.assets / "cards").mkdir(parents=True)
        for name in ("The Sun", "The Moon", "The Star"):
            Image.new("RGB", (120, 200), "purple").save(self.assets / "cards" / f"{name}.png")
        self.store = BatchStore(self.root / "output")

    def create(self, **kwargs):
        return self.store.create(StudioSettings(asset_dir=str(self.assets), **kwargs))

    def test_voice_rotation_and_per_job_changes_survive_reload_and_regeneration(self):
        from app.services.tarot_voice import VoiceProfile
        female=VoiceProfile()
        male=VoiceProfile(voice_id='vi-VN-NamMinhNeural-Male')
        batch=self.create(count=6,random_voice_rotation=True,voice_pool=[female,male])
        identities=[j['payload']['voice_profile']['voice_id'] for j in self.store.jobs(batch)]
        self.assertTrue(all(a!=b for a,b in zip(identities,identities[1:])))
        self.store.regenerate_script(batch,1)
        self.assertEqual(self.store.job(batch,1)['payload']['voice_profile']['voice_id'],identities[0])
        self.store.save_input(batch,1,'audio',b'old narration','.wav')
        selected=male.model_copy(update={'speed':.9,'pitch':-3,'volume':.65})
        self.store.set_voice(batch,1,selected)
        reloaded=BatchStore(self.store.root)
        payload=reloaded.job(batch,1)['payload']
        self.assertEqual(payload['voice_profile'],selected.model_dump())
        self.assertEqual((payload['audio'],payload['srt']),('',''))
        reloaded.enqueue(batch)
        with self.assertRaises(ValueError):
            reloaded.set_voice(batch,1,female)

    def test_volume_only_change_keeps_uploaded_narration(self):
        from app.services.tarot_voice import VoiceProfile
        batch=self.create()
        self.store.save_input(batch,1,'audio',b'local narration','.wav')
        self.store.save_input(batch,1,'srt',b'local cues','.srt')
        before=self.store.job(batch,1)['payload']
        self.store.set_voice(batch,1,VoiceProfile(volume=.5))
        after=self.store.job(batch,1)['payload']
        self.assertEqual((before['audio'],before['srt']),(after['audio'],after['srt']))

    def test_voice_tab_and_job_voice_editor_render_inside_existing_studio(self):
        from streamlit.testing.v1 import AppTest
        from webui import tarot_studio
        self.create()
        fake=SimpleNamespace(store=self.store,configure=lambda _:None)
        with patch.object(tarot_studio,'manager',return_value=fake):
            app=AppTest.from_string('from webui.tarot_studio import render_studio\nrender_studio()')
            app.session_state['studio_asset_dir']=str(self.assets)
            app.run(timeout=15)
            self.assertEqual(len(app.exception),0)
            self.assertIn('Voice Studio',[tab.label for tab in app.tabs])
            self.assertTrue(any(s.label=='Nhà cung cấp giọng đọc' for s in app.selectbox))

    def test_online_worker_reuses_voice_cache_and_preserves_selected_speed_and_gain(self):
        from app.services import tarot_voice, tarot_video, video
        from app.services import tarot_studio_worker as worker
        profile=tarot_voice.VoiceProfile(speed=.9,pitch=3,volume=.45)
        batch=self.create(voice_profile=profile,hardware=False)
        self.store.enqueue(batch)
        self.store.claim()
        cached_audio=self.root/'cached.mp3'
        cached_srt=self.root/'cached.srt'
        cached_audio.write_bytes(b'cached audio')
        cached_srt.write_text('cached subtitle',encoding='utf-8')
        cached=dict(audio=str(cached_audio),words=str(cached_srt),sentences=str(cached_srt),
                    key='voice-cache-key',cache_hit=True,timing_source='provider word boundaries')
        def fit(*args,**kwargs):
            Path(args[3]).write_text('synchronized subtitles',encoding='utf-8')
            return dict(original_audio_duration=28,narration_duration=28,speech_rate_adjustment=1)
        def mix(*args,**kwargs):
            self.assertEqual(args[4].voice_volume,.45)
            Path(args[3]).write_bytes(b'finished video')
            return True
        with patch.object(tarot_voice,'generate_cached',return_value=cached) as generate, \
             patch.object(worker,'fit_audio',side_effect=fit) as fitting, \
             patch.object(tarot_video,'render'),patch.object(video,'generate_video',side_effect=mix), \
             patch.object(worker,'validate_output'):
            worker.run_job(self.store,batch,1)
        job=self.store.job(batch,1)
        self.assertEqual(job['status'],'completed',job['error'])
        self.assertEqual(generate.call_args.args[1],profile)
        self.assertTrue(fitting.call_args.kwargs['preserve_speed'])
        self.assertTrue(job['payload']['render']['voice_cache_hit'])
        self.assertTrue(cached_audio.exists())

    def test_audio_fitting_handles_long_short_and_mild_mismatches(self):
        import wave
        import numpy as np
        from app.services import voice
        from app.services.tarot_studio_worker import fit_audio, _stamp
        script='Lá The Sun gợi nhắc niềm vui và sự sáng tỏ trong hôm nay.'
        for duration, target, expected in ((59.2,27,59.2), (5,57,5), (29,27,27), (25,27,27)):
            with self.subTest(duration=duration, target=target):
                source=self.root/'source.wav'
                srt=self.root/'source.srt'
                out=self.root/'fitted.wav'
                subtitle=self.root/'fitted.srt'
                with wave.open(str(source),'wb') as audio:
                    audio.setparams((1,2,16000,0,'NONE','not compressed'))
                    audio.writeframes(np.zeros(round(duration*16000),dtype='<i2').tobytes())
                srt.write_text(f'1\n00:00:00,000 --> {_stamp(duration)}\n{script}\n',encoding='utf-8')
                result=fit_audio(source,srt,out,subtitle,target,script)
                self.assertAlmostEqual(result['narration_duration'],expected,delta=.1)
                self.assertAlmostEqual(voice.get_audio_duration(str(out)),expected,delta=.1)
                self.assertIn(script,subtitle.read_text(encoding='utf-8'))
                self.assertIn(_stamp(expected),subtitle.read_text(encoding='utf-8'))
                self.assertGreaterEqual(result['speech_rate_adjustment'],.8)
                self.assertLessEqual(result['speech_rate_adjustment'],1.2)

    def test_adapted_worker_uses_same_timeline_for_render_validation_and_export(self):
        from app.services import tarot_video, video
        from app.services import tarot_studio_worker as worker
        batch=self.create(offline=True,hardware=False)
        self.store.save_input(batch,1,'audio',b'local audio','.wav')
        self.store.save_input(batch,1,'srt',b'local subtitle','.srt')
        self.store.enqueue(batch)
        self.store.claim()
        timing={'original_audio_duration':59.2,'narration_duration':59.2,
                'requested_narration_duration':27,'speech_rate_adjustment':1}
        def fit(*args):
            Path(args[3]).write_text('synchronized subtitles',encoding='utf-8')
            return timing.copy()
        def mix(*args,**kwargs):
            Path(args[3]).write_bytes(b'finished video')
            return True
        with patch.object(worker,'fit_audio',side_effect=fit), \
             patch.object(tarot_video,'render') as render, \
             patch.object(video,'generate_video',side_effect=mix), \
             patch.object(worker,'validate_output') as validate:
            worker.run_job(self.store,batch,1)
        job=self.store.job(batch,1)
        self.assertEqual(job['status'],'completed',job['error'])
        self.assertAlmostEqual(render.call_args.args[3],59.2)
        self.assertAlmostEqual(validate.call_args.args[1],62.2)
        self.assertAlmostEqual(job['payload']['render']['output_duration'],62.2)
        self.assertIn('62.2', (self.store.batch_dir(batch)/'metadata.csv').read_text(encoding='utf-8-sig'))

    def test_generated_script_budget_preserves_all_selected_cards(self):
        from app.services import tarot_scripts as scripts
        selected=registry.selected_cards(registry.discover(self.assets),['major-19'])
        lengths=[]
        for duration in (30,45,60):
            metadata=scripts.generate('daily',selected,duration,cta_duration=8)
            scripts.validate_script(metadata['script'],['major-19'])
            self.assertIn(scripts.DISCLAIMER,metadata['script'])
            lengths.append(len(metadata['script'].split()))
            self.assertLessEqual(lengths[-1],int((duration-8)*3))
        self.assertLess(lengths[0],lengths[-1])
        selected=registry.selected_cards(registry.discover(self.assets),['major-19','major-18','major-17'])
        metadata=scripts.generate('daily',selected,30)
        scripts.validate_script(metadata['script'],[c['id'] for c in selected])

    def test_long_music_does_not_extend_exported_mp4(self):
        import numpy as np
        from moviepy import ColorClip, AudioClip, VideoFileClip
        from app.models.schema import VideoParams
        from app.services import video
        from app.services.tarot_studio_worker import validate_output

        picture, narration, music, final = [self.root/name for name in
                                            ('picture.mp4', 'voice.wav', 'music.wav', 'final.mp4')]
        with ColorClip((120, 200), color=(80, 0, 100), duration=1) as clip:
            clip.write_videofile(str(picture), fps=10, codec='libx264', audio=False, logger=None)
        for path, duration in ((narration, .7), (music, 65)):
            with AudioClip(lambda t: .05*np.sin(2*np.pi*220*t), duration=duration, fps=44100) as clip:
                clip.write_audiofile(str(path), codec='pcm_s16le', logger=None)
        params = VideoParams(video_subject='test', subtitle_enabled=False,
                             bgm_type='custom', bgm_volume=.12, n_threads=1)
        with patch.object(video, '_get_configured_video_codec', return_value='libx264'):
            self.assertTrue(video.generate_video(str(picture), str(narration), '', str(final),
                                                 params, bgm_file_override=str(music)))
        validate_output(final, 1, resolution=(120, 200))
        with VideoFileClip(str(final)) as clip:
            self.assertLess(abs(clip.duration-1), .15)
            self.assertLess(abs(clip.audio.duration-1), .15)

    def effect(self, name):
        directory = self.assets / "effects"
        directory.mkdir(exist_ok=True)
        path = directory / name
        path.write_bytes(b"not a video; unused assets should never be decoded")
        return path

    def test_directory_first_classification_and_known_filenames(self):
        expected = {"card-flip.mp4": "CARD_FLIP", "purple-smoke.mp4": "SMOKE",
                    "magical-light.mp4": "LIGHT", "gold-particles.mp4": "PARTICLES",
                    "transition.mp4": "TRANSITION"}
        for filename, category in expected.items():
            self.effect(filename)
            self.assertEqual(registry.classify_file(f"effects/{filename}"), category)
            self.assertEqual(registry.classify_file(f"backgrounds/{filename}"), "BACKGROUND")
        for suffix in ("png", "jpg"):
            self.assertEqual(registry.classify_file(f"cards/card-back.{suffix}"), "CARD_IMAGE")
        self.assertIsNone(registry.classify_file("cards/purple-smoke.mp4"))
        self.assertEqual({e['name']: e['category'] for e in registry.discover(self.assets)['effects']}, expected)

    def test_background_never_enters_unknown_effects(self):
        directory = self.assets / "backgrounds"
        directory.mkdir()
        (directory / "purple-smoke-background.mp4").write_bytes(b"unused background")
        Image.new("RGB", (120, 200)).save(directory / "purple-smoke-background.png")
        report = registry.discover(self.assets)
        self.assertEqual(len(report['backgrounds']), 2)
        self.assertEqual(report['unknown_effects'], [])
        self.assertFalse(any('chưa xác định' in w for w in report['warnings']))

    def test_unknown_unused_is_warning_but_required_is_actionable_error(self):
        path = self.effect("unfamiliar.mp4")
        report = registry.discover(self.assets)
        self.assertTrue(any(str(path) in w for w in report['warnings']))
        registry.validate_required(report)
        self.assertEqual(report['effects'], [])
        batch = self.create(count=2)
        self.assertEqual(self.store.enqueue(batch), 2)
        with self.assertRaisesRegex(ValueError, 'unfamiliar.mp4.*Assign its category'):
            self.create(effects=['effects/unfamiliar.mp4'])

    def test_manifest_assignment_survives_new_process_and_clears_warning(self):
        self.effect("custom.mp4")
        self.assertEqual(registry.discover(self.assets)['unknown_effects'], ['custom.mp4'])
        registry.correct_mapping(self.assets, 'effects', 'custom.mp4', 'TRANSITION')
        manifest = json.loads((self.assets / registry.MANIFEST_NAME).read_text())
        self.assertEqual(manifest['effects']['custom.mp4'], 'TRANSITION')
        report = registry.discover(self.assets)
        self.assertEqual(report['unknown_effects'], [])
        self.assertFalse(any('custom.mp4' in w for w in report['warnings']))
        result = subprocess.run([sys.executable, '-c',
                                 'import sys; from app.services.tarot_registry import discover; '
                                 'r=discover(sys.argv[1]); assert not r["unknown_effects"]; '
                                 'assert r["effects"][0]["category"]=="TRANSITION"', str(self.assets)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_legacy_manifest_categories_are_read_and_migrated(self):
        self.effect('custom.mp4')
        (self.assets / registry.MANIFEST_NAME).write_text(json.dumps({'effects': {'custom.mp4': 'purple_smoke'}}))
        self.assertEqual(registry.discover(self.assets)['effects'][0]['category'], 'SMOKE')
        registry.correct_mapping(self.assets, 'effects', 'custom.mp4', 'magical_light')
        self.assertEqual(registry.read_mapping(self.assets)['effects']['custom.mp4'], 'LIGHT')

    def test_enqueue_revalidates_selected_effect_after_manifest_changes(self):
        self.effect('custom.mp4')
        registry.correct_mapping(self.assets, 'effects', 'custom.mp4', 'SMOKE')
        # Mock decoding only while preparing the draft; enqueue must read disk again.
        with patch.object(registry, 'validate_required'):
            batch = self.create(effects=['effects/custom.mp4'])
        registry.correct_mapping(self.assets, 'effects', 'custom.mp4', None)
        with self.assertRaisesRegex(ValueError, 'custom.mp4.*Assign its category'):
            self.store.enqueue(batch)
        self.assertEqual(self.store.job(batch, 1)['status'], 'draft')

    def test_corrupt_selected_video_fails_but_unused_one_does_not(self):
        self.effect('purple-smoke.mp4')
        self.create()
        with self.assertRaisesRegex(ValueError, 'Required asset cannot be decoded: effects/purple-smoke.mp4'):
            self.create(effects=['effects/purple-smoke.mp4'])

    def test_assignment_refreshes_webui_without_stale_session_warnings(self):
        from streamlit.testing.v1 import AppTest
        self.effect('mystery.mp4')
        source = ("from webui.tarot_studio import _assets_panel\n"
                  "from app.services.tarot_registry import discover\n"
                  f"_assets_panel(discover({str(self.assets)!r}))")
        app = AppTest.from_string(source).run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any('mystery.mp4' in w.value for w in app.warning))
        identity = f'{self.assets.resolve()}:mystery.mp4'
        app.selectbox(key=f'studio_unknown_category:{identity}').select('PARTICLES')
        app.button(key=f'studio_unknown_save:{identity}').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertFalse(any('mystery.mp4' in w.value for w in app.warning))
        app.run()
        self.assertFalse(any('mystery.mp4' in w.value for w in app.warning))
        self.assertEqual(registry.discover(self.assets)['effects'][0]['category'], 'PARTICLES')

    def test_saved_job_warnings_are_replaced_by_current_scan_diagnostics(self):
        self.effect('mystery.mp4')
        report = registry.discover(self.assets)
        saved = report['warnings'] + ['Có MP4 chưa xác định loại. Gán loại hiệu ứng trước khi dùng.',
                                       'Music mixing failed; narration-only output retained.']
        registry.correct_mapping(self.assets, 'effects', 'mystery.mp4', 'LIGHT')
        refreshed = registry.current_render_warnings(registry.discover(self.assets), saved)
        self.assertFalse(any('chưa xác định' in w for w in refreshed))
        self.assertIn('Music mixing failed; narration-only output retained.', refreshed)

    def test_selected_background_video_decodes_and_does_not_require_effect_assignment(self):
        from moviepy import ColorClip
        directory = self.assets / 'backgrounds'
        directory.mkdir()
        path = directory / 'purple-smoke-background.mp4'
        with ColorClip((120, 200), color=(80, 0, 100), duration=.4) as clip:
            clip.write_videofile(str(path), fps=5, codec='libx264', audio=False, logger=None)
        self.effect('unused.mp4')
        batch = self.create(background='backgrounds/purple-smoke-background.mp4')
        self.assertEqual(self.store.enqueue(batch), 1)
        report = registry.discover(self.assets)
        self.assertEqual(report['unknown_effects'], ['unused.mp4'])

    def test_external_assignment_changes_and_file_removal_refresh_on_rerun(self):
        from streamlit.testing.v1 import AppTest
        path = self.effect('external.mp4')
        source = ("from webui.tarot_studio import _assets_panel\n"
                  "from app.services.tarot_registry import discover\n"
                  f"_assets_panel(discover({str(self.assets)!r}))")
        app = AppTest.from_string(source).run()
        registry.correct_mapping(self.assets, 'effects', 'external.mp4', 'SMOKE')
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertFalse(any('external.mp4' in w.value for w in app.warning))
        registry.correct_mapping(self.assets, 'effects', 'external.mp4', None)
        app.run()
        self.assertTrue(any('external.mp4' in w.value for w in app.warning))
        path.unlink()
        app.button(key='studio_rescan').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertFalse(any('external.mp4' in w.value for w in app.warning))

    def test_rescan_adds_resources_and_preserves_form_settings(self):
        from streamlit.testing.v1 import AppTest
        source = ("from webui.tarot_studio import _assets_panel, _configuration\n"
                  "from app.services.tarot_registry import discover\n"
                  f"report=discover({str(self.assets)!r})\n"
                  "_assets_panel(report)\n_configuration(report)")
        app = AppTest.from_string(source).run()
        app.slider(key='studio_music_volume').set_value(.25).run()
        directory = self.assets / 'backgrounds'
        directory.mkdir()
        Image.new('RGB', (120, 200)).save(directory/'new-background.png')
        self.effect('gold-particles.mp4')
        music = self.assets / 'music'
        music.mkdir()
        (music/'new-music.wav').write_bytes(b'preview only')
        app.button(key='studio_rescan').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn('backgrounds/new-background.png', app.selectbox(key='studio_background').options)
        self.assertIn('music/new-music.wav', app.selectbox(key='studio_music').options)
        self.assertIn('effects/gold-particles.mp4', app.multiselect(key='studio_effects').options)
        self.assertEqual(app.slider(key='studio_music_volume').value, .25)
        self.assertTrue(any('Đã quét lại' in item.value for item in app.success))
        self.assertTrue(any('Quét thủ công gần nhất:' in item.value for item in app.caption))

    def test_rescan_clears_removed_selection_without_resetting_valid_assets(self):
        from streamlit.testing.v1 import AppTest
        background = self.assets/'backgrounds'
        background.mkdir()
        Image.new('RGB', (120, 200)).save(background/'keep.png')
        effect = self.effect('card-flip.mp4')
        source = ("from webui.tarot_studio import _assets_panel, _configuration\n"
                  "from app.services.tarot_registry import discover\n"
                  f"report=discover({str(self.assets)!r})\n"
                  "_assets_panel(report)\n_configuration(report)")
        app = AppTest.from_string(source).run()
        app.selectbox(key='studio_background').select('backgrounds/keep.png').run()
        app.multiselect(key='studio_effects').select('effects/card-flip.mp4').run()
        effect.unlink()
        app.button(key='studio_rescan').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(app.multiselect(key='studio_effects').value, [])
        self.assertEqual(app.selectbox(key='studio_background').value, 'backgrounds/keep.png')
        self.assertTrue(any('effects/card-flip.mp4' in w.value for w in app.warning))

    def test_local_registry_duplicate_mapping_requires_correction(self):
        Image.new("RGB", (120, 200)).save(self.assets / "cards" / "major-19.png")
        report = registry.discover(self.assets)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            registry.selected_cards(report, ["major-19"])
        registry.correct_mapping(self.assets, "cards", "major-19.png", None)
        self.assertEqual(len(registry.selected_cards(registry.discover(self.assets), ["major-19"])), 1)

    def test_batch_persists_and_claim_respects_concurrency(self):
        batch = self.create(count=3)
        self.assertEqual(len({j["payload"]["fingerprint"] for j in self.store.jobs(batch)}), 3)
        self.assertEqual(self.store.enqueue(batch), 3)
        self.assertIsNotNone(self.store.claim(1))
        self.assertIsNone(self.store.claim(1))
        reloaded = BatchStore(self.store.root)
        self.assertEqual(sum(j["status"] == "running" for j in reloaded.jobs(batch)), 1)
        self.assertEqual(reloaded.cancel_pending(batch), 2)
        self.assertEqual(sum(j["status"] == "running" for j in reloaded.jobs(batch)), 1)

    def test_changed_script_invalidates_uploaded_narration(self):
        batch = self.create(offline=True)
        self.store.save_input(batch, 1, "audio", b"placeholder", ".wav")
        self.store.save_input(batch, 1, "srt", b"placeholder", ".srt")
        script = self.store.job(batch, 1)["payload"]["script"]
        self.store.edit(batch, 1, script=script)
        self.assertTrue(self.store.job(batch, 1)["payload"]["audio"])
        self.store.edit(batch, 1, script=script + " Hãy dành thời gian suy nghĩ thêm.")
        payload = self.store.job(batch, 1)["payload"]
        self.assertEqual((payload["audio"], payload["srt"]), ("", ""))
        self.assertEqual(self.store.enqueue(batch), 0)
        self.assertEqual(self.store.job(batch, 1)["status"], "failed")

    def test_active_job_cannot_be_edited_and_dead_worker_can_retry(self):
        batch = self.create()
        self.store.enqueue(batch)
        with self.assertRaises(ValueError):
            self.store.edit(batch, 1, audio="changed.wav")
        self.store.claim()
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET updated=0")
        with patch("app.services.tarot_batch.process_alive", return_value=False):
            self.store.recover()
        self.assertEqual(self.store.job(batch, 1)["status"], "interrupted")
        self.assertEqual(self.store.enqueue(batch, failed_only=True), 1)
        self.store.claim()
        self.assertEqual(self.store.job(batch, 1)["attempt"], 2)

    def test_zip_includes_only_completed_videos(self):
        batch = self.create(count=2)
        self.store.output(batch, 1).write_bytes(b"completed video")
        self.store.output(batch, 2).write_bytes(b"old unfinished video")
        self.store.patch(batch, 1, status="completed", progress=100)
        with zipfile.ZipFile(self.store.zip_completed(batch)) as archive:
            self.assertEqual(set(archive.namelist()), {"video-001.mp4", "metadata.csv", "scripts.json", "captions.txt"})

    def test_dashboard_allows_switching_batches(self):
        from streamlit.testing.v1 import AppTest
        from webui import tarot_studio

        first, second = self.create(), self.create()
        app = AppTest.from_string(
            "from webui.tarot_studio import dashboard, manager\n"
            "dashboard(manager())"
        )
        with patch.object(tarot_studio, "manager", return_value=SimpleNamespace(store=self.store)):
            app.session_state["studio_selected_batch"] = first
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.selectbox(key="studio_batch_picker").value, first)
            app.selectbox(key="studio_batch_picker").select(second).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.selectbox(key="studio_batch_picker").value, second)


if __name__ == "__main__":
    unittest.main()
