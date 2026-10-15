# Tarot Video Assets Library

## Tarot Reels Studio

For multi-provider narration, previews, custom presets, and per-video batch
voices, see [Vietnamese Voice Studio](tarot-voice-studio.md).

Choose **Tarot Reels Studio** in the workspace selector to prepare and render
batches using an existing local deck. Set the asset directory to a folder with
`cards/`, plus optional `effects/`, `backgrounds/`, `music/`, and `branding/`
subdirectories. Original artwork is read in place. Review missing cards,
duplicate matches, and unrecognized filenames; use the mapping controls to
correct names without renaming the originals.
When available, Studio selects `cards/card-back.png` as the card back and
`backgrounds/purple-smoke-background.mp4` as the background by default.
Both selections can be changed before preparing a batch.

For a local synthesized ambient music bed, run
`.venv\Scripts\python.exe scripts/create_tarot_music.py`. This creates
`music/tarot-ambient.wav` (65 seconds, stereo) inside `tarot-assets/`, using
only synthesized tones. Studio selects it by default when present, at 12%
mix volume, with an audio preview. Choose **Không nhạc nền** to disable it.
Existing batches retain the music settings saved when they were prepared.

Studio classifies assets by directory first: videos in `backgrounds/` are
backgrounds, images in `cards/` are card artwork (including recognized card
backs), and MP4s in `effects/` are effect candidates. Known effect names are
`card-flip.mp4` (CARD_FLIP), `purple-smoke.mp4` (SMOKE), `magical-light.mp4`
(LIGHT), `gold-particles.mp4` (PARTICLES), and `transition.mp4` (TRANSITION).
Names are case insensitive and accept spaces, hyphens, or underscores.

Unknown effects show their full local path and a category dropdown. Assignments
are written atomically to the JSON asset manifest `.tarot-mapping.json` in the
asset directory; existing card mappings and older effect-category names remain
supported. Studio reads the manifest and scans files on every rerun, without a
cached metadata snapshot. Use **Quét lại tài nguyên** after external file edits.
The rescan action confirms completion, shows the last manual scan time in
Vietnam time, and refreshes resource counts including card backs and music.
Valid selections and other form settings are retained. Removed or unclassified
selected assets are cleared with a notice so previews cannot use stale paths.
Saved job asset warnings are also replaced with the current scan results.

Unused unknown or corrupt effects never block a batch and are never selected
automatically. Only chosen template dependencies are validated at preparation,
enqueue, and worker startup. A required unclassified effect names the path and
asks for an assignment; a selected invalid video reports a decode error.
Background videos do not require an effect-category assignment.

Choose 1 or 3 cards, 30/45/60 seconds, themes, and up to 100 videos. Local
script templates need no LLM; the explicitly selected LLM option uses the
existing provider configuration. Prepare scripts for review before queueing,
or use the render action to prepare and enqueue together. Online narration
uses Edge TTS. Offline rendering requires separate matching audio and SRT
uploads for each video. Changing a script clears its previous audio/SRT
links; upload matching files again before rendering offline.
Studio gives Edge TTS at least 90 seconds per attempt (120/150 seconds for
45/60-second videos), or the configured longer `edge_tts_timeout`. The shared
30-second default can cut off healthy full-length narration streams. Failed
attempts preserve the DNS, timeout, or empty-stream reason in the job error.

The selected duration is a target. Generated scripts budget narration separately
from the CTA and preserve complete card meanings and the disclaimer. After
speech synthesis (or local narration upload), Studio fits small timing differences
within 0.8–1.2× speech speed, retiming the SRT together with the audio. For larger
differences, it uses the original narration duration and speed instead: it never
cuts speech or forces an extreme speaking rate. For example, a 59.2-second
narration with a 3-second CTA produces a 62.2-second video. Short narration also
shortens the video rather than leaving a long silent tail. Music loops/trims to
the resulting duration. The dashboard, CSV and render metadata show actual output
duration, and a notice explains any adjustment. This also applies to retries of
existing batches; no additional voice or paid provider is required.

The batch dashboard supports script edits, per-video regeneration, retries,
pending-job cancellation, previews, and downloads. Switching batches preserves
their SQLite-backed queue state. One render process runs by default, with a
configurable maximum of four. Interrupted jobs can be retried; completed
outputs remain available. Output lives under ignored `output/tarot-reels/`,
with metadata CSV, scripts JSON, captions, and a ZIP of completed MP4s.
Rendering never publishes the batch to social media.

Run the focused Studio checks with:

```powershell
.venv\Scripts\python.exe -m unittest test.services.test_tarot_studio -v
```

Choose **Tarot · thư viện cục bộ** under Video Source in the WebUI at
<http://127.0.0.1:8501>. The reusable **Thư viện Tarot** panel is available for
all video sources, and the **Video Tarot 9:16** panel appears in Tarot mode.

## Import and manage assets

Click **Nhập các lá bài còn thiếu từ Commons** to import the 78
Rider–Waite–Smith cards. The importer queries the Wikimedia Commons file API,
requires both `LicenseShortName = Public domain` and `Copyrighted = False`,
and saves each file's original metadata, source URL and SHA-256 hashes.
Imports resume without downloading existing cards. Individual failures are
shown and can be retried. There is no scraping, stock search, or AI image generation.

Source: [Rider–Waite tarot deck on Wikimedia Commons](https://commons.wikimedia.org/wiki/Category:Rider-Waite_tarot_deck).
For example, [The Fool](https://commons.wikimedia.org/wiki/File:RWS_Tarot_00_Fool.jpg)
is identified as public domain, with Pamela Colman Smith as the artist.
The importer verifies each of the other 77 records separately rather than
assuming a category grants a license. Locally uploaded replacements are labeled
`user supplied` and do not inherit the Commons public-domain claim.

Assets are stored in the existing ignored `storage/` tree:

```text
storage/tarot/
  cards/major-00.png … major-21.png
  cards/wands-01.png … wands-14.png
  cards/cups-01.png … cups-14.png
  cards/swords-01.png … swords-14.png
  cards/pentacles-01.png … pentacles-14.png
  metadata/<card-id>.json
  effects/card_flip/<sha256>.mp4
  effects/purple_smoke/<sha256>.mp4
  effects/magical_light/<sha256>.mp4
  effects/particles/<sha256>.mp4
```

Filter cards by Major/Minor Arcana, suit, and Vietnamese/English name. Select a
card to preview its image and source or upload a replacement PNG/JPG/WEBP.
Upload one or more local MP4 effects under their category, then select the
effects for a video. MP4s must decode successfully, be at most 120 seconds and
150 MB. Use **black-background light/smoke/particle overlays**: they loop and
screen-blend behind the card. Their audio is ignored. Effects are optional;
the template also supplies local animated smoke, particles, glow, and flips.
No bundled stock effects or third-party MP4s are licensed by this feature.

## Create a video

1. Supply a Vietnamese script using full card names, for example:
   `Lá The Sun mang đến niềm vui. Lá Nữ Hoàng Kiếm nhắc bạn giữ sự sáng suốt.`
2. Review the automatically selected images in the Tarot panel. English and
   Vietnamese aliases are matched locally, ignoring accents and case, with word
   boundaries and longest-name matching. There is no fuzzy or LLM matching.
3. For scripts without card names, select cards manually in presentation order.
   Explicit names in the script take priority over the manual fallback. A missing
   named image fails before TTS, rather than substituting a different card or
   searching a stock provider. For an AI-generated script, matching runs once
   the script is available; preview generated names by generating/reviewing the
   script before submitting the video.
4. Choose Hoài My or Nam Minh. Tarot mode uses Vietnamese Edge TTS and its
   synchronized subtitle cues with the bundled Be Vietnam Pro font. Edge TTS
   needs Internet but no API key. Failed subtitle generation stops Tarot output.
5. Select optional MP4 overlays and configure the end-screen URL and duration.
   The default URL is `https://trai-bai-tarot.vercel.app`; an empty URL disables
   the end screen. The end screen follows narration and is not spoken.
6. Generate with the existing task controls. Output is H.264, 1080×1920 at 30 fps,
   suitable for TikTok, Facebook Reels and YouTube Shorts. Rendering does not
   publish a video; existing separately configured publishing behavior is unchanged.

The renderer applies perspective projection to the **existing image pixels**,
reveals the original face after a card-back flip, and adds gentle zoom. Glow and
MP4 overlays stay behind the face. It never sends cards to a generative model.
Card changes follow subtitle cue times when available, otherwise script-relative
positions. Subtitle synchronization comes from the existing Edge TTS pipeline.

## Python, API and CLI reuse

`app.services.tarot_assets` owns the catalog, bounded asset paths, imports,
uploads, matching, and metadata. `app.services.tarot_video` owns template settings,
timelines, and deterministic rendering. `webui.tarot_library` contains reusable
Streamlit components. The existing shared task pipeline handles speech,
subtitles, mixing, task history, and error reporting.

```python
from app.services import tarot_assets

result = tarot_assets.import_public_domain()
cards = tarot_assets.cards()  # id, names, category, suit, image_path, provenance
selected = tarot_assets.select_cards("The Sun và Nữ Hoàng Kiếm")
```

Use the existing video API with `video_source: "tarot"` and these optional fields:

```json
{
  "video_subject": "Thông điệp hôm nay",
  "video_source": "tarot",
  "video_script": "Lá The Sun mang đến niềm vui.",
  "tarot_card_ids": ["major-19"],
  "tarot_effect_ids": [],
  "tarot_voice": "vi-VN-HoaiMyNeural-Female",
  "tarot_cta_url": "https://trai-bai-tarot.vercel.app",
  "tarot_cta_duration": 3,
  "bgm_type": ""
}
```

```powershell
.venv\Scripts\python.exe cli.py --video-source tarot --video-subject "Tarot" --video-script "The Sun mang đến niềm vui." --tarot-cta-url "https://trai-bai-tarot.vercel.app"
.venv\Scripts\python.exe -m unittest test.services.test_tarot -v
```

Other video sources keep their existing pipeline. Tarot mode bypasses LLM
keyword generation, semantic reranking, Pexels/Pixabay, and image/video generation
providers. An explicitly requested AI script or existing paid music provider can
still make its normal calls; supplying the script and choosing local/no music
avoids those calls. Card/effect uploads are available from the WebUI, not new
unauthenticated HTTP upload routes.
