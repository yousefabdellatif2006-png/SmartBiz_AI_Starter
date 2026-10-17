import os
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

import mysql.connector
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "development-key-change-before-deployment")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["WTF_CSRF_TIME_LIMIT"] = 3600
csrf = CSRFProtect(app)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "smartbiz_ai"),
    "charset": "utf8mb4",
    "autocommit": False,
}


def get_connection():
    return mysql.connector.connect(**DB_CONFIG)


def fetch_all(sql, params=()):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def fetch_one(sql, params=()):
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def write_audit(cursor, action, entity_type=None, entity_id=None, details=None):
    cursor.execute(
        "INSERT INTO audit_logs (user_id, action, entity_type, entity_id, details, ip_address) VALUES (%s,%s,%s,%s,%s,%s)",
        (
            session.get("user_id"), action, entity_type,
            str(entity_id) if entity_id is not None else None,
            details, request.remote_addr,
        ),
    )


def safe_decimal(value, label):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"قيمة {label} غير صحيحة.")
    if not number.is_finite() or number < 0:
        raise ValueError(f"قيمة {label} لازم تكون رقمًا موجبًا أو صفرًا.")
    return number.quantize(Decimal("0.01"))


@app.template_filter("money")
def money(value):
    try:
        return f"{Decimal(str(value or 0)):,.2f} ج.م"
    except (InvalidOperation, ValueError, TypeError):
        return "0.00 ج.م"


@app.template_filter("date_ar")
def date_ar(value):
    if not value:
        return "—"
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d %H:%M" if isinstance(value, datetime) else "%Y-%m-%d")
    return str(value)


@app.context_processor
def inject_globals():
    return {"current_user_name": session.get("full_name", ""), "current_role": session.get("role", "")}


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = fetch_one(
            "SELECT id, username, password_hash, full_name, role, is_active FROM users WHERE username = %s",
            (username,),
        )
        if user and user["is_active"] and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role"]
            return redirect(url_for("dashboard"))
        flash("اسم المستخدم أو كلمة المرور غير صحيحة.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("تم تسجيل الخروج بأمان.", "success")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    today = date.today().isoformat()
    month_start = date.today().replace(day=1).isoformat()
    metrics = fetch_one(
        """SELECT
             COALESCE(SUM(CASE WHEN DATE(created_at) = %s AND status = 'completed' THEN total_amount ELSE 0 END), 0) AS today_sales,
             COALESCE(SUM(CASE WHEN DATE(created_at) = %s AND status = 'completed' THEN profit_amount ELSE 0 END), 0) AS today_profit,
             COALESCE(SUM(CASE WHEN DATE(created_at) >= %s AND status = 'completed' THEN total_amount ELSE 0 END), 0) AS month_sales,
             COUNT(CASE WHEN DATE(created_at) = %s AND status = 'completed' THEN 1 END) AS today_orders
           FROM sales""",
        (today, today, month_start, today),
    )
    stock = fetch_one(
        "SELECT COUNT(*) AS product_count, COALESCE(SUM(stock),0) AS stock_units, COUNT(CASE WHEN stock <= min_stock THEN 1 END) AS low_stock FROM products WHERE is_active=1"
    )
    recent_sales = fetch_all(
        "SELECT id, sale_number, COALESCE(NULLIF(customer_name,''),'عميل نقدي') AS customer, total_amount, status, created_at FROM sales ORDER BY id DESC LIMIT 7"
    )
    low_products = fetch_all(
        "SELECT id, name, stock, min_stock FROM products WHERE is_active=1 AND stock <= min_stock ORDER BY stock ASC, name ASC LIMIT 6"
    )
    month_expenses = fetch_one(
        "SELECT COALESCE(SUM(amount),0) AS amount FROM expenses WHERE expense_date >= %s",
        (month_start,),
    )
    net_estimate = Decimal(str(metrics["month_sales"] or 0)) - Decimal(str(month_expenses["amount"] or 0))
    return render_template(
        "dashboard.html", metrics=metrics, stock=stock, recent_sales=recent_sales,
        low_products=low_products, month_expenses=month_expenses["amount"], net_estimate=net_estimate,
    )


@app.route("/products")
@login_required
def products():
    search = request.args.get("q", "").strip()
    if search:
        rows = fetch_all(
            "SELECT * FROM products WHERE is_active=1 AND (name LIKE %s OR COALESCE(sku,'') LIKE %s OR category LIKE %s) ORDER BY id DESC",
            (f"%{search}%", f"%{search}%", f"%{search}%"),
        )
    else:
        rows = fetch_all("SELECT * FROM products WHERE is_active=1 ORDER BY id DESC")
    return render_template("products.html", products=rows, search=search)


def validate_product_form():
    sku = request.form.get("sku", "").strip() or None
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "عام").strip() or "عام"
    description = request.form.get("description", "").strip() or None
    if not name:
        raise ValueError("اسم المنتج مطلوب.")
    buy_price = safe_decimal(request.form.get("buy_price", "0"), "سعر الشراء")
    sell_price = safe_decimal(request.form.get("sell_price", "0"), "سعر البيع")
    try:
        stock = int(request.form.get("stock", "0"))
        min_stock = int(request.form.get("min_stock", "5"))
    except ValueError:
        raise ValueError("الكميات لازم تكون أرقامًا صحيحة.")
    if stock < 0 or min_stock < 0:
        raise ValueError("الكميات لا يمكن أن تكون بالسالب.")
    return sku, name, category, description, buy_price, sell_price, stock, min_stock


@app.route("/products/new", methods=["GET", "POST"])
@login_required
def product_new():
    if request.method == "POST":
        conn = None
        cursor = None
        try:
            data = validate_product_form()
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO products (sku,name,category,description,buy_price,sell_price,stock,min_stock) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", data,
            )
            product_id = cursor.lastrowid
            if data[6] > 0:
                cursor.execute(
                    "INSERT INTO inventory_movements (product_id,movement_type,quantity_change,reference_type,reference_id,notes,created_by) VALUES (%s,'adjustment',%s,'product_opening',%s,'رصيد افتتاحي',%s)",
                    (product_id, data[6], product_id, session["user_id"]),
                )
            write_audit(cursor, "إضافة منتج", "product", product_id, data[1])
            conn.commit()
            flash("تمت إضافة المنتج بنجاح.", "success")
            return redirect(url_for("products"))
        except ValueError as exc:
            flash(str(exc), "error")
        except mysql.connector.IntegrityError:
            if conn:
                conn.rollback()
            flash("كود المنتج مستخدم بالفعل. اختار كودًا مختلفًا.", "error")
        except mysql.connector.Error as exc:
            if conn:
                conn.rollback()
            app.logger.exception("Database error while adding product")
            flash("حصل خطأ أثناء حفظ المنتج. راجع إعدادات قاعدة البيانات.", "error")
        finally:
            if cursor:
                cursor.close()
            if conn and conn.is_connected():
                conn.close()
    return render_template("product_form.html", product=None, page_title="إضافة منتج")


@app.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def product_edit(product_id):
    product = fetch_one("SELECT * FROM products WHERE id=%s AND is_active=1", (product_id,))
    if not product:
        abort(404)
    if request.method == "POST":
        conn = None
        cursor = None
        try:
            data = validate_product_form()
            if data[6] != product["stock"]:
                flash("تعديل الكمية من صفحة المنتج متوقف لحماية سجل المخزون؛ سجّل فرق الكمية من حركة مخزون في المرحلة التالية. باقي بيانات المنتج محفوظة.", "error")
                data = data[:6] + (product["stock"],) + data[7:]
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE products SET sku=%s,name=%s,category=%s,description=%s,buy_price=%s,sell_price=%s,stock=%s,min_stock=%s WHERE id=%s",
                data + (product_id,),
            )
            write_audit(cursor, "تعديل منتج", "product", product_id, data[1])
            conn.commit()
            flash("تم تحديث بيانات المنتج.", "success")
            return redirect(url_for("products"))
        except ValueError as exc:
            flash(str(exc), "error")
        except mysql.connector.IntegrityError:
            if conn:
                conn.rollback()
            flash("كود المنتج مستخدم بالفعل. اختار كودًا مختلفًا.", "error")
        except mysql.connector.Error:
            if conn:
                conn.rollback()
            app.logger.exception("Database error while updating product")
            flash("حصل خطأ أثناء تحديث المنتج.", "error")
        finally:
            if cursor:
                cursor.close()
            if conn and conn.is_connected():
                conn.close()
    return render_template("product_form.html", product=product, page_title="تعديل منتج")


@app.route("/products/<int:product_id>/archive", methods=["POST"])
@login_required
def product_archive(product_id):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE products SET is_active=0 WHERE id=%s AND is_active=1", (product_id,))
        if cursor.rowcount:
            write_audit(cursor, "إيقاف منتج", "product", product_id)
            conn.commit()
            flash("تم إخفاء المنتج من قائمة المنتجات، مع الاحتفاظ بسجله في الفواتير.", "success")
        else:
            conn.rollback()
            flash("المنتج غير موجود.", "error")
    except mysql.connector.Error:
        conn.rollback()
        app.logger.exception("Database error while archiving product")
        flash("تعذر إيقاف المنتج.", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("products"))


@app.route("/sales")
@login_required
def sales():
    rows = fetch_all(
        "SELECT id,sale_number,COALESCE(NULLIF(customer_name,''),'عميل نقدي') AS customer,total_amount,profit_amount,status,created_at FROM sales ORDER BY id DESC LIMIT 200"
    )
    return render_template("sales.html", sales=rows)


@app.route("/sales/new", methods=["GET", "POST"])
@login_required
def sale_new():
    active_products = fetch_all("SELECT id,name,sku,sell_price,buy_price,stock FROM products WHERE is_active=1 AND stock>0 ORDER BY name")
    active_customers = fetch_all("SELECT id,name,phone FROM customers ORDER BY name")
    if request.method == "POST":
        ids = request.form.getlist("product_id[]")
        quantities = request.form.getlist("quantity[]")
        customer_name = request.form.get("customer_name", "").strip() or "عميل نقدي"
        customer_id = None
        raw_customer_id = request.form.get("customer_id", "").strip()
        if raw_customer_id:
            try:
                customer_id = int(raw_customer_id)
            except ValueError:
                flash("اختيار العميل غير صحيح.", "error")
                return render_template("sale_form.html", products=active_products, customers=active_customers, form=request.form)
            selected_customer = fetch_one("SELECT id,name FROM customers WHERE id=%s", (customer_id,))
            if not selected_customer:
                flash("العميل المحدد غير موجود.", "error")
                return render_template("sale_form.html", products=active_products, customers=active_customers, form=request.form)
            customer_name = selected_customer["name"]
        if not ids or len(ids) != len(quantities):
            flash("أضف منتجًا واحدًا على الأقل إلى الفاتورة.", "error")
            return render_template("sale_form.html", products=active_products, customers=active_customers, form=request.form)
        requested = {}
        try:
            for raw_id, raw_quantity in zip(ids, quantities):
                if not raw_id and not raw_quantity:
                    continue
                product_id = int(raw_id)
                quantity = int(raw_quantity)
                if quantity <= 0:
                    raise ValueError("الكمية لازم تكون أكبر من صفر.")
                requested[product_id] = requested.get(product_id, 0) + quantity
            if not requested:
                raise ValueError("اختار منتجًا واحدًا على الأقل.")
        except ValueError as exc:
            flash(str(exc) if str(exc) else "تأكد من اختيار المنتجات والكميات بشكل صحيح.", "error")
            return render_template("sale_form.html", products=active_products, customers=active_customers, form=request.form)

        conn = None
        cursor = None
        try:
            conn = get_connection()
            conn.start_transaction()
            cursor = conn.cursor(dictionary=True)
            selected = []
            total = Decimal("0.00")
            cost_total = Decimal("0.00")
            for product_id, quantity in requested.items():
                cursor.execute("SELECT id,name,sell_price,buy_price,stock FROM products WHERE id=%s AND is_active=1 FOR UPDATE", (product_id,))
                product = cursor.fetchone()
                if not product:
                    raise ValueError("أحد المنتجات المختارة غير متاح.")
                if product["stock"] < quantity:
                    raise ValueError(f"الكمية المطلوبة من {product['name']} أكبر من المخزون المتاح ({product['stock']}).")
                unit_price = Decimal(str(product["sell_price"]))
                unit_cost = Decimal(str(product["buy_price"]))
                line_total = (unit_price * quantity).quantize(Decimal("0.01"))
                line_cost = (unit_cost * quantity).quantize(Decimal("0.01"))
                total += line_total
                cost_total += line_cost
                selected.append({"product": product, "quantity": quantity, "unit_price": unit_price, "unit_cost": unit_cost, "line_total": line_total, "line_cost": line_cost})
            sale_number = "SB-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2).upper()
            cursor.execute(
                "INSERT INTO sales (sale_number,customer_id,customer_name,total_amount,cost_amount,profit_amount,payment_method,status,created_by) VALUES (%s,%s,%s,%s,%s,%s,'cash','completed',%s)",
                (sale_number, customer_id, customer_name, total, cost_total, total - cost_total, session["user_id"]),
            )
            sale_id = cursor.lastrowid
            for item in selected:
                p = item["product"]
                qty = item["quantity"]
                cursor.execute(
                    "INSERT INTO sale_items (sale_id,product_id,quantity,unit_price,unit_cost,line_total,line_cost) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (sale_id, p["id"], qty, item["unit_price"], item["unit_cost"], item["line_total"], item["line_cost"]),
                )
                cursor.execute("UPDATE products SET stock=stock-%s WHERE id=%s", (qty, p["id"]))
                cursor.execute(
                    "INSERT INTO inventory_movements (product_id,movement_type,quantity_change,reference_type,reference_id,notes,created_by) VALUES (%s,'sale',%s,'sale',%s,%s,%s)",
                    (p["id"], -qty, sale_id, f"فاتورة {sale_number}", session["user_id"]),
                )
            write_audit(cursor, "إنشاء فاتورة بيع", "sale", sale_id, sale_number)
            conn.commit()
            flash(f"تم حفظ الفاتورة {sale_number} وتحديث المخزون تلقائيًا.", "success")
            return redirect(url_for("sales"))
        except ValueError as exc:
            if conn:
                conn.rollback()
            flash(str(exc), "error")
        except mysql.connector.Error:
            if conn:
                conn.rollback()
            app.logger.exception("Database error while saving sale")
            flash("لم يتم حفظ الفاتورة بسبب خطأ في قاعدة البيانات. لم يتم تغيير المخزون.", "error")
        finally:
            if cursor:
                cursor.close()
            if conn and conn.is_connected():
                conn.close()
    return render_template("sale_form.html", products=active_products, customers=active_customers, form={})


@app.route("/sales/<int:sale_id>/cancel", methods=["POST"])
@login_required
def sale_cancel(sale_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        conn.start_transaction()
        cursor.execute("SELECT id,sale_number,status FROM sales WHERE id=%s FOR UPDATE", (sale_id,))
        sale = cursor.fetchone()
        if not sale:
            conn.rollback()
            flash("الفاتورة غير موجودة.", "error")
            return redirect(url_for("sales"))
        if sale["status"] == "cancelled":
            conn.rollback()
            flash("الفاتورة ملغاة بالفعل.", "error")
            return redirect(url_for("sales"))
        cursor.execute("SELECT product_id,quantity FROM sale_items WHERE sale_id=%s", (sale_id,))
        items = cursor.fetchall()
        for item in items:
            cursor.execute("UPDATE products SET stock=stock+%s WHERE id=%s", (item["quantity"], item["product_id"]))
            cursor.execute(
                "INSERT INTO inventory_movements (product_id,movement_type,quantity_change,reference_type,reference_id,notes,created_by) VALUES (%s,'return',%s,'sale',%s,%s,%s)",
                (item["product_id"], item["quantity"], sale_id, f"إرجاع مخزون بسبب إلغاء {sale['sale_number']}", session["user_id"]),
            )
        cursor.execute("UPDATE sales SET status='cancelled' WHERE id=%s", (sale_id,))
        write_audit(cursor, "إلغاء فاتورة وإرجاع المخزون", "sale", sale_id, sale["sale_number"])
        conn.commit()
        flash("تم إلغاء الفاتورة وإرجاع الكميات إلى المخزون.", "success")
    except mysql.connector.Error:
        conn.rollback()
        app.logger.exception("Database error while cancelling sale")
        flash("تعذر إلغاء الفاتورة.", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("sales"))



@app.route("/customers")
@login_required
def customers():
    search = request.args.get("q", "").strip()
    params = ()
    where = ""
    if search:
        where = "WHERE c.name LIKE %s OR COALESCE(c.phone,'') LIKE %s OR COALESCE(c.email,'') LIKE %s"
        params = (f"%{search}%", f"%{search}%", f"%{search}%")
    rows = fetch_all(
        f"""SELECT c.*,
                   COALESCE(s.orders_count,0) AS orders_count,
                   COALESCE(s.total_spent,0) AS total_spent
            FROM customers c
            LEFT JOIN (
                SELECT customer_id, COUNT(*) AS orders_count, SUM(total_amount) AS total_spent
                FROM sales WHERE status='completed' AND customer_id IS NOT NULL
                GROUP BY customer_id
            ) s ON s.customer_id=c.id
            {where}
            ORDER BY c.id DESC""",
        params,
    )
    return render_template("customers.html", customers=rows, search=search)


@app.route("/customers/new", methods=["GET", "POST"])
@login_required
def customer_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip() or None
        email = request.form.get("email", "").strip() or None
        address = request.form.get("address", "").strip() or None
        notes = request.form.get("notes", "").strip() or None
        if not name:
            flash("اسم العميل مطلوب.", "error")
            return render_template("customer_form.html", customer=None, form=request.form, page_title="إضافة عميل")
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO customers (name,phone,email,address,notes) VALUES (%s,%s,%s,%s,%s)",
                (name, phone, email, address, notes),
            )
            customer_id = cursor.lastrowid
            write_audit(cursor, "إضافة عميل", "customer", customer_id, name)
            conn.commit()
            flash("تمت إضافة العميل بنجاح.", "success")
            return redirect(url_for("customers"))
        except mysql.connector.Error:
            conn.rollback()
            app.logger.exception("Database error while creating customer")
            flash("تعذر حفظ بيانات العميل.", "error")
        finally:
            cursor.close()
            conn.close()
    return render_template("customer_form.html", customer=None, form={}, page_title="إضافة عميل")


@app.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
def customer_edit(customer_id):
    customer = fetch_one("SELECT * FROM customers WHERE id=%s", (customer_id,))
    if not customer:
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip() or None
        email = request.form.get("email", "").strip() or None
        address = request.form.get("address", "").strip() or None
        notes = request.form.get("notes", "").strip() or None
        if not name:
            flash("اسم العميل مطلوب.", "error")
            return render_template("customer_form.html", customer=customer, form=request.form, page_title="تعديل عميل")
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE customers SET name=%s,phone=%s,email=%s,address=%s,notes=%s WHERE id=%s",
                (name, phone, email, address, notes, customer_id),
            )
            write_audit(cursor, "تعديل بيانات عميل", "customer", customer_id, name)
            conn.commit()
            flash("تم تحديث بيانات العميل.", "success")
            return redirect(url_for("customers"))
        except mysql.connector.Error:
            conn.rollback()
            app.logger.exception("Database error while updating customer")
            flash("تعذر تحديث بيانات العميل.", "error")
        finally:
            cursor.close()
            conn.close()
    return render_template("customer_form.html", customer=customer, form=customer, page_title="تعديل عميل")


@app.route("/suppliers")
@login_required
def suppliers():
    search = request.args.get("q", "").strip()
    params = ()
    where = ""
    if search:
        where = "WHERE s.name LIKE %s OR COALESCE(s.phone,'') LIKE %s OR COALESCE(s.email,'') LIKE %s"
        params = (f"%{search}%", f"%{search}%", f"%{search}%")
    rows = fetch_all(
        f"""SELECT s.*,
                   COALESCE(p.purchases_count,0) AS purchases_count,
                   COALESCE(p.total_purchased,0) AS total_purchased
            FROM suppliers s
            LEFT JOIN (
                SELECT supplier_id, COUNT(*) AS purchases_count, SUM(total_amount) AS total_purchased
                FROM purchases WHERE status='received' AND supplier_id IS NOT NULL
                GROUP BY supplier_id
            ) p ON p.supplier_id=s.id
            {where}
            ORDER BY s.id DESC""",
        params,
    )
    return render_template("suppliers.html", suppliers=rows, search=search)


@app.route("/suppliers/new", methods=["GET", "POST"])
@login_required
def supplier_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip() or None
        email = request.form.get("email", "").strip() or None
        address = request.form.get("address", "").strip() or None
        notes = request.form.get("notes", "").strip() or None
        if not name:
            flash("اسم المورد مطلوب.", "error")
            return render_template("supplier_form.html", supplier=None, form=request.form, page_title="إضافة مورد")
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO suppliers (name,phone,email,address,notes) VALUES (%s,%s,%s,%s,%s)",
                (name, phone, email, address, notes),
            )
            supplier_id = cursor.lastrowid
            write_audit(cursor, "إضافة مورد", "supplier", supplier_id, name)
            conn.commit()
            flash("تمت إضافة المورد بنجاح.", "success")
            return redirect(url_for("suppliers"))
        except mysql.connector.Error:
            conn.rollback()
            app.logger.exception("Database error while creating supplier")
            flash("تعذر حفظ بيانات المورد.", "error")
        finally:
            cursor.close()
            conn.close()
    return render_template("supplier_form.html", supplier=None, form={}, page_title="إضافة مورد")


@app.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
@login_required
def supplier_edit(supplier_id):
    supplier = fetch_one("SELECT * FROM suppliers WHERE id=%s", (supplier_id,))
    if not supplier:
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip() or None
        email = request.form.get("email", "").strip() or None
        address = request.form.get("address", "").strip() or None
        notes = request.form.get("notes", "").strip() or None
        if not name:
            flash("اسم المورد مطلوب.", "error")
            return render_template("supplier_form.html", supplier=supplier, form=request.form, page_title="تعديل مورد")
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE suppliers SET name=%s,phone=%s,email=%s,address=%s,notes=%s WHERE id=%s",
                (name, phone, email, address, notes, supplier_id),
            )
            write_audit(cursor, "تعديل بيانات مورد", "supplier", supplier_id, name)
            conn.commit()
            flash("تم تحديث بيانات المورد.", "success")
            return redirect(url_for("suppliers"))
        except mysql.connector.Error:
            conn.rollback()
            app.logger.exception("Database error while updating supplier")
            flash("تعذر تحديث بيانات المورد.", "error")
        finally:
            cursor.close()
            conn.close()
    return render_template("supplier_form.html", supplier=supplier, form=supplier, page_title="تعديل مورد")


@app.route("/purchases")
@login_required
def purchases():
    rows = fetch_all(
        """SELECT p.id,p.purchase_number,COALESCE(NULLIF(p.supplier_name,''),'بدون مورد') AS supplier,
                  p.total_amount,p.status,p.created_at,
                  COALESCE((SELECT SUM(pi.quantity) FROM purchase_items pi WHERE pi.purchase_id=p.id),0) AS total_units
           FROM purchases p ORDER BY p.id DESC LIMIT 200"""
    )
    return render_template("purchases.html", purchases=rows)


@app.route("/purchases/new", methods=["GET", "POST"])
@login_required
def purchase_new():
    active_products = fetch_all("SELECT id,name,sku,buy_price,sell_price,stock FROM products WHERE is_active=1 ORDER BY name")
    active_suppliers = fetch_all("SELECT id,name,phone FROM suppliers ORDER BY name")
    if request.method == "POST":
        product_ids = request.form.getlist("product_id[]")
        quantities = request.form.getlist("quantity[]")
        unit_costs = request.form.getlist("unit_cost[]")
        raw_supplier_id = request.form.get("supplier_id", "").strip()
        supplier_id = None
        supplier_name = request.form.get("supplier_name", "").strip() or None
        try:
            if raw_supplier_id:
                supplier_id = int(raw_supplier_id)
                supplier = fetch_one("SELECT id,name FROM suppliers WHERE id=%s", (supplier_id,))
                if not supplier:
                    raise ValueError("المورد المحدد غير موجود.")
                supplier_name = supplier["name"]
            if not product_ids or len(product_ids) != len(quantities) or len(product_ids) != len(unit_costs):
                raise ValueError("أضف منتجًا واحدًا على الأقل إلى فاتورة الشراء.")
            lines = []
            for raw_pid, raw_qty, raw_cost in zip(product_ids, quantities, unit_costs):
                if not raw_pid:
                    continue
                try:
                    product_id = int(raw_pid)
                    quantity = int(raw_qty)
                except (ValueError, TypeError):
                    raise ValueError("اختيار المنتج والكمية لازم يكون صحيحًا.")
                if quantity <= 0:
                    raise ValueError("كمية الشراء لازم تكون أكبر من صفر.")
                unit_cost = safe_decimal(raw_cost, "سعر شراء الوحدة")
                lines.append({"product_id": product_id, "quantity": quantity, "unit_cost": unit_cost,
                              "line_total": (unit_cost * quantity).quantize(Decimal("0.01"))})
            if not lines:
                raise ValueError("اختار منتجًا واحدًا على الأقل.")
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("purchase_form.html", products=active_products, suppliers=active_suppliers, form=request.form)

        conn = None
        cursor = None
        try:
            conn = get_connection()
            conn.start_transaction()
            cursor = conn.cursor(dictionary=True)
            products_locked = {}
            for line in lines:
                pid = line["product_id"]
                if pid not in products_locked:
                    cursor.execute("SELECT id,name,buy_price,stock FROM products WHERE id=%s AND is_active=1 FOR UPDATE", (pid,))
                    locked_product = cursor.fetchone()
                    if not locked_product:
                        raise ValueError("أحد المنتجات المحددة غير موجود أو متوقف.")
                    products_locked[pid] = locked_product
            total = sum((line["line_total"] for line in lines), Decimal("0.00"))
            purchase_number = "PO-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2).upper()
            cursor.execute(
                "INSERT INTO purchases (purchase_number,supplier_id,supplier_name,total_amount,status,created_by) VALUES (%s,%s,%s,%s,'received',%s)",
                (purchase_number, supplier_id, supplier_name, total, session["user_id"]),
            )
            purchase_id = cursor.lastrowid
            incoming = {}
            for line in lines:
                p = products_locked[line["product_id"]]
                cursor.execute(
                    "INSERT INTO purchase_items (purchase_id,product_id,quantity,unit_cost,line_total) VALUES (%s,%s,%s,%s,%s)",
                    (purchase_id, line["product_id"], line["quantity"], line["unit_cost"], line["line_total"]),
                )
                pid = line["product_id"]
                if pid not in incoming:
                    incoming[pid] = {"quantity": 0, "cost": Decimal("0.00"), "product": p}
                incoming[pid]["quantity"] += line["quantity"]
                incoming[pid]["cost"] += line["line_total"]
                cursor.execute(
                    "INSERT INTO inventory_movements (product_id,movement_type,quantity_change,reference_type,reference_id,notes,created_by) VALUES (%s,'purchase',%s,'purchase',%s,%s,%s)",
                    (pid, line["quantity"], purchase_id, f"فاتورة شراء {purchase_number}", session["user_id"]),
                )
            for pid, item in incoming.items():
                p = item["product"]
                old_stock = int(p["stock"])
                old_cost = Decimal(str(p["buy_price"]))
                added_qty = item["quantity"]
                new_stock = old_stock + added_qty
                weighted_cost = ((old_cost * old_stock + item["cost"]) / new_stock).quantize(Decimal("0.01")) if new_stock else old_cost
                cursor.execute("UPDATE products SET stock=%s,buy_price=%s WHERE id=%s", (new_stock, weighted_cost, pid))
            write_audit(cursor, "تسجيل فاتورة شراء", "purchase", purchase_id, purchase_number)
            conn.commit()
            flash(f"تم تسجيل فاتورة الشراء {purchase_number} وتحديث المخزون وتكلفة الشراء.", "success")
            return redirect(url_for("purchases"))
        except ValueError as exc:
            if conn:
                conn.rollback()
            flash(str(exc), "error")
        except mysql.connector.Error:
            if conn:
                conn.rollback()
            app.logger.exception("Database error while saving purchase")
            flash("لم يتم حفظ فاتورة الشراء. لم يتم تغيير المخزون.", "error")
        finally:
            if cursor:
                cursor.close()
            if conn and conn.is_connected():
                conn.close()
    return render_template("purchase_form.html", products=active_products, suppliers=active_suppliers, form={})


@app.route("/ai-analytics")
@login_required
def ai_analytics():
    today = date.today()
    start = today - timedelta(days=29)
    raw_daily = fetch_all(
        """SELECT DATE(created_at) AS sale_day, SUM(total_amount) AS revenue,
                  SUM(profit_amount) AS gross_profit, COUNT(*) AS orders_count
           FROM sales WHERE status='completed' AND DATE(created_at) BETWEEN %s AND %s
           GROUP BY DATE(created_at) ORDER BY sale_day""",
        (start.isoformat(), today.isoformat()),
    )
    daily_map = {row["sale_day"].isoformat(): row for row in raw_daily}
    dates = [start + timedelta(days=i) for i in range(30)]
    daily_revenue = [float(daily_map.get(day.isoformat(), {}).get("revenue", 0) or 0) for day in dates]
    daily_profit = [float(daily_map.get(day.isoformat(), {}).get("gross_profit", 0) or 0) for day in dates]
    total_30 = sum(daily_revenue)
    total_profit_30 = sum(daily_profit)
    orders_30 = sum(int(daily_map.get(day.isoformat(), {}).get("orders_count", 0) or 0) for day in dates)
    last_7 = sum(daily_revenue[-7:])
    previous_7 = sum(daily_revenue[-14:-7])
    if previous_7 > 0:
        weekly_change = (last_7 - previous_7) / previous_7 * 100
    else:
        weekly_change = None

    # A transparent linear trend forecast is used so the page works without optional ML libraries.
    n = len(daily_revenue)
    mean_x = (n - 1) / 2
    mean_y = (sum(daily_revenue) / n) if n else 0
    denom = sum((x - mean_x) ** 2 for x in range(n)) or 1
    slope = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(daily_revenue)) / denom if n else 0
    forecast_dates = [today + timedelta(days=i) for i in range(1, 8)]
    forecast_values = [round(max(0.0, mean_y + slope * (n - 1 + i - mean_x)), 2) for i in range(1, 8)]
    forecast_total = sum(forecast_values)
    forecast_daily_average = forecast_total / 7 if forecast_values else 0

    best_products = fetch_all(
        """SELECT p.name, SUM(si.quantity) AS units, SUM(si.line_total) AS revenue,
                  SUM(si.line_total-si.line_cost) AS gross_profit
           FROM sale_items si JOIN sales s ON s.id=si.sale_id JOIN products p ON p.id=si.product_id
           WHERE s.status='completed' AND DATE(s.created_at) BETWEEN %s AND %s
           GROUP BY p.id,p.name ORDER BY revenue DESC LIMIT 5""",
        (start.isoformat(), today.isoformat()),
    )
    low_products = fetch_all(
        "SELECT id,name,stock,min_stock FROM products WHERE is_active=1 AND stock<=min_stock ORDER BY stock ASC,name ASC LIMIT 8"
    )
    inventory = fetch_one("SELECT COUNT(*) AS product_count, COALESCE(SUM(stock*buy_price),0) AS stock_cost FROM products WHERE is_active=1")
    recommendations = []
    if orders_30 == 0:
        recommendations.append("لا توجد فواتير بيع مكتملة خلال آخر 30 يومًا. سجّل المبيعات الفعلية أولًا حتى تصبح التحليلات والتوقعات مفيدة.")
    else:
        recommendations.append(f"إجمالي المبيعات في آخر 30 يومًا بلغ {total_30:,.2f} ج.م من {orders_30} فاتورة مكتملة.")
        if weekly_change is not None:
            if weekly_change < -5:
                recommendations.append(f"المبيعات في آخر 7 أيام انخفضت بنسبة {abs(weekly_change):.1f}% مقارنة بالأيام السبعة السابقة؛ راجع المنتجات والأسعار وحركة الطلب.")
            elif weekly_change > 5:
                recommendations.append(f"المبيعات في آخر 7 أيام ارتفعت بنسبة {weekly_change:.1f}% مقارنة بالأيام السبعة السابقة؛ راجع المخزون لتجنب نفاد المنتجات المطلوبة.")
            else:
                recommendations.append("المبيعات الأسبوعية قريبة من مستوى الأسبوع السابق، ولا يظهر تغير كبير في إجمالي الإيرادات.")
        if best_products:
            recommendations.append(f"المنتج الأعلى إيرادًا خلال الفترة هو «{best_products[0]['name']}» بإيراد {Decimal(str(best_products[0]['revenue'] or 0)):,.2f} ج.م.")
    if low_products:
        recommendations.append(f"يوجد {len(low_products)} منتجًا عند الحد الأدنى للمخزون أو أقل؛ راجع قائمة المخزون المنخفض قبل تسجيل مشتريات جديدة.")
    else:
        recommendations.append("لا توجد منتجات عند الحد الأدنى للمخزون حاليًا.")
    recommendations.append("توقع الأسبوع القادم تقدير إحصائي مبسط من اتجاه المبيعات اليومية، وليس ضمانًا للنتائج؛ تكون قراءته أفضل كلما توفرت بيانات تاريخية أكثر.")

    chart_labels = [day.isoformat() for day in dates]
    forecast_labels = [day.isoformat() for day in forecast_dates]
    return render_template(
        "ai_analytics.html", total_30=total_30, total_profit_30=total_profit_30,
        orders_30=orders_30, last_7=last_7, previous_7=previous_7,
        weekly_change=weekly_change, forecast_total=forecast_total,
        forecast_daily_average=forecast_daily_average, best_products=best_products,
        low_products=low_products, inventory=inventory, recommendations=recommendations,
        chart_labels=chart_labels, daily_revenue=daily_revenue,
        forecast_labels=forecast_labels, forecast_values=forecast_values,
    )

@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "عام").strip() or "عام"
        notes = request.form.get("notes", "").strip() or None
        expense_date = request.form.get("expense_date", date.today().isoformat())
        conn = None
        cursor = None
        try:
            if not title:
                raise ValueError("اسم المصروف مطلوب.")
            amount = safe_decimal(request.form.get("amount", ""), "المصروف")
            if amount <= 0:
                raise ValueError("قيمة المصروف لازم تكون أكبر من صفر.")
            datetime.strptime(expense_date, "%Y-%m-%d")
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO expenses (title,category,amount,notes,expense_date,created_by) VALUES (%s,%s,%s,%s,%s,%s)",
                (title, category, amount, notes, expense_date, session["user_id"]),
            )
            expense_id = cursor.lastrowid
            write_audit(cursor, "إضافة مصروف", "expense", expense_id, title)
            conn.commit()
            flash("تم تسجيل المصروف.", "success")
            return redirect(url_for("expenses"))
        except ValueError as exc:
            flash(str(exc), "error")
        except mysql.connector.Error:
            if conn:
                conn.rollback()
            app.logger.exception("Database error while creating expense")
            flash("تعذر حفظ المصروف.", "error")
        finally:
            if cursor:
                cursor.close()
            if conn and conn.is_connected():
                conn.close()
    rows = fetch_all("SELECT id,title,category,amount,notes,expense_date FROM expenses ORDER BY expense_date DESC,id DESC LIMIT 200")
    return render_template("expenses.html", expenses=rows, today=date.today().isoformat())


@app.route("/reports")
@login_required
def reports():
    start = request.args.get("start", date.today().replace(day=1).isoformat())
    end = request.args.get("end", date.today().isoformat())
    try:
        datetime.strptime(start, "%Y-%m-%d")
        datetime.strptime(end, "%Y-%m-%d")
        if start > end:
            raise ValueError
    except ValueError:
        flash("حدد فترة زمنية صحيحة.", "error")
        start = date.today().replace(day=1).isoformat()
        end = date.today().isoformat()
    summary = fetch_one(
        """SELECT COUNT(*) AS orders_count,
                  COALESCE(SUM(total_amount),0) AS sales_total,
                  COALESCE(SUM(cost_amount),0) AS cost_total,
                  COALESCE(SUM(profit_amount),0) AS gross_profit
           FROM sales WHERE status='completed' AND DATE(created_at) BETWEEN %s AND %s""",
        (start, end),
    )
    exp = fetch_one("SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE expense_date BETWEEN %s AND %s", (start, end))
    summary["expenses_total"] = exp["total"]
    summary["net_profit"] = Decimal(str(summary["gross_profit"])) - Decimal(str(summary["expenses_total"]))
    best_products = fetch_all(
        """SELECT p.name, SUM(si.quantity) AS units, SUM(si.line_total) AS revenue
           FROM sale_items si JOIN sales s ON s.id=si.sale_id JOIN products p ON p.id=si.product_id
           WHERE s.status='completed' AND DATE(s.created_at) BETWEEN %s AND %s
           GROUP BY p.id,p.name ORDER BY revenue DESC LIMIT 10""",
        (start, end),
    )
    daily = fetch_all(
        """SELECT DATE(created_at) AS day, SUM(total_amount) AS sales
           FROM sales WHERE status='completed' AND DATE(created_at) BETWEEN %s AND %s
           GROUP BY DATE(created_at) ORDER BY day""",
        (start, end),
    )
    return render_template("reports.html", start=start, end=end, summary=summary, best_products=best_products, daily=daily)


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="الصفحة المطلوبة غير موجودة."), 404


@app.errorhandler(500)
def server_error(_error):
    return render_template("error.html", code=500, message="حصل خطأ داخلي. راجع نافذة التشغيل في VS Code لمعرفة التفاصيل."), 500


if __name__ == "__main__":
    app.run(debug=False, use_reloader=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
@app.route("/expenses/<int:expense_id>/delete", methods=["POST"])
@login_required
def expense_delete(expense_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, title FROM expenses WHERE id=%s", (expense_id,))
        expense = cursor.fetchone()
        if not expense:
            flash("المصروف غير موجود.", "error")
            return redirect(url_for("expenses"))
        
        cursor.execute("DELETE FROM expenses WHERE id=%s", (expense_id,))
        write_audit(cursor, "حذف مصروف", "expense", expense_id, expense["title"])
        conn.commit()
        flash("تم حذف المصروف بنجاح.", "success")
    except mysql.connector.Error:
        conn.rollback()
        app.logger.exception("Database error while deleting expense")
        flash("تعذر حذف المصروف.", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("expenses"))
@app.route("/expenses/<int:expense_id>/delete", methods=["POST"])
@login_required
def expense_delete(expense_id):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, title FROM expenses WHERE id=%s", (expense_id,))
        expense = cursor.fetchone()
        if not expense:
            flash("المصروف غير موجود.", "error")
            return redirect(url_for("expenses"))
        
        cursor.execute("DELETE FROM expenses WHERE id=%s", (expense_id,))
        write_audit(cursor, "حذف مصروف", "expense", expense_id, expense["title"])
        conn.commit()
        flash("تم حذف المصروف بنجاح.", "success")
    except mysql.connector.Error:
        conn.rollback()
        app.logger.exception("Database error while deleting expense")
        flash("تعذر حذف المصروف.", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("expenses"))
