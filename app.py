from flask import (
    Flask, request, jsonify, session,redirect, Blueprint
)
from flask_cors import CORS
import hashlib
from datetime import datetime,timedelta
import secrets
import requests
import mysql.connector
import os
from backend.utils import token_required,get_user_id,send_email, send_basic_plan_invoice_email,send_pro_plan_invoice_email,save_log_activity,generate_reference,detect_location,save_security_activity
import jwt
from functools import wraps
import cloudinary
import cloudinary.uploader
import cloudinary.api
import os
from werkzeug.utils import secure_filename



conn = mysql.connector.connect(
        host = os.getenv("DB_HOST"),
        user =  os.getenv("DB_USER"),
        password =  os.getenv("DB_PASSWORD"),
        database =  os.getenv("DB_NAME"), 
        port =  os.getenv("DB_PORT"),
)
cursor = conn.cursor()

cloudinary.config(
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key =  os.getenv("CLOUDINARY_API_KEY"),
    api_secret = os.getenv("COULDINARY_API_SECRET")
)




app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

CORS(
    app,
    resources={r"/*": {"origins": "*"}},
    supports_credentials=True
)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax"
)

# ==========================
# CONSTANTS
# ==========================
APP_LOGO_URL = "https://res.cloudinary.com/dkb987i8w/image/upload/v1772108684/app_logo_ky1yis.png"
SECURITY_URL = "https://yourapp.com/security-settings"
DASHBOARD_URL = "https://yourapp.com/dashboard"
SECRET_KEY = os.getenv("SECRET_KEY")

@app.route("/api/save-push-token", methods=["POST"])
@token_required
def save_push(current_user_id, current_user_role):
    data = request.get_json()

    if not data or "token" not in data:
        return jsonify({
            "status": "error",
            "message": "Token is required"
        }), 400

    token = data["token"]
    print(token)

    try:
        cursor.execute(
            """
            UPDATE user_base
            SET push_token=%s
            WHERE user_id=%s
            """,
            (token, current_user_id)
        )
        conn.commit()
        return jsonify({
            "status": "success",
            "message": "Push token saved successfully"
        }), 200
    except Exception as e:
        conn.rollback()
        print(e)
        return jsonify({
            "status": "error",
            "message": f"Error: {e}"
        }), 500

@app.route("/api/dashboard", methods=["GET"])
@token_required
def dashboard(current_user_id, current_user_role):

    cursor = conn.cursor(dictionary=True, buffered=True)

    cursor.execute("""
        SELECT profilepicurl, profilename  
        FROM cust_base 
        WHERE user_id=%s
    """, (current_user_id,))
    
    cust = cursor.fetchone()
    if not cust:
        return jsonify({"error": "Customer not found"}), 404

    # Total invoices
    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM invoices
        WHERE user_id=%s
    """, (current_user_id,))
    total_invoices = cursor.fetchone()["total"]

    # Paid invoices
    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM invoices
        WHERE user_id=%s AND status=%s
    """, (current_user_id, "paid"))
    paid_invoices = cursor.fetchone()["total"]

    # Pending invoices
    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM invoices
        WHERE user_id=%s AND status=%s
    """, (current_user_id, "pending"))
    pending_invoices = cursor.fetchone()["total"]

    # Revenue
    cursor.execute("""
        SELECT COALESCE(SUM(total_amount), 0) AS revenue
        FROM invoices
        WHERE user_id=%s AND status=%s
    """, (current_user_id, "paid"))
    total_revenue = cursor.fetchone()["revenue"]

    # Settings
    cursor.execute("""
        SELECT currency, currency_symbol
        FROM user_settings
        WHERE user_id=%s
    """, (current_user_id,))
    settings = cursor.fetchone()
    if not settings:
        return jsonify({"error": "Settings not found"}), 404

    # Wallet
    cursor.execute("""
        SELECT wallet_balance
        FROM wallet_base
        WHERE user_id=%s
    """, (current_user_id,))
    wallet = cursor.fetchone()
    if not wallet:
        return jsonify({"error": "Wallet not found"}), 404

    # Get Activities
    cursor.execute(
        """
        SELECT id,title, status,created_at AS time, amount
        FROM log_activity
        WHERE user_id=%s  AND  created_at>=%s
        ORDER BY created_at DESC
        """,
        (current_user_id,datetime.now() - timedelta(days=1))
    )
    activities = cursor.fetchall()

    return jsonify({
        "status": "success",
        "user": {
            "id": current_user_id,
            "role": current_user_role
        },
        "profilename": cust["profilename"],
        "profile_picture_url": cust["profilepicurl"],
        "total_invoices": total_invoices,
        "paid_invoices": paid_invoices,
        "pending_invoices": pending_invoices,
        "total_revenue": total_revenue,
        "currency_symbol": settings["currency_symbol"],
        "wallet_balance": wallet["wallet_balance"],
        "activities": activities,
    }), 200

@app.route("/api/securitycenter", methods=["GET"])
@token_required
def security_center(current_user_id, current_user_role):
    cursor = conn.cursor(dictionary=True, buffered=True)

    cursor.execute(
        """
        SELECT auto_logout_minutes
        FROM user_settings
        WHERE user_id=%s
        """,
        (current_user_id,)
    )
    setting = cursor.fetchone()

    if not setting:
        return jsonify({
            "status": "error",
            "message": "No settings found for this user."
        }), 400 

    return jsonify({
        "status": "success",
        "user": {
            "id": current_user_id,
            "role": current_user_role
        },
        "setting": setting,
    })

@app.route("/api/view-invoices", methods=["GET"])
@token_required
def view_invoice(current_user_id, current_user_role):

    cursor = conn.cursor(dictionary=True, buffered=True)

    # Get invoices
    cursor.execute(
        """
        SELECT 
            id AS invoice_number,
            client_name,
            status,
            due_date,
            invoice_date,
            total_amount AS total
        FROM invoices
        WHERE user_id = %s
        """,
        (current_user_id,)
    )

    invoices = cursor.fetchall()

    # Get currency settings
    cursor.execute(
        """
        SELECT currency, currency_symbol,invoice_prefix
        FROM user_settings
        WHERE user_id = %s
        """,
        (current_user_id,)
    )

    settings = cursor.fetchone()

    if not settings:
        cursor.close()
        return jsonify({"error": "Settings not found"}), 404

    currency = settings["currency"]
    currency_symbol = settings["currency_symbol"]

    cursor.close()

    return jsonify({
        "status": "success",
        "user": {
            "id": current_user_id,
            "role": current_user_role
        },
        "invoices": invoices,
        "currency": currency,
        "currency_symbol": currency_symbol,
        "invoicePrefix": settings["invoice_prefix"],
        "year": datetime.now().year
    }), 200


@app.route("/api/view-draft", methods=["GET"])
@token_required
def view_draft(current_user_id, current_user_role):
    cursor = conn.cursor(dictionary=True, buffered=True)

    cursor.execute(
        """
         SELECT 
            invoice_draft.draft_id,
            invoice_draft.client_name,
            invoice_draft.client_email,
            clients.client_phone,
            clients.client_address,
            invoice_draft.created_at,
            invoice_draft.due_date,
            invoice_draft.status,
            invoice_draft.description,
            invoice_draft.quantity,
            invoice_draft.price,
            invoice_draft.subtotal,
            invoice_draft.tax,
            invoice_draft.total_amount,
            invoice_draft.amount_paid,
            invoice_draft.balance
        FROM invoice_draft
        JOIN clients ON invoice_draft.client_email = clients.client_email AND invoice_draft.user_id = clients.user_id
        WHERE invoice_draft.user_id = %s
        """,
        (current_user_id,)
    )
    drafts = cursor.fetchall()

    # Get currency settings
    cursor.execute(
        """
        SELECT currency, currency_symbol
        FROM user_settings
        WHERE user_id = %s
        """,
        (current_user_id,)
    )

    settings = cursor.fetchone()

    if not settings:
        cursor.close()
        return jsonify({"error": "Settings not found"}), 404

    currency = settings["currency"]
    currency_symbol = settings["currency_symbol"]

    cursor.close()
    
    return jsonify({
        "status": "success",
        "user": {
            "id": current_user_id,
            "role": current_user_role
        },
        "drafts": drafts,
        "currency": currency,
        "currency_symbol": currency_symbol,
    }), 200

@app.route("/api/view-clients", methods=["GET"])
@token_required
def view_clients(current_user_id, current_user_role):
    cursor = conn.cursor(dictionary=True, buffered=True)

    # ================= CLIENTS =================
    cursor.execute(
        """
        SELECT 
            client_id,
            client_name AS name,
            client_email AS email,
            client_phone AS phone,
            client_address AS address
        FROM clients
        WHERE user_id = %s
        ORDER BY client_name ASC
        """,
        (current_user_id,)
    )

    clients_raw = cursor.fetchall()

    # ================= INVOICE AGGREGATES =================
    cursor.execute(
        """
        SELECT
            client_id,
            COUNT(*) AS total_invoices,
            COALESCE(
                SUM(
                    CASE 
                        WHEN status != 'paid' THEN total_amount
                        ELSE 0
                    END
                ), 0
            ) AS outstanding_amount
        FROM invoices
        WHERE user_id = %s
        GROUP BY client_id
        """,
        (current_user_id,)
    )

    invoice_aggregates_raw = cursor.fetchall()

    # Create lookup map
    invoice_map = {
        row["client_id"]: {
            "total_invoices": row["total_invoices"],
            "outstanding": float(row["outstanding_amount"])
        }
        for row in invoice_aggregates_raw
    }

    clients = []

    for c in clients_raw:
        client_id = c["client_id"]

        invoice_data = invoice_map.get(client_id, {
            "total_invoices": 0,
            "outstanding": 0
        })

        s_name = (c["name"][:2] if c["name"] else "NA").upper()

        clients.append({
            "id": client_id,
            "name": c["name"],
            "s_name": s_name,
            "email": c["email"],
            "phone": c["phone"],
            "address": c["address"],
            "total_invoices": invoice_data["total_invoices"],
            "outstanding": invoice_data["outstanding"]
        })

    cursor.close()

    return jsonify({
        "status": "success",
        "user": {
            "id": current_user_id,
            "role": current_user_role
        },
        "clients": clients
    }), 200

@app.route("/api/view-profile", methods=["GET"])
@token_required
def view_profile(current_user_id, current_user_role):
    cursor = conn.cursor(dictionary=True, buffered=True)

    cursor.execute(
        """
        SELECT profilename, profilepicurl
        FROM cust_base
        WHERE user_id = %s
        """,
        (current_user_id,)
    )
    profile = cursor.fetchone()

    if not profile:
        cursor.close()
        return jsonify({"error": "Profile not found"}), 404
    
    profilename = profile["profilename"]
    profilepicurl= profile["profilepicurl"]

    cursor.execute(
        """
        SELECT wallet_balance
        FROM wallet_base
        WHERE user_id = %s
        """,
        (current_user_id,)
    )

    wallet = cursor.fetchone()

    wallet_balance = wallet["wallet_balance"] if wallet else 0.0    
    cursor.close()

    return jsonify({
        "status": "success",
        "user": {
            "id": current_user_id,
            "role": current_user_role
        },
        "profile_name":  profilename ,
        "profile_pic_url" : profilepicurl,
        "wallet_balance": wallet_balance,
    }), 200

@app.route("/api/settings", methods=["GET"])
@token_required
def settings_page(current_user_id, current_user_role):
    cursor = conn.cursor(dictionary=True, buffered=True)

    cursor.execute(
    """
    SELECT 
        invoice_prefix, next_invoice_number, default_due_date, default_tax_rate, show_tax, show_discount, footer_note,
        currency, currency_symbol, timezone, date_format, email_notifications, due_date_reminder, reminder_days_before,
        theme, language, auto_logout_minutes, require_pin_for_delete
    FROM user_settings
    WHERE user_id=%s
    """,
    (current_user_id,)
    )
    settings = cursor.fetchone()

    if not settings:
        return jsonify({
            "status": "error",
            "message": "An error occured while trying to fetch settings"
        }), 400

    return jsonify({
        "status": "success",
        "user": {
            "id": current_user_id,
            "role": current_user_role
        },
        "invoiceprefix": settings["invoice_prefix"],
        "nextinvoicenumber": settings["next_invoice_number"],
        "defaultduedate": settings["default_due_date"],
        "defaulttaxrate": settings["default_tax_rate"],
        "showtax": bool(settings['show_tax']),
        "showdiscount": bool(settings["show_discount"]),
        "footernote": settings['footer_note'],
        "currency": settings["currency"],
        "currencysymbol": settings['currency_symbol'],
        "timezone": settings["timezone"],
        "emailnotifications": bool(settings["email_notifications"]),
        "duedatereminder": bool(settings["due_date_reminder"]),
        "reminderdaysbefore": settings["reminder_days_before"],
        "autologoutminutes": settings["auto_logout_minutes"],
        "requirepinfordelete": bool(settings["require_pin_for_delete"]),
        "dateformate": settings["date_format"],
    }), 200


@app.route("/api/payments", methods=["GET"])
@token_required
def payment_page(current_user_id, current_user_role):
    cursor = conn.cursor(dictionary=True, buffered=True)

    # ================= TOTAL RECEIVED =================
    cursor.execute("""
        SELECT COALESCE(SUM(total_amount), 0) AS total
        FROM invoices
        WHERE user_id=%s AND status=%s
    """, (current_user_id, "paid"))
    total_received = cursor.fetchone()["total"]

    # ================= OUTSTANDING =================
    cursor.execute("""
        SELECT COALESCE(SUM(total_amount), 0) AS total
        FROM invoices
        WHERE user_id=%s AND status=%s
    """, (current_user_id, "pending"))
    outstanding = cursor.fetchone()["total"]

    # ================= OVERDUE =================
    cursor.execute("""
        SELECT COALESCE(SUM(total_amount), 0) AS total
        FROM invoices
        WHERE user_id=%s AND status=%s
    """, (current_user_id, "overdue"))
    overdue = cursor.fetchone()["total"]

    # ================= USER SETTINGS =================
    cursor.execute("""
        SELECT currency, currency_symbol, invoice_prefix
        FROM user_settings
        WHERE user_id=%s
    """, (current_user_id,))
    settings = cursor.fetchone()

    if not settings:
        return jsonify({"error": "Settings not found"}), 404

    currency = settings["currency"]
    currency_symbol = settings["currency_symbol"]
    invoice_prefix = settings["invoice_prefix"]

    # ================= ALL INVOICES =================
    cursor.execute("""
        SELECT 
            id,
            client_name AS client,
            DATE_FORMAT(invoice_date, '%Y-%m-%d') AS date,
            total_amount AS amount,
            status
        FROM invoices
        WHERE user_id=%s
        ORDER BY invoice_date DESC
    """, (current_user_id,))
    invoices = cursor.fetchall()

    return jsonify({
        "status": "success",
        "user": {
            "id": current_user_id,
            "role": current_user_role
        },
        "totalReceived": total_received,   # NUMBER
        "outstanding": outstanding,       # NUMBER
        "overdue": overdue,               # NUMBER
        "currency": currency_symbol,
        "invoiceprefix": invoice_prefix,
        "invoices": invoices
    })

@app.route("/api/profile", methods=["GET"])
@token_required
def get_profile(current_user_id, current_user_role):
    cursor = conn.cursor(dictionary=True,buffered=True)

    cursor.execute(
        """
        SELECT 
            cust_base.fullname,
            cust_base.profilename,
            cust_base.address,
            cust_base.country,
            cust_base.phone,
            cust_base.alternateemail,
            cust_base.website,
            cust_base.profilepicurl, 
            cust_base.bio, 
            cust_base.companylogourl,
            user_base.username      
        FROM cust_base 
        JOIN user_base ON cust_base.user_id = user_base.user_id
        WHERE cust_base.user_id=%s
        """,
        (current_user_id,)
    )
    profile = cursor.fetchone()
    if not profile:
        return jsonify({
            "status": "error",
            "message": "Profile Not found."
        })
    
    fullname,profilename,address,country,phone,alternateemail,website,profilepicurl, bio, companylogourl, username = profile
    print(profilepicurl)

    return jsonify({
        "status": "success",
        "user": {
            "id": current_user_id,
            "role": current_user_role
        },
        "fullname":profile["fullname"],
        "profilename": profile["profilename"],
        "address":profile["address"],
        "country": profile["country"],
        "phone": profile["phone"],
        "website": profile["website"],
        "alternateemail": profile["alternateemail"],
        "Profile_image": profile["profilepicurl"],
        "bio": profile["bio"],
        "username": profile["username"],
        "company_logo": profile["companylogourl"],
    })

@app.route("/api/transactions", methods=["GET"])
@token_required
def transation_page(current_user_id,current_user_role):
    
    cursor = conn.cursor(dictionary=True,buffered=True)
    cursor.execute(
        """
        SELECT id, type, amount, reference AS title, status, paid_at AS date
        FROM transactions
        WHERE user_id=%s
        """,
        (current_user_id,)
    )
    transactions= cursor.fetchall()

    return jsonify({
        "status": "success",
        "user": {
            "id": current_user_id,
            "role": current_user_role
        },
        "transactions": transactions,
    })


@app.route("/api/drafts/<int:draft_id>", methods=["GET"])
@token_required
def full_drafts(current_user_id, current_user_role, draft_id):
    cursor = conn.cursor(dictionary=True, buffered=True)

    cursor.execute(
        """
        SELECT 
            client_email,
            due_date,
            invoice_date,
            description,
            quantity,
            price,
            tax,
            amount_paid
        FROM invoice_draft
        WHERE user_id =%s AND  draft_id=%s
        """,
        (current_user_id, draft_id)
    )
    draft = cursor.fetchone()

    return jsonify({
        "status": "success",
        "user": {
            "id": current_user_id,
            "role": current_user_role
        },
        "client_email": draft["client_email"],
        "due_date": draft["due_date"],
        "invoice_date": draft["invoice_date"],
        "tax": draft["tax"],
        "amount_paid": draft["amount_paid"],
        "items": [
            draft["description"],
            draft["price"],
            draft["quantity"]
        ],
    })


@app.route("/api/clients/<int:client_id>", methods=["GET"])
@token_required
def full_client(current_user_id,current_user_role,client_id):
    cursor = conn.cursor(dictionary=True, buffered=True)

    cursor.execute(
        """
        SELECT 
            client_email AS email,
            client_name AS name,
            client_phone AS phone,
            client_address AS address
        FROM clients
        WHERE user_id=%s AND client_id=%s
        """,
        (current_user_id,client_id)
    )
    client = cursor.fetchone()

    if not client:
        return jsonify({
            "status": "error",
            "message": "Client Not Found."
        })

    return jsonify({
        "status": "success",
        "user": {
            "id": current_user_id,
            "role": current_user_role
        },
        "client": client,
    })


@app.route("/api/client/<int:client_id>", methods=["GET"])
@token_required
def view_single_client(current_user_id, current_user_role, client_id):
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT client_id, client_name, client_email,
               client_phone, client_address
        FROM clients
        WHERE client_id = %s AND user_id = %s
    """, (client_id, current_user_id))

    client = cursor.fetchone()

    if not client:
        return jsonify({"status": "error", "message": "Client not found"}), 404

    # Fetch invoices for this client
    cursor.execute("""
        SELECT id, status, total_amount, due_date
        FROM invoices
        WHERE client_id = %s
        ORDER BY id DESC
    """, (client_id,))

    invoices = cursor.fetchall()

    cursor.close()

    return jsonify({
        "status": "success",
        "client": client,
        "invoices": invoices
    }), 200

@app.route("/api/notifications", methods=["GET"])
@token_required
def get_notifications(current_user_id, current_user_role):
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT id, title, message, type, is_read, created_at
        FROM notifications
        WHERE user_id = %s
        ORDER BY created_at DESC
    """, (current_user_id,))

    notifications = cursor.fetchall()
    cursor.close()

    return jsonify({
        "status": "success",
        "notifications": notifications
    }), 200

import requests

def send_push_notification(expo_token, title, message):
    requests.post(
        "https://exp.host/--/api/v2/push/send",
        json={
            "to": expo_token,
            "title": title,
            "body": message,
        },
    )

@app.route("/api/notifications/<int:notif_id>/read", methods=["PUT"])
@token_required
def mark_notification_read(current_user_id, current_user_role, notif_id):
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE notifications
        SET is_read = TRUE
        WHERE id = %s AND user_id = %s
    """, (notif_id, current_user_id))

    conn.commit()
    cursor.close()

    return jsonify({"status": "success"}), 200

@app.route("/api/cust", methods=["POST"])
def create_profile():
    data = request.get_json()

    if not data:
        return jsonify({
            "status": "error",
            "message": "Invalid or missing JSON"
        }), 400

    required_fields = [
        "username",
        "profile_name",
        "full_name",
        "address",
        "country",
        "currency",
        "dob",
    ]

    # GET USER ID FOR INDEXING
    user_id = get_user_id(data['username'])
    if not user_id:
        return jsonify({
        "status": "error",
        "message": "User not found"
        }), 404

    # lOAD DATA FROM DATABASE TO ENSURE NO DUPLICATES
    cursor.execute("SELECT profilename FROM cust_base")
    existing_profiles = {row[0] for row in cursor.fetchall()}
    if data["profile_name"] in existing_profiles:
        return jsonify({
            "status": "error",
            "message": "Profile name already exists"
        }), 400
    
    # Validate required fields
    for field in required_fields:
        if not data.get(field):
            return jsonify({
                "status": "error",
                "message": f"Missing field: {field}"
            }), 400


    try:
        cursor.execute("""
            INSERT INTO cust_base
            (user_id,profilename, fullname, address, country, currency, dob)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id,
            data["profile_name"],
            data["full_name"],
            data["address"],
            data["country"],
            data["currency"],
            data["dob"]
            
        ))

        conn.commit()

        return jsonify({
            "status": "success",
            "message": "Profile created successfully"
        }), 201

    except Exception as e:
        conn.rollback()
        print(e)
        return jsonify({
            "status": "error",
            "message": "Database error",
            "details": str(e)
        }), 500


@app.route("/api/user", methods=["POST"])
def create_user():
    data = request.get_json()

    if not data:
        return jsonify({
            "status": "error",
            "message": "Invalid or missing JSON"
        }), 400

    required_fields = [
        "username",
        "email",
        "password",
        "security_question",
        "security_answer"
    ]

    # Validate required fields FIRST
    for field in required_fields:
        if not data.get(field):
            return jsonify({
                "status": "error",
                "message": f"Missing field: {field}"
            }), 400

    try:
        # Check duplicate username properly
        cursor.execute(
            "SELECT 1 FROM user_base WHERE username = %s",
            (data["username"],)
        )
        if cursor.fetchone():
            return jsonify({
                "status": "error",
                "message": "Username already exists"
            }), 400

        # Insert user
        cursor.execute("""
            INSERT INTO user_base
            (username, email, password_hash, sequrity_question, sequrity_answer_hash,
             failed_attempts, last_login, last_failed_login, trial_ends_at,
             locked, lock_reason, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            data["username"],
            data["email"],
            hashlib.sha256(data["password"].encode()).hexdigest(),
            data["security_question"],
            hashlib.sha256(data["security_answer"].encode()).hexdigest(),
            0,
            None,
            None,
            (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"),
            False,
            "",
            True
        ))

        conn.commit()

        code = secrets.token_hex(3)
        session['email_code'] = code
        send_email(
            data['email'],
            "Business Essential - Verify Your Email",
            f'Your verification code is {code}',
            html=False
        )

        return jsonify({
            "status": "success",
            "message": "User created successfully"
        }), 201

    except Exception as e:
        conn.rollback()
        print(e)
        return jsonify({
            "status": "error",
            "message": f"Database error {e}"
        }), 500

@app.route("/api/verify", methods=["POST"])
def verify_user():
    data = request.get_json()

    if not data:
        return jsonify({
            "status": "error",
            "message": "Invalid or missing JSON"
        }), 400

    required_fields = [
       "entered_code"
    ]

    # Validate required fields
    for field in required_fields:
        if not data.get(field):
            return jsonify({
                "status": "error",
                "message": f"Missing field: {field}"
            }), 400

    genereted_code = session.get("email_code")
    # Here you would normally check the verification code against what was sent/stored
    if genereted_code != data["entered_code"]:
        return jsonify({
            "status": "error",
            "message": "Invalid verification code"
        }), 400
    
    session.clear()
    return jsonify({
        "status": "success",
        "message": "User verified successfully"
    }), 200


@app.route("/api/pin", methods=["POST"])
def add_pin():
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "status": "error",
                "message": "Invalid or missing JSON"
            }), 400

        required_fields = [
           "AppPin",
            "ConfirmAppPin",
            "username",
        ]

        # Validate required fields
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    "status": "error",
                    "message": f"Missing field: {field}"
                }), 400
            
        user_id = get_user_id(data['username'])
        if not user_id:
            return jsonify({
            "status": "error",
            "message": "User not found"
            }), 404

        if data["AppPin"] != data["ConfirmAppPin"]:
            return jsonify({
            "status": "error",
            "message": "Pin didn't match each other."
            }), 404

        apppin = hashlib.sha256(data["AppPin"].encode()).hexdigest()
  
        cursor.execute(
            """
            UPDATE user_base
            SET app_pin=%s
            WHERE user_id=%s
            """,
            (apppin, user_id)
        )
        conn.commit()

        return jsonify({
            "status": "success",
            "message": "App Pin Added"
        }), 200
    except Exception as e:
        conn.rollback()
        print(e)
        return jsonify({
            "status": "error",
            "message": "Database error",
            "details": str(e)
        }), 500
        
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/api/completecust", methods=["POST"])
def complete_cust():
    # Since we are sending FormData, use request.form and request.files
    form = request.form
    file = request.files.get("profile_picture")

    # Required fields
    required_fields = [
        "username",
        "email",
        "profile_name",
        "phone_number",
        "alternate_email",
        "website",
        "bio"
    ]

    # Validate required fields
    for field in required_fields:
        if not form.get(field):
            return jsonify({
                "status": "error",
                "message": f"Missing field: {field}"
            }), 400

    username = form.get("username")
    user_id = get_user_id(username)  # Assuming this function exists


    # Example saving file
    file = request.files.get("profile_picture")  # Make sure your input type="file"
    if file:
        filename = secure_filename(f"{user_id}_{file.filename}")  # safe filename
        result = cloudinary.uploader.upload(
            file,
            folder="profile_images",
            transformation = [
                {"width":300, "height":300, "crop":"fill"}
            ],
            public_id = f"user_{user_id}",
            overwrite= True
        )
        save_path = result['secure_url']

    try:
        cursor.execute("""
            UPDATE cust_base
            SET phone=%s,
                alternateemail=%s,
                website=%s,
                profilepicurl=%s,
                bio=%s
            WHERE profilename=%s AND user_id=%s
        """, (
            form.get("phone_number"),
            form.get("alternate_email"),
            form.get("website"),
            save_path,
            form.get("bio"),
            form.get("profile_name"),
            user_id
        ))

        cursor.execute(
            """
            INSERT INTO user_settings (user_id, footer_note
            )
            VALUES (%s, %s)
            """,
            (
                user_id,
                "Thanks for doing business with us."
            )
        )

        cursor.execute(
            """
            INSERT INTO wallet_base (user_id, date_created)
            VALUES(%s,%s)
            """,
            (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )

        conn.commit()

        # welcome html
        first_name = form['profile_name']
        year = datetime.now().year
        welcome_html = f"""

<body style="margin:0; padding:0; background-color:#f4f6f8; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;">

  <table width="100%" cellpadding="0" cellspacing="0" style="padding:40px 0;">
    <tr>
      <td align="center">


    <!-- Card -->
    <table width="100%" cellpadding="0" cellspacing="0"
      style="max-width:600px; background:#ffffff; border-radius:14px; box-shadow:0 10px 30px rgba(0,0,0,0.08); overflow:hidden;">

      <!-- Header -->
      <tr>
        <td style="background:linear-gradient(135deg, #2563eb, #1e40af); padding:28px; text-align:center;">
          <img src="{APP_LOGO_URL}" alt="Business Essential Logo" width="56" height="56"
            style="display:block; margin:0 auto 10px;" />
          <h1 style="margin:0; font-size:22px; color:#ffffff;">Welcome to Business Essential 🎉</h1>
          <p style="margin:6px 0 0; font-size:14px; color:#dbeafe;">
            Simple • Secure • Professional Invoicing
          </p>
        </td>
      </tr>

      <!-- Body -->
      <tr>
        <td style="padding:36px; color:#111827;">
          <h2 style="margin-top:0; font-size:24px;">
            Hi {first_name},
          </h2>

          <p style="font-size:15px; line-height:1.7;">
            Welcome aboard! We’re excited to have you join <strong>Business Essential</strong>.
            Your account has been successfully created, and you’re now ready to start managing
            invoices, customers, and payments with ease.
          </p>

          <!-- Feature List -->
          <table width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0;">
            <tr>
              <td style="font-size:15px; line-height:1.8;">
                ✅ Create and manage professional invoices<br />
                ✅ Track payments and customer activity<br />
                ✅ Secure your account with built-in protections<br />
                ✅ Access your data anytime, anywhere
              </td>
            </tr>
          </table>

          <!-- CTA -->
          <table width="100%" cellpadding="0" cellspacing="0" style="margin:32px 0;">
            <tr>
              <td align="center">
                <a href="{DASHBOARD_URL}"
                  style="background:#2563eb; color:#ffffff; text-decoration:none;
                         padding:14px 26px; border-radius:10px;
                         font-size:15px; font-weight:600; display:inline-block;">
                  Go to Dashboard
                </a>
              </td>
            </tr>
          </table>

          <p style="font-size:15px; line-height:1.7;">
            If you ever need help, our support team is always here to assist you.
            We recommend starting by completing your profile and creating your first invoice.
          </p>

          <p style="font-size:15px; line-height:1.7;">
            We’re glad you’re here — let’s build something great together 🚀
          </p>

          <p style="margin-top:32px; font-size:14px; color:#374151;">
            Warm regards,<br />
            <strong>The Business Essential Team</strong>
          </p>
        </td>
      </tr>

      <!-- Footer -->
      <tr>
        <td style="background:#f9fafb; padding:18px; text-align:center; font-size:12px; color:#6b7280;">
          You’re receiving this email because you created an Business Essential account.<br />
          © {year} Business Essential. All rights reserved.
        </td>
      </tr>

    </table>

  </td>
</tr>


  </table>

</body>

"""
        send_email(
            recipient=form["email"],
            subject="Welcome to Business Essential 🎉",
            body=welcome_html,
            html=True
        )

        return jsonify({
            "status": "success",
            "message": "Customer profile completed successfully"
        }), 200

    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "message": "Database error",
            "details": str(e)
        }), 500




    
@app.route("/api/resend", methods=["POST"])
def resend_verification():
    data = request.get_json()

    if not data:
        return jsonify({
            "status": "error",
            "message": "Invalid or missing JSON"
        }), 400

    required_fields = [
        "email",
        "verification_code"
    ]

    for field in required_fields:
        if not data.get(field):
            return jsonify({
                "status": "error",
                "message": f"Missing field: {field}"
            }), 400

    send_email(
        recipient=data["email"],
        subject="Verification Code Resent",
        body=f"Here is your verification code: {data['verification_code']}",
        html=False
    )

    return jsonify({
        "status": "success",
        "message": "Verification code resent successfully"
    }), 200


@app.route("/loginp", methods=["POST"])
def verifylogin():
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "status": "error",
                "message": "Invalid JSON"
            }), 400

        username = data.get("username")
        password = data.get("password")

        if not username or not password:
            return jsonify({
                "status": "error",
                "message": "Username and password required"
            }), 400

  

        cursor = conn.cursor(dictionary=True, buffered=True)

        cursor.execute("""
            SELECT user_id, password_hash, locked, 
                   failed_attempts, email, lock_reason, trial_ends_at, email, role
            FROM user_base
            WHERE username=%s
            LIMIT 1
        """, (username,))
        
        user = cursor.fetchone()

        if not user:
            return jsonify({
                "status": "error",
                "message": "User not found"
            }), 404

        user_id = user["user_id"]
        if user["locked"]:
            save_security_activity(
                user_id=user_id,
                type_="Login",
                title="Login Failed",
                description="Login failed. Account locked!",
                severity="MEDIUM",
                ip_address=get_client_ip(request)
            )
            return jsonify({
                "status": "error",
                "message": f"Account locked: {user['lock_reason']}"
            }), 403

   
        hashed = hashlib.sha256(password.encode()).hexdigest()

        if hashed != user["password_hash"]:
            new_attempts = user["failed_attempts"] + 1

            cursor.execute("""
                UPDATE user_base
                SET failed_attempts=%s,
                    last_failed_login=NOW()
                WHERE user_id=%s
            """, (new_attempts, user["user_id"]))

            if new_attempts >= 3:
                cursor.execute("""
                    UPDATE user_base
                    SET locked=1,
                        lock_reason=%s
                    WHERE user_id=%s
                """, ("Too many failed login attempts", user["user_id"]))
                save_security_activity(
                    user_id=user_id,
                    type_="Login",
                    title="Login Failed",
                    description=f"Login failed. Account locked,Too many failed login attempts",
                    severity="HIGH",
                    ip_address=get_client_ip(request)
                )

            conn.commit()

            save_security_activity(
                user_id=user_id,
                type_="Login",
                title="Login Failed",
                description=f"Login failed. Incorrect Password, attempts({new_attempts})",
                severity="MEDIUM",
                ip_address=get_client_ip(request)
            )
            return jsonify({
                "status": "error",
                "message": "Incorrect password"
            }), 401


        cursor.execute("""
            UPDATE user_base
            SET failed_attempts=0,
                last_login=NOW()
            WHERE user_id=%s
        """, (user["user_id"],))


        cursor.execute("""
            SELECT wallet_id
            FROM wallet_base
            WHERE user_id=%s
            LIMIT 1
        """, (user["user_id"],))

        wallet = cursor.fetchone()

        if not wallet:
            cursor.execute("""
                INSERT INTO wallet_base (user_id, date_created)
                VALUES (%s, NOW())
            """, (user["user_id"],))


        if not user["trial_ends_at"]:
            cursor.execute("""
                UPDATE user_base
                SET trial_ends_at=%s
                WHERE user_id=%s
            """, (
                datetime.utcnow() + timedelta(days=30),
                user["user_id"]
            ))

        conn.commit()


        payload = {
            "user_id": user["user_id"],
            "role": user["role"],
            "exp": datetime.utcnow() + timedelta(hours=24)
        }

        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")

 
        def get_location_from_ip(ip):
            try:
                response = requests.get(f"https://ipinfo.io/{ip}/json", timeout=5)
                data = response.json()

                city = data.get("city", "Unknown City")
                region = data.get("region", "Unknown Region")
                country = data.get("country", "Unknown Country")
                return city, state, country
            except Exception:
                return "Unknown City", "Unknown Region", "Unknown Country"

        deviceinfo = data['device'] 
        brand = deviceinfo["brand"]
        modelName = deviceinfo["modelName"]
        osName = deviceinfo["osName"]
        osVersion = deviceinfo["osVersion"]
      

        def get_client_ip(request):
            if request.headers.get("X-Forwarded-For"):
                return request.headers.get("X-Forwarded-For").split(",")[0]
            return request.remote_addr
    

        login_ip = get_client_ip(request)
        country, state, city = get_location_from_ip(login_ip)
        year = datetime.now().year

        login_html = f"""

<body style="margin:0; padding:0; background-color:#f4f6f8; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;">

  <table width="100%" cellpadding="0" cellspacing="0" style="padding:40px 0;">
    <tr>
      <td align="center">


    <!-- Main Card -->
    <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px; background:#ffffff; border-radius:12px; box-shadow:0 8px 24px rgba(0,0,0,0.08); overflow:hidden;">

      <!-- Header -->
      <tr>
        <td style="background:#111827; padding:24px; text-align:center;">
          <img src="{APP_LOGO_URL}" alt="Business Essential Logo" width="48" height="48" style="display:block; margin:0 auto 8px;" />
          <h1 style="color:#ffffff; font-size:20px; margin:0;">Business Essential</h1>
          <p style="color:#9ca3af; margin:4px 0 0; font-size:14px;">Security Notification</p>
        </td>
      </tr>

      <!-- Content -->
      <tr>
        <td style="padding:32px; color:#111827;">
          <h2 style="margin-top:0; font-size:22px;">New Sign-In Detected</h2>

          <p style="font-size:15px; line-height:1.6;">
            We noticed a new sign-in to your Invoice App account.  
            For your security, we’re letting you know whenever your account is accessed from a new device or location.
          </p>

          <!-- Details Box -->
          <table width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0; background:#f9fafb; border-radius:8px; padding:16px;">
            <tr>
              <td style="font-size:14px; line-height:1.8;">
                <strong>Login details</strong><br />
                <strong>IP Address:</strong> {login_ip}<br />
                <strong>Location:</strong> {city}, {state}, {country}<br />
                <strong>Date & Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br />
                <strong>Device:</strong> {brand} {modelName} ({osName} {osVersion})
              </td>
            </tr>
          </table>

          <p style="font-size:15px; line-height:1.6;">
            <strong>Was this you?</strong><br />
            If you recognize this activity, no action is required. You can safely ignore this message.
          </p>

          <p style="font-size:15px; line-height:1.6;">
            <strong>Was this not you?</strong><br />
            If you do not recognize this sign-in, we strongly recommend taking action immediately to protect your account:
          </p>

          <ul style="font-size:15px; line-height:1.6; padding-left:20px;">
            <li>Change your account password</li>
            <li>Review recent account activity</li>
            <li>Update your security questions or recovery details</li>
          </ul>

          <!-- CTA Button -->
          <table cellpadding="0" cellspacing="0" style="margin:28px 0;">
            <tr>
              <td align="center">
                <a href="{SECURITY_URL}" style="background:#2563eb; color:#ffffff; text-decoration:none; padding:12px 20px; border-radius:8px; font-weight:600; display:inline-block;">
                  Secure My Account
                </a>
              </td>
            </tr>
          </table>

          <p style="font-size:14px; color:#374151; line-height:1.6;">
            If you believe your account has been compromised or need assistance, please contact our support team immediately.
          </p>

          <p style="font-size:14px; color:#6b7280; margin-top:32px;">
            Thank you for helping us keep your account secure,<br />
            <strong>The Business Essential Security Team</strong>
          </p>
        </td>
      </tr>

      <!-- Footer -->
      <tr>
        <td style="background:#f9fafb; padding:16px; text-align:center; font-size:12px; color:#6b7280;">
          This is an automated security message. Please do not reply.<br />
          © {year} Business Essential. All rights reserved.
        </td>
      </tr>

    </table>

  </td>
</tr>


  </table>

</body>


        """
        
      

        send_email(
            recipient=user["email"],
            subject="New Sign-In Detected — Business Essential",
            body=login_html,
            html=True
        )


        save_security_activity(
            user_id=user_id,
            type_="account",
            title="User Login",
            description=f"A login into this app was noticed on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.",
            severity="LOW",
            ip_address=login_ip
        )
        return jsonify({
            "status": "success",
            "message": "Login successful",
            "token": token,
            "user_id": user["user_id"]
        }), 200

    except Exception as e:
        conn.rollback()
        print(e)
        return jsonify({
            "status": "error",
            "message": "Database error",
            "details": str(e)
        }), 500



@app.route("/api/resetpass", methods=["POST"])
def reset():
    data = request.get_json()
    print("RESET PASS HIT")

    required_fields = ["email", "security_question", "security_answer"]
    for field in required_fields:
        if not data.get(field):
            return jsonify({"status": "error", "message": f"Missing {field}"}), 400

    try:
        cursor.execute(
            """
            SELECT sequrity_question, sequrity_answer_hash, email
            FROM user_base
            WHERE email=%s
            """,
            (data['email'],)
        )
        user = cursor.fetchone()

        if not user:
            return jsonify({"status": "error", "message": "User not found"}), 404

        question, answer_hash, email = user
        incoming_answer_hash = hashlib.sha256(
            data['security_answer'].encode()
        ).hexdigest()
        incoming_question = data['security_question']

        if incoming_question != question or incoming_answer_hash != answer_hash:
            return jsonify({"status": "error", "message": "Invalid security details"}), 400

        reset_code = secrets.token_hex(3)
        reset_code_hash = hashlib.sha256(reset_code.encode()).hexdigest()

        reset_code_expires = datetime.utcnow() + timedelta(minutes=10)

        cursor.execute(
            "UPDATE user_base SET reset_code_hash=%s, reset_code_expires=%s WHERE email=%s",
            (reset_code_hash,reset_code_expires, email)
        )
        conn.commit()
        reset_password_html = f"""
<body style="margin:0; padding:0; background-color:#f4f6f8; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="padding:40px 0;">
        <tr>
            <td align="center">
                <table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px; background:#ffffff; border-radius:10px; box-shadow:0 4px 12px rgba(0,0,0,0.08); overflow:hidden;">
                    
                    <!-- Header -->
                    <tr>
                        <td style="background:#1558B0; padding:20px; text-align:center;">
                            <h2 style="margin:0; color:#ffffff; font-weight:600;">
                                Business Essential
                            </h2>
                        </td>
                    </tr>

                    <!-- Body -->
                    <tr>
                        <td style="padding:30px;">
                            <h3 style="margin-top:0; color:#333333;">
                                Reset Your Password
                            </h3>

                            <p style="color:#555555; font-size:15px; line-height:1.6;">
                                We received a request to reset your password.  
                                If you didn’t make this request, you can safely ignore this email.
                            </p>

                            <p style="color:#555555; font-size:15px; line-height:1.6;">
                                Use the verification code below to reset your password:
                            </p>

                            <!-- Code box -->
                            <div style="text-align:center; margin:25px 0;">
                                <span style="display:inline-block; padding:14px 24px; font-size:20px; letter-spacing:3px; background:#f1f5ff; color:#1558B0; border-radius:6px; font-weight:600;">
                                    {reset_code}
                                </span>
                            </div>

                            <p style="color:#777777; font-size:14px; line-height:1.6;">
                                This code will expire in 10 minutes.
                            </p>

                            <p style="color:#555555; font-size:14px; line-height:1.6;">
                                Need help? Contact our support team.
                            </p>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background:#f4f6f8; padding:16px; text-align:center;">
                            <p style="margin:0; color:#888888; font-size:13px;">
                                © {datetime.now().year} Business Essential. All rights reserved.
                            </p>
                        </td>
                    </tr>

                </table>
            </td>
        </tr>
    </table>
</body>
"""


        send_email(
            recipient=email,
            subject="Business Essential - Password Reset Code",
            body=reset_password_html,
            html=True
        )

        return jsonify({
            "status": "success",
            "message": "Reset code sent to email"
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": "Server error",
            "details": str(e)
        }), 500


    
@app.route("/api/save-password", methods=["POST"])
def savepassword():
    data = request.get_json()

    if not data:
        return jsonify({
            "status": "error",
            "message": "Invalid or missing JSON"
        }), 400

    required_fields = ["email", "entered_code", "password","confirmpassword"]
    for field in required_fields:
        if not data.get(field):
            return jsonify({
                "status": "error",
                "message": f"Missing field: {field}"
            }), 400
        
    if data['password'] != data['confirmpassword']:
        return jsonify({
            "status": "error",
            "message": "Password doesn't match."
        }), 400

    try:
        cursor.execute(
            """
            SELECT reset_code_hash, reset_code_expires, email
            FROM user_base
            WHERE email=%s
            """,
            (data["email"],)
        )
        user = cursor.fetchone()

        if not user:
            return jsonify({
                "status": "error",
                "message": "User not found"
            }), 404

        stored_hash, expires_at, email = user

        if not stored_hash or not expires_at:
            return jsonify({
                "status": "error",
                "message": "No active reset request"
            }), 400
        


        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)



        if datetime.utcnow() > expires_at:
            return jsonify({
                "status": "error",
                "message": "Reset code expired"
            }), 400

        entered_hash = hashlib.sha256(
            data["entered_code"].encode()
        ).hexdigest()
        if entered_hash != stored_hash:
            return jsonify({
                "status": "error",
                "message": "Invalid reset code"
            }), 400

        new_password_hash = hashlib.sha256(
            data["password"].encode()
        ).hexdigest()

        cursor.execute(
            """
            UPDATE user_base
            SET password_hash=%s,
                reset_code_hash=NULL,
                reset_code_expires=NULL,
                locked=0
            WHERE username=%s
            """,
            (new_password_hash, data["username"])
        )
        conn.commit()

        # Email Notification
        password_reset_success_html = f"""
<body style="margin:0; padding:0; background-color:#f4f6f8; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="padding:40px 0;">
        <tr>
            <td align="center">
                <table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px; background:#ffffff; border-radius:10px; box-shadow:0 4px 12px rgba(0,0,0,0.08); overflow:hidden;">
                    
                    <!-- Header -->
                    <tr>
                        <td style="background:#1aa251; padding:20px; text-align:center;">
                            <h2 style="margin:0; color:#ffffff; font-weight:600;">
                                Business Essential
                            </h2>
                        </td>
                    </tr>

                    <!-- Body -->
                    <tr>
                        <td style="padding:30px;">
                            <h3 style="margin-top:0; color:#333333;">
                                Password Reset Successful 🎉
                            </h3>

                            <p style="color:#555555; font-size:15px; line-height:1.6;">
                                Your password has been successfully reset.
                            </p>

                            <p style="color:#555555; font-size:15px; line-height:1.6;">
                                You can now log in to your account using your new password.
                            </p>

                            <!-- Login Button -->
                            <div style="text-align:center; margin:30px 0;">
                                <a href="{{LOGIN_URL}}"
                                   style="display:inline-block; padding:12px 26px; background:#1558B0; color:#ffffff; text-decoration:none; border-radius:6px; font-weight:500; font-size:15px;">
                                    Go to Login
                                </a>
                            </div>

                            <p style="color:#777777; font-size:14px; line-height:1.6;">
                                If you did not perform this action, please contact support immediately.
                            </p>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background:#f4f6f8; padding:16px; text-align:center;">
                            <p style="margin:0; color:#888888; font-size:13px;">
                                © {datetime.now().year} Business Essential. All rights reserved.
                            </p>
                        </td>
                    </tr>

                </table>
            </td>
        </tr>
    </table>
</body>
"""
        send_email(
            recipient=email,
            subject="Business Essential - Password Reset Successful",
            body=password_reset_success_html,
            html=True
        )


        return jsonify({
            "status": "success",
            "message": "Password updated successfully"
        }), 200

    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "message": "Database error",
            "details": str(e)
        }), 500



@app.route("/api/create-invoice", methods=["POST"])
@token_required
def create_invoice(current_user_id, current_user_role):
    data = request.get_json(force=True) 
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

   

    client_name = data.get("client_name")
    client_email = data.get("client_email")
    invoice_date = data.get("invoice_date")
    due_date = data.get("due_date")
    items = data.get("items", [])
    notes = data.get("notes", "")
    subtotal = float(data.get("subtotal", 0))
    tax = float(data.get("tax", 0))
    total = float(data.get("total", 0))
    amount_paid = float(data.get("amount_paid", 0))

    # Validation
    if not all([client_name, client_email, invoice_date, due_date]):
        return jsonify({"status": "error", "message": "Missing required fields"}), 400

    # Calculate balance & status
    balance = max(total - amount_paid, 0)

    if balance <= 0:
        status = "paid"
    elif amount_paid > 0:
        status = "pending"
    else:
        status = "unpaid"

    try:
        # Verify user
        cursor.execute(
            "SELECT username, plan, trial_ends_at FROM user_base WHERE user_id=%s",
            (current_user_id,)
        )
        user_info = cursor.fetchone()
        if not user_info:
            return jsonify({"status": "error", "message": "User not found"}), 404
        
        # Feth total invoice
        cursor.execute("""
            SELECT COUNT(*)
            FROM invoices
            WHERE user_id=%s
        """, (current_user_id,))
        total_invoices = cursor.fetchone()[0]
        
        if user_info[1] == "trial":
            if total_invoices >= 30:
                return jsonify({
                    "status": "error",
                    "message": "Trial period has ended. Please upgrade your plan."
                }), 400

        # Find or create client (email-based)
        cursor.execute(
            "SELECT client_id FROM clients WHERE user_id=%s AND client_email=%s",
            (current_user_id, client_email)
        )
        client = cursor.fetchone()

        if client:
            client_id = client[0]
        else:
            cursor.execute(
                """
                INSERT INTO clients (user_id, client_name, client_email)
                VALUES (%s, %s, %s)
                """,
                (current_user_id, client_name, client_email)
            )
            client_id = cursor.lastrowid

        # Insert invoice
        cursor.execute(
            """
            INSERT INTO invoices (
                user_id,
                client_id,
                client_name,
                client_email,
                subtotal,
                tax,
                invoice_date,
                due_date,
                notes,
                total_amount,
                amount_paid,
                balance,
                status
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                current_user_id,
                client_id,
                client_name,
                client_email,
                subtotal,
                tax,
                invoice_date,
                due_date,
                notes,
                total,
                amount_paid,
                balance,
                status
            )
        )
        invoice_id = cursor.lastrowid

        # Insert items
        for item in items:
            if not item.get("description"):
                continue

            cursor.execute(
                """
                INSERT INTO invoice_items (invoice_id, description, quantity, price)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    invoice_id,
                    item.get("description"),
                    item.get("quantity", 1),
                    item.get("price", 0)
                )
            )

      

        # Get Invoice prefix
        cursor.execute(
            """
            SELECT invoice_prefix
            FROM user_settings
            WHERE user_id=%s
            """,
            (current_user_id,)
        )
        invoice_prefix= cursor.fetchone()[0]

        # record to transaction 
        reference = generate_reference(invoice_prefix)
        cursor.execute(
            """
            INSERT INTO transactions
            (user_id,invoice_id,amount,reference,status)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (current_user_id,invoice_id,amount_paid,reference,status)
        )

        conn.commit()

        if user_info[1] == "basic":
            current_month = datetime.now().month
            cursor.execute(
                """
                SELECT COUNT(*) FROM invoices
                WHERE user_id=%s AND MONTH(created_at)=%s AND YEAR(created_at)=YEAR(NOW())
                """,
                (current_user_id, current_month)
            )
            invoice_count = cursor.fetchone()[0]
            if invoice_count > 100:
                return jsonify({
                    "status": "error",
                    "message": "Invoice limit reached for Basic plan. Please upgrade your plan."
                }), 400 
            
            send_basic_plan_invoice_email(client_email,client_name,invoice_id,invoice_date,due_date,status,subtotal,tax,total,amount_paid, balance, notes,items)
  
        if user_info[1] == "pro":
            send_pro_plan_invoice_email(client_email,client_name,invoice_id,invoice_date,due_date,status,subtotal,tax,total,amount_paid, balance, notes,items)
        
        if user_info[1] == "trial":
            send_basic_plan_invoice_email(client_email,client_name,invoice_id,invoice_date,due_date,status,subtotal,tax,total,amount_paid, balance, notes,items)

  

        save_log_activity(
           current_user_id ,
            "Invoice",
            "Created Invoice",
            f"Invoice #{invoice_id} created for {client_name}",
            total,
            status
        )
        # ================= CREATE NOTIFICATION =================
        cursor.execute("""
            INSERT INTO notifications (user_id, title, message, type)
            VALUES (%s, %s, %s, %s)
        """, (
            current_user_id,
            "Invoice Created",
            f"Invoice #{invoice_id} was successfully created.",
            "invoice"
        ))

        conn.commit()

        cursor.execute("""
            SELECT push_token FROM user_base
            WHERE user_id =%s
        """, (current_user_id,))

        user = cursor.fetchone()

        if user and user[0]:
            send_push_notification(
                user[0],
                "Invoice Created",
                f"Invoice #{invoice_id} created successfully."
            )

        return jsonify({
            "status": "success",
            "message": "Invoice created successfully",
            "invoice_id": invoice_id
        }), 201

    except Exception as e:
        conn.rollback()
        print("Create invoice error:", e)
        return jsonify({"status": "error", "message": f"Server error: {e}"}), 500



@app.route("/api/invoice/drafts", methods=["POST"])
@token_required
def save_draft(current_user_id, current_user_role):

    data = request.get_json(force=True)
    if not data:
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    client_name = data.get("client_name")
    client_email = data.get("client_email")
    invoice_date = data.get("invoice_date")
    due_date = data.get("due_date")
    items = data.get("items", [])
    notes = data.get("notes", "")
    subtotal = float(data.get("subtotal", 0))
    tax = float(data.get("tax", 0))
    total = float(data.get("total", 0))
    amount_paid= float(data.get("amount_paid", 0))
    balance = float(data.get("balance", 0))

    if not all([client_name, client_email, invoice_date, due_date]):
        return jsonify({"status": "error", "message": "Missing required fields"}), 400

    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT username FROM user_base WHERE user_id=%s",
            (current_user_id,)
        )

        if not cursor.fetchone():
            cursor.close()
            return jsonify({"status": "error", "message": "User not found"}), 404

        for item in items:
            cursor.execute(
                """
                INSERT INTO invoice_draft (
                    user_id,
                    client_name,
                    client_email,
                    invoice_date,
                    due_date,
                    notes,
                    description,
                    quantity,
                    price,
                    subtotal,
                    tax,
                    total_amount,
                    amount_paid,
                    balance
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    current_user_id,
                    client_name,
                    client_email,
                    invoice_date,
                    due_date,
                    notes,
                    item.get("description"),
                    item.get("quantity", 1),
                    item.get("price", 0),
                    subtotal,
                    tax,
                    total,
                    amount_paid,
                    balance
                )
            )

        conn.commit()

        save_log_activity(
            current_user_id,
            "Invoice",
            "Draft Saved",
            f"Draft saved for {client_name}",
            total,
            "pending"
        )

        cursor.close()

        return jsonify({
            "status": "success",
            "message": "Invoice saved as draft"
        }), 200

    except Exception as e:
        conn.rollback()
        cursor.close()
        print("Save draft error:", e)


@app.route("/api/pin/update", methods=["POST"])
@token_required
def update_pin(current_user_id, current_user_role):
    cursor = conn.cursor(dictionary=True)
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "status": "error",
                "message": "Invalid or missing JSON"
            }), 400
    

        required_fields = ["currentPin", "Newpin", "ConfirmNewpin"]
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    "status": "error",
                    "message": f"Missing field: {field}"
                }), 400
            
        cursor.execute(
            """
            SELECT app_pin, username, email
            FROM user_base
            WHERE user_id=%s
            """,
            (current_user_id,)
        )
        user = cursor.fetchone()

        if not user:
            return jsonify({
                "status": "error",
                "message": "User Not Found."
            }), 400 
        
        current_pin = hashlib.sha256(data["currentPin"].encode()).hexdigest()

        if current_pin != user["app_pin"]:
            return jsonify({
                "status": "error",
                "message": "Current Password Incorrect."
            }), 400
    
        if data["Newpin"] != data["ConfirmNewpin"]:
            return jsonify({
                "status": "error",
                "message": "Pin doesn't match."
            }), 400

        cursor.execute(
            """
            UPDATE user_base 
            SET app_pin=%s
            WHERE user_id=%s
            """,
            (current_pin,current_user_id)
        )
        conn.commit()

        year = datetime.now().year
        security_url = f"/security-center"
        support_email = "support@businessessential.com"
        username = data["username"]
        change_pin_html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>PIN Changed</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f6f9;font-family:Arial,sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f6f9;padding:40px 0;">
<tr>
<td align="center">

<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:10px;padding:40px;box-shadow:0 5px 15px rgba(0,0,0,0.05);">

<tr>
<td align="center" style="padding-bottom:20px;">
<h2 style="margin:0;color:#111827;">Business Essential</h2>
</td>
</tr>

<tr>
<td>
<h3 style="color:#111827;">Your PIN Was Successfully Changed 🔐</h3>

<p style="color:#4b5563;font-size:15px;line-height:1.6;">
Hi {username},
</p>

<p style="color:#4b5563;font-size:15px;line-height:1.6;">
This is a confirmation that your account security PIN was recently updated.
If you made this change, no further action is required.
</p>

<p style="color:#4b5563;font-size:15px;line-height:1.6;">
If you did NOT request this change, please secure your account immediately.
</p>

<div style="text-align:center;margin:30px 0;">
<a href="{security_url}" style="background:#111827;color:#ffffff;padding:12px 25px;border-radius:6px;text-decoration:none;font-weight:bold;">
Review Security Settings
</a>
</div>

<p style="color:#6b7280;font-size:13px;line-height:1.6;">
For your protection, we never include sensitive details in email notifications.
</p>

<hr style="border:none;border-top:1px solid #e5e7eb;margin:30px 0;">

<p style="color:#9ca3af;font-size:12px;text-align:center;">
Need help? Contact our support team at {support_email}.
<br><br>
© {year} Business Essential. All rights reserved.
</p>

</td>
</tr>

</table>

</td>
</tr>
</table>

</body>
</html>
"""
        send_email(
            user["email"],
            "Bussiness Essential - Changed Password",
            change_pin_html,
            html=True
        )

        return jsonify({
            "status": "success",
            "message": "Pin updated successfully"
        }), 200
    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "message": "Database error",
            "details": str(e)
        }), 500


@app.route("/api/password/update", methods=["POST"])
@token_required
def update_password(current_user_id, current_user_role):
    cursor = conn.cursor(dictionary=True)
    try:
        data = request.get_json()

        if not data:
            return jsonify({
                "status": "error",
                "message": "Invalid or missing JSON"
            }), 400
    


        required_fields = ["currentPassword", "NewPassword", "ConfirmNewPassword"]
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    "status": "error",
                    "message": f"Missing field: {field}"
                }), 400
    
        cursor.execute(
            """
            SELECT password_hash, username, email
            FROM user_base
            WHERE user_id=%s
            """,
            (current_user_id,)
        )
        user = cursor.fetchone()

        if not user:
            return jsonify({
                "status": "error",
                "message": "User Not Found."
            }), 400 
    
        current_password = hashlib.sha256(data["currentPassword"].encode()).hexdigest()

        if current_password != user["password_hash"]:
            return jsonify({
                "status": "error",
                "message": "Current Password Incorrect."
            }), 400
    
        if data["NewPassword"] != data["ConfirmNewPassword"]:
            return jsonify({
                "status": "error",
                "message": "Password doesn't match."
            }), 400
    
        cursor.execute(
            """
            UPDATE user_base 
            SET password_hash=%s,
                last_password_change=NOW()
            WHERE user_id=%s
            """,
            (current_password, current_user_id)
        )

        conn.commit()

        username = data["username"]
        year = datetime.now().year
        reset_password_url = f"/security-center"
        support_email = "support@businessessential.com"
        change_password_html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Password Changed</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f6f9;font-family:Arial,sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f6f9;padding:40px 0;">
<tr>
<td align="center">

<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:10px;padding:40px;box-shadow:0 5px 15px rgba(0,0,0,0.05);">

<tr>
<td align="center" style="padding-bottom:20px;">
<h2 style="margin:0;color:#111827;">Business Essential</h2>
</td>
</tr>

<tr>
<td>
<h3 style="color:#111827;">Your Password Has Been Updated 🔑</h3>

<p style="color:#4b5563;font-size:15px;line-height:1.6;">
Hi {username},
</p>

<p style="color:#4b5563;font-size:15px;line-height:1.6;">
This email confirms that your account password was successfully changed.
</p>

<p style="color:#4b5563;font-size:15px;line-height:1.6;">
If you made this change, you can safely ignore this message.
</p>

<p style="color:#dc2626;font-size:14px;line-height:1.6;font-weight:bold;">
If you did not change your password, your account may be at risk.
Please reset your password immediately.
</p>

<div style="text-align:center;margin:30px 0;">
<a href="{reset_password_url}" style="background:#dc2626;color:#ffffff;padding:12px 25px;border-radius:6px;text-decoration:none;font-weight:bold;">
Secure My Account
</a>
</div>

<hr style="border:none;border-top:1px solid #e5e7eb;margin:30px 0;">

<p style="color:#6b7280;font-size:13px;">
Security Tip:
Never share your password with anyone. Business Essential will never ask for your password via email.
</p>

<hr style="border:none;border-top:1px solid #e5e7eb;margin:30px 0;">

<p style="color:#9ca3af;font-size:12px;text-align:center;">
Need assistance? Contact us at {support_email}.
<br><br>
© {year} Business Essential. All rights reserved.
</p>

</td>
</tr>

</table>

</td>
</tr>
</table>

</body>
</html>
"""
        send_email(
            user["email"],
            "Bussiness Essential - Changed Password",
            change_password_html,
            html=True
        )

        return jsonify({
            "status": "success",
            "message": "Password updated successfully"
        }), 200
    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "message": "Database error",
            "details": str(e)
        }), 500

@app.route("/api/update-settings", methods=["POST"])
@token_required
def update_settings(current_user_id, current_user_role):
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "status": "error",
                "message": "Invalid or missing JSON"
            }), 400
        
        cursor.execute(
            """
            UPDATE user_settings
            SET 
                show_tax=%s,
                show_discount=%s,
                email_notifications=%s,
                due_date_reminder=%s,
                require_pin_for_delete=%s,
                invoice_prefix=%s,
                next_invoice_number=%s,
                default_due_date=%s,
                default_tax_rate=%s,
                footer_note=%s,
                currency=%s,
                currency_symbol=%s,
                timezone=%s,
                reminder_days_before=%s,
                auto_logout_minutes=%s,
                date_format=%s
            WHERE user_id = %s
            """,
            (
                data.get("showTax"),
                data.get("enableDiscount"),
                data.get("emailNotifications"),
                data.get("dueReminder"),
                data.get("requirePin"),
                data.get("invoicePrefix", ""),
                data.get("nextInvoiceNumber", 0),
                data.get("defaultDueDate", 0),
                data.get("defaultTaxRate", 0),
                data.get("invoiceFooterNote", ""),
                data.get("currency", ""),
                data.get("currencySymbol", ""),
                data.get("timeZone", ""),
                data.get("reminderDays", 0),
                data.get("autoLogout", 0),
                data.get("dateFormat", ""),
                current_user_id
            )
        )
        conn.commit()

        return jsonify({
            "status": "success",
            "message": "Settings updated successfully"
        }), 200
    except Exception as e:
        conn.rollback()
        print(e)
        return jsonify({
            "status": "error",
            "message": "Database error",
            "details": str(e)
        }), 500

@app.route("/api/invoices/<int:invoice_id>", methods=["GET"])
@token_required
def get_invoice(current_user_id, current_user_role, invoice_id):
    cursor = conn.cursor(dictionary=True, buffered=True)

    # ================= INVOICE =================
    cursor.execute("""
        SELECT id, status, client_email, subtotal, tax,
               total_amount, amount_paid, balance,
               invoice_date, due_date
        FROM invoices
        WHERE id=%s AND user_id=%s
    """, (invoice_id, current_user_id))

    invoice = cursor.fetchone()

    if not invoice:
        return jsonify({
            "status": "error",
            "message": "Invoice not found"
        }), 404

    # Extract values safely
    invoice_id = invoice["id"]
    status = invoice["status"]
    client_email = invoice["client_email"]
    subtotal = invoice["subtotal"]
    tax = invoice["tax"]
    total = invoice["total_amount"]
    amount_paid = invoice["amount_paid"]
    balance = invoice["balance"]
    invoice_date = invoice["invoice_date"]
    due_date = invoice["due_date"]

    # ================= CLIENT =================
    cursor.execute("""
        SELECT 
            client_email AS email, 
            client_name AS name, 
            client_address AS address, 
            client_phone AS phone
        FROM clients
        WHERE user_id=%s AND client_email=%s
    """, (current_user_id, client_email))

    client = cursor.fetchone()

    # ================= ITEMS =================
    cursor.execute("""
        SELECT description, quantity, price
        FROM invoice_items
        WHERE invoice_id=%s
    """, (invoice_id,))

    items = cursor.fetchall()

    # ================= SETTINGS =================
    cursor.execute("""
        SELECT invoice_prefix
        FROM user_settings
        WHERE user_id=%s
    """, (current_user_id,))

    settings = cursor.fetchone()
    invoice_prefix = settings["invoice_prefix"] if settings else "INV"

    # ================= YEAR (Correct way) =================
    year = invoice_date.strftime("%Y") if invoice_date else ""

    # ================= USER INFO =================
    cursor.execute("""
        SELECT  user_base.email,
                user_base.user_id,
                cust_base.fullname AS name,
                cust_base.address,
                cust_base.phone
        FROM user_base
        JOIN cust_base ON user_base.user_id = cust_base.user_id
        WHERE user_base.user_id=%s
    """, (current_user_id,))

    user_info = cursor.fetchone()

    return jsonify({
        "status": "success",
        "auth_user": {   # renamed to avoid duplicate key
            "id": current_user_id,
            "role": current_user_role
        },
        "invoiceNumber": f"{invoice_prefix}-{year}-{invoice_id}",
        "status_value": status,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "amount_paid": amount_paid,
        "balance": balance,
        "invoice_date": invoice_date.strftime("%Y-%m-%d %H:%M:%S") if invoice_date else None,
        "due_date": due_date.strftime("%Y-%m-%d") if due_date else None,
        "client": client,
        "items": items,
        "user": user_info,
        "company": {
            "name": "Business Essential",
            "email": "billing@businessessential.com",
            "address": "No 9 Lamina Estate, Ikorodu, Lagos"
        }
    }), 200



@app.route("/api/clients/add", methods=["POST"])
@token_required
def add_clients(current_user_id, current_user_role):
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "status": "error",
                "message": "Invalid or missing JSON"
            }), 40
        
        required_fields = ["name", "email", "phone", "address"]
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    "status": "error",
                    "message": f"Missing field: {field}"
                }), 400
        
        cursor.execute(
            """
            SELECT client_id FROM clients 
            WHERE user_id =%s AND client_email =%s
            """,
            (current_user_id, data['email'])
        )
        client = cursor.fetchone()
        if client:
            return jsonify({"status": "error", "message": "Client already exist"}), 404
        

        cursor.execute(
            """
            INSERT INTO clients(user_id,client_name, client_email, client_phone, client_address)
            VALUE(%s, %s, %s, %s, %s)
            """,
            (current_user_id,data['name'], data['email'], data['phone'], data['address'])
        )
        conn.commit()

        return jsonify({
            "status": "success",
            "message": "Client Added Successfully."
        })
    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "message": f"Database error: {e}",
            "details": str(e)
        }), 500

@app.route("/api/update/profilepic",methods=["POST"])
@token_required
def update_profile_pic(current_user_id,current_user_role):
    try:
        file = request.files.get("profile_picture")  
 
        if not file:
            return jsonify({
                "status":"error",
                "message":"failed",
            })
        
        result = cloudinary.uploader.upload(
            file,
            folder="profile_images",
            transformation = [
                {"width":300, "height":300, "crop":"fill"}
            ],
            public_id = f"user_{current_user_id}",
            overwrite= True
        )
        save_path = result['secure_url']
        cursor.execute(
            """
            UPDATE cust_base 
            SET profilepicurl=%s
            WHERE user_id=%s
            """,
            (save_path, current_user_id)
        )

        conn.commit()

        return jsonify({
            "status": "success",
            "message": "Profile Picture changed successfully."
        }), 200
    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "message": "Database error",
            "details": str(e)
        }), 500


@app.route("/api/clients/update/<int:client_id>", methods=["POST"])
@token_required
def update_client(current_user_id,current_user_role,client_id):
    data = request.get_json()

    if not data:
        return jsonify({
            "status": "error",
            "message": "Invalid or missing JSON"
        }), 400

    required_fields = [
        "name", 
        "email", 
        "phone", 
        "address"
    ]

    for field in required_fields:
        if not data.get(field):
            return jsonify({
                "status": "error",
                "message": f"Missing field: {field}"
            }), 400
        
    try: 
        cursor.execute(
            """
            UPDATE clients
            SET client_name=%s,
                client_email=%s,
                client_phone=%s,
                client_address=%s
            WHERE user_id=%s AND client_id=%s
            """,
            (data["name"], data["email"], data["phone"], data["address"], current_user_id,client_id)
        )
        conn.commit()

        return jsonify({
            "status": "success",
            "message": "Updated client successfully."
        })
    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "message": "Database error",
            "details": str(e)
        }), 500

    
@app.route("/api/update/companylogo", methods=["POST"])
@token_required
def update_company_logo(current_user_id,current_user_role):
    file = request.files.get("company_logo")  # Make sure your input type="file"
        
    if file:
        
        result = cloudinary.uploader.upload(
            file,
            folder="company_logo",
            transformation = [
                {"width":300, "height":300, "crop":"fill"}
            ],
            public_id = f"user_logo_{current_user_id}",
            overwrite= True
        )
        save_path = result['secure_url']
    
    try:
        cursor.execute(
            """
            UPDATE cust_base 
            SET companylogourl=%s
            WHERE user_id=%s
            """,
            (save_path, current_user_id)
        )

        conn.commit()
        return jsonify({
            "status": "success",
            "message": "Company Logo changed successfully."
        }), 200
    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "message": "Database error",
            "details": str(e)
        }), 500



@app.route("/api/profile/update", methods=["POST"])
@token_required
def update_profile(current_user_id,current_user_role):
    data = request.get_json()

    if not data:
        return jsonify({
            "status": "error",
            "message": "Invalid or missing JSON"
        }), 400

    required_fields = [
        "fullname",
        "username",
        "address",
        "phone",
        "alternateEmail",
        "country",
        "profileName",
        "website",
        "bio",
    ]

    for field in required_fields:
        if not data.get(field):
            return jsonify({
                "status": "error",
                "message": f"Missing field: {field}"
            }), 400
    
    try:
        cursor.execute(
            """
            UPDATE cust_base 
            SET fullname=%s,
                address=%s,
                phone=%s,
                alternateemail=%s,
                country=%s,
                profilename=%s,
                website=%s,
                bio=%s
            WHERE user_id=%s
            """,
            (data["fullname"],data["address"],data["phone"],data["alternateEmail"],data["country"],data["profileName"],data["website"],data["bio"],current_user_id)
        )

        cursor.execute(
            """
            UPDATE user_base 
            SET username=%s
            WHERE user_id=%s
            """,
            (data['username'], current_user_id)
        )

        conn.commit()

        return jsonify({
            "status": "success",
            "message": "Updated Profile Successfully."
        }), 200
    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "message": "Database error",
            "details": str(e)
        }), 500

@app.route("/api/delete-draft/<int:draft_id>", methods=["DELETE"])
@token_required
def delete_draft(current_user_id, current_user_role,draft_id):

    try:
        cursor.execute(
            """
            DELETE from invoice_draft WHERE user_id=%s AND draft_id=%s
            """,
            (current_user_id,draft_id)
        )
        conn.commit()
        
        save_log_activity(
            current_user_id,
            "Invoice",
            "Invoice Draft",
            f"Draft `{draft_id}` Deleted Successfull."
        )
        return jsonify({"status": "success", "message": "Draft Deleted Successfully"})
    except Exception as e:
        # Rollback in case of error
        conn.rollback()
        print("Error deleting draft:", e)
        return jsonify({"status": "error", "message": str(e)}), 500



@app.route("/api/delete-account", methods=["DELETE"])
@token_required
def delete_account(current_user_id, current_user_role):
    cursor = conn.cursor(dictionary=True, buffered=True)

    data = request.get_json()

    if not data or "password" not in data:
        return jsonify({
            "status": "error",
            "message": "Password is required"
        }), 400

    password = data["password"]

    try:
        # ================= VERIFY USER PASSWORD =================
        cursor.execute(
            """
            SELECT password_hash
            FROM user_base
            WHERE user_id=%s
            """,
            (current_user_id,)
        )
        user = cursor.fetchone()

        if not user:
            return jsonify({
                "status": "error",
                "message": "User Not Found."
            }), 400 
    
        current_password = hashlib.sha256(data["password"].encode()).hexdigest()

        if current_password != user["password_hash"]:
            return jsonify({
                "status": "error",
                "message": "Password Incorrect."
            }), 400
    

        # ================= BEGIN TRANSACTION =================
        conn.start_transaction()

        # Delete invoice items first
        cursor.execute(
            """
            DELETE ii FROM invoice_items ii
            JOIN invoices i ON ii.invoice_id = i.id
            WHERE i.user_id = %s
            """,
            (current_user_id,)
        )

        # Delete invoices
        cursor.execute(
            "DELETE FROM invoices WHERE user_id = %s",
            (current_user_id,)
        )

        # Delete clients
        cursor.execute(
            "DELETE FROM clients WHERE user_id = %s",
            (current_user_id,)
        )

        # Delete customer base (if exists)
        cursor.execute(
            "DELETE FROM cust_base WHERE user_id = %s",
            (current_user_id,)
        )

        # Delete user settings
        cursor.execute(
            "DELETE FROM user_settings WHERE user_id = %s",
            (current_user_id,)
        )

        # Delete Transactions
        cursor.execute(
            "DELETE FROM transactions WHERE user_id = %s",
            (current_user_id,)
        )

        # Delete Log activity
        cursor.execute(
            "DELETE FROM log_activity WHERE user_id = %s",
            (current_user_id,)
        )

        # Delete security Log
        cursor.execute(
            "DELETE FROM security_activity WHERE user_id=%s",
            (current_user_id,)
        )

        cursor.execute(
            "DELETE FROM notifications WHERE user_id=%s",
            (current_user_id,)
        )
        
        # Delete Drafts
        cursor.execute(
            "DELETE FROM invoice_draft WHERE user_id = %s",
            (current_user_id,)
        )

        # Finally delete user account
        cursor.execute(
            "DELETE FROM user_base WHERE user_id = %s",
            (current_user_id,)
        )

        # ================= COMMIT =================
        conn.commit()

        return jsonify({
            "status": "success",
            "message": "Account deleted successfully"
        }), 200

    except Exception as e:
        conn.rollback()
        return jsonify({
            "status": "error",
            "message": "Failed to delete account",
            "details": str(e)
        }), 500

    finally:
        cursor.close()


if __name__ == "__main__":
    app.run()
































































































