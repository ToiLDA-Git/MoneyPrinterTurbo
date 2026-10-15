"""Vietnamese, card-grounded editorial plans; AI is an explicit opt-in."""
from __future__ import annotations

import hashlib
import random
import re

from app.services import tarot_assets as assets

THEMES = {
    "love": "Tình yêu và các mối quan hệ", "career": "Sự nghiệp", "finance": "Tài chính",
    "daily": "Tarot hằng ngày", "healing": "Chữa lành", "growth": "Phát triển bản thân", "pick": "Chọn một lá bài",
}
DISCLAIMER = "Tarot chỉ để giải trí và tự chiêm nghiệm, không khẳng định tương lai."
MAJOR_MEANINGS = [
    "một khởi đầu mới cần sự cởi mở, đồng thời cần nhìn rõ rủi ro",
    "khả năng biến ý tưởng thành hành động bằng nguồn lực đang có",
    "trực giác và khoảng lặng để quan sát điều chưa được nói ra",
    "sự nuôi dưỡng, sáng tạo và chăm sóc những nhu cầu thiết thực",
    "cấu trúc, trách nhiệm và ranh giới giúp tạo cảm giác vững vàng",
    "giá trị được học từ cộng đồng, cùng quyền lựa chọn điều phù hợp với mình",
    "một lựa chọn cần sự đồng thuận giữa giá trị cá nhân và mối quan hệ",
    "ý chí tiến lên khi các hướng đi được điều phối rõ ràng",
    "sức mạnh mềm, lòng kiên nhẫn và cách đối xử tử tế với bản thân",
    "thời gian nhìn vào bên trong thay vì vội tìm câu trả lời từ bên ngoài",
    "sự thay đổi theo chu kỳ và khả năng thích nghi với phần mình không kiểm soát",
    "sự công bằng, trách nhiệm và việc nhìn đủ dữ kiện trước khi quyết định",
    "một khoảng dừng có chủ đích để nhìn vấn đề từ góc khác",
    "sự kết thúc của một giai đoạn để nhường chỗ cho cách sống mới",
    "nhịp độ vừa sức và sự kết hợp hài hòa giữa những nhu cầu khác nhau",
    "thói quen hoặc sự lệ thuộc cần được nhận diện mà không tự trách",
    "một cấu trúc cũ cần xem lại khi thực tế không còn khớp với kỳ vọng",
    "hy vọng được nuôi bằng những bước hồi phục nhỏ và đều đặn",
    "cảm xúc mơ hồ cần được lắng nghe nhưng chưa nên coi là bằng chứng",
    "sự rõ ràng, niềm vui giản dị và khả năng nhìn nhận điều đang tốt lên",
    "việc nhìn lại, tự đánh giá và chọn cách đáp lại có ý thức",
    "sự hoàn tất một chặng đường và nhận ra điều mình đã học được",
]
SUIT_MEANINGS = {
    "Wands": "năng lượng, sáng tạo và động lực", "Cups": "cảm xúc và sự kết nối",
    "Swords": "suy nghĩ, lời nói và cách xử lý xung đột", "Pentacles": "nguồn lực, công việc và nền tảng vật chất",
}
RANK_MEANINGS = [
    "một cơ hội mới đang được nhận diện", "hai hướng lựa chọn cần cân nhắc",
    "sự hợp tác và bước phát triển đầu tiên", "nhu cầu ổn định, nghỉ ngơi hoặc xem lại giới hạn",
    "mâu thuẫn hoặc thiếu hụt cần được gọi tên", "trao nhận, điều chỉnh và tìm lại sự cân bằng",
    "đánh giá lựa chọn thay vì phản ứng theo áp lực", "luyện tập, chuyển động và những nỗ lực lặp lại",
    "bài học về tính tự chủ và điều mình đã tích lũy", "kết quả của một chu kỳ, cả thành quả lẫn trách nhiệm",
    "tinh thần học hỏi và một thông điệp cần quan sát", "động lực hành động cần đi cùng sự tỉnh táo",
    "khả năng chăm sóc và làm chủ trải nghiệm bên trong", "trách nhiệm dẫn dắt và cách dùng kinh nghiệm chín chắn",
]
# Specific pip meanings prevent a generic suit/rank description from flattening the reading.
PIP_MEANINGS = {
    "Cups": ["cởi mở với cảm xúc mới", "sự tương hỗ trong kết nối", "niềm vui được sẻ chia", "xem lại điều mình đang bỏ qua", "thừa nhận mất mát nhưng không quên điều còn lại", "ký ức và sự thân thuộc", "nhiều viễn cảnh cần được phân biệt với thực tế", "rời điều không còn nuôi dưỡng mình", "ghi nhận điều đủ đầy", "cảm giác thuộc về và sự hòa hợp"],
    "Swords": ["một góc nhìn rõ ràng", "trạng thái phân vân cần thêm dữ kiện", "nỗi đau cần được thừa nhận", "khoảng nghỉ cho tâm trí", "cái giá của việc phải thắng bằng mọi cách", "chuyển dần khỏi giai đoạn căng thẳng", "chiến lược và tính minh bạch", "những giới hạn trong cách nhìn của mình", "lo âu cần được chia sẻ và hỗ trợ", "khép lại một chu kỳ suy nghĩ đã cạn sức"],
    "Wands": ["một tia hứng khởi", "lập kế hoạch và nhìn xa hơn", "mở rộng góc nhìn sau bước khởi đầu", "một nền tảng đáng ăn mừng", "khác biệt quan điểm và sự cạnh tranh", "ghi nhận nỗ lực và tiến bộ", "giữ vững ranh giới trước áp lực", "nhịp chuyển động nhanh cần được điều phối", "sự bền bỉ đi cùng nhu cầu nghỉ ngơi", "gánh nặng cần được chia sẻ"],
    "Pentacles": ["cơ hội xây nền tảng thiết thực", "cân đối nhiều trách nhiệm", "tay nghề và sự hợp tác", "nhu cầu an toàn nhưng tránh giữ quá chặt", "khó khăn và việc tìm nguồn hỗ trợ", "trao nhận nguồn lực một cách công bằng", "kiên nhẫn đánh giá kết quả", "rèn kỹ năng bằng công việc đều đặn", "sự độc lập được xây từ thói quen", "nền tảng lâu dài và trách nhiệm chung"],
}
LENSES = {
    "love": ["việc nói rõ nhu cầu", "ranh giới trong một mối quan hệ", "cách bạn lắng nghe", "sự cân bằng giữa cho và nhận", "niềm tin được xây từ hành động", "khoảng cách cần thiết để bình tâm"],
    "career": ["một bước chuyển trong công việc", "cách phối hợp với đồng nghiệp", "kỹ năng bạn muốn rèn", "ưu tiên giữa nhiều nhiệm vụ", "cách phản hồi trước áp lực", "tiêu chuẩn thành công của riêng bạn"],
    "finance": ["thói quen ghi lại chi tiêu", "quỹ dự phòng vừa sức", "sự khác biệt giữa nhu cầu và mong muốn", "cách nhìn một lời mời đầu tư", "nhịp tiết kiệm có thể duy trì", "nguồn lực bạn đang bỏ quên"],
    "daily": ["một lựa chọn nhỏ trong hôm nay", "nhịp làm việc và nghỉ ngơi", "cuộc trò chuyện bạn đang trì hoãn", "một việc có thể đơn giản hóa", "điều đang thu hút sự chú ý", "một thay đổi nhỏ trong lịch trình"],
    "healing": ["việc cho phép mình nghỉ", "cách gọi tên cảm xúc", "một nhu cầu chưa được chăm sóc", "ranh giới với điều khiến bạn cạn sức", "sự hỗ trợ bạn có thể tìm đến", "một lời tử tế dành cho chính mình"],
    "growth": ["một thói quen bạn muốn đổi", "cách học từ sai sót", "niềm tin cần được xem lại", "sự kiên nhẫn với quá trình học", "quyền lựa chọn nhịp đi riêng", "mục tiêu có ý nghĩa với bạn"],
    "pick": ["điều khiến bạn dừng lại ở lá bài này", "một câu hỏi bạn đang mang theo", "một hướng nhìn mới", "điều bạn muốn giữ lại", "một hành động vừa sức", "thông điệp bạn muốn tự viết cho mình"],
}
HOOKS = [
    "Có điều gì bạn đang cố hiểu, dù chưa tìm được lời gọi tên?",
    "Trước khi đưa ra quyết định, hãy dành một nhịp để nhìn lại.",
    "Nếu hôm nay chỉ làm một việc cho mình, bạn sẽ chọn điều gì?",
    "Bạn có đang nhầm giữa điều mình muốn và điều mình cần?",
    "Một khoảng dừng đôi khi giúp ta nhìn rõ hơn một bước vội.",
    "Điều gì đang lấy đi nhiều năng lượng hơn bạn nhận ra?",
    "Có câu hỏi nào bạn chưa cho phép mình trả lời thật lòng?",
    "Hãy nghĩ đến một tình huống gần đây khiến bạn phân vân.",
    "Bạn đang giữ điều này vì còn phù hợp, hay vì đã quen?",
    "Nếu bớt một chút áp lực, bạn muốn thay đổi điều gì trước?",
    "Có một góc nhìn khác mà bạn chưa thử đặt mình vào.",
    "Đâu là điều bạn có thể tự quyết, ngay trong hôm nay?",
]
ACTIONS = [
    "Thử viết một câu về điều bạn cần, rồi chọn một bước nhỏ có thể làm hôm nay.",
    "Hãy tách điều đã biết khỏi điều đang suy đoán. Bạn chưa cần kết luận ngay.",
    "Bạn có thể tạm gác một việc không cấp thiết và dành thời gian cho điều quan trọng hơn.",
    "Thử nói rõ một giới hạn bằng lời bình tĩnh. Quan sát phản hồi thực tế thay vì tự đoán.",
    "Ghi lại một điều đang hỗ trợ bạn. Từ điểm tựa ấy, chọn việc tiếp theo vừa sức.",
    "Hãy hỏi mình đang cần thêm thông tin, thêm thời gian, hay một sự hỗ trợ cụ thể.",
]


def meaning(card):
    if card["category"] == "major":
        return MAJOR_MEANINGS[card["number"]]
    if card["number"] <= 10:
        return PIP_MEANINGS[card["suit"]][card["number"]-1]
    return f"{RANK_MEANINGS[card['number']-1]} trong {SUIT_MEANINGS[card['suit']]}"


def select_random(available_ids, count, rng=None, used=None):
    if count not in (1,3) or len(available_ids) < count:
        raise ValueError("Need enough distinct local cards for a one-card or three-card reading")
    rng = rng or random.Random()
    for _ in range(200):
        chosen = rng.sample(sorted(available_ids), count)
        if used is None or tuple(sorted(chosen)) not in used:
            return chosen
    # Exhausted one-card combinations may recur with a different editorial lens.
    return rng.sample(sorted(available_ids), count)


def validate_script(script, card_ids):
    if not isinstance(script,str) or not 40 <= len(script.strip()) <= 12000:
        raise ValueError("Script must contain 40–12000 characters")
    normalized = assets.normalize(script)
    forbidden = ("chac chan se", "100%", "nguoi ay chac chan", "dam bao ban se", "nhat dinh se")
    if any(term in normalized for term in forbidden):
        raise ValueError("Script makes a certainty claim. Use reflective language rather than guaranteed predictions.")
    found = {m["id"] for m in assets.mentions(script)}
    if found != set(card_ids):
        raise ValueError("Script must name exactly the selected cards; review the names and selection")
    return script.strip()


def generate(theme, selected, duration, variant=0, *, ai=False, manual_script="", cta_url=assets.DEFAULT_CTA_URL, cta_text="Trải bài Tarot miễn phí tại", app_config=None, cta_duration=3):
    if theme not in THEMES or duration not in (30,45,60):
        raise ValueError("Choose a supported theme and 30/45/60 seconds")
    lens = LENSES[theme][(variant//len(HOOKS)) % len(LENSES[theme])]
    question = ["Bạn muốn bắt đầu từ đâu?", "Điều gì có thể nhẹ nhàng buông xuống?", "Bạn cần thêm sự hỗ trợ nào?"][(variant//(len(HOOKS)*len(LENSES[theme])))%3]
    hook = f"{HOOKS[variant % len(HOOKS)]} Với {lens}, {question[0].lower()+question[1:]}"
    names = [card["name"] for card in selected]
    interpretations = [{"card_id": c["id"], "name": c["name"], "meaning": meaning(c)} for c in selected]
    intro = ["Hãy nhìn những lá bài này như một gợi ý để tự hỏi, không phải lời tiên đoán.",
             "Mình cùng mở một góc nhìn về điều bạn đang quan tâm qua những lá bài này.",
             "Giữ trong đầu một câu hỏi. Những hình ảnh này là điểm bắt đầu để suy ngẫm."][variant % 3]
    paragraphs = [hook, intro]
    for index, interpretation in enumerate(interpretations):
        lead = ["Lá", "Tiếp theo, lá", "Cuối cùng, lá"][min(index,2)]
        paragraphs.append(f"{lead} {interpretation['name']} gợi nhắc {interpretation['meaning']}.")
    paragraphs.append(f"Với {lens}, bạn có thể xem đâu là nhu cầu thật của mình, thay vì vội làm theo kỳ vọng.")
    if len(selected) == 3:
        paragraphs.append(f"Đặt {names[0]} cạnh {names[-1]}, hãy thử nhìn cả điều cần giữ lẫn điều có thể thay đổi.")
    paragraphs.append(ACTIONS[(variant//len(LENSES[theme])+variant) % len(ACTIONS)])
    if duration >= 45:
        paragraphs.append(f"Nhớ lại một tình huống cụ thể liên quan đến {lens}. Điều gì đã xảy ra trên thực tế? Điều gì chỉ là cách bạn đang diễn giải?")
        paragraphs.append(ACTIONS[(variant+2) % len(ACTIONS)])
    if duration == 60:
        paragraphs.append("Bạn không cần giải quyết mọi thứ trong một lần. Có thể thử một thay đổi nhỏ, quan sát cảm giác của mình, rồi điều chỉnh khi có thêm thông tin.")
        paragraphs.append(f"Một tuần nữa, bạn muốn nhìn lại {lens} với cảm giác nào? Hãy chọn một hành động thật cụ thể để bắt đầu.")
    paragraphs += ["Thông điệp nào khiến bạn muốn dừng lại suy nghĩ?", DISCLAIMER]
    if cta_url:
        paragraphs.append(f"{cta_text} website trên màn hình.")
    script = "\n\n".join(paragraphs)
    speech_duration=max(1, duration-(cta_duration if cta_url else 0))
    word_budget=max(25, int(speech_duration*3))
    if not manual_script and not ai:
        # Keep complete card meanings and the disclaimer. Fill the remaining
        # narration budget with whole sentences instead of truncating artwork names.
        hook=HOOKS[variant % len(HOOKS)]
        reading=[f"Lá {item['name']} gợi nhắc {item['meaning']}." for item in interpretations]
        compact=[hook, *reading]
        ending=[DISCLAIMER]
        candidates=[f"Hôm nay, hãy nhìn lại {lens}.",
                    ACTIONS[(variant//len(LENSES[theme])+variant) % len(ACTIONS)],
                    intro, question, *paragraphs[2+len(selected):]]
        if cta_url:
            candidates.insert(1,f"{cta_text} website trên màn hình.")
        for sentence in candidates:
            if sentence in compact or sentence in ending:
                continue
            if len(' '.join([*compact, sentence, *ending]).split()) <= word_budget:
                compact.append(sentence)
        script='\n\n'.join([*compact,*ending])
    if manual_script:
        script = manual_script
    elif ai:
        from app.services import llm
        prompt = f"""Viết lời đọc Tarot tiếng Việt tự nhiên tối đa {word_budget} từ (đếm theo khoảng trắng), cho {speech_duration} giây lời đọc trong video {duration} giây.
Chủ đề: {THEMES[theme]}; góc nhìn riêng: {lens}; biến thể {variant}.
Chỉ dùng đúng các lá sau và giữ nguyên tên tiếng Anh: {interpretations!r}.
Mở đầu bằng một hook, giới thiệu lựa chọn, giải nghĩa từng lá bằng tình huống cụ thể,
kết bằng câu hỏi tự suy ngẫm và CTA: {cta_text if cta_url else 'không quảng bá'}.
Không đảm bảo tương lai, không khẳng định cảm xúc của người khác. Thêm câu: {DISCLAIMER}
Đổi cấu trúc kể chuyện, không chỉ thay từ. Chỉ trả về lời đọc, không nhãn thời gian, không JSON."""
        script = llm._generate_response(prompt, app_config=app_config, request_timeout=60, max_retries=0)
        if not script or script.startswith("Error:"):
            raise ValueError(f"LLM script failed: {(script or 'empty response')[:300]}")
        hook = re.split(r"[.!?\n]",script)[0].strip()
    if "tu chiem nghiem" not in assets.normalize(script):
        script += "\n\n" + DISCLAIMER
    validate_script(script, [c["id"] for c in selected])
    title = f"{THEMES[theme]} · {selected[0]['name_vi']} · Góc nhìn {variant+1:03}"
    hashtags = ["#Tarot", "#TarotVietNam", "#TuChiemNghiem", {"love":"#TinhYeu","career":"#SuNghiep","finance":"#TaiChinh","daily":"#TarotMoiNgay","healing":"#ChuaLanh","growth":"#PhatTrienBanThan","pick":"#PickACard"}[theme]]
    caption = f"{title}\n{hook}\n{DISCLAIMER}" + (f"\n{cta_text} {cta_url}" if cta_url else "") + "\n" + " ".join(hashtags)
    return {"title": title, "hook": hook, "script": script, "card_ids": [c["id"] for c in selected],
            "meanings": interpretations, "caption": caption, "hashtags": hashtags,
            "fingerprint": hashlib.sha256(script.encode()).hexdigest(),
            "timing": {"hook": [0,3], "introduction": [3,8], "reading": [8,duration-8], "summary_cta": [duration-8,duration]}}


def visual_variant(index, seed=0):
    # Orthogonal continuous parameters give up to 100 distinct combinations,
    # even when only one local MP4 effect is available.
    return {"seed": seed+index, "intensity": .65+(index%10)*.055,
            "opacity": .3+((index//10)%10)*.05, "zoom": .025+(index%7)*.007,
            "flip_duration": .65+(index%5)*.1, "particle_color": "gold"}
