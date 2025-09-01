import os
import re
import json
import time
import base64
import logging
from functools import lru_cache

import pandas as pd
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ذكاء اصطناعي (Gemini)
import google.generativeai as genai

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)
logging.basicConfig(level=logging.INFO)

###############################################################################
#                         قراءة الشبكة + كاش محسّن                           #
###############################################################################
EXCEL_PATH = os.path.join(os.path.abspath(os.path.dirname(__file__)), "network_data.xlsx")
DATA_MTIME = 0
DATA_CACHE = []

def _normalize_ar(s: str) -> str:
    if not s:
        return ""
    s = str(s)
    # ازالة تشكيل/رموز وتوحيد بعض الحروف
    s = re.sub(r"[ًٌٍَُِّْـ]", "", s)
    s = s.replace("أ","ا").replace("إ","ا").replace("آ","ا")
    s = s.replace("ى","ي").replace("ؤ","و").replace("ئ","ي").replace("ة","ه")
    s = re.sub(r"[^0-9A-Za-z\u0600-\u06FF\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()

def _phones_from_row(row):
    phones = []
    for i in range(5, 9):  # Telephone1..Telephone4
        v = str(row.iloc[i]).replace(".0", "").strip()
        if v and v != "0" and v.lower() != "nan":
            phones.append(v)
    return phones

def load_network_data(force=False):
    """يقرأ ملف الشبكة مرّة واحدة ويخزّنه في الذاكرة مع مراقبة تغيّر الملف."""
    global DATA_MTIME, DATA_CACHE
    try:
        if not os.path.exists(EXCEL_PATH):
            app.logger.error("لم يتم العثور على network_data.xlsx")
            return []

        mtime = os.path.getmtime(EXCEL_PATH)
        if force or mtime != DATA_MTIME or not DATA_CACHE:
            df = pd.read_excel(EXCEL_PATH, sheet_name=0, header=0).fillna("")
            data = []
            for idx, row in df.iterrows():
                # ترتيب الأعمدة حسب طلبك:
                # 0: المنطقة/المحافظة | 1: نوع مقدم الخدمة(=التخصص الرئيسي)
                # 2: التخصص الفرعي | 3: اسم مقدم الخدمة
                # 4: العنوان | 5..8 تلفونات | 9: Hotline
                if len(row) < 10:
                    continue
                gov  = str(row.iloc[0]).strip()
                ptyp = str(row.iloc[1]).strip()
                ssub = str(row.iloc[2]).strip()
                name = str(row.iloc[3]).strip()
                addr = str(row.iloc[4]).strip()
                phones = _phones_from_row(row)
                hotline = str(row.iloc[9]).replace(".0","").strip()
                hotline = hotline if hotline and hotline != "0" and hotline.lower() != "nan" else ""

                item = {
                    "id": f"row-{idx}",
                    "governorate": gov,
                    "provider_type": ptyp,
                    "specialty_sub": ssub,
                    "name": name,
                    "address": addr,
                    "phones": phones,
                    "hotline": hotline,
                    # مفاتيح مساعدة للبحث السريع
                    "_norm_gov": _normalize_ar(gov),
                    "_norm_addr": _normalize_ar(addr),
                    "_norm_name": _normalize_ar(name),
                    "_norm_typ": _normalize_ar(ptyp),
                    "_norm_sub": _normalize_ar(ssub),
                }
                data.append(item)
            DATA_CACHE = data
            DATA_MTIME = mtime
            app.logger.info(f"Loaded {len(DATA_CACHE)} providers into memory.")
        return DATA_CACHE
    except Exception as e:
        app.logger.error(f"Excel load error: {e}", exc_info=True)
        return []

###############################################################################
#                      خرائط مرادفات/تطبيع المواقع                           #
###############################################################################
BASE_LOCATION_SYNONYMS = {
    # أمثلة مهمة—يمكنك إضافة مرادفات لاحقاً بدون كسر الكود
    "الجيزه":"الجيزة", "جيزه":"الجيزة", "giza":"الجيزة", "هرم":"الجيزة", "الهرم":"الجيزة", "الطالبيه":"الجيزة",
    "القاهره":"القاهرة", "cairo":"القاهرة", "nasr city":"القاهرة", "مدينه نصر":"القاهرة",
    "اسكندريه":"الإسكندرية", "اسكندرية":"الإسكندرية", "alex":"الإسكندرية",
}

def resolve_location(user_text, dataset):
    """يحاول فهم المحافظة/المنطقة من نص حر (عامي)."""
    if not user_text:
        return ""
    t = _normalize_ar(user_text)
    # مرادفات ثابتة
    for k, v in BASE_LOCATION_SYNONYMS.items():
        if _normalize_ar(k) in t:
            return v
    # استنتاج من البيانات نفسها
    unique_govs = sorted({d["governorate"] for d in dataset if d["governorate"].strip()})
    unique_norms = [(g, _normalize_ar(g)) for g in unique_govs]
    best = ""
    best_len = 0
    for g, gn in unique_norms:
        if not gn: 
            continue
        if gn in t or any(w for w in gn.split() if w in t):
            if len(gn) > best_len:
                best = g
                best_len = len(gn)
    return best or user_text.strip()

###############################################################################
#                  ذكاء اصطناعي: تصنيف التخصص + نصائح                        #
###############################################################################
def _setup_genai():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY غير مُعدّ.")
    genai.configure(api_key=key)
    return genai.GenerativeModel("gemini-1.5-flash")

@lru_cache(maxsize=256)
def ai_classify_specialty(symptoms_text, available_types, available_subs):
    """يعيد: recommended_specialty, doctor_explanation, temporary_advice(list)"""
    model = _setup_genai()
    prompt = f"""
أنت طبيب استشاري. حلّل الأعراض العربية التالية وحدّد التخصص الأنسب بدقة.
- لا ترشح "صيدلية" لحالات تحتاج طبيب/مستشفى.
- لو الحالة طارئة (ألم صدر شديد، نزيف، فقدان وعي...) رشّح "طوارئ مستشفى".
- التزم فقط بالتخصصات التالية (رئيسيّة وفرعية) المتاحة في الشبكة:
الأنواع المتاحة: {available_types}
التخصصات الفرعية المتاحة: {available_subs}

أعد JSON فقط:
{{
  "recommended_specialty": "اسم التخصص الأنسب (رئيسي أو فرعي)",
  "doctor_explanation": "شرح مبسط واحترافي للأعراض المحتملة",
  "temporary_advice": ["نصيحة 1", "نصيحة 2", "نصيحة 3"]
}}    

الأعراض: "{symptoms_text}"
"""
    r = model.generate_content(prompt)
    txt = r.text.strip().replace("```json","").replace("```","")
    try:
        data = json.loads(txt)
    except Exception:
        data = {
            "recommended_specialty": "",
            "doctor_explanation": "",
            "temporary_advice": []
        }
    return (
        data.get("recommended_specialty",""),
        data.get("doctor_explanation",""),
        data.get("temporary_advice",[]) or []
    )

###############################################################################
#                         ترتيب النتائج + خرائط GPS                           #
###############################################################################
def make_maps_url(name, address):
    q = f"{name} {address}".strip()
    return f"https://www.google.com/maps/search/?api=1&query={json.dumps(q, ensure_ascii=False)[1:-1]}"

def score_provider(p, gov_norm, spec_norm):
    score = 0
    if gov_norm:
        if gov_norm in p["_norm_gov"] or gov_norm in p["_norm_addr"]:
            score += 60
    # مطابقة التخصص
    if spec_norm:
        if spec_norm in p["_norm_sub"] or spec_norm in p["_norm_typ"]:
            score += 30
    # تفصيلة إضافية: الاسم والعنوان
    if spec_norm and spec_norm in p["_norm_name"]:
        score += 5
    if gov_norm and gov_norm in p["_norm_name"]:
        score += 5
    return score

def pick_providers(dataset, resolved_gov, recommended_specialty, limit=20):
    gov_norm = _normalize_ar(resolved_gov)
    spec_norm = _normalize_ar(recommended_specialty)
    items = []
    for p in dataset:
        s = score_provider(p, gov_norm, spec_norm)
        if s > 0:
            items.append((s, p))
    items.sort(key=lambda x: x[0], reverse=True)
    ranked = [p for _, p in items][:limit]
    # علّم الأول "الترشيح الأنسب"
    out = []
    for i, p in enumerate(ranked):
        out.append({
            "id": p["id"],
            "name": p["name"],
            "governorate": p["governorate"],
            "provider_type": p["provider_type"],
            "specialty_sub": p["specialty_sub"],
            "address": p["address"],
            "phones": p["phones"],
            "hotline": p["hotline"],
            "maps_url": make_maps_url(p["name"], p["address"]),
            "best": i == 0
        })
    return out

###############################################################################
#                                 Routes                                      #
###############################################################################
@app.route("/")
def home():
    return send_from_directory("static", "index.html")

# البحث الذكي بالأعراض + المكان
@app.route("/api/symptoms-search", methods=["POST"])
def symptoms_search():
    try:
        payload = request.get_json(force=True)
        symptoms = (payload.get("symptoms") or "").strip()
        location = (payload.get("location") or "").strip()
        if not symptoms or not location:
            return jsonify({"error":"الرجاء إدخال الأعراض والمكان."}), 400

        data = load_network_data()
        if not data:
            return jsonify({"error":"قاعدة بيانات الشبكة غير متاحة."}), 500

        resolved_loc = resolve_location(location, data)

        # قوائم التخصصات المتاحة لضبط الذكاء الاصطناعي
        types = sorted({d["provider_type"] for d in data if d["provider_type"]})
        subs  = sorted({d["specialty_sub"] for d in data if d["specialty_sub"]})

        rec_spec, expl, tips = ai_classify_specialty(symptoms, tuple(types), tuple(subs))
        providers = pick_providers(data, resolved_loc, rec_spec, limit=25)

        return jsonify({
            "resolved_location": resolved_loc,
            "recommended_specialty": rec_spec,
            "doctor_explanation": expl,
            "temporary_advice": tips,
            "providers": providers
        })
    except Exception as e:
        app.logger.error(f"/api/symptoms-search error: {e}", exc_info=True)
        return jsonify({"error":"حدث خطأ غير متوقع أثناء البحث."}), 500

# تحليل التقارير/التحاليل + ترشيح مقدمي الخدمة
@app.route("/api/analyze", methods=["POST"])
def analyze_reports():
    try:
        payload = request.get_json(force=True)
        files_payload = payload.get("files", [])
        location = (payload.get("location") or "").strip()

        if not files_payload:
            return jsonify({"error":"لم يتم إرفاق ملفات."}), 400

        model = _setup_genai()
        prompt_parts = ["""
أنت طبيب استشاري. حلّل ملفات التقارير/التحاليل/الأشعة المُرفقة وقدّم JSON فقط:
{
  "interpretation": "شرح مبسط لما يظهر في التقارير",
  "temporary_advice": ["نصيحة 1","نصيحة 2","نصيحة 3"],
  "recommended_specialty": "التخصص الأنسب للمتابعة"
}
أكّد أن التحليل إرشادي ولا يغني عن زيارة الطبيب.
        """]
        for f in files_payload:
            prompt_parts.append({"mime_type": f["mime_type"], "data": f["data"]})
        r = model.generate_content(prompt_parts)
        txt = r.text.strip().replace("```json","").replace("```","")
        result = json.loads(txt)

        # ترشيح مقدمي خدمة (لو المكان موجود)
        providers = []
        resolved_loc = ""
        if location:
            data = load_network_data()
            resolved_loc = resolve_location(location, data)
            providers = pick_providers(data, resolved_loc, result.get("recommended_specialty",""), limit=25)

        return jsonify({
            "interpretation": result.get("interpretation",""),
            "temporary_advice": result.get("temporary_advice",[]) or [],
            "recommended_specialty": result.get("recommended_specialty",""),
            "resolved_location": resolved_loc,
            "providers": providers
        })
    except Exception as e:
        app.logger.error(f"/api/analyze error: {e}", exc_info=True)
        return jsonify({"error":"تعذر تحليل التقارير حالياً."}), 500

if __name__ == "__main__":
    load_network_data(force=True)  # warm cache
    app.run(host="0.0.0.0", port=5000, debug=True)
