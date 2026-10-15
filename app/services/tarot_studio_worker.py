"""One isolated Tarot render. Never searches stock or publishes to social media."""
from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import subprocess
import time
from difflib import SequenceMatcher
from pathlib import Path

from app.services import tarot_assets as assets, tarot_registry as registry, tarot_scripts as scripts
from app.services.tarot_batch import BatchStore


def _run(command, timeout=180):
    result=subprocess.run(command,capture_output=True,timeout=timeout,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise ValueError('FFmpeg failed: '+result.stderr.decode('utf-8',errors='replace')[-800:])


def hardware_codec(ffmpeg):
    for codec in ('h264_nvenc','h264_qsv','h264_amf','h264_videotoolbox'):
        try:
            _run([ffmpeg,'-v','error','-f','lavfi','-i','color=c=black:s=128x128:r=1',
                  '-frames:v','1','-c:v',codec,'-f','null','-'],timeout=12)
            return codec
        except (ValueError,subprocess.TimeoutExpired):
            continue
    return 'libx264'


def _stamp(seconds):
    milliseconds=max(0,round(seconds*1000))
    h,milliseconds=divmod(milliseconds,3600000)
    m,milliseconds=divmod(milliseconds,60000)
    s,ms=divmod(milliseconds,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def fit_audio(audio, srt, output_audio, output_srt, target, script, *, preserve_speed=False):
    """Fit mild mismatches; adapt the timeline for large ones without cutting speech."""
    from app.services import voice
    from app.services.tarot_video import _seconds
    from app.utils import utils
    source_duration=voice.get_audio_duration(str(audio))
    if not math.isfinite(source_duration) or source_duration <= 0:
        raise ValueError('Narration audio could not be decoded')
    if not math.isfinite(target) or target <= 0:
        raise ValueError('Narration target duration must be positive')
    requested_target = target
    ratio=source_duration/target
    if preserve_speed or ratio > 1.2 or ratio < .8:
        target = source_duration
        ratio = 1.0
    rate=ratio
    text=Path(srt).read_text(encoding='utf-8-sig')
    cues=[]
    for block in re.split(r'\n\s*\n',text.strip()):
        lines=block.splitlines()
        line=next((i for i,v in enumerate(lines) if ' --> ' in v),None)
        if line is None:
            continue
        start,end=(_seconds(v.strip()) for v in lines[line].split(' --> '))
        content=' '.join(lines[line+1:]).strip()
        if not content or start<0 or end<=start or end>source_duration+1:
            raise ValueError('Invalid SRT cues or subtitle timings do not fit the narration')
        cues.append((start,end,content))
    if not cues or any(a[0]>b[0] for a,b in zip(cues,cues[1:])):
        raise ValueError('SRT must contain ordered, nonempty synchronized cues')
    clean=lambda value: re.sub(r'\W','',assets.normalize(value))
    if SequenceMatcher(None,clean(' '.join(c[2] for c in cues)),clean(script),autojunk=False).ratio()<.65:
        raise ValueError('SRT transcript does not match this job script; upload matching audio and subtitles')
    subtitle=[]
    for index,(start,end,content) in enumerate(cues,1):
        begin,finish=start/rate,min(target,end/rate)
        if begin>=finish:
            continue
        subtitle.append(f'{index}\n{_stamp(begin)} --> {_stamp(finish)}\n{content}\n')
    Path(output_srt).write_text('\n'.join(subtitle),encoding='utf-8')
    _run([utils.get_ffmpeg_binary(),'-y','-v','error','-i',str(audio),'-af',
          f'atempo={rate:.8f},apad,atrim=duration={target:.6f}', '-ar','44100','-ac','1',str(output_audio)])
    return {'original_audio_duration':source_duration,'requested_narration_duration':requested_target,
            'narration_duration':target,'speech_rate_adjustment':rate}


def validate_output(path, duration, resolution=(1080,1920)):
    from moviepy import VideoFileClip
    if not Path(path).is_file() or Path(path).stat().st_size<1000:
        raise ValueError('FFmpeg did not produce a usable MP4')
    with VideoFileClip(str(path)) as clip:
        if tuple(clip.size)!=tuple(resolution) or abs(clip.duration-duration)>.15 or clip.audio is None:
            raise ValueError(f'Output validation failed: size={clip.size}, duration={clip.duration}, audio={clip.audio is not None}')
        clip.get_frame(max(0,clip.duration-.1))


def safe_cleanup(work, batch_directory):
    work=Path(work).resolve()
    allowed=(Path(batch_directory).resolve()/'.work').resolve()
    if not work.is_relative_to(allowed) or work==allowed:
        raise ValueError('Refusing to clean outside the job temporary directory')
    if work.exists():
        shutil.rmtree(work)


def run_job(store, batch_id, job_id):
    from app.config import config
    from app.models.schema import VideoParams
    from app.services import tarot_video, video, voice
    from app.services import tarot_voice
    from app.utils import utils

    job=store.job(batch_id,job_id)
    if not job or job['status']!='running':
        return
    settings=store.settings(batch_id)
    directory=store.batch_dir(batch_id)
    work=directory/'.work'/f'video-{job_id:03}'/f'attempt-{job["attempt"]}'
    work.mkdir(parents=True,exist_ok=True)
    store.patch(batch_id,job_id,pid=os.getpid(),progress=3)
    payload=job['payload']
    last_heartbeat=[0.0]

    def progress(value):
        if time.monotonic()-last_heartbeat[0]>1:
            store.patch(batch_id,job_id,progress=value)
            last_heartbeat[0]=time.monotonic()

    try:
        report=registry.discover(settings.asset_dir)
        registry.validate_required(report, effects=settings.effects, background=settings.background,
                                   card_back=settings.card_back, logo=settings.logo, music=settings.music)
        selected=registry.selected_cards(report,payload['card_ids'])
        script=scripts.validate_script(payload['script'],payload['card_ids'])
        profile=tarot_voice.VoiceProfile.model_validate(payload.get('voice_profile') or
            (settings.voice_profile.model_dump() if settings.voice_profile else {'voice_id':settings.voice})).checked()
        cached_voice=None
        style={**payload.get('visual',{}),'opacity':settings.opacity*(.75+payload.get('visual',{}).get('opacity',.5)),
               'intensity':settings.intensity*payload.get('visual',{}).get('intensity',1),
               'effect_duration':settings.effect_duration,'cta_text':settings.cta_text,
               'smoke':settings.smoke,'glow':settings.glow,'particles':settings.particles,'flip':settings.flip}
        if not settings.zoom:
            style['zoom']=0
        style['effect_paths']=[str(registry.resolve(report['root'],f)) for f in settings.effects]
        for field,setting in [('background_path',settings.background),('logo_path',settings.logo),
                              ('card_back_path',settings.card_back or next(iter(report['card_backs']),''))]:
            if setting:
                style[field]=str(registry.resolve(report['root'],setting))
        source_audio,source_srt=work/'source.mp3',work/'source.srt'
        if payload.get('audio'):
            source_audio=registry.resolve(directory,payload['audio'])
            if not payload.get('srt'):
                raise ValueError('Uploaded narration requires a matching SRT file')
            source_srt=registry.resolve(directory,payload['srt'])
        elif settings.offline:
            raise ValueError('Offline mode requires local audio and SRT for this job')
        else:
            progress(8)
            # Edge may stream a full narration for longer than the shared 30s
            # deadline. Keep a bounded budget with room for narration and setup.
            tts_timeout=max(90, settings.duration*2+30, voice.get_edge_tts_timeout_seconds() or 0)
            cached_voice=tarot_voice.generate_cached(script,profile,timeout=tts_timeout)
            source_audio=Path(cached_voice['audio'])
            source_srt=Path(cached_voice['words'] if settings.subtitle_style=='word' else cached_voice['sentences'])
        cta_duration=settings.cta_duration if settings.cta_url else 0
        narration_duration=settings.duration-cta_duration
        audio,srt=work/'narration.wav',work/'subtitle.srt'
        fit_options={'preserve_speed':True} if profile.speed != 1 else {}
        timing=fit_audio(source_audio,source_srt,audio,srt,narration_duration,script,**fit_options)
        narration_duration=timing['narration_duration']
        output_duration=narration_duration+cta_duration
        timing['requested_video_duration']=settings.duration
        timing['output_duration']=output_duration
        params=VideoParams(video_subject=payload['title'],video_source='tarot',video_script=script,
                           tarot_voice=settings.voice,tarot_cta_url=settings.cta_url,tarot_cta_duration=settings.cta_duration,
                           bgm_type='',n_threads=2)
        tarot_video.apply_template(params)
        params.subtitle_display_mode='word_by_word' if settings.subtitle_style=='word' else 'sentence'
        params.text_background_color='#160C24' if settings.subtitle_style=='boxed' else False
        params.rounded_subtitle_background=settings.subtitle_style=='boxed'
        params.font_size=52
        params.voice_volume=profile.volume
        codec=hardware_codec(utils.get_ffmpeg_binary()) if settings.hardware else 'libx264'
        config.app['video_codec']=codec
        progress(20)
        combined=work/'combined.mp4'
        tarot_video.render(combined,selected,script,narration_duration,params,str(srt),style=style,
                           progress_callback=lambda p:progress(20+p*50))
        progress(72)
        final=work/'final.mp4'
        music=str(registry.resolve(report['root'],settings.music)) if settings.music else ''
        if music and settings.music_volume > 0:
            # Local music must loop/trim to the adapted timeline, not end early
            # or extend the output when narration changes the video duration.
            fitted_music=work/'music.wav'
            _run([utils.get_ffmpeg_binary(),'-y','-v','error','-stream_loop','-1','-i',music,
                  '-t',f'{output_duration:.6f}','-ar','44100',str(fitted_music)])
            music=str(fitted_music)
        params.bgm_type='custom' if music else ''
        params.bgm_volume=settings.music_volume
        bgm_ok=video.generate_video(str(combined),str(audio),str(srt),str(final),params,bgm_file_override=music)
        validate_output(final,output_duration)
        # The previous successful video remains in place throughout a regeneration.
        os.replace(final,store.output(batch_id,job_id))
        assets._write(directory/f'video-{job_id:03}.srt',srt.read_bytes())
        # Asset warnings are shown from a live scan, not persisted as stale diagnostics.
        warnings=[] if bgm_ok else ['Music mixing failed; narration-only output retained.']
        if abs(output_duration-settings.duration) > .15:
            warnings.append(f'Đã điều chỉnh video từ {settings.duration}s thành {output_duration:.1f}s theo lời đọc, giữ đầy đủ nội dung và CTA.')
        notice=tarot_voice.style_notice(profile)
        if notice:
            warnings.append(notice)
        if cached_voice and cached_voice.get('notice'):
            warnings.append(cached_voice['notice'])
        payload['render']={**timing,'requested_codec':codec,'warnings':warnings,
                           'voice_profile':profile.model_dump(),
                           'voice_cache_key':cached_voice['key'] if cached_voice else '',
                           'voice_cache_hit':cached_voice['cache_hit'] if cached_voice else False,
                           'actual_voice_provider':cached_voice.get('actual_provider',profile.provider) if cached_voice else 'uploaded audio',
                           'actual_voice_model':cached_voice.get('actual_model','') if cached_voice else '',
                           'voice_timing_source':cached_voice['timing_source'] if cached_voice else 'uploaded SRT'}
        store.patch(batch_id,job_id,status='completed',progress=100,error='',payload=payload)
    except MemoryError:
        store.patch(batch_id,job_id,status='failed',error='Out of memory. Reduce concurrency to 1 and retry this job.')
    except Exception as exc:
        from loguru import logger
        logger.exception('Tarot Studio render failed')
        store.patch(batch_id,job_id,status='failed',error=f'{type(exc).__name__}: {str(exc)[:900]}')
    finally:
        store.export(batch_id)
        safe_cleanup(work,directory)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    parser.add_argument('--batch',required=True)
    parser.add_argument('--job',type=int,required=True)
    args=parser.parse_args()
    run_job(BatchStore(args.root),args.batch,args.job)


if __name__=='__main__':
    main()
