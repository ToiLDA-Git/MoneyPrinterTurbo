"""Google AI Studio TTS: explicit 3.8 Interactions and 2.5 generateContent schemas."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import ssl
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from google import genai
from google.genai import types

from app.config import config
from app.services import tarot_assets as assets

MODELS = {
    'gemini-3.8-flash-tts': 'interactions-3.8',
    'gemini-3.8-flash-lite-tts': 'interactions-3.8',
    'gemini-2.5-flash-preview-tts': 'generate-content-2.5',
}
DEFAULT_MODEL = 'gemini-2.5-flash-preview-tts'
STATE_ROOT = Path(__file__).resolve().parents[2]/'storage'/'tarot-voice'/'gemini'


class SpeechError(RuntimeError):
    """Only safe, fixed messages cross the service/UI boundary."""
    def __init__(self, code=0, *, quota=False, temporary=False):
        self.code, self.quota, self.temporary = code, quota, temporary
        message = ('Gemini TTS hết quota hoặc giới hạn tốc độ (429).'
                   if quota else f'Gemini TTS không khả dụng (mã {code or "kết nối"}).')
        super().__init__(message+' Kiểm tra model, quyền truy cập và cấu hình Google AI Studio.')


@dataclass
class AudioResult:
    maker: object = None
    provider: str = 'gemini'
    model: str = ''
    voice_id: str = ''
    notice: str = ''


def api_key():
    return str(config.app.get('gemini_api_key') or os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY') or '').strip()


def client():
    key=api_key()
    if not key:
        raise SpeechError(401)
    # OS trust store, never verify=False. Disable SDK retries to avoid nested retries.
    connection=genai.Client(api_key=key,http_options=types.HttpOptions(timeout=120000,
        retry_options=types.HttpRetryOptions(attempts=1),client_args={'verify':ssl.create_default_context()}))
    disable_sdk_retries(connection)
    return connection


def disable_sdk_retries(connection):
    # In SDK 2.31, HttpRetryOptions(attempts=0) is normalized to 1, and
    # Interactions interprets it as one *additional* retry. Its resource-level
    # retry strategy must be disabled to keep the outer budget truly bounded.
    for resource in (connection.interactions,connection.voices):
        resource.sdk_configuration.retry_config.strategy='none'


def _safe_error(error):
    try:
        code=int(getattr(error,'code',0) or getattr(error,'status_code',0) or 0)
    except (ValueError,TypeError):
        code=0
    # Inspect structured diagnostics privately. Never return their text or request URL.
    details=str(getattr(error,'details',''))+str(getattr(error,'response_json',''))
    exhausted=any(term in details.lower() for term in ('billing','perday','per_day','limit: 0','"quota_value": "0"'))
    return SpeechError(code,quota=code==429,temporary=code in (429,500,502,503,504) and not exhausted)


def _retry(call, *, attempts=3, sleep=time.sleep):
    for attempt in range(attempts):
        try:
            return call()
        except SpeechError:
            raise
        except Exception as error:
            safe=_safe_error(error)
            if not safe.temporary or attempt==attempts-1:
                raise safe from None
            sleep(min(30,2**attempt+random.uniform(0,.25)))


def verify_models():
    """Metadata access is not a claim that synthesis quota is available."""
    try:
        with client() as connection:
            listed=_retry(lambda:list(connection.models.list()))
        names={m.name.removeprefix('models/') for m in listed if m.name}
        return {name:dict(listed=name in names,schema=schema,probed=False)
                for name,schema in MODELS.items()}
    except SpeechError as error:
        return {name:dict(listed=False,schema=schema,probed=False,error=str(error),code=error.code)
                for name,schema in MODELS.items()}


def probe_models():
    """Explicit small synthesis probes, separately from free catalog discovery."""
    report=verify_models()
    for model in MODELS:
        target=STATE_ROOT/'probes'/_quota_path().stem/(model+'.wav')
        if target.exists() and time.time()-target.stat().st_mtime<300:
            report[model].update(probed=True,audio_ready=True,from_cache=True)
            report[model].pop('error',None)
            report[model].pop('code',None)
            continue
        if _in_cooldown():
            report[model].update(probed=False,audio_ready=False,code=429,
                error='Đang chờ quota hồi phục; chưa gửi request tổng hợp cho model này.')
            continue
        try:
            generate('Xin chào. Đây là lời đọc Tarot tiếng Việt.', 'Kore',target,
                     model=model,notes='Đọc tự nhiên, ấm áp.',fallback=False)
            report[model].update(probed=True,audio_ready=True,from_cache=False)
            report[model].pop('error',None)
            report[model].pop('code',None)
        except SpeechError as error:
            report[model].update(probed=True,audio_ready=False,error=str(error),code=error.code)
    return report


def available_voices(model):
    if model not in MODELS:
        raise ValueError('Unsupported Gemini speech model')
    from app.services import voice
    if MODELS[model]=='generate-content-2.5':
        # Official prebuilt voices bundled with the existing adapter; 2.5 does
        # not accept arbitrary IDs from the newer Extended Voice Library.
        return [dict(id=f'gemini:{name}',name=f'{name} · {style}',gender=None,source='official prebuilt')
                for name,style in voice.GEMINI_TTS_VOICES]
    with client() as connection:
        if not hasattr(connection,'voices'):
            raise SpeechError(426)
        response=_retry(lambda:connection.voices.list(language_code=['vi-VN'],type_=['prebuilt'],page_size=100))
        result=[]
        seen=set()
        while response:
            for item in response.voices or []:
                if item.id not in seen:
                    gender=str(getattr(item,'gender','')).title()
                    result.append(dict(id='gemini:'+item.id,name=item.display_name or item.id,
                                       gender=gender if gender in ('Male','Female') else None,source='Google Voice Library'))
                    seen.add(item.id)
            token=getattr(response,'next_page_token',None)
            if not token or token in seen:
                break
            seen.add(token)
            response=_retry(lambda:connection.voices.list(language_code=['vi-VN'],type_=['prebuilt'],page_size=100,page_token=token))
        return result


def _request(connection,model,text,voice_id,notes):
    schema=MODELS.get(model)
    if schema=='interactions-3.8':
        # Transcript and turn-level delivery are separate fields in 3.8.
        content=dict(type='text',text=text)
        if notes.strip():
            content['annotations']=[dict(type='speech_metadata',style=notes.strip())]
        response=connection.interactions.create(model=model,
            input=[dict(type='user_input',content=[content])],response_format={'type':'audio','mime_type':'audio/wav'},
            generation_config={'speech_config':[{'voice':voice_id}]})
        audio=getattr(response,'output_audio',None)
        if audio is None:
            for step in getattr(response,'steps',[]) or []:
                if getattr(step,'type','')=='model_output':
                    audio=next((c for c in getattr(step,'content',[]) or [] if getattr(c,'type','')=='audio'),audio)
        if audio is None:
            raise SpeechError(502)
        return _decode(audio.data),'audio/wav'
    if schema=='generate-content-2.5':
        prompt=(f'Read the transcript in natural Vietnamese. Delivery directions (do not read aloud): {notes}\n\nTranscript:\n{text}'
                if notes else text)
        response=connection.models.generate_content(model=model,contents=prompt,
            config=types.GenerateContentConfig(response_modalities=['AUDIO'],speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_id)))))
        for candidate in response.candidates or []:
            for part in getattr(candidate.content,'parts',[]) or []:
                data=getattr(part,'inline_data',None)
                if data and data.data:
                    return _decode(data.data),data.mime_type or 'audio/L16;rate=24000'
        raise SpeechError(502)
    raise ValueError('Unsupported Gemini speech model; no request schema registered')


def _decode(data):
    return base64.b64decode(data,validate=True) if isinstance(data,str) else bytes(data)


def _export(data,mime,output):
    from pydub import AudioSegment
    from app.services import voice
    voice._configure_pydub_ffmpeg(AudioSegment)
    output=Path(output)
    if len(data)>100*1024*1024 or not data:
        raise SpeechError(502)
    if mime.lower().startswith('audio/wav'):
        # Validate the actual WAV; never wrap it as headerless PCM.
        with wave.open(io.BytesIO(data),'rb') as wav:
            if wav.getnframes()<=0:
                raise SpeechError(502)
        segment=AudioSegment.from_file(io.BytesIO(data),format='wav')
    elif mime.lower().startswith(('audio/l16','audio/pcm')):
        import re
        rate=re.search(r'rate=(\d+)',mime,re.I)
        segment=AudioSegment.from_raw(io.BytesIO(data),sample_width=2,frame_rate=int(rate[1]) if rate else 24000,channels=1)
    else:
        raise SpeechError(415)
    output.parent.mkdir(parents=True,exist_ok=True)
    format='wav' if output.suffix.lower()=='.wav' else 'mp3'
    buffer=io.BytesIO()
    segment.export(buffer,format=format)
    assets._write(output,buffer.getvalue())


def _quota_path():
    identity=hashlib.sha256(api_key().encode()).hexdigest()
    return STATE_ROOT/(identity+'.json')


def _in_cooldown():
    try:
        path=_quota_path()
        return json.loads(path.read_text()).get('until',0)>time.time() if path.exists() else False
    except (ValueError,OSError):
        return False


def generate(text,voice_id,output,*,model=DEFAULT_MODEL,notes='',fallback=False,fallback_gender=None):
    if model not in MODELS:
        raise ValueError('Unsupported Gemini speech model')
    quota=_quota_path()
    cooldown=_in_cooldown()
    try:
        if cooldown:
            raise SpeechError(429,quota=True)
        with client() as connection:
            data,mime=_retry(lambda:_request(connection,model,text,voice_id,notes))
        _export(data,mime,output)
        return AudioResult(model=model,voice_id=voice_id)
    except SpeechError as error:
        if error.quota:
            assets._write(quota,json.dumps({'until':time.time()+60}).encode())
        if not error.quota or not fallback:
            raise
        from app.services import voice
        selected='vi-VN-NamMinhNeural-Male' if fallback_gender=='Male' else 'vi-VN-HoaiMyNeural-Female'
        maker=voice.azure_tts_v1(text,selected,1,str(output),timeout_seconds=150,raise_on_error=True)
        return AudioResult(maker=maker,provider='edge',voice_id=selected,
            notice='Gemini bị giới hạn quota; bản đọc này dùng Edge TTS tự nhiên. Phong cách Gemini/Director’s Notes không được áp dụng.')
    except Exception:
        # Conversion/SDK errors can contain request URLs, API keys or transcript.
        raise SpeechError(502) from None
