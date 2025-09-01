import os  # <<< التعديل الأول: إضافة هذه المكتبة
import google.generativeai as genai
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import json
import base64

# تهيئة التطبيق
app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)

# --- بداية التعديل الثاني ---
# بناء المسار الكامل للملف لضمان العثور عليه دائمًا
basedir = os.path.abspath(os.path.dirname(__file__))
NETWORK_DATA_PATH = os.path.join(basedir, 'static', 'network_data.json')
# --- نهاية التعديل الثاني ---

# تحميل بيانات الشبكة والتخصصات المتاحة عند بدء التشغيل
def load_network_data():
    """تحميل بيانات الشبكة من ملف JSON وتحديد قائمة التخصصات الفريدة."""
    try:
        # استخدام المسار الكامل الذي قمنا ببنائه
        with open(NETWORK_DATA_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # استخراج قائمة فريدة من التخصصات الرئيسية والأنواع
            specialties = set(item['specialty_main'] for item in data)
            types = set(item['type'] for item in data)
            # دمج القائمتين وإزالة التكرار
            available_items = sorted(list(specialties.union(types)))
            # تحويلها إلى سلسلة نصية لاستخدامها في الـ prompt
            return ", ".join([f'"{item}"' for item in available_items])
    except FileNotFoundError:
        print(f"خطأ فادح: ملف network_data.json غير موجود في المسار: {NETWORK_DATA_PATH}")
        return '"باطنة", "عظام", "اسنان", "صيدلية", "مستشفى", "معمل", "أشعة"'

AVAILABLE_SPECIALTIES = load_network_data()

@app.route('/')
def serve_index():
    """عرض ملف الواجهة الأمامية الرئيسي."""
    return send_from_directory('static', 'index.html')

@app.route('/api/network')
def get_network_data():
    """إرسال بيانات الشبكة الطبية كاملة للواجهة الأمامية."""
    return send_from_directory('static', 'network_data.json')

@app.route("/api/recommend", methods=["POST"])
def recommend_specialty():
    """
    يحلل أعراض المريض ويرشح التخصص الطبي الأنسب.
    """
    try:
        data = request.get_json()
        symptoms = data.get('symptoms')
        if not symptoms:
            return jsonify({"error": "Missing symptoms"}), 400
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("خطأ فادح: متغير البيئة GEMINI_API_KEY غير معين.")
            return jsonify({"error": "خطأ في إعدادات الخادم."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        prompt = f"""
        أنت مساعد طبي خبير ومحترف في شركة خدمات طبية كبرى. مهمتك هي تحليل شكوى المريض بدقة واقتراح أفضل تخصص طبي من القائمة المتاحة.
        قائمة التخصصات المتاحة هي: [{AVAILABLE_SPECIALTIES}]
        شكوى المريض: "{symptoms}"
        
        المطلوب:
        1.  حلل الشكوى بعناية.
        2.  اختر التخصص **الأنسب فقط** من القائمة أعلاه.
        3.  اكتب شرحاً احترافياً ومبسطاً للمريض يوضح سبب اختيار هذا التخصص تحديداً.
        4.  قدم قائمة من ثلاث نصائح أولية وعامة يمكن للمريض اتباعها حتى زيارة الطبيب.
        
        ردك **يجب** أن يكون بصيغة JSON فقط، بدون أي نصوص أو علامات قبله أو بعده، ويحتوي على:
        - `recommendations`: قائمة تحتوي على عنصر واحد فقط به "id" (اسم التخصص) و "reason" (سبب الترشيح).
        - `temporary_advice`: قائمة (array) من ثلاثة (3) أسطر نصائح.
        """
        
        response = model.generate_content(prompt)
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        json_response = json.loads(cleaned_text)
        return jsonify(json_response)
        
    except Exception as e:
        print(f"ERROR in /api/recommend: {str(e)}")
        return jsonify({"error": "حدث خطأ داخلي في الخادم."}), 500

@app.route("/api/analyze", methods=["POST"])
def analyze_report():
    """
    يحلل التقارير الطبية المرفوعة (صور، PDF) ويقدم تفسيراً أولياً.
    """
    try:
        data = request.get_json()
        files_data = data.get('files')

        if not files_data:
            return jsonify({"error": "Missing files"}), 400
        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("خطأ فادح: متغير البيئة GEMINI_API_KEY غير معين.")
            return jsonify({"error": "خطأ في إعدادات الخادم."}), 500

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        file_parts = []
        for file in files_data:
            file_parts.append({
                "mime_type": file["mime_type"],
                "data": base64.b64decode(file["data"])
            })

        prompt = f"""
        أنت محلل تقارير طبية ذكي في شركة خدمات طبية. مهمتك هي تحليل الملفات الطبية (صور، PDF) وتقديم إرشادات أولية احترافية.
        قائمة التخصصات المتاحة هي: [{AVAILABLE_SPECIALTIES}]

        المطلوب منك تحليل الملفات وتقديم رد بصيغة JSON فقط، بدون أي علامات، يحتوي على الحقول التالية:
        1.  `interpretation`: (String) شرح احترافي ومبسط لما يظهر في التقرير. ركز على المؤشرات غير الطبيعية. **لا تقدم تشخيصاً نهائياً أبداً وأكد أن هذه ملاحظات أولية.**
        2.  `temporary_advice`: (Array of strings) قائمة من 3 نصائح عامة ومؤقتة.
        3.  `recommendations`: (Array of objects) قائمة تحتوي على **تخصص واحد فقط** هو الأنسب للحالة، وتحتوي على `id` و `reason`.

        **هام:** إذا كانت الملفات غير واضحة، أعد رداً مناسباً في حقل `interpretation` واترك الحقول الأخرى فارغة.
        """
        
        content = [prompt] + file_parts
        response = model.generate_content(content)
        
        cleaned_text = response.text.strip().replace("```json", "").replace("```", "")
        json_response = json.loads(cleaned_text)
        return jsonify(json_response)

    except json.JSONDecodeError:
        print(f"ERROR in /api/analyze: JSONDecodeError. Response text: {response.text}")
        return jsonify({"error": "فشل المساعد الذكي في تكوين رد صالح."}), 500
    except Exception as e:
        print(f"ERROR in /api/analyze: {str(e)}")
        return jsonify({"error": f"حدث خطأ غير متوقع."}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
