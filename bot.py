#!/usr/bin/env python3
"""
================================================================================
   📢 نظام المراقبة المتكامل - بدون بروكسي + تقنيات ضد الحظر 📢
   ✅ مراقبة التغييرات في المشاريع
   ✅ إضافة/حذف/تعديل المشاريع من لوحة التحكم
   ✅ تنبيهات فورية عبر تليجرام (قناة)
   ✅ تنبيهات بتنسيق احترافي
   ✅ تقنيات ضد الحظر: تأخير عشوائي، تناوب User-Agent، Retry with Backoff، Cache
================================================================================
"""

import json
import os
import time
import random
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from functools import wraps

from flask import Flask, render_template, request, jsonify
from curl_cffi import requests as cf_requests

# =========================================================
# 🔧 إعدادات التليجرام - غيّر هذه القيم 🔧
# =========================================================
TELEGRAM_BOT_TOKEN = "8985562280:AAGP5tm2UiHPjm3NvR1dEE0pAPRPywEpsF0"  # ضع توكن البوت هنا
TELEGRAM_CHANNEL_ID = "6084420852"

TELEGRAM_ENABLED = True                      # تفعيل/تعطيل التنبيهات

# =========================================================
# إعدادات Flask
# =========================================================
app = Flask(__name__)
PROJECTS_FILE = "projects.json"
CHECK_INTERVAL = 10  # ثانية بين الفحوصات

# =========================================================
# 🔧 إعدادات ضد الحظر (بدون بروكسي)
# =========================================================

# قائمة بـ User-Agents حقيقية متنوعة
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/120.0",
]

# إعدادات التأخير العشوائي
MIN_DELAY = 0.5   # ثانية
MAX_DELAY = 2.0   # ثانية

# إعدادات Cache
CACHE_TTL_SECONDS = 25  # صلاحية cache بالثواني

def random_delay():
    """تأخير عشوائي بين الطلبات لتجنب الحظر"""
    delay = random.uniform(MIN_DELAY, MAX_DELAY)
    time.sleep(delay)

def get_random_user_agent():
    """اختيار User-Agent عشوائي من القائمة"""
    return random.choice(USER_AGENTS)

def get_headers():
    """الحصول على هيدرز عشوائية متغيرة"""
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ar,en;q=0.9",
        "app-locale": "ar",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "user-agent": get_random_user_agent(),
        "referer": "https://sakani.sa/app/land-projects",
        "origin": "https://sakani.sa",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }

# =========================================================
# 💾 نظام Cache المتقدم
# =========================================================
class DataCache:
    """نظام Cache آمن للخيوط مع صلاحية زمنية"""
    
    def __init__(self, ttl_seconds=30):
        self.cache = {}
        self.ttl = timedelta(seconds=ttl_seconds)
        self.lock = threading.Lock()
    
    def get(self, key):
        """استرجاع بيانات من cache إذا كانت لا تزال صالحة"""
        with self.lock:
            if key in self.cache:
                data, timestamp = self.cache[key]
                if datetime.now() - timestamp < self.ttl:
                    return data
                else:
                    # انتهت الصلاحية، حذف
                    del self.cache[key]
        return None
    
    def set(self, key, data):
        """تخزين بيانات في cache"""
        with self.lock:
            self.cache[key] = (data, datetime.now())
    
    def clear(self):
        """مسح cache بالكامل"""
        with self.lock:
            self.cache.clear()
    
    def clear_expired(self):
        """حذف البيانات منتهية الصلاحية"""
        with self.lock:
            now = datetime.now()
            expired = [
                k for k, (_, ts) in self.cache.items()
                if now - ts >= self.ttl
            ]
            for k in expired:
                del self.cache[k]
    
    def get_size(self):
        """الحصول على حجم cache"""
        with self.lock:
            return len(self.cache)

# إنشاء cache عام للمشاريع
project_cache = DataCache(ttl_seconds=CACHE_TTL_SECONDS)

# =========================================================
# 🔌 Session لإعادة استخدام الاتصالات
# =========================================================
_session = None
_session_lock = threading.Lock()

def get_session():
    """الحصول على Session معاد استخدامه (آمن للخيوط)"""
    global _session
    with _session_lock:
        if _session is None:
            _session = cf_requests.Session()
            _session.impersonate = "chrome120"
            _session.timeout = 25
        return _session

def reset_session():
    """إعادة تعيين الجلسة (في حالة حدوث مشاكل)"""
    global _session
    with _session_lock:
        if _session:
            try:
                _session.close()
            except:
                pass
        _session = None

# =========================================================
# ✅ دوال التليجرام (للقناة)
# =========================================================
def send_telegram_message(message: str):
    """إرسال رسالة إلى التليجرام"""
    if not TELEGRAM_ENABLED:
        return

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("⚠️ لم يتم إعداد التليجرام")
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": message,
            "parse_mode": "HTML"
        }

        response = cf_requests.post(url, json=data, timeout=10)

        if response.status_code == 200:
            print("✅ تم إرسال رسالة التليجرام")
        else:
            print(f"❌ فشل إرسال التليجرام: {response.status_code}")
            print(response.text)

    except Exception as e:
        print(f"❌ خطأ في إرسال التليجرام: {e}")
def send_test_message():
    """إرسال رسالة إلى القناة"""
    if not TELEGRAM_ENABLED:
        return
    
    # التحقق من صحة الإعدادات
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE" or not TELEGRAM_CHANNEL_ID:
        print("⚠️ لم يتم إعداد التليجرام: يرجى إدخال التوكن ومعرف القناة")
        return
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        
        data = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        
        # محاولة الإرسال مع إعادة المحاولة
        for attempt in range(3):
            try:
                response = cf_requests.post(url, json=data, timeout=10)
                if response.status_code == 200:
                    print(f"📨 تم إرسال التنبيه إلى القناة {TELEGRAM_CHANNEL_ID}")
                    return
                else:
                    print(f"⚠️ فشل إرسال التنبيه (حاول {attempt+1}/3): {response.status_code}")
                    if response.status_code == 403:
                        print("❌ البوت ليس لديه صلاحية الإرسال في القناة. تأكد من إضافة البوت كمدير في القناة")
                        return
            except Exception as e:
                print(f"⚠️ خطأ في الإرسال (حاول {attempt+1}/3): {e}")
            time.sleep(1)  # انتظار ثانية بين المحاولات
            
    except Exception as e:
        print(f"❌ خطأ عام في إرسال التليجرام: {e}")

def send_test_message():
    """إرسال رسالة اختبار للتحقق من إعدادات التليجرام للقناة"""
    message = """
🔰 <b>نظام المراقبة يعمل!</b> 🔰

✅ تم تشغيل نظام المراقبة بنجاح
📊 سيتم إشعارك عند أي تغيير في المشاريع
📢 تم إعداد التنبيهات للإرسال إلى القناة
🛡️ تقنيات ضد الحظر: تأخير عشوائي | تناوب User-Agent | Cache | Retry with Backoff
⏰ الوقت: {time}

<i>جميع التنبيهات اللاحقة ستصل بهذا التنسيق</i>
""".format(time=datetime.now().strftime('%Y-%m-%d %I:%M:%S %p'))
    
    send_telegram_message(message)

# =========================================================
# دوال المشاريع (حفظ وتحميل)
# =========================================================
def load_projects():
    """تحميل المشاريع من ملف JSON"""
    if os.path.exists(PROJECTS_FILE):
        try:
            with open(PROJECTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_projects(projects):
    """حفظ المشاريع في ملف JSON"""
    with open(PROJECTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(projects, f, indent=2, ensure_ascii=False)

# =========================================================
# قاعدة البيانات
# =========================================================
def init_db():
    """تهيئة قاعدة البيانات"""
    conn = sqlite3.connect('monitoring.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS project_history (
            project_id INTEGER,
            timestamp TEXT,
            available_units INTEGER,
            booked_units INTEGER,
            inactive_units INTEGER,
            PRIMARY KEY (project_id, timestamp)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            project_id INTEGER,
            project_name TEXT,
            change_type TEXT,
            old_value INTEGER,
            new_value INTEGER,
            alert_text TEXT,
            sent_to_telegram INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def save_history(project_id, data):
    """حفظ تاريخ المشروع"""
    conn = sqlite3.connect('monitoring.db')
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO project_history 
        (project_id, timestamp, available_units, booked_units, inactive_units)
        VALUES (?, ?, ?, ?, ?)
    ''', (project_id, data['timestamp'], data['available_units'], 
          data['booked_units'], data['inactive_units']))
    conn.commit()
    conn.close()

def save_alert(project_id, project_name, change_type, old_value, new_value, alert_text, sent_to_telegram=0):
    """حفظ التنبيه في قاعدة البيانات"""
    conn = sqlite3.connect('monitoring.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO alerts (timestamp, project_id, project_name, change_type, old_value, new_value, alert_text, sent_to_telegram)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), project_id, 
          project_name, change_type, old_value, new_value, alert_text, sent_to_telegram))
    conn.commit()
    conn.close()

def get_last_history(project_id):
    """جلب آخر تاريخ للمشروع"""
    conn = sqlite3.connect('monitoring.db')
    c = conn.cursor()
    c.execute('''
        SELECT available_units, booked_units, inactive_units, timestamp
        FROM project_history 
        WHERE project_id = ? 
        ORDER BY timestamp DESC LIMIT 1
    ''', (project_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            'available_units': row[0],
            'booked_units': row[1],
            'inactive_units': row[2],
            'timestamp': row[3]
        }
    return None

# =========================================================
# جلب بيانات المشروع من API - مع تقنيات ضد الحظر
# =========================================================
def fetch_project_data_from_api(project_id: int, reference_unit: int) -> Optional[Dict]:
    """جلب بيانات المشروع من API مع Retry و Backoff"""
    url = f"https://sakani.sa/mainIntermediaryApi/v4/units/{reference_unit}?include=project,project.project_unit_types"
    
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            # تأخير عشوائي قبل الطلب
            random_delay()
            
            # الحصول على Session معاد استخدامه
            session = get_session()
            
            # هيدرز متغيرة (مع User-Agent مختلف)
            headers = get_headers()
            
            print(f"🔄 جلب المشروع {project_id} (محاولة {attempt+1}/{max_retries}) - User-Agent: {headers['user-agent'][:50]}...")
            
            response = session.get(
                url,
                headers=headers,
                timeout=25
            )
            
            if response.status_code == 200:
                return response.json()
                
            elif response.status_code == 429:  # Too Many Requests
                wait_time = (attempt + 1) * 5  # 5, 10, 15 ثانية
                print(f"⚠️ تم تجاوز المعدل المسموح للمشروع {project_id}، انتظار {wait_time} ثانية...")
                time.sleep(wait_time)
                continue
                
            elif response.status_code >= 500:  # خطأ في السيرفر
                wait_time = 2 ** attempt  # 1, 2, 4 ثواني (Backoff أسي)
                print(f"⚠️ خطأ في السيرفر {response.status_code} للمشروع {project_id}، انتظار {wait_time} ثانية...")
                time.sleep(wait_time)
                continue
                
            else:
                print(f"⚠️ المشروع {project_id}: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            print(f"❌ خطأ في المشروع {project_id} (محاولة {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Backoff أسي: 1, 2, 4
                time.sleep(wait_time)
            else:
                # في حالة الفشل التام، نحاول إعادة تعيين الجلسة
                reset_session()
                return None
    
    return None

def process_project_data(project_id: int, project_info: dict, raw_data: dict) -> Optional[Dict]:
    """معالجة البيانات الخام وتحويلها إلى الشكل المطلوب"""
    try:
        total_inactive = 0
        total_units = 0
        available_units = 0
        
        for item in raw_data.get('included', []):
            if item.get('type') == 'projects':
                attrs = item.get('attributes', {})
                stats = attrs.get('units_statistic_data', {})
                total_units = stats.get('all_units_count', 0)
                available_units = stats.get('available_units_count', 0)
            
            if item.get('type') == 'project_unit_types':
                total_inactive += item.get('attributes', {}).get('inactive_unit_count', 0)
        
        # حساب الوحدات المحجوزة بطريقة صحيحة
        booked_units = total_units - available_units - total_inactive
        
        # التأكد من عدم وجود قيم سالبة
        if booked_units < 0:
            booked_units = 0
        
        return {
            'project_id': project_id,
            'project_name': project_info['name'],
            'total_units': total_units,
            'available_units': available_units,
            'booked_units': booked_units,
            'inactive_units': total_inactive,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    except Exception as e:
        print(f"❌ خطأ في معالجة بيانات المشروع {project_id}: {e}")
        return None

def get_project_data(project_id: int, project_info: dict) -> Optional[Dict]:
    """جلب بيانات المشروع مع استخدام cache"""
    reference_unit = project_info.get('reference_unit')
    if not reference_unit:
        return None
    
    cache_key = f"project_{project_id}_{reference_unit}"
    
    # 1️⃣ محاولة القراءة من cache
    cached_data = project_cache.get(cache_key)
    if cached_data:
        print(f"💾 استخدام cache للمشروع {project_info['name']} (ID: {project_id})")
        return cached_data
    
    # 2️⃣ غير موجود في cache → طلب من API
    raw_data = fetch_project_data_from_api(project_id, reference_unit)
    
    if raw_data:
        processed_data = process_project_data(project_id, project_info, raw_data)
        if processed_data:
            # تخزين في cache للاستخدام المستقبلي
            project_cache.set(cache_key, processed_data)
            print(f"✅ تم تحديث cache للمشروع {project_info['name']}")
            return processed_data
    
    return None


# =========================================================
# 🔗 جلب روابط القطع المتاحة للمشروع
# =========================================================
def fetch_available_unit_links(project_id: int) -> List[str]:
    """جلب روابط القطع المتاحة من API لاستخدامها في تنبيهات الإلغاء/الإتاحة"""
    url = f"https://sakani.sa/marketplaceApi/search/v1/projects/{project_id}/available-units?limit=100&_={int(time.time() * 1000)}"

    try:
        random_delay()
        session = get_session()
        response = session.get(url, headers=get_headers(), timeout=25)

        if response.status_code != 200:
            print(f"⚠️ فشل جلب القطع المتاحة للمشروع {project_id}: HTTP {response.status_code}")
            return []

        data = response.json()
        links = []

        for unit in data.get("data", []):
            attrs = unit.get("attributes", {}) or {}

            unit_number = (
                unit.get("id")
                or attrs.get("unit_id")
                or attrs.get("id")
                or attrs.get("code")
                or attrs.get("unit_code")
                or attrs.get("reference_number")
            )

            if unit_number:
                link = f"https://sakani.sa/app/units/{unit_number}"
                if link not in links:
                    links.append(link)

        print(f"🔗 تم جلب {len(links)} رابط قطعة متاحة للمشروع {project_id}")
        return links

    except Exception as e:
        print(f"❌ خطأ في جلب روابط القطع للمشروع {project_id}: {e}")
        return []

# =========================================================
# تنسيق التنبيهات
# =========================================================
def format_telegram_alert(project_name: str, change_type: str, old_value: int, new_value: int, project_id: int = None) -> str:
    """تنسيق التنبيه للتليجرام (HTML)"""
    now = datetime.now()
    time_str = now.strftime('%I:%M:%S:%f')[:-3] + ' ' + now.strftime('%p')
    
    # تحديد العنوان والأيقونة حسب نوع التغيير
    if change_type == 'booked' and new_value > old_value:
        title = "🔴🔴 حجز جديد 🔴🔴"
    elif change_type == 'inactive' and new_value > old_value:
        title = "🟢🟢 إلغاء حجز 🟢🟢"
    elif change_type == 'available' and new_value > old_value:
        title = "🟡🟡 وحدات جديدة متاحة 🟡🟡"
    else:
        return None
    
    # تنسيق التنبيه
    alert = f"""
{title}
_________________________

<b>🏠 المشروع :</b> {project_id}
<b>📋 الاسم :</b> {project_name}
<b>📊 السابق :</b> {old_value}
<b>📊 الحالي :</b> {new_value}
<b>⏰ الوقت :</b> {time_str}
_________________________

⚡ <b>تنبيه فوري - قناة المراقبة</b>
"""
    return alert

def format_console_alert(project_name: str, change_type: str, old_value: int, new_value: int, project_id: int = None) -> str:
    """تنسيق التنبيه للكونسول"""
    now = datetime.now()
    time_str = now.strftime('%I:%M:%S:%f')[:-3] + ' ' + now.strftime('%p')
    
    if change_type == 'booked' and new_value > old_value:
        title = "🔴🔴 حجز جديد 🔴🔴"
    elif change_type == 'inactive' and new_value > old_value:
        title = "🟢🟢 إلغاء حجز 🟢🟢"
    elif change_type == 'available' and new_value > old_value:
        title = "🟡🟡 وحدات جديدة متاحة 🟡🟡"
    else:
        return None
    
    alert_lines = []
    alert_lines.append("=" * 50)
    alert_lines.append(title)
    alert_lines.append("=" * 50)
    alert_lines.append(f"📌 المشروع ID: {project_id}")
    alert_lines.append(f"📌 اسم المشروع: {project_name}")
    alert_lines.append(f"📊 السابق: {old_value}")
    alert_lines.append(f"📊 الحالي: {new_value}")
    alert_lines.append("-" * 50)
    alert_lines.append(f"⏰ الوقت: {time_str}")
    alert_lines.append("=" * 50)
    
    return "\n".join(alert_lines)

# =========================================================
# التحقق من التغييرات وإرسال التنبيه
# =========================================================
last_data = {}
last_available_units_links = {}

def check_and_alert(project_id: int, project_name: str, current_data: dict):
    """التحقق من التغييرات وإرسال تنبيه"""
    global last_data
    
    previous = last_data.get(project_id)
    
    if not previous:
        last_data[project_id] = current_data
        last_available_units_links[project_id] = fetch_available_unit_links(project_id)
        save_history(project_id, current_data)
        return
    
    changes = []
    
    # فحص الوحدات الغير نشطة (إلغاء حجز) - فقط عندما تزيد
    if previous.get('inactive_units') != current_data['inactive_units']:
        old_val = previous.get('inactive_units', 0)
        new_val = current_data['inactive_units']
        if new_val > old_val:
            changes.append(('inactive', old_val, new_val))
    
    # فحص الوحدات المتاحة - فقط عندما تزيد
    if previous.get('available_units') != current_data['available_units']:
        old_val = previous.get('available_units', 0)
        new_val = current_data['available_units']
        if new_val > old_val:
            changes.append(('available', old_val, new_val))
    
    # فحص الوحدات المحجوزة - فقط عندما تزيد
    if previous.get('booked_units') != current_data['booked_units']:
        old_val = previous.get('booked_units', 0)
        new_val = current_data['booked_units']
        if new_val > old_val:
            changes.append(('booked', old_val, new_val))
    
    # إرسال تنبيه لكل تغيير
    for change_type, old_val, new_val in changes:
        # تنبيه للكونسول
        console_alert = format_console_alert(project_name, change_type, old_val, new_val, project_id)
        if console_alert:
            print(f"\n{console_alert}\n")
        
        # تنبيه للتليجرام
        telegram_alert = format_telegram_alert(project_name, change_type, old_val, new_val, project_id)
        if telegram_alert:
            # عند زيادة المتاح أو حدوث إلغاء: نحاول معرفة القطعة التي أصبحت متاحة
            if change_type in ("available", "inactive"):
                if change_type == "inactive":
                    print("⏳ تم رصد إلغاء حجز، انتظار 3 ثواني ثم فحص القطع المتاحة...")
                    time.sleep(3)

                current_links = fetch_available_unit_links(project_id)
                old_links = last_available_units_links.get(project_id, [])
                new_links = [link for link in current_links if link not in old_links]

                if new_links:
                    link_title = "🔗 رابط القطعة الجديدة" if change_type == "available" else "🔗 رابط القطعة بعد الإلغاء"
                    telegram_alert += "\n\n<b>" + link_title + ":</b>\n" + "\n".join(new_links[:5])
                else:
                    print(f"⚠️ لم يتم العثور على رابط قطعة جديد للمشروع {project_id}")

                last_available_units_links[project_id] = current_links

            send_telegram_message(telegram_alert)
        
        # حفظ في قاعدة البيانات
        save_alert(project_id, project_name, change_type, old_val, new_val, console_alert or telegram_alert, 1)
    
    # تحديث آخر البيانات
    last_data[project_id] = current_data
    save_history(project_id, current_data)

# =========================================================
# حلقة المراقبة المستمرة - نسخة محسنة ضد الحظر
# =========================================================
def monitoring_loop():
    """حلقة المراقبة المستمرة مع طلبات متسلسلة وتأخيرات ذكية"""
    print("=" * 60)
    print("   📢 نظام المراقبة - قيد التشغيل (بدون بروكسي)")
    print("=" * 60)
    print("✅ يمكنك إدارة المشاريع من http://localhost:5000")
    print(f"⏱️  فترة الفحص: كل {CHECK_INTERVAL} ثانية")
    
    # عرض حالة التليجرام
    is_telegram_ready = (TELEGRAM_ENABLED and 
                         TELEGRAM_BOT_TOKEN != "YOUR_BOT_TOKEN_HERE" and 
                         TELEGRAM_CHANNEL_ID and
                         TELEGRAM_CHANNEL_ID != "@alhql5bot")
    print(f"📨 التليجرام: {'مفعل (قناة: ' + TELEGRAM_CHANNEL_ID + ')' if is_telegram_ready else 'غير مفعل'}")
    
    # عرض إعدادات ضد الحظر
    print(f"🛡️ تقنيات ضد الحظر:")
    print(f"   - تأخير عشوائي: {MIN_DELAY}-{MAX_DELAY} ثانية")
    print(f"   - تناوب User-Agent: {len(USER_AGENTS)} وكيل مختلف")
    print(f"   - Cache: {CACHE_TTL_SECONDS} ثانية صلاحية")
    print(f"   - Retry with Backoff: 3 محاولات")
    print("=" * 60)
    print("\n🚀 بدء المراقبة...\n")
    
    # إرسال رسالة اختبار للتليجرام
    if is_telegram_ready:
        send_test_message()
    
    loop_count = 0
    
    while True:
        try:
            loop_count += 1
            print(f"\n{'='*40}")
            print(f"📊 دورة الفحص #{loop_count} - {datetime.now().strftime('%H:%M:%S')}")
            print(f"{'='*40}")
            
            projects = load_projects()
            
            if not projects:
                print("⚠️ لا توجد مشاريع للمراقبة")
                time.sleep(CHECK_INTERVAL)
                continue
            
            # جلب المشاريع بشكل متسلسل (أفضل ضد الحظر من التوازي)
            for project_id_str, info in projects.items():
                project_id = int(project_id_str)
                
                if not info.get('reference_unit'):
                    print(f"⚠️ المشروع {project_id} ليس لديه وحدة مرجعية، تخطي")
                    continue
                
                print(f"\n🔍 جاري فحص: {info['name']} (ID: {project_id})")
                
                current_data = get_project_data(project_id, info)
                
                if current_data:
                    check_and_alert(project_id, info['name'], current_data)
                    print(f"   ✅ متاح: {current_data['available_units']} | محجوز: {current_data['booked_units']} | غير نشط: {current_data['inactive_units']}")
                else:
                    print(f"   ❌ فشل في جلب بيانات المشروع {info['name']}")
                
                # ✅ انتظار إضافي بين المشاريع لتجنب الحظر
                between_projects_delay = random.uniform(0.3, 0.8)
                print(f"   ⏳ انتظار {between_projects_delay:.1f} ثانية قبل المشروع التالي...")
                time.sleep(between_projects_delay)
            
            # تنظيف cache منتهي الصلاحية
            expired_count = project_cache.clear_expired()
            if expired_count:
                print(f"🧹 تم تنظيف {expired_count} عنصر منتهي الصلاحية من cache")
            
            # عرض إحصائيات cache
            print(f"\n📈 إحصائيات: حجم cache = {project_cache.get_size()}")
            
            print(f"\n⏳ انتظار {CHECK_INTERVAL} ثانية حتى الدورة القادمة...")
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n🛑 تم إيقاف المراقبة")
            break
        except Exception as e:
            print(f"❌ خطأ غير متوقع: {e}")
            time.sleep(CHECK_INTERVAL)

# =========================================================
# APIs للوحة التحكم
# =========================================================
@app.route('/')
def index():
    """الصفحة الرئيسية"""
    return render_template('dashboard.html')

@app.route('/api/projects', methods=['GET'])
def api_get_projects():
    """جلب قائمة المشاريع"""
    projects = load_projects()
    return jsonify(projects)

@app.route('/api/projects', methods=['POST'])
def api_add_project():
    """إضافة مشروع جديد"""
    data = request.get_json()
    
    project_id = data.get('project_id')
    project_name = data.get('project_name')
    reference_unit = data.get('reference_unit')
    
    if not project_id or not project_name or not reference_unit:
        return jsonify({'error': 'جميع الحقول مطلوبة'}), 400
    
    try:
        project_id = str(int(project_id))
        reference_unit = int(reference_unit)
    except ValueError:
        return jsonify({'error': 'رقم المشروع والوحدة المرجعية يجب أن تكون أرقاماً'}), 400
    
    projects = load_projects()
    
    if project_id in projects:
        return jsonify({'error': f'المشروع {project_id} موجود مسبقاً'}), 400
    
    projects[project_id] = {
        'name': project_name,
        'reference_unit': reference_unit,
        'alert_enabled': True,
        'added_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    save_projects(projects)
    
    # مسح cache للمشروع الجديد (للتأكد من جلب بيانات جديدة)
    project_cache.clear()
    
    # إرسال إشعار تليجرام بإضافة مشروع جديد
    msg = f"➕ <b>تم إضافة مشروع جديد</b>\n\n<b>🏠 المشروع:</b> {project_name}\n<b>🆔 ID:</b> {project_id}\n<b>🔢 الوحدة المرجعية:</b> {reference_unit}"
    send_telegram_message(msg)
    
    return jsonify({'success': True, 'message': 'تم إضافة المشروع بنجاح'})

@app.route('/api/projects/<project_id>', methods=['DELETE'])
def api_delete_project(project_id):
    """حذف مشروع"""
    projects = load_projects()
    
    if project_id not in projects:
        return jsonify({'error': 'المشروع غير موجود'}), 404
    
    project_name = projects[project_id].get('name', 'غير معروف')
    del projects[project_id]
    save_projects(projects)
    
    if project_id in last_data:
        del last_data[project_id]
    
    # مسح cache
    project_cache.clear()
    
    # إرسال إشعار تليجرام بحذف مشروع
    msg = f"🗑️ <b>تم حذف مشروع</b>\n\n<b>🏠 المشروع:</b> {project_name}\n<b>🆔 ID:</b> {project_id}"
    send_telegram_message(msg)
    
    return jsonify({'success': True, 'message': 'تم حذف المشروع بنجاح'})

@app.route('/api/projects/<project_id>', methods=['PUT'])
def api_update_project(project_id):
    """تحديث وحدة مرجعية لمشروع"""
    data = request.get_json()
    projects = load_projects()
    
    if project_id not in projects:
        return jsonify({'error': 'المشروع غير موجود'}), 404
    
    old_ref = projects[project_id].get('reference_unit')
    
    if 'reference_unit' in data:
        projects[project_id]['reference_unit'] = int(data['reference_unit'])
    
    save_projects(projects)
    
    # مسح cache للمشروع المحدد
    cache_key = f"project_{project_id}_{old_ref}"
    project_cache.cache.pop(cache_key, None)
    
    # إرسال إشعار تليجرام بتحديث المشروع
    msg = f"✏️ <b>تم تحديث مشروع</b>\n\n<b>🏠 المشروع:</b> {projects[project_id]['name']}\n<b>🆔 ID:</b> {project_id}\n<b>🔢 الوحدة المرجعية القديمة:</b> {old_ref}\n<b>🔢 الوحدة المرجعية الجديدة:</b> {projects[project_id]['reference_unit']}"
    send_telegram_message(msg)
    
    return jsonify({'success': True, 'message': 'تم تحديث المشروع بنجاح'})

@app.route('/api/status')
def api_status():
    """جلب الحالة الحالية مع إضافة booked_units"""
    projects = load_projects()
    status = {}
    
    for pid, info in projects.items():
        if info.get('reference_unit'):
            data = get_project_data(int(pid), info)
            if data:
                status[pid] = {
                    'name': info['name'],
                    'available': data['available_units'],
                    'booked': data['booked_units'],
                    'inactive': data['inactive_units'],
                    'total': data['total_units']
                }
    
    return jsonify(status)

@app.route('/api/alerts')
def api_alerts():
    """جلب آخر التنبيهات"""
    conn = sqlite3.connect('monitoring.db')
    c = conn.cursor()
    c.execute('SELECT timestamp, project_name, change_type, old_value, new_value FROM alerts ORDER BY id DESC LIMIT 50')
    rows = c.fetchall()
    conn.close()
    
    alerts = []
    for row in rows:
        alerts.append({
            'timestamp': row[0],
            'project_name': row[1],
            'change_type': row[2],
            'old_value': row[3],
            'new_value': row[4]
        })
    
    return jsonify(alerts)

@app.route('/api/telegram/status')
def api_telegram_status():
    """جلب حالة التليجرام"""
    is_configured = TELEGRAM_BOT_TOKEN != "YOUR_BOT_TOKEN_HERE" and TELEGRAM_CHANNEL_ID and TELEGRAM_CHANNEL_ID != "@alhql5bot"
    return jsonify({
        'enabled': TELEGRAM_ENABLED,
        'configured': is_configured,
        'channel_id': TELEGRAM_CHANNEL_ID if is_configured else None,
        'is_channel': True
    })

@app.route('/api/telegram/test', methods=['POST'])
def api_telegram_test():
    """إرسال رسالة اختبار للتليجرام"""
    send_test_message()
    return jsonify({'success': True, 'message': f'تم إرسال رسالة اختبار إلى القناة {TELEGRAM_CHANNEL_ID}'})

@app.route('/api/cache/clear', methods=['POST'])
def api_cache_clear():
    """مسح cache يدوياً"""
    project_cache.clear()
    return jsonify({'success': True, 'message': 'تم مسح cache بنجاح'})

@app.route('/api/cache/stats')
def api_cache_stats():
    """إحصائيات cache"""
    return jsonify({
        'size': project_cache.get_size(),
        'ttl_seconds': CACHE_TTL_SECONDS,
        'max_age_seconds': CACHE_TTL_SECONDS
    })

# =========================================================
# التشغيل الرئيسي
# =========================================================
def main():
    """تشغيل النظام"""
    init_db()
    
    # إنشاء مشاريع افتراضية إذا لم يكن هناك
    if not os.path.exists(PROJECTS_FILE):
        default_projects = {
            "165": {
                "name": "مشروع التلال",
                "reference_unit": 615079,
                "alert_enabled": True,
                "added_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            },
            "208": {
                "name": "مشروع ربوع الأسياح",
                "reference_unit": 663602,
                "alert_enabled": True,
                "added_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        }
        save_projects(default_projects)
    
    # تشغيل المراقبة في خيط منفصل
    monitor_thread = threading.Thread(target=monitoring_loop, daemon=True)
    monitor_thread.start()
    
    # تشغيل خادم Flask
    print("\n🌐 فتح لوحة التحكم: http://localhost:5000")
    print("🛡️ النظام يعمل بدون بروكسي مع تقنيات متقدمة ضد الحظر\n")
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
