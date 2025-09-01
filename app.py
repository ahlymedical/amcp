import os
import json
import base64
import pandas as pd
import google.generativeai as genai
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import logging

# --- إعدادات أساسية ---
app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)
logging.basicConfig(level=logging.INFO)

# --- ذاكرة تخزين البيانات لسرعة القراءة (Cache) ---
NETWORK_DATA_CACHE = None

def get_network_data():
    """
    قراءة بيانات الشبكة من ملف الإكسل مرة واحدة مع تنظيف الأعمدة.
    الأعمدة المتوقعة بالترتيب:
    0: المنطقة/المحافظة  | 1: نوع مقدم الخدمة (التخصص الرئيسي)
    2: التخصص الفرعي    | 3: اسم مقدم الخدمة
    4: العنوان           | 5..8: Telephone1..Telephone4
    9: Hotline
    """
    global NETWORK_DATA_CACHE
    if NETWORK_DATA_CACHE is not None:
        return NETWORK_DATA_CACHE

    basedir = os.path.abspath(os.path.dirname(__file__))
    excel_file_path = os.path.join(basedir, 'network_data.xlsx')

    if not os.path.exists(excel_file_path):
        app.logger.error(f"ملف الإكسل '{excel_file_path}' غير موجود.")
        return []

    try:
        df = pd.read_excel(excel_file_path, sheet_name=0, header=0)
        df.dropna(how='all', inplace=True)
        df = df.astype(str).replace('nan', '')

        data_list = []
        for idx, row in df.iterrows():
            # تأكيد وجود الأعمدة المطلوبة
            if len(row) < 10:
                continue

            phones = []
            for i in range(5, 9):
                v = str(row.iloc[i]).replace('.0', '').strip()
                if v and v != '0':
                    phones.append(v)

            hotline = str(row.iloc[9]).replace('.0', '').strip()
            hotline = hotline if hotline and hotline != '0' else None

            item = {
                "governorate": row.iloc[0].strip(),         # المحافظة/المنطقة
                "provider_type": row.iloc[1].strip(),        # نوع مقدم الخدمة / التخصص الرئيسي
                "specialty_sub": row.iloc[2].strip(),        # التخصص الفرعي
                "name": row.iloc[3].strip(),                 # اسم مقدم الخدمة
                "address": row.iloc[4].strip(),              # العنوان
                "phones": phones,                            # Telephone1..4
                "hotline": hotline,                          # Hotline
                "id": f"row-{idx}"
            }
            data_list.append(item)

        NETWORK_DATA_CACHE = data_list
        app.logger.info(f"Loaded {len(NETWORK_DATA_CACHE)} providers into memory cache.")
        return NETWORK_DATA_CACHE

    except Exception as e:
        app.logger.error(f"Excel read error: {e}", exc_info=True)
        return []

# --- Serve index.html ---
@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')

# --- بحث بالأعراض + المكان (ذكاء اصطناعي) ---
@app.route('/api/symptoms-search', methods=['POST'])
def symptoms_search():
    try:
        data = request.get_json()
        symptoms = (data.get('symptoms') or '').strip()
        location = (data.get('location') or '').strip()

        if not symptoms or not location:
            return jsonify({"error": "Symptoms or location are missing"}), 400

        network_data = get_network_data()
        if not network_data:
            return jsonify({"error": "Network data not available"}), 500

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return jsonify({"error": "Server configuration error."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        # نرسل عينة كبيرة كفاية لفهم شكل البيانات مع التزام بالأداء
        sample_for_llm = network_data[:250]

        prompt = f"""
أنت "مرشد طبي خبير" للشبكة الطبية المصرية. افهم شكوى المريض بالعربي (حتى لو عامية)،
وافهم المكان سياقيًا (مثال: "الطالبية هرم" => الجيزة). مهمتك:

- حدد التخصص الطبي الأنسب بدقة (لا ترشح صيدلية لمريض يحتاج مستشفى).
- حوّل المكان لصيغة قياسية للبحث داخل البيانات.
- استخرج من قاعدة البيانات مقدمي خدمة مناسبين بالقرب من هذا المكان.

قائمة الحقول:
governorate (المحافظة/المنطقة)، provider_type (نوع مقدم الخدمة/تخصص رئيسي)،
specialty_sub (التخصص الفرعي)، name (الاسم)، address (العنوان)،
phones (Tele1..4)، hotline.

هذه عينة من البيانات لتفهم الهيكل:
{json.dumps(sample_for_llm, ensure_ascii=False)}

المدخلات:
- الأعراض: "{symptoms}"
- المكان: "{location}"

أعد JSON فقط بهذا الشكل:
{{
  "recommended_specialty": "اسم التخصص المناسب",
  "doctor_explanation": "شرح احترافي مبسط يطمئن المريض ويشرح الأسباب المحتملة",
  "temporary_advice": ["نصيحة 1", "نصيحة 2", "نصيحة 3"],
  "best_match_indices": [0,1,2],
  "other_indices": [3,4,5]
}}

القواعد:
- لا تخرج عن JSON.
- لا تقترح صيدلية إلا لو الحالة واضحة أدوية بسيطة بدون حاجة لطبيب.
- لو الأعراض طارئة (ألم صدر شديد، نزيف، فقدان وعي...) رشح "طوارئ مستشفى" فورًا.
"""
        # نخلي الموديل هو اللي يحدد الفهارس بالاعتماد على ترتيب data_list الذي سترسله الواجهة
        response = model.generate_content(prompt)
        cleaned = response.text.strip().replace("```json", "").replace("```", "")
        llm = json.loads(cleaned)

        # نتحقق ونعيد حتى لو قوائم المؤشرات فاضية
        return jsonify({
            "recommended_specialty": llm.get("recommended_specialty") or "",
            "doctor_explanation": llm.get("doctor_explanation") or "",
            "temporary_advice": llm.get("temporary_advice") or [],
            "best_match_indices": llm.get("best_match_indices") or [],
            "other_indices": llm.get("other_indices") or []
        })

    except Exception as e:
        app.logger.error(f"ERROR in /api/symptoms-search: {e}", exc_info=True)
        return jsonify({"error": "حدث خطأ داخلي أثناء البحث الذكي."}), 500

# --- تحليل التقارير/التحاليل (صور أو PDF) ---
@app.route('/api/analyze', methods=['POST'])
def analyze_report():
    try:
        data = request.get_json()
        files_payload = data.get('files')
        if not files_payload:
            return jsonify({"error": "No files provided"}), 400

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return jsonify({"error": "Server configuration error."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        # prompt متعدد الوسائط
        prompt_parts = ["""
أنت طبيب استشاري خبير. حلّل الملفات (تقارير/تحاليل/أشعة) وقدّم JSON فقط:
{
  "interpretation": "شرح طبي مبسط ودقيق لما يظهر في التقارير",
  "temporary_advice": ["نصيحة 1","نصيحة 2","نصيحة 3"],
  "recommended_specialty": "التخصص الأنسب للمتابعة"
}
اذكر أن هذا تحليل إرشادي ولا يغني عن زيارة الطبيب.
"""]
        for f in files_payload:
            prompt_parts.append({"mime_type": f["mime_type"], "data": f["data"]})

        response = model.generate_content(prompt_parts)
        cleaned = response.text.strip().replace("```json", "").replace("```", "")
        return jsonify(json.loads(cleaned))

    except Exception as e:
        app.logger.error(f"ERROR in /api/analyze: {e}", exc_info=True)
        return jsonify({"error": "حدث خطأ داخلي أثناء تحليل التقارير."}), 500

if __name__ == "__main__":
    get_network_data()  # warm cache
    app.run(debug=True, port=5000)
