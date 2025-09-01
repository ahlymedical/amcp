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
    """
    global NETWORK_DATA_CACHE
    if NETWORK_DATA_CACHE is not None:
        app.logger.info("Loading network data from cache.")
        return NETWORK_DATA_CACHE

    basedir = os.path.abspath(os.path.dirname(__file__))
    excel_file_path = os.path.join(basedir, 'network_data.xlsx')
    
    if not os.path.exists(excel_file_path):
        app.logger.error(f"FATAL ERROR: Excel file not found at '{excel_file_path}'")
        return []

    try:
        app.logger.info(f"Loading network data from Excel file: {excel_file_path}")
        df = pd.read_excel(excel_file_path, sheet_name=0, header=0)
        df.dropna(how='all', inplace=True)
        df.dropna(subset=[df.columns[0], df.columns[3]], inplace=True) 
        df = df.astype(str).replace('nan', '')

        data_list = []
        for index, row in df.iterrows():
            if len(row) < 10: continue
            phones = [str(row.iloc[i]).replace('.0', '').strip() for i in range(5, 9) if str(row.iloc[i]).replace('.0', '').strip() not in ['0', '']]
            hotline = str(row.iloc[9]).replace('.0', '').strip() if str(row.iloc[9]).replace('.0', '').strip() not in ['0', ''] else None
            item = {
                'governorate': row.iloc[0], 'provider_type': row.iloc[1], 'specialty_sub': row.iloc[2],
                'name': row.iloc[3], 'address': row.iloc[4], 'phones': phones, 'hotline': hotline,
                'id': f"row-{index}"
            }
            data_list.append(item)
        
        NETWORK_DATA_CACHE = data_list
        app.logger.info(f"SUCCESS: Loaded {len(NETWORK_DATA_CACHE)} records into cache.")
        return NETWORK_DATA_CACHE
    except Exception as e:
        app.logger.error(f"FATAL ERROR during Excel read: {e}", exc_info=True)
        return []

# --- Endpoints الخاصة بالتطبيق ---
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
            return jsonify({"error": "الرجاء إدخال الأعراض والموقع."}), 400

        network_data = get_network_data()
        if not network_data:
            return jsonify({"error": "عفواً، قاعدة البيانات غير متاحة حالياً."}), 500
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key: 
            app.logger.error("FATAL: GEMINI_API_KEY is not set.")
            return jsonify({"error": "خطأ في إعدادات الخادم."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        # ===== تعديل جذري ومهم جداً: الـ Prompt الجديد ليصبح الذكاء الاصطناعي خبيراً =====
        prompt = f"""
        أنت "مرشد طبي خبير" ومساعد ذكي متخصص في الشبكة الطبية لشركة الأهلي للخدمات الطبية في مصر. مهمتك هي فهم شكوى المريض بعمق وتقديم خدمة احترافية عالمية.

        **قواعد صارمة يجب اتباعها:**
        1.  **فهم القصد، وليس الكلمات فقط:** إذا قال المريض "تعبان"، "مرهق"، "عندي صداع"، "بطني واجعاني"، فقصده هو رؤية **طبيب أو الذهاب لمستشفى**. في هذه الحالات، يجب **دائماً** ترشيح (`مستشفى`, `مركز طبي`, `باطنة`). **ممنوع منعاً باتاً** ترشيح (`صيدلية`, `معمل`, `أشعة`) كحل أولي لهذه الشكاوى العامة.
        2.  **احترم الطلب المباشر:** إذا طلب المريض "مستشفى" أو "صيدلية" بالاسم، يجب أن تقتصر نتائجك على هذا النوع فقط.
        3.  **فهم اللهجة المصرية:** يجب أن تفهم أن "فيصل" أو "الطالبية" أو "ميدان لبنان" هي مناطق داخل محافظات أكبر مثل "الجيزة" أو "القاهرة". قم بالربط الصحيح.

        **سياق الحوار:**
        - **شكوى المريض:** "{symptoms}"
        - **موقع المريض:** "{location}"
        - **قاعدة البيانات المتاحة (عينة):** {json.dumps(network_data[:200], ensure_ascii=False, indent=2)}

        **مهمتك المطلوبة (نفذها بالترتيب وبأقصى درجات الاحترافية):**

        **1. الشرح الطبي الاحترافي (Initial Advice):**
           - **تصرف كطبيب متخصص:** ابدأ بكتابة شرح طبي مبدئي ومفصل للمريض.
           - **اشرح الأسباب المحتملة:** وضح بأسلوب بسيط ومطمئن ما قد تعنيه هذه الأعراض (مثال: "الأعراض التي وصفتها قد تشير إلى إرهاق عضلي أو ربما بداية التهاب في الأعصاب...").
           - **قدم نصائح علاجية مؤقتة:** أعطِ المريض 2-3 نصائح عملية ومؤقتة يمكنه القيام بها فوراً (مثال: "ننصح بالراحة التامة، استخدام كمادات دافئة، وشرب الكثير من السوائل...").
           - **أكد على أهمية الطبيب:** اختتم الفقرة بالتأكيد على أن هذا الشرح لا يغني أبداً عن زيارة الطبيب المختص للتشخيص الدقيق.

        **2. البحث الذكي والموسع:**
           - **استنتج التخصص** بناءً على القواعد الصارمة أعلاه.
           - **افهم الموقع** وقم بالربط الصحيح بالمحافظة.
           - **ابحث بشكل موسع:** هدفك هو إيجاد **أكبر عدد ممكن** من مقدمي الخدمة المطابقين.

        **3. ترتيب النتائج والإخراج النهائي:**
           - **اختر "الترشيح الأنسب" (Best Match):** من كل النتائج، اختر **مقدم خدمة واحد فقط** تراه الأفضل (بياناته مكتملة، مستشفى أو مركز طبي كبير).
           - **باقي الترشيحات (Other Results):** ضع كل النتائج الأخرى التي وجدتها في هذه القائمة.
           - **الإخراج كملف JSON:** أعد كل ما سبق في ملف JSON منظم يحتوي على الحقول التالية فقط: `initial_advice`، `best_match` (قائمة بعنصر واحد)، `other_results` (قائمة بكل النتائج الأخرى).
        """
        response = model.generate_content(prompt)
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        return jsonify(json.loads(cleaned_text))

    except Exception as e:
        app.logger.error(f"ERROR in /api/symptoms-search: {e}", exc_info=True)
        return jsonify({"error": "عفواً، حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى."}), 500

@app.route("/api/analyze", methods=["POST"])
def analyze_report():
    # هذا الكود يعمل بشكل صحيح ولا يحتاج تعديل
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
