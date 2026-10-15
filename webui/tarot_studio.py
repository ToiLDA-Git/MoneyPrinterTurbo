"""Production surface inside the existing WebUI; widgets never run render work."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from app.services import tarot_assets as assets, tarot_registry as registry, tarot_scripts as scripts
from app.services.tarot_batch import BatchManager, StudioSettings, STATUSES
from app.services.tarot_voice import VoiceProfile


@st.cache_resource
def manager():
    return BatchManager()


def _upload(root,folder,upload,allowed):
    suffix=Path(upload.name).suffix.lower()
    if suffix not in allowed or upload.size>150*1024*1024:
        raise ValueError('Unsupported upload type or file larger than 150 MB')
    data=upload.getvalue()
    name=assets.hashlib.sha256(data).hexdigest()+suffix
    relative=f'{folder}/{name}'
    assets._write(Path(root)/relative,data)
    return relative


def _request_rescan():
    st.session_state['studio_rescan_requested'] = True


def _sync_asset_selections(report):
    """Keep valid selections, clear removed files before their widgets render."""
    branding = [f'branding/{p.name}' for p in (Path(report['root'])/'branding').glob('*')
                if p.is_file() and p.suffix.lower() in registry.IMAGE_TYPES]
    options = {
        'studio_background': report['backgrounds'], 'studio_back': report['card_backs'],
        'studio_music': report['music'], 'studio_logo': branding,
        'studio_effects': [e['path'] for e in report['effects']],
        'studio_cards': list(report['cards']),
    }
    removed = []
    for name, available in options.items():
        if name not in st.session_state:
            continue
        value = st.session_state[name]
        if isinstance(value, list):
            missing = [item for item in value if item not in available]
            if missing:
                removed.extend(missing)
                st.session_state[name] = [item for item in value if item in available]
        elif value and value not in available:
            removed.append(value)
            st.session_state[name] = ''
    return removed


def _assets_panel(report):
    st.button('Quét lại tài nguyên', key='studio_rescan', on_click=_request_rescan,
              help='Đọc lại tệp và ánh xạ đã lưu; cập nhật bài, nền, hiệu ứng, logo và nhạc.')
    removed = _sync_asset_selections(report)
    if st.session_state.pop('studio_rescan_requested', False):
        scanned_at = datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%H:%M:%S · %d/%m/%Y')
        st.session_state['studio_last_rescan'] = scanned_at
        st.success('Đã quét lại tài nguyên và cập nhật kết quả kiểm tra.')
    if st.session_state.get('studio_last_rescan'):
        st.caption(f"Quét thủ công gần nhất: {st.session_state['studio_last_rescan']} (giờ Việt Nam)")
    if removed:
        st.warning('Đã bỏ các lựa chọn không còn khả dụng: '+', '.join(removed)+'. Vui lòng chọn lại nếu cần.')
    st.write(f"{len(report['cards'])}/78 lá nhận diện · {len(report['card_backs'])} lưng bài · {len(report['effects'])} hiệu ứng đã gán loại · {len(report['backgrounds'])} nền · {len(report['music'])} bản nhạc")
    if report['complete']:
        st.success('Đủ 78 lá bài cục bộ, không có ảnh trùng hoặc lỗi.')
    for warning in report['warnings']:
        st.warning(warning)
    for label,key in [('Lá còn thiếu','missing'),('Ảnh trùng lá','duplicates'),('Tên chưa nhận diện','unmatched'),('Ảnh không đọc được','invalid')]:
        if report[key]:
            st.error(label)
            st.json(report[key])
    if report['ignored']:
        st.caption('Bỏ qua ảnh bìa/tệp được đánh dấu bỏ qua: '+', '.join(report['ignored']))
    with st.expander('Sửa ánh xạ tên tệp và gán hiệu ứng'):
        files=[p.name for p in sorted((Path(report['root'])/'cards').iterdir()) if p.is_file() and p.suffix.lower() in registry.IMAGE_TYPES]
        if files:
            filename=st.selectbox('Tệp ảnh cần sửa',files,key='studio_map_file')
            labels={card['id']:f"{card['name_vi']} · {card['name']}" for card in assets.catalog()}
            card_id=st.selectbox('Lá bài chính xác',['ignore',*labels],format_func=lambda cid:'Bỏ qua tệp này' if cid=='ignore' else labels[cid],key='studio_map_id')
            if st.button('Lưu ánh xạ ảnh',key='studio_map_save'):
                registry.correct_mapping(report['root'],'cards',filename,None if card_id=='ignore' else card_id)
                st.rerun()
        # Unknown assets get their own controls and path; never guess their role.
        for filename in report['unknown_effects']:
            identity=f"{report['root']}:{filename}"
            st.code(str(Path(report['root'])/'effects'/filename), language=None)
            category=st.selectbox(f'Loại hiệu ứng · {filename}',registry.EFFECT_CATEGORIES,
                                  key=f'studio_unknown_category:{identity}')
            if st.button(f'Lưu loại · {filename}',key=f'studio_unknown_save:{identity}'):
                registry.correct_mapping(report['root'],'effects',filename,category)
                st.rerun()
        effect_files=[e['name'] for e in report['effects']]
        if effect_files:
            filename=st.selectbox('Tệp hiệu ứng',effect_files,key='studio_map_effect')
            current=next(e['category'] for e in report['effects'] if e['name']==filename)
            category=st.selectbox('Loại hiệu ứng',registry.EFFECT_CATEGORIES,
                                  index=registry.EFFECT_CATEGORIES.index(current),
                                  key=f"studio_map_category:{report['root']}:{filename}:{current}")
            if st.button('Gán loại cho MP4',key='studio_map_effect_save'):
                registry.correct_mapping(report['root'],'effects',filename,category)
                st.rerun()
    with st.expander('Xem thư viện và tải tài nguyên bổ sung'):
        group=st.selectbox('Lọc nhóm bài',['all','major','minor'],format_func={'all':'Tất cả','major':'Ẩn chính','minor':'Ẩn phụ'}.get,key='studio_asset_group')
        query=st.text_input('Tìm tên bài',key='studio_asset_search')
        options=[cid for cid,c in report['cards'].items() if (group=='all' or c['category']==group) and (not query or registry.key(query) in registry.key(c['name']+' '+c['name_vi']))]
        if options:
            cid=st.selectbox('Xem ảnh gốc',options,format_func=lambda cid:report['cards'][cid]['name'],key='studio_asset_preview')
            st.image(report['cards'][cid]['local_path'],width=180)
        kind=st.selectbox('Tải vào nhóm',['cards','effects','backgrounds','branding','music'],key='studio_upload_kind')
        allowed={ 'cards':registry.IMAGE_TYPES,'effects':{'.mp4'},'backgrounds':registry.IMAGE_TYPES|registry.VIDEO_TYPES,
                  'branding':registry.IMAGE_TYPES,'music':{'.mp3','.wav','.m4a','.ogg'}}[kind]
        uploads=st.file_uploader('Tài nguyên cục bộ',type=[s[1:] for s in sorted(allowed)],accept_multiple_files=True,key='studio_upload_assets')
        back=st.checkbox('Dùng ảnh tải lên làm lưng bài',key='studio_upload_back') if kind=='cards' else False
        if st.button('Lưu tài nguyên',disabled=not uploads,key='studio_upload_save'):
            for upload in uploads:
                if kind=='cards':
                    # Keep the real filename for matching; forbid path separators.
                    filename=Path(upload.name.replace('\\','/')).name
                    if filename!=upload.name or Path(filename).suffix.lower() not in allowed:
                        raise ValueError('Choose a plain image filename')
                    from PIL import Image
                    import io
                    data=upload.getvalue()
                    with Image.open(io.BytesIO(data)) as image:
                        image.verify()
                    name=('card-back'+Path(filename).suffix.lower()) if back else filename
                    target=Path(report['root'])/'cards'/name
                    if target.exists():
                        st.warning(f'{name} đã tồn tại; đổi tên tệp để tránh ghi đè ảnh gốc.')
                        continue
                    assets._write(target,data)
                else:
                    _upload(report['root'],kind,upload,allowed)
            st.rerun()


def _configuration(report):
    left,right=st.columns(2)
    with left:
        action=st.radio('Cách sản xuất',['Một video','Batch Generate'],horizontal=True,key='studio_production')
        count=st.number_input('Số video',1,100,10,key='studio_count') if action=='Batch Generate' else 1
        themes=st.multiselect('Chủ đề',list(scripts.THEMES),default=['daily'],format_func=scripts.THEMES.get,key='studio_themes')
        duration=st.select_slider('Thời lượng video (giây)',[30,45,60],value=30,key='studio_duration')
        st.caption('Đây là thời lượng mục tiêu. Nếu bản đọc dài/ngắn hơn nhiều, video tự điều chỉnh theo lời đọc; không cắt nội dung hoặc ép giọng quá nhanh.')
        card_count=st.radio('Số lá mỗi video',[1,3],horizontal=True,key='studio_card_count')
        automatic=st.radio('Chọn bài',['Tự động','Thủ công'],horizontal=True,key='studio_selection')=='Tự động'
        card_ids=[]
        if not automatic:
            card_ids=st.multiselect('Các lá được chọn',list(report['cards']),format_func=lambda cid:report['cards'][cid]['name'],key='studio_cards')
            for cid in card_ids:
                st.image(report['cards'][cid]['local_path'],width=130)
        script_mode=st.selectbox('Nguồn kịch bản',['Mẫu cục bộ đa dạng','LLM đã cấu hình','Kịch bản thủ công'],key='studio_script_mode')
        manual=st.text_area('Một kịch bản cho mỗi video; ngăn cách bằng dòng ---',height=180,key='studio_manual_scripts') if script_mode=='Kịch bản thủ công' else ''
        if script_mode=='LLM đã cấu hình':
            st.caption('Chỉ lựa chọn này gọi LLM hiện có. Lỗi một kịch bản được lưu riêng để sửa hoặc thử lại.')
        offline=st.checkbox('Dựng offline bằng audio + SRT có sẵn',key='studio_offline')
        active_voice=st.session_state.get('studio_active_voice')
        voice=st.selectbox('Giọng Edge mặc định',['vi-VN-HoaiMyNeural-Female','vi-VN-NamMinhNeural-Male'],key='studio_voice',disabled=offline or bool(active_voice))
        if active_voice and not offline:
            st.caption(f"Voice Studio đang áp dụng: {active_voice['provider']} · {active_voice['voice_id']}.")
            if st.button('Dùng lại giọng Edge mặc định',key='studio_clear_active_voice'):
                st.session_state.pop('studio_active_voice',None)
                st.rerun()
        subtitle=st.selectbox('Kiểu phụ đề',['clean','boxed','word'],format_func={'clean':'Chữ trắng, viền tối','boxed':'Nền tối dễ đọc','word':'Đồng bộ từng từ'}.get,key='studio_subtitle')
        music_options=['',*report['music']]
        default_music='music/tarot-ambient.wav'
        music=st.selectbox('Nhạc nền cục bộ',music_options,index=music_options.index(default_music) if default_music in music_options else 0,format_func=lambda v:'Tarot Ambient' if v==default_music else v or 'Không nhạc nền',key='studio_music')
        if music:
            st.audio(str(registry.resolve(report['root'],music)))
        volume=st.slider('Âm lượng nhạc nền',0.0,1.0,.12,.01,key='studio_music_volume')
    with right:
        st.write('Khung hình cố định: 1080 × 1920 · 9:16 · 30 fps')
        backgrounds=['',*report['backgrounds']]
        card_backs=['',*report['card_backs']]
        default_background='backgrounds/purple-smoke-background.mp4'
        default_back='cards/card-back.png'
        background=st.selectbox('Ảnh/video nền',backgrounds,index=backgrounds.index(default_background) if default_background in backgrounds else 0,format_func=lambda v:v or 'Nền khói cục bộ',key='studio_background')
        back=st.selectbox('Lưng bài',card_backs,index=card_backs.index(default_back) if default_back in card_backs else 0,format_func=lambda v:v or 'Lưng bài dự phòng',key='studio_back')
        effects=st.multiselect('Overlay MP4',[e['path'] for e in report['effects']],key='studio_effects')
        if effects:
            with st.expander('Xem trước overlay'):
                st.video(str(registry.resolve(report['root'],effects[0])))
        opacity=st.slider('Độ hiện overlay',0.0,1.0,.5,.05,key='studio_opacity')
        intensity=st.slider('Cường độ hiệu ứng',0.0,2.0,1.0,.05,key='studio_intensity')
        effect_duration=st.slider('Thời lượng overlay (giây)',1.0,60.0,30.0,1.0,key='studio_effect_duration')
        flips=st.checkbox('Lật bài có phối cảnh',True,key='studio_flip')
        smoke=st.checkbox('Khói tím',True,key='studio_smoke')
        glow=st.checkbox('Ánh sáng phép thuật',True,key='studio_glow')
        particles=st.checkbox('Hạt vàng',True,key='studio_particles')
        zoom=st.checkbox('Zoom điện ảnh',True,key='studio_zoom')
        logos=[f'branding/{p.name}' for p in sorted((Path(report['root'])/'branding').glob('*')) if p.suffix.lower() in registry.IMAGE_TYPES]
        logo=st.selectbox('Logo cục bộ',['',*logos],format_func=lambda v:v or 'Không logo',key='studio_logo')
        enabled=st.checkbox('Hiện CTA cuối video',True,key='studio_cta_enabled')
        cta_text=st.text_input('Nội dung CTA',value='Trải bài Tarot miễn phí tại',key='studio_cta_text',disabled=not enabled)
        cta_url=st.text_input('Website',value=assets.DEFAULT_CTA_URL,key='studio_cta_url',disabled=not enabled)
        cta_duration=st.slider('Thời lượng CTA (đã tính trong video)',1.0,8.0,3.0,.5,key='studio_cta_duration')
        hardware=st.checkbox('Thử mã hóa phần cứng, tự lùi về CPU',True,key='studio_hardware')
        concurrency=st.number_input('Số render đồng thời',1,4,1,key='studio_concurrency',help='Mặc định 1 để giữ RAM/CPU ổn định. Thay đổi áp dụng cho lượt bắt đầu tiếp theo.')
    settings=StudioSettings(asset_dir=report['root'],themes=themes or ['daily'],count=int(count),card_count=card_count,
        manual_card_ids=card_ids,duration=duration,ai_scripts=script_mode=='LLM đã cấu hình',voice=voice,offline=offline,
        voice_profile=VoiceProfile.model_validate(active_voice) if active_voice else None,
        random_voice_rotation=bool(st.session_state.get('studio_random_voice_rotation',False)),
        voice_pool=[VoiceProfile.model_validate(p) for p in st.session_state.get('studio_voice_pool',[])],
        subtitle_style=subtitle,effects=effects,opacity=opacity,intensity=intensity,effect_duration=effect_duration,
        background=background,card_back=back,logo=logo,music=music,music_volume=volume,smoke=smoke,glow=glow,
        particles=particles,flip=flips,zoom=zoom,cta_url=cta_url if enabled else '',cta_text=cta_text,
        cta_duration=cta_duration,hardware=hardware,concurrency=int(concurrency))
    valid=bool(themes) and (automatic or len(card_ids)==card_count)
    manual_scripts=[s.strip() for s in manual.split('\n---\n') if s.strip()] if script_mode=='Kịch bản thủ công' else None
    if script_mode=='Kịch bản thủ công' and len(manual_scripts)!=count:
        st.info(f'Cần {count} kịch bản riêng; đang có {len(manual_scripts)}.')
        valid=False
    return settings,manual_scripts,valid


@st.fragment(run_every='2s')
def dashboard(batch_manager):
    store=batch_manager.store
    batches=store.batches()
    if not batches:
        st.info('Chưa có batch. Chuẩn bị kịch bản ở phần thiết lập để bắt đầu.')
        return
    ids=[b['id'] for b in batches]
    selected=st.session_state.pop('studio_selected_batch', None)
    if selected in ids and st.session_state.get('studio_batch_picker')!=selected:
        st.session_state['studio_batch_picker']=selected
    batch_id=st.selectbox('Batch đang quản lý',ids,key='studio_batch_picker')
    jobs=store.jobs(batch_id)
    settings=store.settings(batch_id)
    totals={state:sum(j['status']==state for j in jobs) for state in STATUSES}
    columns=st.columns(5)
    for column,label,value in zip(columns,['Tổng video','Chờ dựng','Đang dựng','Hoàn tất','Lỗi / gián đoạn'],
                                  [len(jobs),totals['pending'],totals['running'],totals['completed'],totals['failed']+totals['interrupted']]):
        column.metric(label,value)
    st.progress(sum(j['progress'] if j['status']!='completed' else 100 for j in jobs)/max(1,len(jobs)*100),text=f"{totals['completed']}/{len(jobs)} video hoàn tất · {totals['draft']} kịch bản chờ duyệt")
    actions=st.columns(4)
    if actions[0].button('Dựng các kịch bản đã duyệt',key='studio_start_batch'):
        st.success(f"Đã đưa {store.enqueue(batch_id)} video vào hàng đợi.")
        st.rerun(scope='fragment')
    if actions[1].button('Thử lại các job lỗi',key='studio_retry_failed'):
        for job in jobs:
            if job['status'] in ('failed','interrupted') and not job['payload'].get('script'):
                try:
                    store.regenerate_script(batch_id,job['id'])
                    store.patch(batch_id,job['id'],status='failed')
                except Exception as exc:
                    store.patch(batch_id,job['id'],error=str(exc))
        store.enqueue(batch_id,failed_only=True)
        st.rerun(scope='fragment')
    if actions[2].button('Hủy các job đang chờ',key='studio_cancel'):
        store.cancel_pending(batch_id)
        st.rerun(scope='fragment')
    if actions[3].button('Làm mới',key='studio_refresh'):
        st.rerun(scope='fragment')
    filters=st.columns(2)
    status=filters[0].selectbox('Lọc trạng thái',['all',*STATUSES],key='studio_status_filter')
    theme=filters[1].selectbox('Lọc chủ đề',['all',*settings.themes],format_func=lambda t:scripts.THEMES.get(t,'Tất cả'),key='studio_theme_filter')
    filtered=[j for j in jobs if (status=='all' or j['status']==status) and (theme=='all' or j['theme']==theme)]
    rows=[{'ID':j['id'],'Theme':scripts.THEMES[j['theme']],'Tarot Cards':', '.join(j['payload'].get('card_ids',[])),
           'Voice':j['payload'].get('voice_profile',{}).get('voice_id',settings.voice),
           'Duration':round(j['payload'].get('render',{}).get('output_duration',settings.duration),1),'Status':j['status'],'Progress':round(j['progress']),
           'Preview':'Sẵn sàng' if j['status']=='completed' else 'Kịch bản',
           'Download':store.output(batch_id,j['id']).name if j['status']=='completed' else '',
           'Retry':'Có thể thử lại' if j['status'] in ('failed','interrupted') else ''} for j in filtered]
    st.dataframe(rows,hide_index=True,width='stretch',column_config={'Progress':st.column_config.ProgressColumn(min_value=0,max_value=100)})
    if filtered:
        jid=st.selectbox('Video để xem / tải / sửa / dựng lại',[j['id'] for j in filtered],key=f'studio_job_{batch_id}')
        job=next(j for j in filtered if j['id']==jid)
        if job['error']:
            st.error(job['error'])
        st.write(job['payload'].get('title',f'Video {jid:03}'))
        idle=job['status'] not in ('pending','running')
        from webui.tarot_voice_studio import render_job_voice
        render_job_voice(store,batch_id,job,settings,idle)
        with st.expander('Duyệt kịch bản, ý nghĩa và phụ đề',expanded=job['status']=='draft'):
            text=st.text_area('Lời đọc tiếng Việt',value=job['payload'].get('script',''),height=240,key=f'studio_script_{batch_id}_{jid}',disabled=not idle)
            st.caption(job['payload'].get('caption',''))
            st.json(job['payload'].get('meanings',[]),expanded=False)
            cards=registry.discover(settings.asset_dir)['cards']
            image_columns=st.columns(3)
            for index,cid in enumerate(job['payload'].get('card_ids',[])):
                if cid in cards:
                    image_columns[index%3].image(cards[cid]['local_path'],width=150,caption=cards[cid]['name'])
            if st.button('Lưu kịch bản',disabled=not idle,key=f'studio_save_{batch_id}_{jid}'):
                store.edit(batch_id,jid,script=text)
                st.success('Đã lưu. Video này đang chờ duyệt lại.')
            if st.button('Tạo biến thể kịch bản mới',disabled=not idle,key=f'studio_regen_script_{batch_id}_{jid}'):
                store.regenerate_script(batch_id,jid)
                st.session_state.pop(f'studio_script_{batch_id}_{jid}',None)
                st.rerun(scope='fragment')
        with st.expander('Audio và SRT cục bộ · dựng offline'):
            st.caption('Mỗi video cần audio và SRT khớp với kịch bản riêng. Không dùng lại một bản đọc cho cả batch.')
            audio=st.file_uploader('Audio của video này',type=['mp3','wav','m4a','ogg'],key=f'studio_audio_{batch_id}_{jid}',disabled=not idle)
            srt=st.file_uploader('SRT đồng bộ của video này',type=['srt'],key=f'studio_srt_{batch_id}_{jid}',disabled=not idle)
            st.caption(f"Đã lưu audio: {bool(job['payload'].get('audio'))} · SRT: {bool(job['payload'].get('srt'))}")
            if st.button('Lưu audio / SRT',disabled=not idle or not (audio or srt),key=f'studio_save_inputs_{batch_id}_{jid}'):
                for kind,upload in [('audio',audio),('srt',srt)]:
                    if upload:
                        store.save_input(batch_id,jid,kind,upload.getvalue(),Path(upload.name).suffix)
                st.success('Đã lưu input riêng cho video này.')
        if st.button('Dựng / dựng lại video này',disabled=not idle,key=f'studio_render_{batch_id}_{jid}'):
            store.enqueue(batch_id,job_id=jid)
            st.rerun(scope='fragment')
        file=store.output(batch_id,jid)
        if file.exists():
            st.video(str(file),width=280)
            st.download_button('Tải MP4',data=lambda:file.read_bytes(),file_name=file.name,mime='video/mp4',key=f'studio_download_{batch_id}_{jid}')
        warnings=registry.current_render_warnings(
            registry.discover(settings.asset_dir),job['payload'].get('render',{}).get('warnings',[]))
        if warnings:
            st.caption(' · '.join(warnings))
    st.caption(str(store.batch_dir(batch_id)))
    export_columns=st.columns(3)
    export_columns[0].download_button('Tải metadata CSV',data=lambda:(store.export(batch_id) or (store.batch_dir(batch_id)/'metadata.csv').read_bytes()),file_name='metadata.csv',mime='text/csv',key='studio_csv')
    export_columns[1].download_button('Tải scripts JSON',data=lambda:(store.export(batch_id) or (store.batch_dir(batch_id)/'scripts.json').read_bytes()),file_name='scripts.json',mime='application/json',key='studio_json')
    export_columns[2].download_button('Tải ZIP các video hoàn tất',data=lambda:store.zip_completed(batch_id).read_bytes(),file_name=f'{batch_id}.zip',mime='application/zip',key='studio_zip',disabled=not totals['completed'])


def render_studio():
    st.header('Tarot Reels Studio')
    st.caption('Chuẩn bị kịch bản, kiểm tra ảnh gốc và dựng video dọc theo batch. Nội dung Tarot để giải trí và tự chiêm nghiệm.')
    batch_manager=manager()
    tabs=st.tabs(['Thiết lập sản xuất','Batch và kịch bản','Voice Studio'])
    with tabs[0]:
        asset_dir=st.text_input('Thư mục tài nguyên Tarot',value=str(Path('tarot-assets').resolve()),key='studio_asset_dir')
        try:
            report=registry.discover(asset_dir)
            _assets_panel(report)
            settings,manual,valid=_configuration(report)
            batch_manager.configure(settings.concurrency)
            controls=st.columns(2)
            preview=controls[0].button('Chuẩn bị kịch bản để duyệt',disabled=not valid,key='studio_prepare')
            generate=controls[1].button(f'Dựng {settings.count} video',type='primary',disabled=not valid or settings.offline,key='studio_generate')
            if preview or generate:
                bar=st.progress(0.0)
                with st.spinner('Đang chuẩn bị các kịch bản riêng…'):
                    batch_id=batch_manager.store.create(settings,manual,bar.progress)
                st.session_state['studio_selected_batch']=batch_id
                if generate:
                    batch_manager.store.enqueue(batch_id)
                st.success(f'Đã tạo {batch_id}. Mở tab Batch và kịch bản để theo dõi.')
        except (ValueError,OSError) as exc:
            st.error(str(exc))
    with tabs[1]:
        try:
            dashboard(batch_manager)
        except (ValueError,OSError) as exc:
            st.error(str(exc))
    with tabs[2]:
        from webui.tarot_voice_studio import render_voice_studio
        try:
            render_voice_studio()
        except (ValueError,OSError) as exc:
            st.error(str(exc))
