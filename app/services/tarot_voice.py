"""Capability-aware Tarot speech, persisted profiles and process-safe audio reuse."""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from difflib import SequenceMatcher
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.config import config
from app.services import tarot_assets as assets, voice
from app.utils import utils

ROOT = Path(__file__).resolve().parents[2] / 'storage' / 'tarot-voice'
PROVIDERS = {
    'edge': 'Edge TTS · miễn phí', 'azure': 'Azure Speech', 'elevenlabs': 'ElevenLabs',
    'gemini': 'Gemini TTS', 'minimax': 'MiniMax', 'fish_audio': 'Fish Audio',
    'siliconflow': 'SiliconFlow', 'mimo': 'MiMo', 'chatterbox': 'Chatterbox',
    'kokoro': 'Kokoro', 'voxcpm': 'VoxCPM',
}
STYLES = {
    'natural': ('Tự nhiên', '', 1.0, None),
    'mystical_female': ('Mystical Female', 'A calm, mysterious female storyteller; intimate, reflective pauses.', .94, 'Female'),
    'romantic_female': ('Soft Romantic Female', 'A warm, soft romantic female delivery, gentle and conversational.', .95, 'Female'),
    'whispering_female': ('Whispering Female', 'An actual gentle female whisper with breathy whispered articulation, not just quieter speech.', 1.0, 'Female'),
    'healing': ('Healing Storyteller', 'A soothing, reassuring storyteller with patient, warm phrasing.', .94, None),
    'deep_male': ('Deep Male Narrator', 'A deep, resonant male narrator, grounded and unhurried.', .94, 'Male'),
    'energetic_female': ('Energetic TikTok Female', 'A bright, energetic female short-video presenter; clear and lively.', 1.12, 'Female'),
}


class VoiceProfile(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    provider: str = 'edge'
    voice_id: str = Field(default='vi-VN-HoaiMyNeural-Female', min_length=1, max_length=300)
    style: str = 'natural'
    speed: float = Field(default=1, ge=.25, le=4)
    pitch: int = Field(default=0, ge=-50, le=50)
    volume: float = Field(default=1, ge=0, le=2)
    model: str = ''
    directors_notes: str = Field(default='',max_length=2000)
    edge_fallback: bool = True

    def checked(self):
        if self.provider not in PROVIDERS or self.style not in STYLES:
            raise ValueError('Unknown speech provider or voice style')
        if self.provider=='gemini':
            from app.services.gemini_speech import MODELS, DEFAULT_MODEL
            if self.model and self.model not in MODELS:
                raise ValueError('Unknown Gemini TTS model/schema')
            if (self.model or DEFAULT_MODEL)=='gemini-2.5-flash-preview-tts' and voice.parse_gemini_voice_name(self.voice_id) not in {name for name,_ in voice.GEMINI_TTS_VOICES}:
                raise ValueError('Gemini 2.5 TTS requires a supported prebuilt voice; use 3.8 for Voice Library IDs')
        if any(c in self.voice_id for c in '\r\n\x00'):
            raise ValueError('Invalid voice ID')
        if self.provider in ('edge', 'azure'):
            allowed = {item['id'] for item in catalog(self.provider)}
            if self.voice_id not in allowed:
                raise ValueError('Choose a supported Vietnamese voice')
        elif not self.voice_id.startswith(self.provider+':'):
            raise ValueError('Voice ID must belong to the selected provider')
        caps = capabilities(self.provider)
        if not caps['speed'] and self.speed != 1:
            raise ValueError('Selected provider does not support numeric speed; use 1.0')
        if not caps['min_speed'] <= self.speed <= caps['max_speed']:
            raise ValueError('Speech speed is outside this provider/model range')
        if not caps['pitch'] and self.pitch:
            raise ValueError('Selected provider does not support pitch')
        if abs(self.pitch) > caps['pitch_max']:
            raise ValueError('Pitch is outside this provider range')
        return self


def capabilities(provider):
    if provider not in PROVIDERS:
        raise ValueError('Unknown speech provider')
    caps = dict(speed=True, min_speed=.5, max_speed=2.0, pitch=False, pitch_max=0,
                pitch_unit='', styles=False, measured_timing=False)
    if provider == 'edge':
        caps.update(pitch=True, pitch_max=50, pitch_unit='Hz', measured_timing=True)
    elif provider == 'azure':
        caps.update(pitch=True, pitch_max=30, pitch_unit='%', measured_timing=True)
    elif provider == 'minimax':
        caps.update(pitch=True, pitch_max=12, pitch_unit='semitones')
    elif provider == 'gemini':
        caps.update(speed=False, styles=True)
    elif provider in ('mimo', 'voxcpm'):
        caps.update(speed=False)
    elif provider == 'elevenlabs':
        model = config.elevenlabs.get('model_id', 'eleven_multilingual_v2')
        caps.update(min_speed=.7, max_speed=1.2)
        if model == 'eleven_v3':
            caps.update(min_speed=.5, max_speed=2.0)
        elif model in ('eleven_v4', 'eleven_v4_turbo'):
            caps.update(speed=False)
    return caps


def configured(provider):
    """Presence checks only; credentials never enter profiles or UI messages."""
    if provider == 'edge':
        return True
    if provider == 'azure':
        return bool(config.azure.get('speech_key') and config.azure.get('speech_region'))
    if provider == 'gemini':
        from app.services.gemini_speech import api_key
        return bool(api_key())
    if provider == 'elevenlabs':
        return bool(voice.get_elevenlabs_api_key())
    if provider == 'minimax':
        return bool(voice.get_minimax_tts_api_key())
    if provider == 'fish_audio':
        return bool(voice.get_fish_audio_api_key())
    if provider in ('mimo', 'voxcpm'):
        settings = config.app if provider == 'mimo' else config.voxcpm
        return bool(settings.get('mimo_api_key') if provider == 'mimo' else settings.get('api_key') and settings.get('model_id'))
    settings = getattr(config, provider)
    return bool(settings.get('base_url') if provider in ('chatterbox', 'kokoro') else settings.get('api_key'))


def catalog(provider, *, remote=False):
    """No remote catalog calls during widget reruns; refresh is explicit."""
    if provider in ('edge', 'azure'):
        suffix = '-V2' if provider == 'azure' else ''
        return [dict(id=f'vi-VN-{name}Neural{suffix}-{gender}', name=label, gender=gender)
                for name, label, gender in [('HoaiMy', 'Hoài My · nữ', 'Female'), ('NamMinh', 'Nam Minh · nam', 'Male')]]
    if provider == 'elevenlabs':
        return voice.get_elevenlabs_voices(voice.get_elevenlabs_api_key(), with_metadata=True) if remote else []
    elif provider == 'minimax' and remote:
        return [dict(id='minimax:'+v['voice_id'],name=v['voice_name'],gender=None)
                for v in voice.get_minimax_voice_catalog()]
    elif provider == 'kokoro':
        entries = voice.get_kokoro_voices(fallback=not remote) if remote else []
    else:
        getter = getattr(voice, f'get_{"minimax" if provider == "minimax" else provider}_voices', None)
        entries = getter() if getter else []
    return [dict(id=entry, name=entry.split(':', 2)[-1],
                 gender='Female' if entry.endswith('-Female') else 'Male' if entry.endswith('-Male') else None)
            for entry in entries]


def apply_style(profile, style):
    profile = profile.model_copy(deep=True)
    profile.style = style
    _, _, rate, gender = STYLES[style]
    voices = catalog(profile.provider)
    matching = next((v for v in voices if v['gender'] == gender), None) if gender else None
    if matching:
        profile.voice_id = matching['id']
    caps = capabilities(profile.provider)
    profile.speed = max(caps['min_speed'], min(caps['max_speed'], rate)) if caps['speed'] else 1
    profile.pitch = 0
    # Whisper is a delivery style, never a gain or synthetic pitch trick.
    return profile.checked()


def style_notice(profile):
    if profile.style != 'natural' and not capabilities(profile.provider)['styles']:
        return 'Provider/giọng này không có điều khiển phong cách đó. Dùng giọng tự nhiên và tốc độ đã chọn; không giả lập thì thầm bằng âm lượng.'
    return ''


def voice_identity(profile):
    """Display names/gender suffixes do not create a different narrator."""
    if profile.provider in ('edge','azure'):
        identity=voice.parse_voice_name(profile.voice_id)
    elif profile.provider=='gemini':
        identity=voice.parse_gemini_voice_name(profile.voice_id)
    elif profile.provider in ('elevenlabs','fish_audio'):
        identity=profile.voice_id.split(':')[1]
    else:
        identity=profile.voice_id.split(':',1)[1]
        if profile.provider in ('chatterbox','kokoro'):
            identity=identity.removesuffix('-Female').removesuffix('-Male')
        elif profile.provider=='mimo':
            identity=identity.split('-')[0]
    return profile.provider,identity


def choose_rotation(profiles, count, rng=None):
    """Shuffle bags avoid adjacent repetition; the assigned profile is persisted."""
    pool = list({p.model_dump_json(): p.checked() for p in profiles}.values())
    if not pool:
        raise ValueError('Add at least one voice to the rotation')
    rng = rng or random.Random()
    groups = {}
    for profile in pool:
        groups.setdefault(voice_identity(profile),[]).append(profile)
    result, last = [], None
    while len(result) < count:
        bag = list(groups)
        rng.shuffle(bag)
        if len(bag) > 1 and bag[0] == last:
            bag[0], bag[1] = bag[1], bag[0]
        for identity in bag:
            result.append(rng.choice(groups[identity]))
            last=identity
    return [p.model_copy(deep=True) for p in result[:count]]


@contextmanager
def _locked(directory):
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory/'lock.sqlite3', timeout=600)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS mutex(id INTEGER PRIMARY KEY)')
        db.execute('BEGIN IMMEDIATE')
        yield
        db.commit()
    finally:
        db.close()


def load_presets(root=None):
    path = Path(root or ROOT)/'presets.json'
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    return {name: VoiceProfile.model_validate(p).checked() for name, p in value.items()}


def save_preset(name, profile, root=None):
    name = name.strip()
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError('Preset name must contain 1–80 printable characters')
    root = Path(root or ROOT)
    with _locked(root/'preset-lock'):
        profiles = load_presets(root)
        profiles[name] = profile.checked()
        assets._write(root/'presets.json', json.dumps({k: v.model_dump() for k,v in profiles.items()}, ensure_ascii=False, indent=2).encode())


def _provider_identity(provider):
    if provider == 'edge':
        return 'edge-v1'
    if provider == 'gemini':
        from app.services.gemini_speech import api_key
        settings = dict(api_key=api_key(),adapter='model-specific-schemas-v1')
    elif provider == 'mimo':
        settings = {k:config.app.get(k) for k in ('mimo_api_key','mimo_base_url','mimo_tts_model_name','mimo_tts_style_prompt')}
    else:
        section = getattr(config, 'minimax_tts' if provider == 'minimax' else provider)
        fields = {
            'azure': ('speech_key','speech_region'),
            'elevenlabs': ('model_id',), 'fish_audio': ('model',),
            'minimax': ('model_id','sample_rate','bitrate','audio_format','channel'),
            'siliconflow': ('api_key',),
        }.get(provider, ('api_key','base_url','model_id'))
        settings = {k:section.get(k) for k in fields}
        if provider == 'elevenlabs':
            settings['api_key']=voice.get_elevenlabs_api_key()
        elif provider == 'fish_audio':
            settings['api_key']=voice.get_fish_audio_api_key()
        elif provider == 'minimax':
            settings.update(api_key=voice.get_minimax_tts_api_key(),endpoint=voice.get_minimax_tts_endpoint())
    # Include model, endpoint and account changes without persisting their values.
    return hashlib.sha256(json.dumps(settings, sort_keys=True, default=str).encode()).hexdigest()


def cache_key(script, profile):
    profile.checked()
    request = profile.model_dump(exclude={'volume'})
    if profile.provider!='gemini':
        for field in ('model','directors_notes','edge_fallback'):
            request.pop(field,None)
    else:
        from app.services.gemini_speech import DEFAULT_MODEL
        request['model']=profile.model or DEFAULT_MODEL
    request['speed'] = float(profile.speed)
    request['voice_id'] = voice_identity(profile)[1]
    if not capabilities(profile.provider)['styles']:
        request['style'] = 'natural'
    data = dict(version=1, text=script, request=request, provider_revision=_provider_identity(profile.provider))
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _synthesize(script, profile, path, timeout):
    provider = profile.provider
    if not configured(provider):
        raise ValueError(f'{PROVIDERS[provider]} chưa cấu hình. Dùng phần Settings hiện có hoặc chọn Edge TTS.')
    if provider == 'edge':
        return voice.azure_tts_v1(script, profile.voice_id, profile.speed, str(path),
                                  pitch_hz=profile.pitch, timeout_seconds=timeout, raise_on_error=True)
    if provider == 'azure':
        return voice.azure_tts_v2(script, profile.voice_id, str(path), profile.speed, pitch_percent=profile.pitch)
    if provider == 'gemini':
        return voice.gemini_tts(script, voice.parse_gemini_voice_name(profile.voice_id), 1, str(path),
                                style_prompt=STYLES[profile.style][1],model_id=profile.model or 'gemini-2.5-flash-preview-tts',
                                directors_notes=profile.directors_notes,edge_fallback=profile.edge_fallback,
                                fallback_gender=STYLES[profile.style][3],return_result=True)
    if provider == 'minimax':
        return voice.minimax_tts(script, profile.voice_id.split(':',1)[1], profile.speed, str(path),
                                 pitch_override=profile.pitch)
    return voice.tts(script, profile.voice_id, profile.speed, str(path), voice_volume=1)


def read_cues(path):
    from app.services.tarot_video import _seconds
    result = []
    for block in re.split(r'\n\s*\n', Path(path).read_text(encoding='utf-8-sig').strip()):
        lines = block.splitlines()
        index = next((i for i,v in enumerate(lines) if ' --> ' in v), None)
        if index is not None:
            start, end = [_seconds(v.strip()) for v in lines[index].split(' --> ')]
            text = ' '.join(lines[index+1:]).strip()
            if text:
                result.append((start, end, text))
    return result


def _read_json(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (ValueError,OSError):
        return {}


def align_script(script, measured, duration):
    """Restore the exact written words onto measured speech anchors."""
    words = script.split()
    clean = lambda s: re.sub(r'\W', '', assets.normalize(s))
    observed = []
    for start, end, content in measured:
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or end > duration+1:
            raise ValueError('Invalid measured subtitle timing')
        tokens = content.split()
        for i, word in enumerate(tokens):
            observed.append((start+(end-start)*i/len(tokens), start+(end-start)*(i+1)/len(tokens), word))
    if any(a[0] > b[0] for a,b in zip(observed,observed[1:])):
        raise ValueError('Subtitle timing must be ordered')
    matcher = SequenceMatcher(None, [clean(w) for w in words], [clean(w[2]) for w in observed], autojunk=False)
    anchors = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            anchors[block.a+offset] = observed[block.b+offset][:2]
    if not words or len(anchors)/len(words) < .6:
        raise ValueError('Audio transcript does not match the Vietnamese script. Review pronunciation/voice or supply matching SRT.')
    # Interpolate only unmatched spans between measured anchors (e.g. English card names).
    result = []
    index = 0
    while index < len(words):
        if index in anchors:
            start, end = anchors[index]
            result.append((start, min(end,duration), words[index]))
            index += 1
            continue
        stop = index
        while stop < len(words) and stop not in anchors:
            stop += 1
        start = result[-1][1] if result else 0
        end = anchors[stop][0] if stop in anchors else duration
        if end <= start:
            raise ValueError('Cannot align unmatched text to measured audio; upload a corrected SRT')
        for i in range(index, stop):
            result.append((start+(end-start)*(i-index)/(stop-index), start+(end-start)*(i-index+1)/(stop-index), words[i]))
        index = stop
    return result


def write_cues(path, cues):
    assets._write(Path(path), '\n'.join(utils.text_to_srt(i, text, start, end)
                                       for i,(start,end,text) in enumerate(cues,1)).encode('utf-8'))


def sentence_cues(words):
    result, group = [], []
    for cue in words:
        group.append(cue)
        if cue[2].endswith(('.', '!', '?', '…', ':', ';')) or len(group) >= 14:
            result.append((group[0][0],group[-1][1],' '.join(c[2] for c in group)))
            group = []
    if group:
        result.append((group[0][0],group[-1][1],' '.join(c[2] for c in group)))
    return result


def generate_cached(script, profile, *, root=None, timeout=150, require_alignment=True):
    """Cache raw audio and both subtitle modes. Gain is local and costs no new TTS."""
    profile.checked()
    if not script.strip() or len(script) > 12000:
        raise ValueError('Narration must contain 1–12000 characters')
    identity = cache_key(script, profile)
    directory = Path(root or ROOT)/'cache'/identity
    audio, words, sentences, manifest = [directory/name for name in ('audio.mp3','words.srt','sentences.srt','result.json')]
    with _locked(directory):
        if manifest.exists():
            saved = _read_json(manifest)
            if saved.get('expires',float('inf'))>time.time() and saved.get('key')==identity and isinstance(saved.get('hashes'),dict) and all(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == saved['hashes'].get(p.name)
                   for p in (audio,words,sentences)):
                return dict(saved, audio=str(audio), words=str(words), sentences=str(sentences), cache_hit=True)
        # Keep successfully synthesized audio even if later Whisper alignment fails.
        raw_manifest = directory/'audio.json'
        audio_hash = hashlib.sha256(audio.read_bytes()).hexdigest() if audio.exists() else ''
        raw = _read_json(raw_manifest)
        maker = None
        synthesized = False
        measured_timing=raw.get('actual_provider',profile.provider) in ('edge','azure')
        if raw.get('expires',float('inf'))<=time.time() or not audio_hash or audio_hash != raw.get('sha256') or (measured_timing and not words.exists()):
            maker = _synthesize(script, profile, audio, timeout)
            synthesized = True
            if maker is None or not audio.is_file():
                raise ValueError(f'{PROVIDERS[profile.provider]} không tạo được audio. Kiểm tra cấu hình/quota hoặc chọn Edge TTS.')
            duration = voice.get_audio_duration(str(audio))
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError('Provider returned invalid narration audio')
            (directory/'measured.srt').unlink(missing_ok=True)
            raw = dict(sha256=hashlib.sha256(audio.read_bytes()).hexdigest(), duration=duration)
            from app.services.gemini_speech import AudioResult
            if isinstance(maker,AudioResult):
                raw.update(actual_provider=maker.provider,actual_model=maker.model,actual_voice=maker.voice_id,notice=maker.notice)
                if maker.provider=='edge' and profile.provider=='gemini':
                    raw['expires']=time.time()+60
                maker=maker.maker
            else:
                raw.update(actual_provider=profile.provider,actual_voice=profile.voice_id)
            measured_timing=raw['actual_provider'] in ('edge','azure')
            if measured_timing:
                voice.create_subtitle(maker, script, str(words), word_level=True)
            assets._write(raw_manifest,json.dumps(raw).encode())
        duration = raw['duration']
        provenance={k:raw[k] for k in ('actual_provider','actual_model','actual_voice','notice','expires') if k in raw}
        if not require_alignment and not measured_timing:
            return dict(key=identity,audio=str(audio),duration=duration,cache_hit=not synthesized,
                        timing_source='pending Whisper alignment',**provenance)
        if measured_timing:
            measured = read_cues(words) if words.exists() else []
            timing_source = 'provider word boundaries'
        else:
            from app.services import subtitle
            measured_path = directory/'measured.srt'
            if not measured_path.exists():
                subtitle.create(str(audio),str(measured_path),word_level=True,log_details=False,language='vi')
            if not measured_path.exists():
                raise ValueError('Whisper alignment unavailable. Configure/download the existing Whisper model, retry (audio is cached), or supply local SRT.')
            measured = read_cues(measured_path)
            timing_source = 'Whisper audio alignment'
        try:
            aligned = align_script(script, measured, duration)
        except ValueError:
            if not measured_timing:
                measured_path.unlink(missing_ok=True)
            raise
        write_cues(words, aligned)
        write_cues(sentences, sentence_cues(aligned))
        saved = dict(key=identity, duration=duration, timing_source=timing_source,**provenance,
                     hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (audio,words,sentences)})
        assets._write(manifest,json.dumps(saved).encode())
        return dict(saved,audio=str(audio),words=str(words),sentences=str(sentences),cache_hit=False)


def preview_audio(result, volume):
    """Apply playback gain locally, identically to the video mixer, never to TTS."""
    if not math.isfinite(volume) or not 0 <= volume <= 2:
        raise ValueError('Invalid playback volume')
    if volume == 1:
        return result['audio']
    source=Path(result['audio'])
    target=source.parent/f'preview-{volume:.3f}.wav'
    with _locked(source.parent/'preview-lock'):
        if not target.exists():
            process=subprocess.run([utils.get_ffmpeg_binary(),'-y','-v','error','-i',str(source),
                                    '-af',f'volume={volume:.6f}',str(target)],capture_output=True,timeout=60,
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if process.returncode:
                target.unlink(missing_ok=True)
                raise ValueError('Cannot prepare local volume preview')
    return str(target)
