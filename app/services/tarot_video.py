"""Deterministic portrait composition. Original card pixels are only projected/scaled."""
from __future__ import annotations

import math
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.services import tarot_assets as assets


def validate_cta(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("CTA must be an http(s) website URL without credentials")
    if len(url) > 250 or any(ord(c) < 32 for c in url):
        raise ValueError("CTA URL is too long or contains control characters")
    return url


def apply_template(params):
    """Called by the shared pipeline, so API/CLI use the same template as WebUI."""
    if params.video_source != "tarot":
        return
    from app.models.schema import VideoAspect, VideoConcatMode, VideoFitMode
    validate_cta(params.tarot_cta_url)
    params.video_aspect = VideoAspect.portrait
    params.video_fit_mode = VideoFitMode.contain
    params.video_concat_mode = VideoConcatMode.sequential
    params.video_transition_mode = None
    params.video_clip_speed = 1.0
    params.match_materials_to_script = True
    params.video_language = "vi-VN"
    params.voice_name = params.tarot_voice
    params.subtitle_enabled = True
    params.font_name = "BeVietnamPro-Bold.ttf"
    params.subtitle_position = "custom"
    params.custom_position = 78


def _seconds(stamp):
    h, m, s = stamp.replace(",", ".").split(":")
    return int(h)*3600 + int(m)*60 + float(s)


def card_timeline(script, selected, duration, subtitle_path=""):
    """Use actual subtitle cues when available; otherwise script-relative timing."""
    found = assets.mentions(script)
    events = []
    if subtitle_path and Path(subtitle_path).exists():
        import re
        from bisect import bisect_right
        text = Path(subtitle_path).read_text(encoding="utf-8-sig")
        cue_texts, cue_times, offsets = [], [], []
        length=0
        for block in re.split(r"\n\s*\n", text.strip()):
            lines = block.splitlines()
            timing = next((i for i, line in enumerate(lines) if " --> " in line), None)
            if timing is not None:
                start = _seconds(lines[timing].split(" --> ")[0])
                content=assets.normalize(' '.join(lines[timing+1:]).strip())
                if content:
                    offsets.append(length)
                    cue_texts.append(content)
                    cue_times.append(start)
                    length+=len(content)+1
        joined=' '.join(cue_texts)
        for mention in assets.mentions(joined):
            # Multiword card names may span several word-level subtitle cues.
            position=round(mention['position']*len(joined))
            index=max(0,bisect_right(offsets,position)-1)
            events.append((cue_times[index],mention['id']))
    if not events:
        events = [(m["position"] * duration, m["id"]) for m in found]
    if not events:
        events = [(i * duration / len(selected), c["id"]) for i, c in enumerate(selected)]
    # At least the first mentioned card is visible during the introduction.
    events = sorted(((max(0, min(duration, t)), cid) for t, cid in events), key=lambda event: event[0])
    events[0] = (0.0, events[0][1])
    return events


def _font(size):
    font = Path(__file__).resolve().parents[2] / "resource/fonts/BeVietnamPro-Bold.ttf"
    return ImageFont.truetype(str(font), size)


def _project(image, angle, size):
    """Perspective projection of the source bitmap onto a rotating planar card."""
    width, height = size
    scale = 1.0
    depth = 3 * width
    points = []
    for x, y in ((-width/2, -height/2), (width/2, -height/2), (width/2, height/2), (-width/2, height/2)):
        z = -x * math.sin(angle)
        factor = depth / (depth + z)
        points.append((width/2 + x*math.cos(angle)*factor*scale, height/2 + y*factor*scale))
    # Wider canvas avoids cropping the nearer edge of the projected card.
    pad = int(width * .3)
    points = [(x+pad, y+pad) for x, y in points]
    source = [(0, 0), (image.width, 0), (image.width, image.height), (0, image.height)]
    matrix, values = [], []
    for (x, y), (u, v) in zip(points, source):
        matrix += [[x,y,1,0,0,0,-u*x,-u*y], [0,0,0,x,y,1,-v*x,-v*y]]
        values += [u, v]
    coefficients = np.linalg.solve(np.array(matrix), np.array(values))
    return image.transform((width+2*pad, height+2*pad), Image.Transform.PERSPECTIVE,
                           coefficients, Image.Resampling.BICUBIC)


def render(output_path, selected, script, audio_duration, params, subtitle_path="", resolution=(1080,1920), fps=30, *, style=None, progress_callback=None):
    from moviepy import VideoClip, VideoFileClip
    from app.services.video import _write_videofile_with_codec_fallback, _get_configured_video_codec

    style = style or {}

    width, height = resolution
    cta_url = validate_cta(params.tarot_cta_url)
    cta_duration = params.tarot_cta_duration if cta_url else 0
    duration = audio_duration + cta_duration
    timeline = card_timeline(script, selected, audio_duration, subtitle_path)
    sources = {}
    for card in selected:
        with Image.open(card.get("local_path") or assets.asset_path(card["image_path"])) as image:
            sources[card["id"]] = image.convert("RGBA")
    rng = np.random.default_rng(style.get("seed",42))
    intensity = style.get("intensity",1)
    particles = rng.random((int(48*intensity), 4))
    back_image = None
    if style.get("card_back_path"):
        with Image.open(style["card_back_path"]) as image:
            back_image = image.convert("RGBA")
    logo = None
    if style.get("logo_path"):
        with Image.open(style["logo_path"]) as image:
            logo = image.convert("RGBA")
            logo.thumbnail((int(width*.22),int(height*.07)))
    # Low-resolution analytical smoke is enlarged and blurred, never painted onto card faces.
    yy, xx = np.mgrid[0:80, 0:45]
    available_effects = {e["id"]: e for e in assets.effects()}
    with ExitStack() as stack:
        overlays = []
        for effect_id in params.tarot_effect_ids:
            if effect_id not in available_effects:
                raise ValueError(f"Missing Tarot effect: {effect_id}")
            clip = stack.enter_context(VideoFileClip(str(assets.asset_path(available_effects[effect_id]["path"])), audio=False))
            overlays.append((clip,.6,duration))
        for effect in style.get("effect_paths",[]):
            clip = stack.enter_context(VideoFileClip(effect, audio=False))
            overlays.append((clip,style.get("opacity",.5),style.get("effect_duration",duration)))
        background = None
        background_video = None
        if style.get("background_path"):
            file = Path(style["background_path"])
            if file.suffix.lower() in (".mp4",".mov",".webm"):
                background_video = stack.enter_context(VideoFileClip(str(file),audio=False))
            else:
                with Image.open(file) as image:
                    background = ImageOps.fit(image.convert("RGB"),(width,height))
        last_progress = [-1]

        def brand(canvas):
            if logo is not None:
                canvas = canvas.convert("RGBA")
                canvas.alpha_composite(logo,(int(width*.08),int(height*.065)))
            return np.asarray(canvas.convert("RGB"))

        def make_frame(t):
            if progress_callback and int(t) != last_progress[0]:
                progress_callback(min(1,t/duration))
                last_progress[0] = int(t)
            smoke = (np.sin(xx*.19+t*.3+np.sin(yy*.14-t*.2))*np.cos(yy*.13+t*.17)+1)*.5
            haze = np.zeros((80,45,3), dtype=np.uint8)
            haze[:,:,0] = 18 + smoke*36
            haze[:,:,1] = 8 + smoke*8
            haze[:,:,2] = 33 + smoke*48
            canvas = Image.fromarray(haze).resize((width,height), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(width*.018))
            if not style.get("smoke",True):
                canvas = Image.new("RGB",(width,height),(19,9,31))
            base = background
            if background_video is not None:
                base = ImageOps.fit(Image.fromarray(background_video.get_frame(t%background_video.duration)).convert("RGB"),(width,height))
            if base is not None:
                canvas = Image.blend(base,canvas,min(.6,.25*intensity)) if style.get("smoke",True) else base.copy()
            draw = ImageDraw.Draw(canvas)
            for px, py, speed, radius in (particles if style.get("particles",True) else []):
                x = int((px + .015*math.sin(t+py*8))*width)
                y = int(((py-t*(.012+speed*.025))%1)*height)
                r = max(1, int((1+radius*2)*width/540))
                draw.ellipse((x-r,y-r,x+r,y+r), fill=(245,205,115) if style.get("particle_color")=="gold" else (179,142,221))
            # Screen blend imported black-background effects behind the untouched card bitmap.
            for overlay,opacity,limit in overlays:
                if t > limit:
                    continue
                frame = Image.fromarray(overlay.get_frame(t % overlay.duration)).convert("RGB").resize((width,height))
                a = np.asarray(canvas, dtype=np.float32)/255
                b = np.asarray(frame, dtype=np.float32)/255
                canvas = Image.fromarray(np.uint8(np.clip(1-(1-a)*(1-b*min(1,opacity*intensity)),0,1)*255))
            if t >= audio_duration and cta_url:
                draw = ImageDraw.Draw(canvas)
                cta_text=style.get("cta_text","Khám phá trải bài của bạn")
                cta_font=_font(max(14,int(width*.045)))
                while draw.textlength(cta_text,font=cta_font)>width*.84 and cta_font.size>14:
                    cta_font=_font(cta_font.size-1)
                draw.text((width/2,height*.38), cta_text, font=cta_font, anchor="mm", fill="white")
                # Wrap long URLs so the entire configured link stays in the safe area.
                label = cta_url.removeprefix("https://").removeprefix("http://")
                font = _font(max(10,int(width*.035)))
                lines, line = [], ""
                for char in label:
                    if draw.textlength(line+char, font=font) > width*.8:
                        lines.append(line)
                        line = ""
                    line += char
                lines.append(line)
                for i, line in enumerate(lines):
                    draw.text((width/2,height*.48+i*width*.05),line,font=font,anchor="mm",fill=(223,194,255))
                return brand(canvas)
            event_index = max(i for i,(start,_) in enumerate(timeline) if start <= min(t,audio_duration))
            start, card_id = timeline[event_index]
            age = t-start
            source = sources[card_id]
            zoom = 1 + (style.get("zoom",.045) or 0) * (1-math.exp(-age/3))
            card_h = int(height*.50*zoom)
            card_w = int(card_h*source.width/source.height)
            if card_w > width*.76:
                card_w = int(width*.76)
                card_h = int(card_w*source.height/source.width)
            phase = min(1, age/style.get("flip_duration",.85)) if style.get("flip",True) else 1
            angle = math.pi*(1-phase)**2
            if angle > math.pi/2:
                if back_image is not None:
                    bitmap=back_image.resize(source.size,Image.Resampling.BICUBIC)
                else:
                    bitmap = Image.new("RGBA", source.size, (55,29,83,255))
                    back = ImageDraw.Draw(bitmap)
                    back.rectangle((12,12,source.width-12,source.height-12),outline=(205,174,234),width=4)
                angle = math.pi-angle
            else:
                bitmap = source
            angle = min(angle, math.pi/2-.015)
            projected = _project(bitmap,angle,(card_w,card_h))
            x, y = int((width-projected.width)/2), int(height*.13-(projected.height-card_h)/2)
            glow = Image.new("RGBA", canvas.size)
            mask = Image.new("L", canvas.size)
            mask.paste(projected.getchannel("A"),(x,y))
            glow.paste((152,68,225,min(255,int(140*intensity))) if style.get("glow",True) else (0,0,0,0),(0,0,width,height),mask.filter(ImageFilter.GaussianBlur(width*.025)))
            canvas = Image.alpha_composite(canvas.convert("RGBA"),glow)
            canvas.alpha_composite(projected,(x,y))
            return brand(canvas)

        clip = stack.enter_context(VideoClip(make_frame, duration=duration))
        _write_videofile_with_codec_fallback(clip, output_file=str(output_path), codec=_get_configured_video_codec(),
                                           atomic_output=True, fps=fps, audio=False,
                                           threads=params.n_threads or 2, logger=None)
    return str(output_path)
