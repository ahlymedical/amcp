import os, re, json, logging, base64
from functools import lru_cache

import pandas as pd
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# =====[ إعداد اختياري لنموذج جوجل ]=====
USE_GENAI = False
try:
    import google.generativeai as genai
    if os.environ.get("GEMINI_API_KEY"):
        genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
        USE_GENAI = True
except Exception:
    USE_GENAI = False

APP_DIR = os.path.abspath(os.path.dirname(__file__))
EXCEL_PATH = os.path.join(APP_DIR, "network_data.xlsx")

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)
logging.basicConfig(level=logging.INFO)

# =========[ تطبيع نصي للبحث الذكي ]=========
AR_DIAC = r"[ًٌٍَُِّْـ]"

def normalize(text: str) -> str:
    if text is None:
        return ""
    t = str(text)
    t = re.sub(AR_DIAC, "", t)
    t = (t.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
           .replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي").replace("ة", "ه"))
    t = re.sub(r"[^0-9A-Za-z\u0600-\u06FF\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()

# =========[ ترشيح أعمدة الإكسل تلقائيًا ]=========
COLUMN_CANDIDATES = {
    "governorate": ["المنطقة","المنطقه","المحافظة","المحافظه","governorate","region","area","city"],
    "provider_type": ["نوع مقدم الخدمة","نوع مقدم الخدمه","التخصص الرئيسي","provider type","main specialty"],
    "specialty_sub": ["التخصص الفرعي","sub specialty","subspecialty","specialty sub"],
    "name": ["اسم مقدم الخدمة","اسم مقدم الخدمه","provider name","name","الاسم"],
    "address": ["العنوان","عنوان مقدم الخدمة","address","provider address"],
    "telephone1": ["telephone1","phone1","تليفون1","تليفون 1"],
    "telephone2": ["telephone2","phone2","تليفون2","تليفون 2"],
    "telephone3": ["telephone3","phone3","تليفون3","تليفون 3"],
    "telephone4": ["telephone4","phone4","تليفون4","تليفون 4"],
    "hotline": ["hotline","الخط الساخن","hot line"]
}

def _match_col(df_cols, candidates):
    norm_cols = {normalize(c): c for c in df_cols}
    for cand in candidates:
        k = normalize(cand)
        for cn, real in norm_cols.items():
            if k == cn or k in cn:
                return real
    return None

def _auto_map_columns(df: pd.DataFrame):
    mapping = {}
    for key, cands in COLUMN_CANDIDATES.items():
        col = _match_col(df.columns, cands)
        if col:
            mapping[key] = col
    # phones fallback لو الأعمدة أسماءها مختلفة
    tel_cols = []
    for c in df.columns:
        cn = normalize(c)
        if cn.startswith("telephone") or cn.startswith("phone") or "تليفون" in cn:
            tel_cols.append(c)
    if tel_cols:
        for i, c in enumerate(tel_cols[:4], start=1):
            mapping.setdefault(f"telephone{i}", c)
    return mapping

# =========[ كاش الشبكة ]=========
_DATA_CACHE = []
_DATA_MTIME = 0

def load_data(force=False):
    """تحميل الشبكة من الإكسل مع كاش قوي."""
    global _DATA_CACHE, _DATA_MTIME
    if not os.path.exists(EXCEL_PATH):
        logging.warning("network_data.xlsx غير موجود.")
        _DATA_CACHE = []
        return []
    mtime = os.path.getmtime(EXCEL_PATH)
    if force or mtime != _DATA_MTIME:
        df = pd.read_excel(EXCEL_PATH).fillna("")
        colmap = _auto_map_columns(df)
        out = []
        for i, row in df.iterrows():
            def g(key):
                c = colmap.get(key)
                return str(row[c]).strip() if c else ""
            phones = []
            for k in ["telephone1","telephone2","telephone3","telephone4"]:
                v = g(k)
                if v and v != "0":
                    phones.append(v.replace(".0",""))
            hotline = g("hotline").replace(".0","").strip()
            hotline = hotline if hotline and hotline != "0" else ""
            d = {
                "id": f"row-{i}",
                "governorate": g("governorate"),
                "provider_type": g("provider_type"),
                "specialty_sub": g("specialty_sub"),
                "name": g("name"),
                "address": g("address"),
                "phones": phones,
                "hotline": hotline,
            }
            d["_gov"]  = normalize(d["governorate"]) + " " + normalize(d["address"])
            d["_spec"] = normalize(d["provider_type"]) + " " + normalize(d["specialty_sub"]) + " " + normalize(d["name"])
            out.append(d)
        _DATA_CACHE = out
        _DATA_MTIME = mtime
        logging.info(f"تم تحميل الشبكة: {len(out)} مقدم خدمة.")
    return _DATA_CACHE

# =========[ مرادفات مكان ولهجة ]=========
LOC_SYNONYMS = {
    "الجيزه":"الجيزة","جيزه":"الجيزة","giza":"الجيزة","الهرم":"الجيزة","هرم":"الجيزة",
    "الطالبية":"الجيزة","الطالبيه":"الجيزة","الطالبيه هرم":"الجيزة","al talbiya":"الجيزة",
    "cairo":"القاهرة","القاهره":"القاهرة","nasr city":"القاهرة","مدينة نصر":"القاهرة","مدينه نصر":"القاهرة",
    "alex":"الإسكندرية","اسكندريه":"الإسكندرية","اسكندرية":"الإسكندرية","alexandria":"الإسكندرية"
}

def resolve_location(txt: str, dataset) -> str:
    t = normalize(txt)
    if not t: return ""
    for k, v in LOC_SYNONYMS.items():
        if normalize(k) in t:
            return v
    for d in dataset:
        gov = normalize(d.get("governorate",""))
        if gov and (gov in t or t in gov):
            return d.get("governorate","")
    return txt.strip()

# =========[ مرادفات أعراض (مصري/عربي/إنجليزي) ]=========
SYMPTOM_LEXICON = {
    # مخ وأعصاب
    "صداع":"مخ واعصاب","مصدع":"مخ واعصاب","migraine":"مخ واعصاب","headache":"مخ واعصاب",
    "دوخه":"مخ واعصاب","dizziness":"مخ واعصاب","تنميل":"مخ واعصاب",
    # باطنة
    "ارهاق":"باطنه","تعبان":"باطنه","تعبان اوي":"باطنه","fever":"باطنه","سخونية":"باطنه","حراره":"باطنه",
    "مغص":"باطنه","وجع بطن":"باطنه","stomach":"باطنه","abdominal":"باطنه","diarrhea":"باطنه","اسهال":"باطنه",
    # قلب وصدر
    "الم في الصدر":"قلبيه وصدر","chest pain":"قلبيه وصدر","shortness of breath":"قلبيه وصدر","ضيق نفس":"قلبيه وصدر","نهجان":"قلبيه وصدر",
    # عظام
    "ركبه":"عظام","كسر":"عظام","back pain":"عظام","الام ظهر":"عظام","muskuloskeletal":"عظام",
    # جلدية
    "حساسيه":"جلديه","rash":"جلديه","طفح":"جلديه","acne":"جلديه",
    # انف واذن وحنجرة
    "التهاب حلق":"انف واذن وحنجره","sore throat":"انف واذن وحنجره","tonsils":"انف واذن وحنجره","اذن":"انف واذن وحنجره",
    # اسنان
    "ضرس":"اسنان","tooth":"اسنان","toothache":"اسنان",
    # عيون
    "vision":"عيون","نظاره":"عيون","احمرار عين":"عيون",
    # نساء
    "حمل":"نساء وتوليد","pregnant":"نساء وتوليد","تاخير دوره":"نساء وتوليد",
    # مسالك
    "حرقان بول":"مسالك بوليه","urinary":"مسالك بوليه","kidney":"مسالك بوليه"
}

# =========[ تصنيف الأعراض → تخصص ]=========
@lru_cache(maxsize=256)
def classify_specialty(symptoms: str, available_types: tuple, available_subs: tuple) -> dict:
    text = normalize(symptoms)
    # حالات طوارئ واضحة → مستشفى
    if any(k in text for k in ["الم في الصدر","chest pain","فقدان وعي","نزيف","bleeding","stroke","جلطه"]):
        return {
            "recommended_specialty":"طوارئ مستشفى",
            "doctor_explanation":"الأعراض قد تشير لحالة طارئة، يُنصح بالتوجه الفوري لأقرب طوارئ.",
            "temporary_advice":["اتصل بالإسعاف عند ألم شديد أو فقدان وعي.","تجنب أي مجهود حتى تقييم الحالة."]
        }

    prelim = None
    for key, spec in SYMPTOM_LEXICON.items():
        if normalize(key) in text:
            prelim = spec
            break
    if not prelim:
        prelim = "باطنه"  # افتراضي آمن بدل صيدلية

    if USE_GENAI:
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            prompt = f"""
أنت طبيب استشاري محترف. حلّل الأعراض (قد تكون بالعربية/المصرية/الإنجليزية) وحدد التخصص الأنسب من المتاح.
لا ترشح صيدلية إلا لو أمر بسيط جدًا. الطوارئ = "طوارئ مستشفى".
الأعراض: "{symptoms}"
التخصص المبدئي: "{prelim}"
أعد JSON فقط:
{{"recommended_specialty":"...","doctor_explanation":"...","temporary_advice":["...","..."]}}
"""
            r = model.generate_content(prompt)
            txt = r.text.strip().replace("```json","").replace("```","")
            data = json.loads(txt)
            # منع الانحراف للصيدلية
            if normalize(data.get("recommended_specialty","")).startswith("صيدل"):
                data["recommended_specialty"] = prelim
            return data
        except Exception as e:
            logging.warning(f"GENAI fallback: {e}")

    advice = {
        "باطنه":["اشرب سوائل كفاية وارتاح.","لو استمرت الأعراض أو ساءت راجع الطبيب."],
        "مخ واعصاب":["قلّل التعرض للضوء ونام كويس.","لو الصداع شديد جدًا أو مصحوب بقيء مستمر راجع الطوارئ."],
        "قلبيه وصدر":["توقف عن المجهود واطلب تقييماً طبياً عاجلاً.","راقب ضيق النفس أو ألم الصدر."],
        "عظام":["إراحة موضع الألم وكمادات باردة.","تجنب حمل أوزان لحين التقييم."],
        "جلديه":["تجنب الحكة واستخدم مرطب مناسب.","لو في انتشار سريع/حرارة راجع الطبيب."],
        "انف واذن وحنجره":["سوائل دافئة ومضمضة ملح.","لو صعوبة بلع/تنفس راجع الطوارئ."],
        "اسنان":["مضمضة بماء دافئ وملح.","تجنب المضغ على الجهة المؤلمة."],
        "عيون":["تجنب العدسات حتى زوال الأعراض.","لو ألم شديد/تشوش رؤية راجع الطوارئ."],
        "نساء وتوليد":["تجنب المجهود العنيف.","متابعة منتظمة مع الطبيب."],
        "مسالك بوليه":["اشرب مياه بكثرة.","لو ألم شديد/دم بالبول راجع الطبيب."]
    }
    return {
        "recommended_specialty": prelim,
        "doctor_explanation": f"الأعراض تميل إلى تخصص {prelim}.",
        "temporary_advice": advice.get(prelim, ["يرجى متابعة الحالة مع الطبيب المختص."])
    }

# =========[ ترتيب وترشيح مقدمي الخدمة ]=========
def score_provider(item: dict, gov_norm: str, spec_norm: str) -> int:
    sc = 0
    if gov_norm and (gov_norm in item.get("_gov","")): sc += 60
    if spec_norm and (spec_norm in item.get("_spec","")): sc += 35
    if spec_norm and spec_norm in normalize(item.get("name","")): sc += 5
    return sc

def pick_providers(data, governorate: str, specialty: str):
    gov_norm = normalize(governorate)
    spec_norm = normalize(specialty)
    scored = []
    for d in data:
        s = score_provider(d, gov_norm, spec_norm)
        if s > 0:
            scored.append((s, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for i,(sc,d) in enumerate(scored[:30]):
        out.append({
            "id": d["id"],
            "name": d["name"],
            "governorate": d["governorate"],
            "provider_type": d["provider_type"],
            "specialty_sub": d["specialty_sub"],
            "address": d["address"],
            "phones": d["phones"],
            "hotline": d["hotline"],
            "maps_url": f"https://www.google.com/maps/search/?api=1&query={d['name']} {d['address']}",
            "best": i == 0
        })
    return out

# =========[ المسارات ]=========
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/api/symptoms", methods=["POST"])
def api_symptoms():
    j = request.get_json(force=True)
    symptoms = j.get("symptoms","")
    location = j.get("location","")
    data = load_data()
    resolved_loc = resolve_location(location, data)
    types = tuple({d["provider_type"] for d in data})
    subs  = tuple({d["specialty_sub"] for d in data})
    ai = classify_specialty(symptoms, types, subs)
    providers = pick_providers(data, resolved_loc, ai.get("recommended_specialty",""))
    return jsonify({
        "resolved_location": resolved_loc,
        "recommended_specialty": ai.get("recommended_specialty",""),
        "doctor_explanation": ai.get("doctor_explanation",""),
        "temporary_advice": ai.get("temporary_advice",[]),
        "providers": providers
    })

@app.route("/api/reports", methods=["POST"])
def api_reports():
    j = request.get_json(force=True)
    files = j.get("files", [])
    location = j.get("location","")
    # تحليل بالتوليد عند توفر API
    interpretation = "تم استلام الملفات وسيتم تحليلها بصورة آلية."
    recommended_specialty = "باطنه"
    advice = ["احتفظ بكل التقارير في ملف واحد.", "راجع الطبيب المختص بعد التحليل."]
    if USE_GENAI and files:
        try:
            parts = ["حلّل التقارير/التحاليل/الأشعة (قد تكون صور/ PDF). أعد JSON فقط: {\"interpretation\":\"...\",\"temporary_advice\":[\"...\"],\"recommended_specialty\":\"...\"}"]
            for f in files:
                parts.append({"mime_type": f.get("mime_type","application/octet-stream"),
                              "data": f.get("data","")})
            model = genai.GenerativeModel("gemini-1.5-flash")
            r = model.generate_content(parts)
            txt = r.text.strip().replace("```json","").replace("```","")
            parsed = json.loads(txt)
            interpretation = parsed.get("interpretation", interpretation)
            advice = parsed.get("temporary_advice", advice)
            recommended_specialty = parsed.get("recommended_specialty", recommended_specialty)
        except Exception as e:
            logging.warning(f"GENAI reports fallback: {e}")
    data = load_data()
    resolved_loc = resolve_location(location, data)
    providers = pick_providers(data, resolved_loc, recommended_specialty)
    return jsonify({
        "interpretation": interpretation,
        "temporary_advice": advice,
        "recommended_specialty": recommended_specialty,
        "resolved_location": resolved_loc,
        "providers": providers
    })

if __name__ == "__main__":
    load_data(force=True)
    app.run(host="0.0.0.0", port=5000, debug=True)
