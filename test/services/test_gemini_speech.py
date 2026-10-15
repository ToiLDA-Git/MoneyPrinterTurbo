import base64
import io
import json
import tempfile
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from google import genai
from google.genai import types

from app.services import gemini_speech as speech, tarot_voice


class GeminiSpeechTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.patcher=patch.object(speech,'STATE_ROOT',self.root/'quota')
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.text='Lá The Star gợi nhắc hy vọng. Hãy nhẹ nhàng với chính mình!'
        output=io.BytesIO()
        with wave.open(output,'wb') as wav:
            wav.setparams((1,2,24000,0,'NONE','not compressed'))
            wav.writeframes(b'\0\0'*2400)
        self.wav=output.getvalue()

    def connection(self,handler):
        connection=genai.Client(api_key='test-only-not-a-real-key',http_options=types.HttpOptions(
            client_args={'transport':httpx.MockTransport(handler)},retry_options=types.HttpRetryOptions(attempts=0)))
        speech.disable_sdk_retries(connection)
        return connection

    def test_both_38_models_use_sdk_interactions_and_verbatim_transcript(self):
        for model in list(speech.MODELS)[:2]:
            captured=[]
            def handler(request):
                captured.append(json.loads(request.content))
                self.assertTrue(request.url.path.endswith('/interactions'))
                return httpx.Response(200,json={'id':'test','status':'completed','model':model,
                    'steps':[{'type':'model_output','content':[{'type':'audio','data':base64.b64encode(self.wav).decode(),
                                                               'mime_type':'audio/wav'}]}]})
            with self.subTest(model=model),self.connection(handler) as client:
                data,mime=speech._request(client,model,self.text,'Kore','Kể ấm áp, dịu dàng.')
            self.assertEqual(data,self.wav)
            body=captured[0]
            self.assertEqual(body['input'][0]['content'][0]['text'],self.text)
            self.assertEqual(body['input'][0]['content'][0]['annotations'][0]['type'],'speech_metadata')
            self.assertEqual(body['generation_config']['speech_config'],[{'voice':'Kore'}])
            self.assertNotIn('contents',body)
            self.assertEqual(mime,'audio/wav')

    def test_25_uses_generate_content_schema_and_pcm(self):
        captured=[]
        pcm=b'\0\0'*2400
        def handler(request):
            captured.append(json.loads(request.content))
            self.assertIn(':generateContent',request.url.path)
            return httpx.Response(200,json={'candidates':[{'content':{'role':'model','parts':[
                {'inlineData':{'mimeType':'audio/L16;rate=24000','data':base64.b64encode(pcm).decode()}}]}}]})
        with self.connection(handler) as client:
            data,mime=speech._request(client,'gemini-2.5-flash-preview-tts',self.text,'Kore','Kể dịu dàng.')
        self.assertEqual(data,pcm)
        self.assertIn('generationConfig',captured[0])
        self.assertNotIn('input',captured[0])
        self.assertIn(self.text,captured[0]['contents'][0]['parts'][0]['text'])

    def test_export_validates_wav_and_does_not_add_a_second_header(self):
        output=self.root/'narration.wav'
        speech._export(self.wav,'audio/wav',output)
        with wave.open(str(output),'rb') as wav:
            self.assertEqual(wav.getnframes(),2400)
            self.assertEqual(wav.getframerate(),24000)
        speech._export(b'\0\0'*2400,'audio/L16;rate=24000',self.root/'legacy.mp3')
        self.assertGreater((self.root/'legacy.mp3').stat().st_size,100)

    def test_backoff_is_bounded_and_client_errors_are_not_retried(self):
        class ApiError(Exception):
            code=503
        call=Mock(side_effect=[ApiError('secret must not escape'),ApiError('secret'),'ok'])
        sleep=Mock()
        with patch.object(speech.random,'uniform',return_value=0):
            self.assertEqual(speech._retry(call,sleep=sleep),'ok')
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[1,2])
        error=ApiError('api-key-sensitive-value')
        error.code=403
        call=Mock(side_effect=error)
        with self.assertRaises(speech.SpeechError) as caught:
            speech._retry(call,sleep=sleep)
        self.assertNotIn('api-key-sensitive-value',str(caught.exception))
        self.assertEqual(call.call_count,1)

    def test_sdk_does_not_multiply_the_application_retry_count(self):
        calls=[]
        def handler(request):
            calls.append(request)
            return httpx.Response(503,json={'error':{'code':503,'status':'UNAVAILABLE','message':'private-key-value'}})
        with self.connection(handler) as client:
            with self.assertRaises(speech.SpeechError) as caught:
                speech._retry(lambda:speech._request(client,'gemini-3.8-flash-tts',self.text,'Kore',''),sleep=Mock())
        self.assertEqual(len(calls),3)
        self.assertNotIn('private-key-value',str(caught.exception))

    def test_single_and_batch_workers_export_actual_mp4s_with_both_google_schemas(self):
        from PIL import Image
        from app.services import subtitle, tarot_video, video
        from app.services import tarot_studio_worker as worker
        from app.services.tarot_batch import BatchStore, StudioSettings
        from app.config import config
        assets=self.root/'render-assets'
        (assets/'cards').mkdir(parents=True)
        Image.new('RGB',(120,200),'purple').save(assets/'cards'/'The Star.png')
        # A short synthetic provider response keeps real FFmpeg coverage cheap.
        current=[]
        def handler(request):
            body=json.loads(request.content)
            if request.url.path.endswith('/interactions'):
                current[:]=[body['input'][0]['content'][0]['text']]
                return httpx.Response(200,json={'id':'mock','status':'completed','steps':[{'type':'model_output',
                    'content':[{'type':'audio','data':base64.b64encode(self.wav).decode(),'mime_type':'audio/wav'}]}]})
            transcript=body['contents'][0]['parts'][0]['text'].split('Transcript:\n')[-1]
            current[:]=[transcript]
            return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'inlineData':{
                'data':base64.b64encode(b'\0\0'*2400).decode(),'mimeType':'audio/L16;rate=24000'}}]}}]})
        def transcribe(audio,output,**kwargs):
            tokens=current[0].split()
            tarot_voice.write_cues(output,[(i*.1/len(tokens),(i+1)*.1/len(tokens),w) for i,w in enumerate(tokens)])
        original_render,original_mix,original_validate=tarot_video.render,video.generate_video,worker.validate_output
        def render(*args,**kwargs):
            return original_render(*args,**kwargs,resolution=(120,200),fps=30)
        def mix(*args,**kwargs):
            args[4].subtitle_enabled=False
            return original_mix(*args,**kwargs)
        def validate(path,duration):
            original_validate(path,duration,resolution=(120,200))
        profiles=[tarot_voice.VoiceProfile(provider='gemini',voice_id='gemini:Kore',model=m,
                    directors_notes='Kể ấm áp.') for m in speech.MODELS]
        with patch.object(tarot_voice,'ROOT',self.root/'cache'), \
             patch.object(tarot_voice,'configured',return_value=True), \
             patch.object(speech,'client',side_effect=lambda:self.connection(handler)), \
             patch.object(subtitle,'create',side_effect=transcribe), \
             patch.object(tarot_video,'render',side_effect=render), \
             patch.object(video,'generate_video',side_effect=mix), \
             patch.object(worker,'validate_output',side_effect=validate), \
             patch.dict(config.app,dict(config.app)):
            for count in (1,3):
                store=BatchStore(self.root/f'render-{count}')
                batch=store.create(StudioSettings(asset_dir=str(assets),count=count,hardware=False,
                    voice_profile=profiles[0],random_voice_rotation=count>1,voice_pool=profiles),
                    manual_scripts=[self.text+f' Góc nhìn số {i+1}.' for i in range(count)])
                self.assertEqual(store.enqueue(batch),count)
                for _ in range(count):
                    key=store.claim()
                    worker.run_job(store,*key)
                jobs=store.jobs(batch)
                self.assertTrue(all(j['status']=='completed' for j in jobs),[j['error'] for j in jobs])
                self.assertTrue(all(store.output(batch,j['id']).is_file() for j in jobs))
                self.assertTrue(all(j['payload']['render']['actual_voice_provider']=='gemini' for j in jobs))

    def test_quota_fallback_is_explicit_and_uses_edge_word_timings(self):
        failure=speech.SpeechError(429,quota=True)
        maker=Mock()
        with patch.object(speech,'client',side_effect=failure), \
             patch('app.services.voice.azure_tts_v1',return_value=maker) as edge:
            result=speech.generate(self.text,'Kore',self.root/'audio.mp3',
                model='gemini-3.8-flash-tts',fallback=True,fallback_gender='Male')
        self.assertEqual(result.provider,'edge')
        self.assertIs(result.maker,maker)
        self.assertEqual(edge.call_args.args[0],self.text)
        self.assertEqual(edge.call_args.args[1],'vi-VN-NamMinhNeural-Male')
        self.assertIn('Edge TTS',result.notice)
        self.assertTrue(speech._quota_path().exists())

    def test_quota_does_not_fallback_when_disabled_and_auth_never_falls_back(self):
        for code in (429,401,403,404):
            with self.subTest(code=code),patch.object(speech,'client',side_effect=speech.SpeechError(code,quota=code==429)), \
                 patch('app.services.voice.azure_tts_v1') as edge:
                # A quota cooldown applies to the account, so reset between cases.
                speech._quota_path().unlink(missing_ok=True)
                with self.assertRaises(speech.SpeechError):
                    speech.generate(self.text,'Kore',self.root/'audio.mp3',fallback=code!=429)
                edge.assert_not_called()

    def test_model_verification_and_vi_voice_catalog_use_actual_sdk_endpoints(self):
        seen=[]
        def handler(request):
            seen.append(str(request.url))
            if request.url.path.endswith('/models'):
                return httpx.Response(200,json={'models':[{'name':'models/gemini-3.8-flash-tts','supportedGenerationMethods':['generateContent']}]})
            self.assertIn('language_code=vi-VN',str(request.url))
            return httpx.Response(200,json={'voices':[{'id':'voice_vi_01','display_name':'Giọng Việt','gender':'female','language_code':'vi-VN'}]})
        with self.connection(handler) as client,patch.object(speech,'client',return_value=client):
            models=speech.verify_models()
        self.assertTrue(models['gemini-3.8-flash-tts']['listed'])
        self.assertFalse(models['gemini-3.8-flash-lite-tts']['listed'])
        with self.connection(handler) as client,patch.object(speech,'client',return_value=client):
            voices=speech.available_voices('gemini-3.8-flash-tts')
        self.assertEqual(voices[0]['id'],'gemini:voice_vi_01')
        self.assertEqual(voices[0]['gender'],'Female')

    def test_model_and_director_notes_are_in_the_audio_cache_key(self):
        a=tarot_voice.VoiceProfile(provider='gemini',voice_id='gemini:Kore',model='gemini-3.8-flash-tts')
        for b in (a.model_copy(update={'model':'gemini-3.8-flash-lite-tts'}),
                  a.model_copy(update={'directors_notes':'Kể dịu dàng.'}),a.model_copy(update={'edge_fallback':False})):
            self.assertNotEqual(tarot_voice.cache_key(self.text,a),tarot_voice.cache_key(self.text,b))

    def test_live_probe_reports_cache_and_does_not_claim_skipped_quota_models_were_tested(self):
        metadata={m:dict(listed=True,schema=s,probed=False) for m,s in speech.MODELS.items()}
        def generate(text,voice,path,**kwargs):
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(self.wav)
        with patch.object(speech,'verify_models',side_effect=lambda:{k:v.copy() for k,v in metadata.items()}), \
             patch.object(speech,'generate',side_effect=generate) as call:
            first=speech.probe_models()
            second=speech.probe_models()
            self.assertEqual(call.call_count,3)
            self.assertTrue(all(v['audio_ready'] for v in first.values()))
            self.assertTrue(all(v['from_cache'] for v in second.values()))
        for path in speech.STATE_ROOT.rglob('*.wav'):
            path.unlink()
        def quota(*args,**kwargs):
            path=speech._quota_path()
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps({'until':time.time()+60}))
            raise speech.SpeechError(429,quota=True)
        with patch.object(speech,'verify_models',side_effect=lambda:{k:v.copy() for k,v in metadata.items()}), \
             patch.object(speech,'generate',side_effect=quota) as call:
            report=speech.probe_models()
            self.assertEqual(call.call_count,1)
            self.assertEqual(sum(v['probed'] for v in report.values()),1)

    def test_extended_voice_ids_are_preserved_for_38_and_rejected_for_25(self):
        identity='gemini:voice_vi-123-extended'
        profile=tarot_voice.VoiceProfile(provider='gemini',voice_id=identity,model='gemini-3.8-flash-tts')
        profile.checked()
        self.assertEqual(tarot_voice.voice_identity(profile)[1],'voice_vi-123-extended')
        with self.assertRaisesRegex(ValueError,'prebuilt'):
            profile.model_copy(update={'model':'gemini-2.5-flash-preview-tts'}).checked()

    def test_fallback_cache_records_actual_edge_voice_and_avoids_whisper(self):
        from app.services import subtitle
        profile=tarot_voice.VoiceProfile(provider='gemini',voice_id='gemini:Kore',model='gemini-3.8-flash-tts')
        def synth(text,profile,path,timeout):
            path.write_bytes(b'edge audio')
            return speech.AudioResult(maker=Mock(),provider='edge',voice_id='vi-VN-HoaiMyNeural-Female',notice='Edge quota fallback')
        def cues(maker,text,path,**kwargs):
            words=text.split()
            tarot_voice.write_cues(path,[(i/10,(i+1)/10,w) for i,w in enumerate(words)])
        with patch.object(tarot_voice,'_synthesize',side_effect=synth), \
             patch.object(tarot_voice.voice,'get_audio_duration',return_value=2), \
             patch.object(tarot_voice.voice,'create_subtitle',side_effect=cues), \
             patch.object(subtitle,'create',side_effect=AssertionError('Edge fallback must use its own measured boundaries')):
            result=tarot_voice.generate_cached(self.text,profile,root=self.root)
            self.assertEqual(result['actual_provider'],'edge')
            self.assertIn('expires',result)
            self.assertIn('Edge',result['notice'])

    def test_single_and_batch_generate_persist_model_style_notes_and_voice_per_video(self):
        from PIL import Image
        from streamlit.testing.v1 import AppTest
        from app.services.tarot_batch import BatchStore
        from webui import tarot_studio
        asset_root=self.root/'assets'
        (asset_root/'cards').mkdir(parents=True)
        Image.new('RGB',(120,200),'purple').save(asset_root/'cards'/'The Star.png')
        for count in (1,3):
            store=BatchStore(self.root/f'batch-{count}')
            manager=SimpleNamespace(store=store,configure=lambda _:None)
            profiles=[tarot_voice.VoiceProfile(provider='gemini',voice_id=f'gemini:{v}',model=m,
                        style=s,directors_notes=f'Kể ấm áp. Biến thể {i}.').model_dump()
                      for i,(m,v,s) in enumerate(zip(speech.MODELS,('Kore','Puck','Zephyr'),
                                                     ('mystical_female','deep_male','healing')))]
            with patch.object(tarot_studio,'manager',return_value=manager):
                app=AppTest.from_string('from webui.tarot_studio import render_studio\nrender_studio()')
                app.session_state['studio_asset_dir']=str(asset_root)
                app.session_state['studio_active_voice']=profiles[0]
                if count>1:
                    app.session_state['studio_voice_pool']=profiles
                    app.session_state['studio_random_voice_rotation']=True
                    app.session_state['voice_rotation_enabled']=True
                app.run(timeout=15)
                if count>1:
                    app.radio(key='studio_production').set_value('Batch Generate').run()
                    app.number_input(key='studio_count').set_value(count).run()
                app.button(key='studio_generate').click().run(timeout=15)
                self.assertEqual(len(app.exception),0)
                jobs=store.jobs(store.batches()[0]['id'])
                self.assertEqual(len(jobs),count)
                self.assertTrue(all(j['status']=='pending' for j in jobs))
                self.assertEqual(len({j['payload']['voice_profile']['model'] for j in jobs}),count)
                self.assertTrue(all(j['payload']['voice_profile']['directors_notes'] for j in jobs))


if __name__=='__main__':
    unittest.main()
