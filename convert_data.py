import json
from bs4 import BeautifulSoup
import pandas as pd
import os

# المسار إلى الملف الذي يحتوي على بيانات الشبكة
# تأكد من وجود مجلد 'network_data_files' وبداخله ملف 'sheet001.htm'
HTML_FILE_PATH = os.path.join('network_data_files', 'sheet001.htm')
OUTPUT_JSON_PATH = os.path.join('static', 'network_data.json')

def convert_html_to_json():
    """
    يقرأ جدول بيانات الشبكة الطبية من ملف HTML المصدر من Excel،
    ويقوم بتنظيفه وتحويله إلى ملف JSON منظم ليستخدمه التطبيق.
    """
    print(f"بدء عملية تحويل الملف: {HTML_FILE_PATH}")

    if not os.path.exists(HTML_FILE_PATH):
        print(f"خطأ: لم يتم العثور على الملف '{HTML_FILE_PATH}'.")
        print("يرجى التأكد من أنك قمت بفك ضغط ملف 'network_data.htm' ووضعت مجلد 'network_data_files' في نفس مسار هذا السكربت.")
        return

    # قراءة محتوى الملف مع تحديد الترميز الصحيح
    with open(HTML_FILE_PATH, 'r', encoding='windows-1256') as f:
        html_content = f.read()

    # استخدام pandas لقراءة جدول HTML مباشرة وهو الأسهل والأكثر دقة
    try:
        df_list = pd.read_html(html_content, header=0)
        df = df_list[0]
    except Exception as e:
        print(f"حدث خطأ أثناء قراءة ملف HTML باستخدام pandas: {e}")
        return

    # إعادة تسمية الأعمدة لتكون باللغة الإنجليزية ليسهل التعامل معها
    df.columns = [
        'id', 'governorate', 'area', 'type', 'specialty_main', 
        'specialty_sub', 'name', 'address', 'phones_str', 'hotline_str'
    ]
    
    # إزالة الصفوف التي لا تحتوي على ID (غالبًا تكون صفوف فارغة أو ترويسات مكررة)
    df.dropna(subset=['id'], inplace=True)

    # تحويل البيانات إلى القائمة المطلوبة من القواميس (dictionaries)
    data_list = []
    for _, row in df.iterrows():
        # تقسيم أرقام الهواتف وتنقيتها
        phones = []
        if pd.notna(row['phones_str']):
            try:
                # التحويل إلى سلسلة نصية لضمان عمل الدالة split
                phones = [p.strip() for p in str(row['phones_str']).split('/') if p.strip()]
            except:
                phones = [] # في حالة وجود خطأ، اجعلها قائمة فارغة
        
        # تنقية رقم الخط الساخن
        hotline = None
        if pd.notna(row['hotline_str']):
             # تحويل الرقم إلى سلسلة نصية وتنقيتها
            hotline_val = str(row['hotline_str']).replace('.0', '').strip()
            if hotline_val.isdigit():
                 hotline = hotline_val
            
        item = {
            'id': row['id'],
            'governorate': row['governorate'],
            'area': row['area'],
            'type': row['type'],
            'specialty_main': row['specialty_main'],
            'specialty_sub': row['specialty_sub'],
            'name': row['name'],
            'address': row['address'],
            'phones': phones,
            'hotline': hotline
        }
        data_list.append(item)

    # إنشاء مجلد 'static' إذا لم يكن موجودًا
    if not os.path.exists('static'):
        os.makedirs('static')

    # حفظ النتائج في ملف JSON
    with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(data_list, f, ensure_ascii=False, indent=4)

    print(f"نجحت العملية! تم حفظ {len(data_list)} سجل في الملف: {OUTPUT_JSON_PATH}")

if __name__ == '__main__':
    convert_html_to_json()
