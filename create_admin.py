import getpass
import os

import mysql.connector
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv()

config = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "smartbiz_ai"),
    "charset": "utf8mb4",
}


def main():
    print("=== إنشاء حساب مالك SmartBiz AI ===")
    username = input("اسم المستخدم: ").strip()
    full_name = input("الاسم الظاهر: ").strip()
    password = getpass.getpass("كلمة المرور (8 أحرف على الأقل): ")
    confirm = getpass.getpass("أعد كتابة كلمة المرور: ")

    if not username or not full_name:
        print("اسم المستخدم والاسم الظاهر مطلوبان.")
        return
    if len(password) < 8:
        print("كلمة المرور لازم تكون 8 أحرف على الأقل.")
        return
    if password != confirm:
        print("كلمتا المرور غير متطابقتين.")
        return

    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**config)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            print("اسم المستخدم موجود بالفعل. اختار اسمًا آخر.")
            return
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name, role) VALUES (%s, %s, %s, 'owner')",
            (username, generate_password_hash(password), full_name),
        )
        conn.commit()
        print("تم إنشاء حساب المالك بنجاح. شغّل التطبيق وسجّل الدخول.")
    except mysql.connector.Error as exc:
        print("تعذر الاتصال بقاعدة البيانات. تأكد من تشغيل MySQL واستيراد init_db.sql.")
        print(f"تفاصيل الخطأ: {exc}")
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


if __name__ == "__main__":
    main()
