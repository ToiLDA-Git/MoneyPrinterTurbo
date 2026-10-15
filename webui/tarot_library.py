"""Reusable Streamlit asset manager and Tarot template controls."""
import streamlit as st

from app.services import tarot_assets as assets
from app.services import tarot_video


def render_asset_manager():
    with st.expander("Thư viện Tarot · lá bài và hiệu ứng", expanded=False):
        st.caption("Lưu tài nguyên trên máy để tái sử dụng. Ảnh gốc được giữ nguyên khi dựng video; không dùng AI vẽ lại mặt bài.")
        library = assets.cards()
        available = sum(c["available"] for c in library)
        st.write(f"Bộ Rider–Waite–Smith: {available}/78 lá đã có ảnh")
        st.markdown("Nguồn: [Wikimedia Commons](https://commons.wikimedia.org/wiki/Category:Rider-Waite_tarot_deck). Trình nhập kiểm tra public domain cho từng ảnh và lưu thông tin nguồn.")
        if st.button("Nhập các lá bài còn thiếu từ Commons", key="tarot_import"):
            bar = st.progress(0.0)
            with st.spinner("Đang kiểm tra nguồn và tải ảnh…"):
                result = assets.import_public_domain(bar.progress)
            if result["errors"]:
                st.warning(f"Đã nhập {len(result['imported'])} ảnh. {len(result['errors'])} ảnh chưa tải được; có thể thử lại mà không tải lại ảnh đã có.")
                st.json(result["errors"])
            else:
                st.success("Đã có đủ 78 ảnh trong thư viện.")
            library = assets.cards()
        cards_tab, effects_tab = st.tabs(["Lá bài", "Hiệu ứng MP4"])
        with cards_tab:
            category = st.selectbox("Nhóm bài", ["Tất cả", "Ẩn chính · Major Arcana", "Ẩn phụ · Minor Arcana"], key="tarot_category")
            suit = st.selectbox("Bộ Ẩn phụ", ["Tất cả", *assets.SUITS], key="tarot_suit")
            query = st.text_input("Tìm tên tiếng Việt hoặc tiếng Anh", key="tarot_search")
            filtered = [c for c in library if
                        (category == "Tất cả" or c["category"] == ("major" if category.startswith("Ẩn chính") else "minor"))
                        and (suit == "Tất cả" or c["suit"] == suit)
                        and (not query or assets.normalize(query) in assets.normalize(c["name"]+" "+c["name_vi"]))]
            st.caption(f"{len(filtered)} lá phù hợp")
            card_id = st.selectbox("Lá bài để xem hoặc cập nhật ảnh", [c["id"] for c in filtered],
                                   format_func=lambda cid: next(f"{c['name_vi']} · {c['name']}" for c in library if c["id"] == cid), key="tarot_inspect") if filtered else None
            if card_id:
                card = next(c for c in library if c["id"] == card_id)
                if card["available"]:
                    st.image(str(assets.asset_path(card["image_path"])), width=200, caption=card["name"])
                    provenance = card["provenance"]
                    st.caption(f"Nguồn: {provenance.get('source', 'local')} · {provenance.get('license', '')}")
                else:
                    st.info("Chưa có ảnh. Nhập bộ bài từ Commons hoặc tải ảnh cho lá này.")
                upload = st.file_uploader("Ảnh lá bài · PNG/JPG/WEBP, tối đa 25 MB", type=["png","jpg","jpeg","webp"], key="tarot_card_upload")
                if st.button("Lưu ảnh cho lá bài này", disabled=upload is None, key="tarot_save_card"):
                    try:
                        assets.save_card(card_id, upload.getvalue())
                        st.success("Đã lưu ảnh. Các video mới sẽ dùng ảnh này.")
                        st.rerun()
                    except (ValueError, OSError) as exc:
                        st.error(str(exc))
        with effects_tab:
            category = st.selectbox("Loại hiệu ứng", assets.EFFECT_CATEGORIES, key="tarot_effect_category",
                                    format_func={"card_flip":"Lật bài", "purple_smoke":"Khói tím", "magical_light":"Ánh sáng phép thuật", "particles":"Hạt sáng"}.get)
            st.caption("Dùng MP4 nền đen để hòa trộn ánh sáng. Hiệu ứng nằm sau lá bài để không che hoặc thay đổi mặt bài. Tối đa 150 MB và 120 giây mỗi tệp.")
            uploads = st.file_uploader("Tải hiệu ứng MP4", type=["mp4"], accept_multiple_files=True, key="tarot_effect_uploads")
            if st.button("Lưu hiệu ứng vào thư viện", disabled=not uploads, key="tarot_save_effects"):
                for upload in uploads:
                    try:
                        assets.save_effect(category, upload.getvalue())
                        st.success(f"Đã lưu {upload.name}")
                    except ValueError as exc:
                        st.error(f"{upload.name}: {exc}")
            filtered_effects = [e for e in assets.effects() if e["category"] == category]
            if filtered_effects:
                choice = st.selectbox("Xem hiệu ứng đã lưu", [e["id"] for e in filtered_effects], key="tarot_effect_preview")
                effect = next(e for e in filtered_effects if e["id"] == choice)
                st.video(str(assets.asset_path(effect["path"])))
            else:
                st.info("Chưa có MP4 trong nhóm này. Video vẫn có hiệu ứng lật, khói, ánh sáng và hạt được dựng cục bộ.")


def render_template(params):
    if params.video_source != "tarot":
        return
    with st.container(border=True):
        st.subheader("Video Tarot 9:16")
        st.caption("TikTok · Facebook Reels · YouTube Shorts / 1080 × 1920. Chỉ dùng tài nguyên Tarot cục bộ; không tìm Pexels/Pixabay.")
        library = assets.cards()
        by_id = {c["id"]: c for c in library}
        params.tarot_card_ids = st.multiselect("Chọn bài khi kịch bản không nêu tên lá", list(by_id), key="tarot_card_ids",
            format_func=lambda cid: f"{by_id[cid]['name_vi']} · {by_id[cid]['name']}" + ("" if by_id[cid]["available"] else " · chưa có ảnh"))
        st.caption("Tên đầy đủ trong kịch bản quyết định lá được dùng và thứ tự xuất hiện. Nếu không có tên, dùng các lá chọn thủ công theo thứ tự trên.")
        detected = assets.mentions(params.video_script or "")
        selected_ids = list(dict.fromkeys(m["id"] for m in detected)) if detected else params.tarot_card_ids
        if selected_ids:
            st.write("Tài nguyên sẽ dùng")
            columns = st.columns(4)
            for index, cid in enumerate(selected_ids):
                card = by_id[cid]
                with columns[index % 4]:
                    if card["available"]:
                        st.image(str(assets.asset_path(card["image_path"])), caption=f"{card['name_vi']} · {card['name']}", width="stretch")
                    else:
                        st.warning(f"Thiếu ảnh: {card['name']}")
        else:
            st.info("Nhập kịch bản có tên bài hoặc chọn lá thủ công để xem trước.")
        effect_list = assets.effects()
        params.tarot_effect_ids = st.multiselect("Hiệu ứng MP4 bổ sung", [e["id"] for e in effect_list], key="tarot_effect_ids")
        for effect in effect_list:
            if effect["id"] in params.tarot_effect_ids:
                with st.expander(f"Xem trước {effect['category']} · {effect['name'][:16]}"):
                    st.video(str(assets.asset_path(effect["path"])))
        params.tarot_voice = st.selectbox("Giọng đọc tiếng Việt · Edge TTS", ["vi-VN-HoaiMyNeural-Female", "vi-VN-NamMinhNeural-Male"], key="tarot_voice")
        st.caption("Mẫu này dùng giọng tiếng Việt và bật phụ đề đồng bộ từ bộ tạo phụ đề hiện có. Edge TTS cần Internet, không cần API key.")
        params.tarot_cta_url = st.text_input("Website trên màn hình kết thúc · để trống để tắt", value=assets.DEFAULT_CTA_URL, key="tarot_cta_url")
        params.tarot_cta_duration = st.slider("Thời lượng màn hình kết thúc (giây)", 1.0, 10.0, 3.0, .5, key="tarot_cta_duration")
        try:
            tarot_video.apply_template(params)
        except ValueError as exc:
            st.error(str(exc))
