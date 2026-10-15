# Vietnamese Voice Studio

## Google AI Studio Gemini TTS

The integration uses official `google-genai==2.31.0`. The SDK and Google Auth
versions are locked in `uv.lock`; other dependency versions are unchanged.
Gemini credentials reuse existing Settings (`app.gemini_api_key`), or the
standard `GEMINI_API_KEY` / `GOOGLE_API_KEY` environment variables. Credentials
are never included in presets, cache manifests, diagnostics, or model results.

The model registry implements separate documented request families:

| Model | SDK request | Delivery directions | Audio returned |
| --- | --- | --- | --- |
| `gemini-3.8-flash-tts` | `client.interactions.create` | `speech_metadata.style` annotation, separate from verbatim transcript | WAV |
| `gemini-3.8-flash-lite-tts` | Same documented 3.8 Interactions family | Same structured annotation | WAV |
| `gemini-2.5-flash-preview-tts` | `client.models.generate_content` with `GenerateContentConfig` / `SpeechConfig` | Instructions plus transcript | PCM, wrapped/encoded locally |

Google explicitly documents a shared schema for the two 3.8 models; the 2.5
adapter remains separate. Unknown model IDs are rejected rather than guessing
request formats. See the [official speech guide](https://ai.google.dev/gemini-api/docs/speech-generation)
and [SDK release](https://github.com/googleapis/python-genai/releases/tag/v2.31.0).

In Voice Studio, choose Gemini, select the model and Tarot style, then enter
optional **Director's Notes**. Prefer concise emotional direction rather than
long paragraphs. The requested Gemini styles include Mystical Female, Romantic
Female, Healing Narrator, Deep Male Voice and Energetic TikTok Narrator.
The existing whisper preset is retained. For 3.8, notes are never inserted into
the transcript. Voice/model/style/notes persist per job and in custom presets,
and all affect speech cache identity. Different batch jobs can use different
models as well as different voices/styles.

**Tải / cập nhật danh sách giọng** retrieves Vietnamese (`vi-VN`) prebuilt
voices from the Google Voice Library for 3.8, including gender metadata when
available. Before that refresh, the selector labels its bundled official
prebuilt voices as such; it does not claim an account catalog was verified.
The 2.5 selector uses the existing official prebuilt list, not arbitrary new
Extended Voice Library IDs.

**Kiểm tra model với API key hiện có** checks the actual account's model catalog.
Catalog presence does not imply synthesis quota. **Thử audio ngắn với cả 3
model** makes small real speech requests (may use credits/quota) and shows
separate per-model audio results. Successful probes are reused for five minutes.
The same redacted verification is available from the CLI:

```powershell
.venv\Scripts\python.exe -X utf8 scripts/check_gemini_tts.py
.venv\Scripts\python.exe -X utf8 scripts/check_gemini_tts.py --synthesize
```

Temporary 429/5xx responses have at most three application-controlled attempts
with exponential backoff and jitter. SDK-internal retries are disabled, with a
transport-level regression test guarding against request multiplication.
Authentication, access and unsupported-model errors do not silently select a
different model. Raw SDK exceptions are replaced with fixed safe diagnostics.

When **Dùng Edge TTS tự nhiên khi Gemini hết quota** is enabled, exhausted quota
uses Hoài My or Nam Minh with the original Vietnamese transcript. Preview and
job metadata report the actual Edge voice and warn that Gemini styles/notes
were not applied. Its own measured boundaries keep subtitles synchronized.
A per-account 60-second cooldown prevents every remaining batch job from
hammering the same exhausted quota. Fallback audio expires from the requested
Gemini cache after 60 seconds, so it does not masquerade permanently as Gemini.
When fallback is disabled, the job reports the quota error.

Both PCM and WAV outputs are validated and converted to renderer-compatible
MP3 or WAV. A 3.8 WAV is decoded directly, never wrapped as raw PCM again.
Successful Gemini audio is cached before Whisper alignment, so an alignment
retry does not require another synthesis call. Previews do not need Whisper;
completed video rendering still requires measured synchronization as described
below.

Development validation uses the real official SDK over a mocked HTTP transport
for all three model schemas, actual WAV/MP3 conversion and real FFmpeg exports
for Single Generate and Batch Generate (reduced video resolution, mocked Google
audio and Whisper timestamps; subtitle timing is checked separately from pixel
rendering). This does not establish live account availability: use the real
probe after configuring a key.

Open **Tarot Reels Studio → Voice Studio**. Edge TTS remains the free default
with Hoài My and Nam Minh. Existing batches without voice profiles continue
using their saved Edge voice. Voice Studio does not replace the ordinary
MoneyPrinterTurbo audio interface or its API/CLI behavior.

## Select, preview, and save

1. Select a provider and a Vietnamese or multilingual voice. Edge/Azure list
   Vietnamese voices directly. ElevenLabs, MiniMax and Kokoro offer an explicit
   catalog refresh button; it does not run on widget changes. Other catalogs
   reuse the existing configured voices. Custom IDs are supported for adapters
   with account-specific voices.
2. Choose a style: Mystical Female, Soft Romantic Female, Whispering Female,
   Healing Storyteller, Deep Male Narrator, Energetic TikTok Female, or Natural.
   These are delivery directions, not guarantees about a voice's gender or
   abilities. Provider gender metadata enables the male/female filter when
   available; Gemini voices have no gender labels in the bundled catalog.
3. Adjust supported controls and enter Vietnamese preview text. Speed and pitch
   controls disappear when the adapter does not implement them. Volume is a
   local gain applied identically to previews and video mixing, not a provider
   style control. Paid providers can charge when **Nghe thử giọng đọc** is clicked.
4. Save a named custom preset and/or click **Áp dụng giọng cho video / batch mới**.
   The production panel shows the active profile. Use **Dùng lại giọng Edge mặc
   định** to return to the basic selector.

Providers use existing credentials and model configuration in MoneyPrinterTurbo
Settings. No credentials are saved in presets, job profiles or cache manifests.
Additional catalogs can include voices/models that do not speak Vietnamese;
verify Vietnamese pronunciation with preview before using custom or multilingual
voices. Selecting a provider does not silently switch to a paid service or
create a new account/key.

| Existing adapter | Numeric speed | Native pitch | Style presets | Timing source |
| --- | --- | --- | --- | --- |
| Edge TTS | Yes | Hz | Natural fallback | Provider word boundaries |
| Azure Speech | Yes | Percent | Natural fallback for listed Vietnamese voices | Provider word boundaries |
| ElevenLabs | Model-dependent | No | Natural fallback | Local Whisper |
| Gemini TTS | No | No | Model-specific delivery instructions, including actual whisper request | Local Whisper; provider boundaries on Edge quota fallback |
| MiniMax | Yes | Semitones | Natural fallback | Local Whisper |
| Fish Audio | Yes | No | Natural fallback | Local Whisper |
| SiliconFlow | Yes | No | Natural fallback | Local Whisper |
| MiMo | No | No | Existing configured delivery; preset fallback | Local Whisper |
| Chatterbox / Kokoro | Yes | No | Natural fallback | Local Whisper |
| VoxCPM | No | No | Natural fallback | Local Whisper |

Unsupported styles produce an explicit notice and retain natural speech with
the selected voice/speed. Whisper is never simulated by lowering volume or
altering pitch. Gemini receives a whisper delivery instruction; actual delivery
still depends on the selected voice and model, so preview it. Other providers
can gain native style support through their adapters later without pretending
it already exists here.

## Batch voices

Add configured profiles to the rotation pool, then enable **Random Voice
Rotation**. The assignment uses shuffled groups of actual provider/voice IDs to
avoid adjacent repetition when more than one voice is available, even if a pool
contains multiple presets of one voice. Each job saves its chosen profile;
retries and script regeneration retain it. Custom presets persist across app
restarts; the current editor and rotation pool are session settings, while each
created batch persists its own settings and assignments.

Under **Batch và kịch bản**, select a video and expand **Giọng đọc riêng của
video** to choose its voice, preview its exact script, and save the override.
Only idle jobs can change voice. Changing synthesis controls clears links to
uploaded audio/SRT; changing volume alone retains them. Existing successful
outputs remain available until the next successful render replaces them.

## Cache and synchronization

`storage/tarot-voice/` contains local presets and content-addressed speech cache.
Keys include exact text (including Vietnamese accents and punctuation), provider,
voice, synthesis controls, model, endpoint and a private configuration digest.
Changing only volume, subtitle display mode, card artwork or video effects
reuses synthesized speech. Unsupported styles with identical effective settings
also share audio. Changing the actual model or voice generates a distinct entry.

Per-entry SQLite transactions serialize duplicate requests across workers;
atomic audio/subtitle manifests are published only after successful preparation.
Raw audio is saved before alignment so a failed Whisper step never requires
another paid synthesis call. Preview can play that raw audio without Whisper.
Private scripts and audio remain in the ignored local storage tree.

Edge and Azure use measured speech boundaries. Providers without real boundaries
use the existing local Whisper service, forced to Vietnamese, rather than their
legacy estimated subtitle timing. Rendering requires a working Whisper model
for these providers; configure/download it using the existing application setup.
If transcription disagrees materially with the script, rendering stops with an
actionable error instead of publishing fabricated synchronization. Matched word
anchors retain measured times; unmatched short spans (such as English Tarot
names) are interpolated between anchors. The final subtitles restore original
Vietnamese spelling and punctuation. Both sentence and word display modes are
cached, and multiword card names work across individual subtitle cues.

Explicit speech-speed choices are preserved by adapting video duration rather
than undoing the selected speed to reach the target length. Audio and subtitle
timelines are retimed together where fitting is used. Card transitions read that
same final SRT. Music and the unspoken CTA follow the adapted video timeline.

## Verification

Whisper alignment uses the configured multilingual model in
`models/whisper-<model_size>/` when `model.bin` is present. A `.part` file is
an unfinished download and must not be renamed without verifying its complete
size and checksum. Automatic Hugging Face downloads use the operating system's
trusted certificate authorities, with TLS verification enabled. After fixing a
model download, retry the same job: previously generated narration is cached
before alignment and does not require another TTS request.

```powershell
.venv\Scripts\python.exe -X utf8 -m unittest test.services.test_tarot_voice test.services.test_tarot_studio test.services.test_tarot test.services.test_voice test.services.test_subtitle test.services.test_video test.services.test_webui_startup -q
```

The focused tests cover adapter routing/capabilities, native pitch, style
fallbacks, cache reuse and concurrency, recovery after alignment failure,
custom-preset persistence, batch assignments/overrides, Vietnamese text and
card synchronization, and Streamlit interactions. Paid-provider mocks validate
integration contracts; they do not establish live service availability or voice
quality.
