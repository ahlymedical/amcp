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

# --- ذاكرة التخزين المؤقت للبيانات لضمان السرعة (Cache) ---
NETWORK_DATA_CACHE = None

def get_network_data():
    """
    تقوم بقراءة بيانات الشبكة من ملف الإكسل مرة واحدة فقط وتحفظها في الذاكرة لضمان السرعة.
    تم تعديل هذه الدالة لتكون أكثر قوة في قراءة البيانات.
    """
    global NETWORK_DATA_CACHE
    if NETWORK_DATA_CACHE is not None:
        return NETWORK_DATA_CACHE

    basedir = os.path.abspath(os.path.dirname(__file__))
    excel_file_path = os.path.join(basedir, 'network_data.xlsx')
    
    if not os.path.exists(excel_file_path):
        app.logger.error(f"خطأ فادح: ملف الإكسل '{excel_file_path}' غير موجود.")
        return []

    try:
        # ===== تعديل رئيسي: قراءة أول ورقة عمل (sheet) بالرقم (0) بدلاً من الاسم =====
        # هذا يضمن عمل الكود حتى لو كان اسم الورقة "Sheet1" أو أي اسم آخر
        df = pd.read_excel(excel_file_path, sheet_name=0, header=0)
        
        df.dropna(how='all', inplace=True)
        # التأكد من أن الأعمدة الأساسية ليست فارغة
        df.dropna(subset=[df.columns[0], df.columns[3]], inplace=True) 
        df = df.astype(str).replace('nan', '')

        data_list = []
        for index, row in df.iterrows():
            # ===== تعديل رئيسي: قراءة الأعمدة بترتيبها الرقمي (iloc) لتجنب أخطاء الأسماء =====
            # الترتيب المتوقع:
            # 0: المنطقة, 1: التخصص الرئيسي, 2: التخصص الفرعي, 3: اسم مقدم الخدمة, 4: العنوان
            # 5-8: الهواتف, 9: الخط الساخن
            
            # التحقق من أن الصف يحتوي على عدد كافٍ من الأعمدة
            if len(row) < 10:
                continue # تجاهل الصفوف غير المكتملة

            phones = [
                str(row.iloc[i]).replace('.0', '').strip() for i in range(5, 9)
                if str(row.iloc[i]).replace('.0', '').strip() not in ['0', '']
            ]
            hotline = str(row.iloc[9]).replace('.0', '').strip() if str(row.iloc[9]).replace('.0', '').strip() not in ['0', ''] else None
            
            item = {
                'governorate': row.iloc[0],
                'provider_type': row.iloc[1],
                'specialty_sub': row.iloc[2],
                'name': row.iloc[3],
                'address': row.iloc[4],
                'phones': phones,
                'hotline': hotline,
                'id': f"row-{index}"
            }
            data_list.append(item)
        
        NETWORK_DATA_CACHE = data_list
        if not data_list:
             app.logger.warning("تحذير: تم تحميل البيانات ولكن القائمة فارغة. تحقق من محتوى ملف الإكسل.")
        else:
            app.logger.info(f"نجحت العملية! تم تحميل {len(NETWORK_DATA_CACHE)} سجل من ملف الإكسل إلى الذاكرة.")
        return NETWORK_DATA_CACHE
    except Exception as e:
        app.logger.error(f"حدث خطأ فادح أثناء قراءة ملف الإكسل: {e}", exc_info=True)
        return []

# --- باقي الكود يبقى كما هو لأنه يعتمد على هذه الدالة ---

@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')

@app.route("/api/symptoms-search", methods=["POST"])
def symptoms_search():
    try:
        data = request.get_json()
        symptoms = data.get('symptoms')
        location = data.get('location')
        if not (symptoms and location):
            return jsonify({"error": "Symptoms or location are missing"}), 400

        network_data = get_network_data()
        if not network_data:
            return jsonify({"error": "Network data not available"}), 500
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key: return jsonify({"error": "Server configuration error."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        prompt = f"""
        أنت نظام ترشيح طبي ذكي وخبير. مهمتك هي مساعدة مريض بناءً على أعراضه وموقعه الجغرافي.
        قاعدة البيانات المتاحة (عينة للتوضيح): {json.dumps(network_data[:100], ensure_ascii=False, indent=2)}
        أعراض المريض: "{symptoms}"
        موقع المريض: "{location}"
        
        المطلوب منك بدقة:
        1.  **استنتاج التخصص**: بناءً على الأعراض، استنتج "التخصص الرئيسي" (provider_type) الأنسب.
        2.  **فهم الموقع والفلترة**: افهم موقع المريض بمرونة (مثال: "الطالبية هرم" تعني محافظة "الجيزة"). ابحث في **كامل** قاعدة البيانات عن **كل** مقدمي الخدمة الذين يتطابقون مع التخصص الذي استنتجته والمحافظة ("المنطقة" أو governorate) التي فهمتها.
        3.  **اختيار الترشيح الأنسب**: من النتائج، اختر **مقدم خدمة واحد فقط** ليكون "الترشيح الأنسب" (best_match) بناءً على اكتمال بياناته.
        4.  **كتابة نصيحة طبية احترافية**: اكتب نصيحة طبية أولية ومؤقتة (initial_advice) كأنك طبيب محترف، بناءً على الأعراض، مع التأكيد على ضرورة زيارة الطبيب.
        5.  **الإخراج النهائي**: أعد النتائج على هيئة ملف JSON **فقط**، يحتوي على الحقول التالية: `initial_advice`, `best_match` (قائمة بعنصر واحد), `other_results` (قائمة بباقي النتائج).
        إذا لم تجد نتائج، أعد القوائم فارغة.
        """
        response = model.generate_content(prompt)
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        return jsonify(json.loads(cleaned_text))

    except Exception as e:
        app.logger.error(f"ERROR in /api/symptoms-search: {e}", exc_info=True)
        return jsonify({"error": "حدث خطأ داخلي أثناء البحث الذكي."}), 500

@app.route("/api/analyze", methods=["POST"])
def analyze_report():
    try:
        data = request.get_json()
        files_payload = data.get('files')
        if not files_payload: return jsonify({"error": "No files provided"}), 400

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key: return jsonify({"error": "Server configuration error."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        prompt_parts = [
            f"""
            أنت طبيب استشاري خبير. حلل الملفات المرفقة بدقة وقدم إجابة احترافية على هيئة ملف JSON فقط.
            التحليل المطلوب يجب أن يحتوي على:
            1.  `interpretation`: شرح مبسط ومفصل للتقرير بأسلوب طبي.
            2.  `temporary_advice`: قائمة بالنصائح الأولية الهامة.
            3.  `recommended_specialty`: اسم التخصص الطبي الدقيق الموصى به.
            تنبيه: أكد أن هذا التحليل إرشادي ولا يغني عن استشارة الطبيب.
            """
        ]
        for file_data in files_payload:
            prompt_parts.append({"mime_type": file_data['mime_type'], "data": file_data['data']})
            
        response = model.generate_content(prompt_parts)
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        return jsonify(json.loads(cleaned_text))

    except Exception as e:
        app.logger.error(f"ERROR in /api/analyze: {e}", exc_info=True)
        return jsonify({"error": "حدث خطأ داخلي أثناء تحليل التقارير."}), 500

if __name__ == '__main__':
    get_network_data()
    app.run(debug=True, port=5000)
