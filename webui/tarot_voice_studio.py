"""Voice Studio controls; no provider request is made just by changing a widget."""
import hashlib

import streamlit as st

from app.services import tarot_voice as voices

SAMPLE = 'Hãy hít thở nhẹ nhàng. Lá The Star gợi nhắc hy vọng và niềm tin. Bạn muốn dành điều gì tốt đẹp cho mình hôm nay?'


def profile_editor(prefix, initial=None, *, disabled=False):
    initial=initial or voices.VoiceProfile()
    provider=st.selectbox('Nhà cung cấp giọng đọc',list(voices.PROVIDERS),
                          index=list(voices.PROVIDERS).index(initial.provider),format_func=voices.PROVIDERS.get,
                          key=f'{prefix}:provider',disabled=disabled)
    caps=voices.capabilities(provider)
    selected_model=''
    directors_notes=''
    edge_fallback=True
    if provider=='gemini':
        from app.services import gemini_speech
        model_options=list(gemini_speech.MODELS)
        selected_model=st.selectbox('Gemini TTS model',model_options,
            index=model_options.index(initial.model) if initial.model in model_options else 0,key=f'{prefix}:gemini_model',disabled=disabled)
        if st.button('Kiểm tra model với API key hiện có',key=f'{prefix}:verify_models',disabled=disabled):
            with st.spinner('Đang kiểm tra quyền truy cập model…'):
                st.session_state['gemini_model_availability']=gemini_speech.verify_models()
        if st.button('Thử audio ngắn với cả 3 model · có thể tính phí',key=f'{prefix}:probe_models',disabled=disabled or not voices.configured('gemini')):
            with st.spinner('Đang xác minh API tổng hợp audio của từng model…'):
                st.session_state['gemini_model_availability']=gemini_speech.probe_models()
        availability=st.session_state.get('gemini_model_availability')
        if availability:
            st.dataframe([{'Model':m,'Trong catalog':'Có' if v['listed'] else 'Chưa xác nhận',
                'Thử audio':'Thành công' if v.get('audio_ready') else 'Chưa tạo được' if v.get('probed') else 'Chưa thử',
                'Thông báo':v.get('error','')} for m,v in availability.items()],hide_index=True)
            st.caption('Có trong catalog chưa xác nhận quota tạo audio. Nghe thử hoặc thử audio ngắn để kiểm tra tổng hợp thực tế.')
        directors_notes=st.text_area("Director's Notes · chỉ dẫn cảm xúc",value=initial.directors_notes,
            max_chars=2000,height=100,key=f'{prefix}:director_notes',disabled=disabled,
            help='Ví dụ: Kể chậm rãi, ấm áp; nhấn nhẹ ở tên lá bài. Chỉ dẫn được tách khỏi transcript với model 3.8.')
        edge_fallback=st.checkbox('Dùng Edge TTS tự nhiên khi Gemini hết quota',value=initial.edge_fallback,
            key=f'{prefix}:gemini_fallback',disabled=disabled)
        st.caption('Với 3.8, chỉ dẫn cảm xúc được tách khỏi lời đọc. Chọn model không tự gọi API tạo giọng.')
    style=st.selectbox('Phong cách giọng đọc',list(voices.STYLES),
                       index=list(voices.STYLES).index(initial.style),
                       format_func=lambda s:({'romantic_female':'Romantic Female','healing':'Healing Narrator',
                           'deep_male':'Deep Male Voice','energetic_female':'Energetic TikTok Narrator'}.get(s,voices.STYLES[s][0])
                           if provider=='gemini' else voices.STYLES[s][0]),key=f'{prefix}:style',disabled=disabled)
    # Provider/style-specific keys prevent unsupported controls leaking across selections.
    identity=f'{prefix}:{provider}:{style}'+(f':{selected_model}' if provider=='gemini' else '')
    remote_key=f'voice_catalog:{provider}:{selected_model}' if provider=='gemini' else f'voice_catalog:{provider}'
    if provider in ('elevenlabs','kokoro','minimax','gemini'):
        if st.button('Tải / cập nhật danh sách giọng',key=f'{prefix}:{provider}:catalog',disabled=disabled or not voices.configured(provider)):
            try:
                with st.spinner('Đang tải danh sách giọng…'):
                    if provider=='gemini':
                        st.session_state[remote_key]=gemini_speech.available_voices(selected_model)
                    else:
                        st.session_state[remote_key]=voices.catalog(provider,remote=True)
            except Exception as exc:
                st.error(f'Không tải được danh sách giọng: {exc}')
    entries=st.session_state.get(remote_key,voices.catalog(provider))
    if provider=='gemini' and selected_model.startswith('gemini-3.8') and remote_key not in st.session_state:
        st.caption('Đang dùng các giọng prebuilt chính thức. Bấm tải danh sách để lấy giọng tiếng Việt từ Voice Library của tài khoản.')
    known_genders=[g for g in ('Female','Male') if any(v['gender']==g for v in entries)]
    if known_genders:
        gender=st.selectbox('Lọc giới tính',['all',*known_genders],
                            format_func={'all':'Tất cả','Female':'Nữ','Male':'Nam'}.get,
                            key=f'{identity}:gender',disabled=disabled)
        entries=[v for v in entries if gender=='all' or v['gender']==gender]
    elif provider=='gemini':
        st.caption('Gemini công bố đặc tính giọng, không công bố giới tính; hãy nghe thử để chọn giọng phù hợp.')
    custom=False
    if provider not in ('edge','azure'):
        st.caption('Giọng đa ngôn ngữ/tùy chỉnh: khả năng phát âm tiếng Việt phụ thuộc model và giọng đã cấu hình. Hãy nghe thử trước khi dựng batch.')
        custom=st.checkbox('Nhập ID giọng tiếng Việt tùy chỉnh',value=not bool(entries),key=f'{identity}:custom',disabled=disabled)
    if custom:
        default=initial.voice_id if initial.provider==provider else ''
        voice_id=st.text_input('ID giọng đã cấu hình',value=default,key=f'{identity}:voice_id',disabled=disabled).strip()
        if not voice_id:
            st.info('Nhập ID giọng của provider để tiếp tục.')
            return None
        if not voice_id.startswith(provider+':'):
            voice_id=provider+':'+voice_id
    else:
        if not entries:
            st.info('Không có giọng phù hợp với bộ lọc. Chọn lại bộ lọc hoặc thêm giọng tùy chỉnh.')
            return None
        options=[v['id'] for v in entries]
        preferred=next((v['id'] for v in entries if v['gender']==voices.STYLES[style][3]),options[0])
        if initial.provider==provider and initial.style==style and initial.voice_id in options:
            preferred=initial.voice_id
        names={v['id']:v['name'] for v in entries}
        voice_id=st.selectbox('Giọng đọc tiếng Việt / đa ngôn ngữ',options,index=options.index(preferred),
                              format_func=names.get,key=f'{identity}:voice',disabled=disabled)
    proposed=voices.apply_style(voices.VoiceProfile(provider=provider,voice_id=voice_id),style)
    same=initial.provider==provider and initial.style==style
    speed=1.0
    if caps['speed']:
        default=initial.speed if same else proposed.speed
        default=max(caps['min_speed'],min(caps['max_speed'],default))
        speed=st.slider('Tốc độ đọc',float(caps['min_speed']),float(caps['max_speed']),float(default),.01,
                         key=f'{identity}:speed',disabled=disabled)
    pitch=0
    if caps['pitch']:
        pitch=st.slider(f"Cao độ ({caps['pitch_unit']})",-caps['pitch_max'],caps['pitch_max'],
                         max(-caps['pitch_max'],min(caps['pitch_max'],initial.pitch)) if same else 0,
                         key=f'{identity}:pitch',disabled=disabled)
    volume=st.slider('Âm lượng lời đọc',0.0,2.0,float(initial.volume),.05,
                     key=f'{identity}:volume',disabled=disabled,
                     help='Áp dụng cục bộ khi nghe thử và trộn video; đổi âm lượng không gọi lại TTS.')
    profile=voices.VoiceProfile(provider=provider,voice_id=voice_id,style=style,speed=speed,pitch=pitch,volume=volume,
        model=selected_model,directors_notes=directors_notes,edge_fallback=edge_fallback).checked()
    notice=voices.style_notice(profile)
    if notice:
        st.info(notice)
    if not caps['measured_timing']:
        st.caption('Phụ đề được căn từ audio bằng Whisper hiện có. Cần model Whisper trên máy; audio đã tạo được giữ trong cache khi căn phụ đề lỗi.')
    if not voices.configured(provider):
        st.warning('Provider chưa được cấu hình. Dùng Settings hiện có của MoneyPrinterTurbo hoặc chọn Edge TTS.')
    return profile


def render_preview(profile, text, prefix, *, disabled=False):
    if profile is None:
        return
    request_key=voices.cache_key(text,profile) if text.strip() else ''
    display_key=hashlib.sha256(f'{request_key}:{profile.volume}'.encode()).hexdigest()
    if st.button('Nghe thử giọng đọc',key=f'{prefix}:preview',disabled=disabled or not text.strip()):
        try:
            with st.spinner('Đang chuẩn bị bản nghe thử…'):
                result=voices.generate_cached(text,profile,require_alignment=False)
                st.session_state[f'{prefix}:preview_result']=dict(signature=display_key,
                    audio=voices.preview_audio(result,profile.volume),cache_hit=result['cache_hit'],notice=result.get('notice',''),
                    actual_provider=result.get('actual_provider',profile.provider),actual_model=result.get('actual_model',profile.model))
        except Exception as exc:
            st.error(str(exc))
    result=st.session_state.get(f'{prefix}:preview_result')
    if result and result['signature']==display_key:
        st.audio(result['audio'])
        if result.get('notice'):
            st.warning(result['notice'])
        st.caption(f"Bản đọc thực tế: {result.get('actual_provider','')} · {result.get('actual_model','')}")
        st.caption('Đã dùng audio trong cache.' if result['cache_hit'] else 'Đã tạo audio và lưu cache cho lần dùng tiếp theo.')
    elif result:
        st.caption('Nội dung hoặc cấu hình giọng đã đổi. Bấm nghe thử để cập nhật audio.')


def render_voice_studio():
    st.subheader('Voice Studio · Giọng đọc tiếng Việt')
    st.caption('Chọn giọng, nghe thử và lưu cấu hình cho video mới. Edge TTS là lựa chọn mặc định miễn phí. Các provider khác dùng cấu hình hiện có.')
    try:
        presets=voices.load_presets()
    except (ValueError,OSError) as exc:
        st.error(f'Không đọc được preset giọng: {exc}')
        presets={}
    seed=voices.VoiceProfile.model_validate(st.session_state.get('voice_editor_seed') or
                                           st.session_state.get('studio_active_voice') or {})
    if presets:
        preset=st.selectbox('Preset đã lưu',list(presets),key='voice_saved_preset')
        if st.button('Mở preset',key='voice_load_preset'):
            st.session_state['voice_editor_seed']=presets[preset].model_dump()
            st.session_state['voice_editor_revision']=st.session_state.get('voice_editor_revision',0)+1
            st.rerun()
    prefix=f"voice_editor:{st.session_state.get('voice_editor_revision',0)}"
    profile=profile_editor(prefix,seed)
    if profile is None:
        return
    text=st.text_area('Lời đọc để nghe thử',value=SAMPLE,height=120,key='voice_preview_text')
    st.caption('Preview có thể dùng API trả phí của provider đã chọn. Dùng đúng lời đọc của job để cache được dùng lại khi dựng.')
    render_preview(profile,text,'voice_main')
    name=st.text_input('Tên preset cá nhân',key='voice_preset_name',max_chars=80)
    if st.button('Lưu preset cá nhân',key='voice_save_preset',disabled=not name.strip()):
        try:
            voices.save_preset(name,profile)
            st.success('Đã lưu preset trên máy.')
        except (ValueError,OSError) as exc:
            st.error(str(exc))
    if st.button('Áp dụng giọng cho video / batch mới',key='voice_apply'):
        st.session_state['studio_active_voice']=profile.model_dump()
        st.success('Đã áp dụng. Video / batch được tạo tiếp theo sẽ dùng cấu hình này.')
    st.write('Random Voice Rotation · luân phiên giọng cho batch')
    pool=st.session_state.get('studio_voice_pool',[])
    if st.button('Thêm giọng hiện tại vào vòng xoay',key='voice_pool_add',disabled=len(pool)>=32):
        if profile.model_dump() not in pool:
            pool=[*pool,profile.model_dump()]
            st.session_state['studio_voice_pool']=pool
    if pool:
        st.dataframe([dict(provider=p['provider'],voice=p['voice_id'],style=voices.STYLES[p['style']][0],speed=p['speed']) for p in pool],hide_index=True)
        remove=st.selectbox('Giọng cần bỏ khỏi vòng xoay',list(range(len(pool))),
                             format_func=lambda i:f"{i+1}. {pool[i]['voice_id']} · {voices.STYLES[pool[i]['style']][0]}",key='voice_pool_remove_index')
        if st.button('Bỏ giọng đã chọn',key='voice_pool_remove'):
            st.session_state['studio_voice_pool']=[p for i,p in enumerate(pool) if i!=remove]
            st.rerun()
    enabled=st.checkbox('Bật Random Voice Rotation cho batch mới',value=False,key='voice_rotation_enabled',disabled=not pool)
    st.session_state['studio_random_voice_rotation']=enabled and bool(pool)
    st.caption('Mỗi job lưu giọng riêng; retry giữ nguyên giọng. Có thể đổi giọng từng video trong tab Batch và kịch bản.')


def render_job_voice(store,batch_id,job,settings,idle):
    profile=voices.VoiceProfile.model_validate(job['payload'].get('voice_profile') or
                (settings.voice_profile.model_dump() if settings.voice_profile else {'voice_id':settings.voice}))
    revision=hashlib.sha256(profile.model_dump_json().encode()).hexdigest()[:10]
    prefix=f'job_voice:{batch_id}:{job["id"]}:{revision}'
    with st.expander('Giọng đọc riêng của video · nghe thử trước khi dựng'):
        st.caption(f"Đang lưu: {voices.PROVIDERS[profile.provider]} · {profile.voice_id}")
        edited=profile_editor(prefix,profile,disabled=not idle)
        if edited is None:
            return
        render_preview(edited,job['payload'].get('script',''),prefix,disabled=not idle)
        if st.button('Lưu giọng cho video này',key=f'{prefix}:save',disabled=not idle):
            store.set_voice(batch_id,job['id'],edited)
            st.rerun(scope='fragment')
        st.caption('Đổi giọng sẽ bỏ liên kết audio/SRT đã tải lên của job; cache audio cũ vẫn được giữ để tái sử dụng.')
