import json
import random
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from app.services import tarot_voice as studio
from app.services import tarot_video


class TarotVoiceTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.script='Lá The Star gợi nhắc hy vọng. Hãy nhẹ nhàng với chính mình!'
        self.words=self.script.split()
        self.cues=[(i*.15,(i+1)*.15,w) for i,w in enumerate(self.words)]
        self.duration=len(self.words)*.15

    def fake_synthesis(self, script, profile, path, timeout):
        path.write_bytes(b'generated audio')
        return Mock()

    def fake_subtitle(self, maker, script, path, word_level=False):
        studio.write_cues(path,self.cues)

    def cache_patches(self):
        return (patch.object(studio,'_synthesize',side_effect=self.fake_synthesis),
                patch.object(studio.voice,'create_subtitle',side_effect=self.fake_subtitle),
                patch.object(studio.voice,'get_audio_duration',return_value=self.duration))

    def test_cache_reuses_preview_audio_across_volume_changes_and_invalidates_voice_pitch_text(self):
        synth,sub,duration=self.cache_patches()
        profile=studio.VoiceProfile()
        with synth as generate,sub,duration:
            first=studio.generate_cached(self.script,profile,root=self.root)
            second=studio.generate_cached(self.script,profile.model_copy(update={'volume':.4}),root=self.root)
            self.assertFalse(first['cache_hit'])
            self.assertTrue(second['cache_hit'])
            self.assertEqual(generate.call_count,1)
            self.assertEqual(first['audio'],second['audio'])
            for changed in (profile.model_copy(update={'pitch':3}),
                            profile.model_copy(update={'speed':1.1}),
                            profile.model_copy(update={'voice_id':'vi-VN-NamMinhNeural-Male'})):
                self.assertNotEqual(studio.cache_key(self.script,changed),first['key'])
            self.assertNotEqual(studio.cache_key(self.script+'?',profile),first['key'])

    def test_cache_concurrent_requests_synthesize_only_once(self):
        def delayed(*args):
            time.sleep(.1)
            return self.fake_synthesis(*args)
        with patch.object(studio,'_synthesize',side_effect=delayed) as synth, \
             patch.object(studio.voice,'create_subtitle',side_effect=self.fake_subtitle), \
             patch.object(studio.voice,'get_audio_duration',return_value=self.duration):
            with ThreadPoolExecutor(max_workers=2) as workers:
                results=list(workers.map(lambda _:studio.generate_cached(self.script,studio.VoiceProfile(),root=self.root),range(2)))
            self.assertEqual(synth.call_count,1)
            self.assertEqual(sum(r['cache_hit'] for r in results),1)

    def test_paid_preview_skips_whisper_and_render_reuses_the_same_audio(self):
        from app.services import subtitle
        profile=studio.VoiceProfile(provider='gemini',voice_id='gemini:Zephyr-Bright')
        def align(audio,output,**kwargs):
            self.assertEqual(kwargs['language'],'vi')
            studio.write_cues(output,self.cues)
        with patch.object(studio,'_synthesize',side_effect=self.fake_synthesis) as synth, \
             patch.object(studio.voice,'get_audio_duration',return_value=self.duration), \
             patch.object(subtitle,'create',side_effect=align) as transcribe:
            preview=studio.generate_cached(self.script,profile,root=self.root,require_alignment=False)
            transcribe.assert_not_called()
            render=studio.generate_cached(self.script,profile,root=self.root)
            self.assertEqual(preview['audio'],render['audio'])
            self.assertEqual(synth.call_count,1)
            self.assertEqual(transcribe.call_count,1)

    def test_corrupt_cache_manifest_rebuilds_timing_without_new_tts(self):
        synth,sub,duration=self.cache_patches()
        with synth as generate,sub,duration:
            result=studio.generate_cached(self.script,studio.VoiceProfile(),root=self.root)
            (Path(result['audio']).parent/'result.json').write_text('invalid json')
            recovered=studio.generate_cached(self.script,studio.VoiceProfile(),root=self.root)
            self.assertEqual(generate.call_count,1)
            for actual,expected in zip(studio.read_cues(recovered['words']),self.cues):
                self.assertAlmostEqual(actual[0],expected[0],delta=.002)
                self.assertAlmostEqual(actual[1],expected[1],delta=.002)
                self.assertEqual(actual[2],expected[2])

    def test_native_pitch_controls_reach_edge_and_escaped_azure_ssml(self):
        captured={}
        class Communicate:
            def __init__(self,text,name,rate,pitch='+0Hz',boundary='WordBoundary'):
                captured.update(text=text,pitch=pitch,rate=rate)
        with patch.object(studio.voice.edge_tts,'Communicate',Communicate):
            studio.voice.create_edge_tts_communicate(self.script,'vi-VN-HoaiMyNeural','-5%',pitch='+12Hz')
        self.assertEqual(captured['text'],self.script)
        self.assertEqual(captured['pitch'],'+12Hz')
        ssml=studio.voice._build_azure_v2_ssml('Giữ dấu & câu <nguyên vẹn>.','vi-VN-HoaiMyNeural',.9,pitch_percent=-5)
        self.assertIn('pitch="-5%"',ssml)
        self.assertIn('Giữ dấu &amp; câu &lt;nguyên vẹn&gt;.',ssml)

    def test_model_changes_invalidate_audio_but_unrelated_music_settings_do_not(self):
        profile=studio.VoiceProfile(provider='elevenlabs',voice_id='elevenlabs:custom:Name')
        with patch.dict(studio.config.elevenlabs,{'model_id':'eleven_multilingual_v2','music_model_id':'first'}):
            first=studio.cache_key(self.script,profile)
            studio.config.elevenlabs['music_model_id']='different'
            self.assertEqual(studio.cache_key(self.script,profile),first)
            studio.config.elevenlabs['model_id']='eleven_v3'
            self.assertNotEqual(studio.cache_key(self.script,profile),first)

    def test_alignment_failure_keeps_paid_audio_and_retry_does_not_regenerate(self):
        from app.services import subtitle
        profile=studio.VoiceProfile(provider='gemini',voice_id='gemini:Zephyr-Bright')
        def align(audio,output,**kwargs):
            studio.write_cues(output,self.cues)
        with patch.object(studio,'_synthesize',side_effect=self.fake_synthesis) as synth, \
             patch.object(studio.voice,'get_audio_duration',return_value=self.duration), \
             patch.object(subtitle,'create',return_value=None):
            with self.assertRaisesRegex(ValueError,'audio is cached'):
                studio.generate_cached(self.script,profile,root=self.root)
        with patch.object(studio,'_synthesize',side_effect=AssertionError('Paid API called twice')), \
             patch.object(subtitle,'create',side_effect=align):
            result=studio.generate_cached(self.script,profile,root=self.root)
        self.assertEqual(synth.call_count,1)
        self.assertEqual(result['timing_source'],'Whisper audio alignment')

    def test_style_fallback_does_not_fake_whisper_by_reducing_volume(self):
        profile=studio.apply_style(studio.VoiceProfile(),'whispering_female')
        self.assertEqual(profile.volume,1)
        self.assertEqual(profile.speed,1)
        self.assertEqual(profile.pitch,0)
        self.assertTrue(studio.style_notice(profile))
        self.assertEqual(studio.cache_key(self.script,profile),studio.cache_key(self.script,studio.VoiceProfile()))

    def test_provider_controls_and_ranges_are_validated(self):
        for provider in ('gemini','mimo','voxcpm'):
            with self.subTest(provider=provider):
                with self.assertRaises(ValueError):
                    studio.VoiceProfile(provider=provider,voice_id=f'{provider}:test',speed=1.1).checked()
        with self.assertRaises(ValueError):
            studio.VoiceProfile(provider='elevenlabs',voice_id='elevenlabs:test',pitch=1).checked()
        with self.assertRaises(ValueError):
            studio.VoiceProfile(voice_id='en-US-AriaNeural-Female').checked()
        with self.assertRaises(ValueError):
            studio.VoiceProfile(provider='gemini',voice_id='elevenlabs:someone').checked()

    def test_dispatch_reuses_edge_azure_gemini_and_existing_other_adapters(self):
        cases=[('edge','vi-VN-HoaiMyNeural-Female','azure_tts_v1',dict(pitch=4)),
               ('azure','vi-VN-NamMinhNeural-V2-Male','azure_tts_v2',dict(pitch=5)),
               ('gemini','gemini:Zephyr-Bright','gemini_tts',dict(style='whispering_female')),
               ('minimax','minimax:custom','minimax_tts',dict(pitch=2)),
               ('elevenlabs','elevenlabs:custom:Name','tts',{}),
               ('fish_audio','fish_audio:custom:Name','tts',{}),
               ('siliconflow','siliconflow:model:voice','tts',{}),
               ('mimo','mimo:default','tts',{}),
               ('chatterbox','chatterbox:custom','tts',{}),
               ('kokoro','kokoro:custom','tts',{}),
               ('voxcpm','voxcpm:custom','tts',{})]
        for provider,identity,method,options in cases:
            with self.subTest(provider=provider), patch.object(studio,'configured',return_value=True), \
                 patch.object(studio.voice,method,return_value='maker') as generate:
                profile=studio.VoiceProfile(provider=provider,voice_id=identity,**options).checked()
                self.assertEqual(studio._synthesize(self.script,profile,self.root/'audio.mp3',150),'maker')
                self.assertEqual(generate.call_args.args[0],self.script)
                if provider=='edge':
                    self.assertEqual(generate.call_args.kwargs['pitch_hz'],4)
                if provider=='azure':
                    self.assertEqual(generate.call_args.kwargs['pitch_percent'],5)
                if provider=='gemini':
                    self.assertIn('actual gentle female whisper',generate.call_args.kwargs['style_prompt'])

    def test_custom_presets_persist_without_credentials(self):
        profile=studio.apply_style(studio.VoiceProfile(),'deep_male')
        studio.save_preset('Giọng kể đêm',profile,self.root)
        self.assertEqual(studio.load_presets(self.root)['Giọng kể đêm'],profile)
        saved=json.loads((self.root/'presets.json').read_text(encoding='utf-8'))
        self.assertEqual(set(saved['Giọng kể đêm']),set(studio.VoiceProfile.model_fields))

    def test_rotation_has_no_adjacent_repetition_and_is_stable_after_assignment(self):
        pool=[studio.VoiceProfile(),studio.VoiceProfile(voice_id='vi-VN-NamMinhNeural-Male')]
        profiles=studio.choose_rotation(pool,100,random.Random(3))
        self.assertEqual(len(profiles),100)
        self.assertTrue(all(a!=b for a,b in zip(profiles,profiles[1:])))
        self.assertEqual(sum(p.voice_id==pool[0].voice_id for p in profiles),50)

    def test_voice_display_names_do_not_bust_cache_or_duplicate_rotation_identity(self):
        first=studio.VoiceProfile(provider='elevenlabs',voice_id='elevenlabs:abc:First name')
        renamed=first.model_copy(update={'voice_id':'elevenlabs:abc:Renamed'})
        other=first.model_copy(update={'voice_id':'elevenlabs:xyz:Another voice'})
        self.assertEqual(studio.cache_key(self.script,first),studio.cache_key(self.script,renamed))
        rotation=studio.choose_rotation([first,renamed,other],20,random.Random(4))
        self.assertTrue(all(studio.voice_identity(a)!=studio.voice_identity(b) for a,b in zip(rotation,rotation[1:])))

    def test_alignment_restores_vietnamese_punctuation_and_rejects_wrong_audio(self):
        measured=[(s,e,studio.assets.normalize(t).strip('.!')) for s,e,t in self.cues]
        aligned=studio.align_script(self.script,measured,self.duration)
        self.assertEqual(' '.join(c[2] for c in aligned),self.script)
        with self.assertRaisesRegex(ValueError,'does not match'):
            studio.align_script(self.script,[(0,1,'entirely unrelated recording')],self.duration)

    def test_card_transitions_find_names_split_across_word_cues(self):
        path=self.root/'words.srt'
        studio.write_cues(path,[(0,.5,'Lá'),(.5,1,'The'),(1,1.5,'Sun'),
                                (4,4.5,'và'),(5,5.5,'The'),(5.5,6,'Moon.')])
        timeline=tarot_video.card_timeline('The Sun và The Moon', [{'id':'major-19'},{'id':'major-18'}],10,str(path))
        self.assertEqual(timeline,[(0,'major-19'),(5,'major-18')])

    def test_voice_ui_shows_only_supported_controls_and_makes_no_api_calls(self):
        from streamlit.testing.v1 import AppTest
        source="from webui.tarot_voice_studio import profile_editor\nprofile_editor('editor')"
        with patch.object(studio,'_synthesize',side_effect=AssertionError('Unexpected API call')):
            app=AppTest.from_string(source).run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any(s.label=='Cao độ (Hz)' for s in app.slider))
            app.selectbox(key='editor:provider').select('gemini').run()
            self.assertEqual(len(app.exception),0)
            self.assertFalse(any('Cao độ' in s.label or s.label=='Tốc độ đọc' for s in app.slider))
            app.selectbox(key='editor:style').select('whispering_female').run()
            self.assertEqual(len(app.exception),0)

    def test_voice_tab_saves_preset_and_applies_rotation_without_api_calls(self):
        from streamlit.testing.v1 import AppTest
        with patch.object(studio,'ROOT',self.root), \
             patch.object(studio,'_synthesize',side_effect=AssertionError('Unexpected API call')):
            app=AppTest.from_string('from webui.tarot_voice_studio import render_voice_studio\nrender_voice_studio()').run()
            self.assertEqual(len(app.exception),0)
            app.text_input(key='voice_preset_name').set_value('Giọng Tarot của tôi')
            app.button(key='voice_save_preset').click().run()
            self.assertIn('Giọng Tarot của tôi',studio.load_presets())
            app.button(key='voice_apply').click().run()
            self.assertEqual(app.session_state['studio_active_voice']['provider'],'edge')
            app.button(key='voice_pool_add').click().run()
            app.checkbox(key='voice_rotation_enabled').check().run()
            self.assertTrue(app.session_state['studio_random_voice_rotation'])
            self.assertEqual(len(app.exception),0)


if __name__=='__main__':
    unittest.main()
