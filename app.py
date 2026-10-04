import sqlite3
from datetime import datetime
import logging
import os
from pathlib import Path

import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore

from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash

app = Flask(__name__)
app.secret_key = "smart-blood-donor-secret-key"

DEFAULT_PROFILE = {
    "name": "Maruthi",
    "email": "maruthi@example.com",
    "phone": "",
    "blood_group": "O+",
    "location": "Guntur"
}

BLOOD_BANK_FILE = Path(r"C:\Users\cheru\OneDrive\Documents\India_Blood_Banks.xlsx")
DATABASE_FILE = Path(app.instance_path) / "smart_blood_donor.db"
logger = logging.getLogger(__name__)


def initialize_firestore():
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if not project_id:
        return None

    try:
        credential = (
            credentials.Certificate(service_account_path)
            if service_account_path
            else credentials.ApplicationDefault()
        )
        try:
            firebase_app = firebase_admin.get_app()
        except ValueError:
            firebase_app = firebase_admin.initialize_app(
                credential,
                {"projectId": project_id}
            )
        return firestore.client(app=firebase_app)
    except Exception:
        logger.exception("Firebase initialization failed; continuing with SQL storage only.")
        return None


firestore_client = initialize_firestore()


def firestore_primary_enabled():
    return (
        firestore_client is not None
        and os.getenv("USE_FIRESTORE_PRIMARY", "true").strip().lower() not in {"0", "false", "no", "off"}
    )


def normalize_firestore_payload(payload):
    normalized = {}
    for key, value in (payload or {}).items():
        if isinstance(value, datetime):
            normalized[key] = value.isoformat()
        else:
            normalized[key] = value
    return normalized


def sync_firestore_document(collection_name, document_id, fields):
    if firestore_client is None:
        return None

    try:
        if document_id is None:
            doc_ref = firestore_client.collection(collection_name).document()
            doc_ref.set(normalize_firestore_payload(fields), merge=True)
            return doc_ref.id

        firestore_client.collection(collection_name).document(str(document_id)).set(
            normalize_firestore_payload(fields),
            merge=True
        )
        return str(document_id)
    except Exception:
        logger.exception("Could not sync a document to Firestore collection %s.", collection_name)
        return None


def delete_firestore_document(collection_name, document_id):
    if firestore_client is None:
        return

    try:
        firestore_client.collection(collection_name).document(str(document_id)).delete()
    except Exception:
        logger.exception("Could not delete a document from Firestore collection %s.", collection_name)


class DatabaseConnection:
    def __init__(self):
        self.use_mysql = bool(os.getenv("MYSQL_DATABASE"))
        if self.use_mysql:
            import pymysql

            self.connection = pymysql.connect(
                host=os.getenv("MYSQL_HOST", "127.0.0.1"),
                port=int(os.getenv("MYSQL_PORT", "3306")),
                user=os.getenv("MYSQL_USER", "root"),
                password=os.getenv("MYSQL_PASSWORD", ""),
                database=os.environ["MYSQL_DATABASE"],
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False
            )
            self.initialize_mysql_schema()
        else:
            self.connection = sqlite3.connect(DATABASE_FILE)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS contact_message (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(120) NOT NULL,
                    email VARCHAR(120) NOT NULL,
                    phone VARCHAR(20),
                    subject VARCHAR(120) NOT NULL,
                    message TEXT NOT NULL,
                    created_at DATETIME
                )
                """
            )
            self.connection.commit()

    def initialize_mysql_schema(self):
        schema = [
            """CREATE TABLE IF NOT EXISTS `user` (
                id INT AUTO_INCREMENT PRIMARY KEY, full_name VARCHAR(120) NOT NULL,
                blood_group VARCHAR(10) NOT NULL, mobile VARCHAR(20) NOT NULL,
                email VARCHAR(120) NOT NULL, country VARCHAR(80) NOT NULL,
                state VARCHAR(80) NOT NULL, district VARCHAR(80) NOT NULL,
                city VARCHAR(80) NOT NULL, user_id VARCHAR(80) NOT NULL,
                password_hash VARCHAR(255) NOT NULL, availability VARCHAR(30),
                last_donation_date DATE, created_at DATETIME
            )""",
            """CREATE TABLE IF NOT EXISTS blood_request (
                id INT AUTO_INCREMENT PRIMARY KEY, patient_name VARCHAR(120) NOT NULL,
                blood_group VARCHAR(10) NOT NULL, required_units INT NOT NULL,
                emergency_level VARCHAR(30) NOT NULL, hospital_name VARCHAR(150) NOT NULL,
                hospital_location VARCHAR(200) NOT NULL, contact_number VARCHAR(20) NOT NULL,
                status VARCHAR(40), created_at DATETIME
            )""",
            """CREATE TABLE IF NOT EXISTS contact_message (
                id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(120) NOT NULL,
                email VARCHAR(120) NOT NULL, phone VARCHAR(20),
                subject VARCHAR(120) NOT NULL, message TEXT NOT NULL,
                created_at DATETIME
            )"""
        ]
        cursor = self.connection.cursor()
        for statement in schema:
            cursor.execute(statement)
        self.connection.commit()
        cursor.close()

    def execute(self, query, parameters=()):
        if self.use_mysql:
            query = query.replace("?", "%s")
            cursor = self.connection.cursor()
            cursor.execute(query, parameters)
            return cursor
        return self.connection.execute(query, parameters)

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()


def get_database():
    return DatabaseConnection()


def format_elapsed_time(created_at):
    if not created_at:
        return "Time unavailable"

    try:
        created_time = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        elapsed_seconds = max(0, (datetime.now() - created_time.replace(tzinfo=None)).total_seconds())
    except ValueError:
        return str(created_at)

    if elapsed_seconds < 60:
        return "just now"
    if elapsed_seconds < 3600:
        minutes = int(elapsed_seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if elapsed_seconds < 86400:
        hours = int(elapsed_seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(elapsed_seconds // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"

SAMPLE_DONORS = [
    {
        "full_name": "Rohit Kumar",
        "blood_group": "A+",
        "mobile": "9000000001",
        "email": "rohit@example.com",
        "state": "Andhra Pradesh",
        "district": "Guntur",
        "city": "Guntur",
        "availability": "Available",
        "last_donation_date": "Not provided"
    },
    {
        "full_name": "Sravani",
        "blood_group": "O+",
        "mobile": "9000000002",
        "email": "sravani@example.com",
        "state": "Andhra Pradesh",
        "district": "Krishna",
        "city": "Vijayawada",
        "availability": "Available",
        "last_donation_date": "Not provided"
    },
    {
        "full_name": "Vikram",
        "blood_group": "B+",
        "mobile": "9000000003",
        "email": "vikram@example.com",
        "state": "Andhra Pradesh",
        "district": "Guntur",
        "city": "Guntur",
        "availability": "Available",
        "last_donation_date": "Not provided"
    },
    {
        "full_name": "Priya",
        "blood_group": "AB+",
        "mobile": "9000000004",
        "email": "priya@example.com",
        "state": "Andhra Pradesh",
        "district": "Guntur",
        "city": "Tenali",
        "availability": "Available",
        "last_donation_date": "Not provided"
    }
]


# =========================
# HOME PAGE
# =========================

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/firebase-status")
def firebase_status():
    if firestore_client is None:
        return {
            "connected": False,
            "message": "Firebase is not configured. Set FIREBASE_PROJECT_ID and credentials."
        }, 503

    try:
        firestore_client.collection("_connection_check").limit(1).get()
    except Exception:
        logger.exception("Firebase connection check failed.")
        return {"connected": False, "message": "Could not connect to Firebase Firestore."}, 503

    return {"connected": True, "message": "Firebase connected successfully!"}


# =========================
# LOGIN
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email")
        password = request.form.get("password")

        # Demo login
        if email and password:
            user_row = None
            if firestore_primary_enabled():
                for doc in firestore_client.collection("donors").stream():
                    donor = doc.to_dict() or {}
                    donor_email = str(donor.get("email", "") or "").lower()
                    user_id = str(donor.get("user_id", "") or "").lower()
                    if donor_email == email.lower() or user_id == email.lower():
                        user_row = {"id": doc.id, **donor}
                        break
            else:
                connection = get_database()
                user_row = connection.execute(
                    """
                    SELECT id, user_id, full_name, email, mobile, blood_group, city
                    FROM `user`
                    WHERE lower(email) = lower(?) OR lower(user_id) = lower(?)
                    LIMIT 1
                    """,
                    (email, email)
                ).fetchone()
                connection.close()

            if not user_row:
                flash("No registered account found for this email. Please register first.", "error")
                return render_template("login.html")

            if isinstance(user_row, dict):
                session["profile"] = {
                    "user_id": user_row.get("user_id") or user_row.get("id"),
                    "name": user_row.get("full_name") or user_row.get("name"),
                    "email": user_row.get("email"),
                    "phone": user_row.get("mobile") or user_row.get("phone"),
                    "blood_group": user_row.get("blood_group"),
                    "location": user_row.get("city") or user_row.get("location")
                }
            elif user_row:
                session["profile"] = {
                    "user_id": user_row["user_id"],
                    "name": user_row["full_name"],
                    "email": user_row["email"],
                    "phone": user_row["mobile"],
                    "blood_group": user_row["blood_group"],
                    "location": user_row["city"]
                }
            flash("Login successful!", "success")

            return redirect(url_for("dashboard"))

        flash("Please enter email and password.", "error")

    return render_template("login.html")


# =========================
# REGISTER
# =========================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get("full_name")
        email = request.form.get("email")
        password = request.form.get("password")
        blood_group = request.form.get("blood_group")

        if name and email and password and blood_group:
            connection = get_database()
            created_at = datetime.now()
            user_id = request.form.get("user_id", email)
            cursor = connection.execute(
                """
                INSERT INTO `user` (
                    full_name, blood_group, mobile, email, country, state,
                    district, city, user_id, password_hash, availability, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    blood_group,
                    request.form.get("mobile", ""),
                    email,
                    request.form.get("country", "India"),
                    request.form.get("state", ""),
                    request.form.get("district", ""),
                    request.form.get("city", ""),
                    user_id,
                    generate_password_hash(password),
                    request.form.get("availability", "Available"),
                    created_at
                )
            )
            connection.commit()
            connection.close()
            sync_firestore_document(
                "donors",
                user_id,
                {
                    "full_name": name,
                    "blood_group": blood_group,
                    "mobile": request.form.get("mobile", ""),
                    "email": email,
                    "country": request.form.get("country", "India"),
                    "state": request.form.get("state", ""),
                    "district": request.form.get("district", ""),
                    "city": request.form.get("city", ""),
                    "user_id": user_id,
                    "availability": request.form.get("availability", "Available"),
                    "created_at": created_at
                }
            )
            flash("Registration successful! Please login.", "success")
            return redirect(url_for("login"))

        flash("Please fill all required fields.", "error")

    return render_template("register.html")


# =========================
# DASHBOARD
# =========================

@app.route("/dashboard")
def dashboard():

    profile = session.get("profile", DEFAULT_PROFILE)
    if firestore_primary_enabled():
        latest_requests = []
        for doc in firestore_client.collection("blood_requests").stream():
            request_data = doc.to_dict() or {}
            if str(request_data.get("status", "Open")).lower() in {"open", "pending"}:
                latest_requests.append({"id": doc.id, **request_data})
        latest_requests = sorted(
            latest_requests,
            key=lambda item: str(item.get("created_at") or ""),
            reverse=True
        )[:5]
        blood_request_count = sum(
            1 for doc in firestore_client.collection("blood_requests").stream()
            if str((doc.to_dict() or {}).get("status", "Open")).lower() in {"open", "pending"}
        )
        donor_count = sum(1 for _ in firestore_client.collection("donors").stream())
    else:
        connection = get_database()
        latest_requests = [dict(row) for row in connection.execute(
            """
            SELECT id, patient_name, blood_group, required_units, emergency_level,
                 hospital_name, hospital_location, contact_number, status, created_at
            FROM blood_request
            WHERE lower(coalesce(status, 'Open')) IN ('open', 'pending')
            ORDER BY id DESC
            LIMIT 5
            """
        ).fetchall()]
        count_row = connection.execute(
            """
            SELECT COUNT(*)
            FROM blood_request
            WHERE lower(coalesce(status, 'Open')) IN ('open', 'pending')
            """
        ).fetchone()
        blood_request_count = next(iter(count_row.values())) if isinstance(count_row, dict) else count_row[0]
        donor_count_row = connection.execute("SELECT COUNT(*) FROM `user`").fetchone()
        donor_count = next(iter(donor_count_row.values())) if isinstance(donor_count_row, dict) else donor_count_row[0]
        connection.close()

    return render_template(
        "dashboard_overview.html",
        profile=profile,
        latest_requests=latest_requests,
        blood_request_count=blood_request_count,
        donor_count=donor_count
    )


# =========================
# FIND DONOR
# =========================

@app.route("/find-donor", methods=["GET", "POST"])
def find_donor():
    donors = []

    if firestore_primary_enabled():
        for doc in firestore_client.collection("donors").stream():
            donor = doc.to_dict() or {}
            if str(donor.get("availability", "Available")).lower() == "available":
                donors.append({"id": doc.id, **donor})
        donors = sorted(donors, key=lambda donor: (str(donor.get("city", "")).lower(), str(donor.get("full_name", "")).lower()))
    else:
        connection = get_database()
        available_registered_donors = [dict(row) for row in connection.execute(
            """
            SELECT full_name, blood_group, mobile, email, state, district, city,
                   availability, last_donation_date
            FROM `user`
            WHERE lower(coalesce(availability, 'Available')) = 'available'
            ORDER BY city, full_name
            """
        ).fetchall()]
        connection.close()
        donors = available_registered_donors

    if request.method == "POST":
        blood_group = request.form.get("blood_group", "").strip()
        state = request.form.get("state", "").strip()
        district = request.form.get("district", "").strip()
        city = request.form.get("city", "").strip()

        if firestore_primary_enabled():
            filtered = []
            for doc in firestore_client.collection("donors").stream():
                donor = doc.to_dict() or {}
                if str(donor.get("availability", "Available")).lower() != "available":
                    continue
                if blood_group and str(donor.get("blood_group", "")).lower() != blood_group.lower():
                    continue
                if state and state.lower() not in str(donor.get("state", "")).lower():
                    continue
                if district and district.lower() not in str(donor.get("district", "")).lower():
                    continue
                if city and city.lower() not in str(donor.get("city", "")).lower():
                    continue
                filtered.append({"id": doc.id, **donor})
            donors = sorted(filtered, key=lambda donor: (str(donor.get("city", "")).lower(), str(donor.get("full_name", "")).lower()))
        else:
            connection = get_database()
            donors = [dict(row) for row in connection.execute(
                """
                     SELECT full_name, blood_group, mobile, email, state, district, city,
                         availability, last_donation_date
                FROM `user`
                WHERE lower(blood_group) = lower(?)
                  AND lower(coalesce(availability, 'Available')) = 'available'
                  AND lower(state) LIKE lower(?)
                  AND lower(district) LIKE lower(?)
                  AND lower(city) LIKE lower(?)
                ORDER BY city, full_name
                """,
                (blood_group, f"%{state}%", f"%{district}%", f"%{city}%")
            ).fetchall()]
            connection.close()

        sample_matches = [
            donor for donor in SAMPLE_DONORS
            if donor["blood_group"].lower() == blood_group.lower()
            and state.lower() in donor["state"].lower()
            and district.lower() in donor["district"].lower()
            and city.lower() in donor["city"].lower()
        ]
        donors.extend(sample_matches)

    return render_template(
        "search_for_donor.html",
        donors=donors
    )


@app.route("/register-profile", methods=["POST"])
def register_profile():
    profile = request.get_json(silent=True) or {}
    required_fields = ["full_name", "blood_group", "mobile", "email", "state", "district", "city", "user_id"]

    if any(not str(profile.get(field, "")).strip() for field in required_fields):
        return {"error": "Please provide all required donor details."}, 400

    created_at = datetime.now()
    donor_payload = {
        "full_name": profile["full_name"].strip(),
        "blood_group": profile["blood_group"].strip(),
        "mobile": profile["mobile"].strip(),
        "email": profile["email"].strip(),
        "country": profile.get("country", "India").strip(),
        "state": profile["state"].strip(),
        "district": profile["district"].strip(),
        "city": profile["city"].strip(),
        "user_id": profile["user_id"].strip(),
        "availability": profile.get("availability", "Available").strip(),
        "created_at": created_at
    }

    if firestore_primary_enabled():
        sync_firestore_document("donors", donor_payload["user_id"], donor_payload)
        return {"message": "Donor profile saved successfully."}, 201

    connection = get_database()
    cursor = connection.execute(
        """
        INSERT INTO `user` (
            full_name, blood_group, mobile, email, country, state,
            district, city, user_id, password_hash, availability, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            donor_payload["full_name"],
            donor_payload["blood_group"],
            donor_payload["mobile"],
            donor_payload["email"],
            donor_payload.get("country", "India"),
            donor_payload["state"],
            donor_payload["district"],
            donor_payload["city"],
            donor_payload["user_id"],
            generate_password_hash(profile.get("password", "")),
            donor_payload["availability"],
            created_at
        )
    )
    connection.commit()
    connection.close()
    sync_firestore_document("donors", cursor.lastrowid, donor_payload)
    return {"message": "Donor profile saved successfully."}, 201


# =========================
# BLOOD REQUEST
# =========================

@app.route("/blood-request", methods=["GET", "POST"])
def blood_request():

    if request.method == "POST":

        patient_name = request.form.get("patient_name")
        blood_group = request.form.get("blood_group")
        hospital = request.form.get("hospital_name")
        location = request.form.get("city")
        units = request.form.get("required_units")
        urgency = request.form.get("emergency_level")

        if patient_name and blood_group and hospital:
            created_at = datetime.now()
            request_payload = {
                "patient_name": patient_name,
                "blood_group": blood_group,
                "required_units": int(units or 1),
                "emergency_level": urgency or "Medium",
                "hospital_name": hospital,
                "hospital_location": location or "",
                "contact_number": request.form.get("contact", ""),
                "status": "Open",
                "created_at": created_at
            }

            if firestore_primary_enabled():
                sync_firestore_document("blood_requests", f"request-{int(created_at.timestamp() * 1000)}", request_payload)
            else:
                connection = get_database()
                cursor = connection.execute(
                    """
                    INSERT INTO blood_request (
                        patient_name, blood_group, required_units, emergency_level,
                        hospital_name, hospital_location, contact_number, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Open', ?)
                    """,
                    (
                        patient_name,
                        blood_group,
                        int(units or 1),
                        urgency or "Medium",
                        hospital,
                        location or "",
                        request.form.get("contact", ""),
                        created_at
                    )
                )
                connection.commit()
                connection.close()
                sync_firestore_document(
                    "blood_requests",
                    cursor.lastrowid,
                    request_payload
                )

            flash(
                "Blood request submitted successfully!",
                "success"
            )

            return redirect(url_for("dashboard"))

        flash(
            "Please fill the required details.",
            "error"
        )

    return render_template("blood_request.html")


# =========================
# BLOOD BANK
# =========================

def load_blood_banks():
    data_files = [
        BLOOD_BANK_FILE,
        *Path(app.instance_path).glob("*.xlsx"),
        *Path(app.instance_path).glob("*.xls"),
        *Path(app.instance_path).glob("*.csv"),
        *Path(app.root_path).glob("*.xlsx"),
        *Path(app.root_path).glob("*.xls"),
        *Path(app.root_path).glob("*.csv")
    ]

    data_files = [data_file for data_file in data_files if data_file.is_file()]

    if not data_files:
        return [
            {"name": "Government Blood Bank", "type": "Government", "city": "Guntur", "phone": "1800-123-4567"},
            {"name": "Red Cross Blood Bank", "type": "Private", "city": "Vijayawada", "phone": "1800-222-4567"},
            {"name": "City Blood Bank", "type": "Private", "city": "Tenali", "phone": "1800-333-4567"}
        ]

    data_file = data_files[0]
    if data_file.suffix.lower() == ".csv":
        data_frame = pd.read_csv(data_file)
    else:
        data_frame = pd.read_excel(data_file)

    column_aliases = {
        "name": ["name", "blood bank name", "blood_bank_name", "blood centre name", "blood center name", "hospital name", "hospital_name"],
        "type": ["type", "category", "facility type", "ownership"],
        "state": ["state", "state / ut", "state ut", "state/ut"],
        "district": ["district"],
        "city": ["city", "town"],
        "address": ["address", "full address"],
        "phone": ["phone", "phone number", "mobile", "contact", "contact number"],
        "email": ["email", "email address"],
        "map_link": ["map link", "map url", "google maps link"]
    }

    normalized_columns = {
        str(column).strip().lower().replace("-", " ").replace("_", " "): column
        for column in data_frame.columns
    }
    records = []

    for row in data_frame.fillna("").to_dict(orient="records"):
        record = {}
        for field, aliases in column_aliases.items():
            value = ""
            for alias in aliases:
                source_column = normalized_columns.get(alias)
                if source_column is not None:
                    value = row.get(source_column, "")
                    break
            record[field] = str(value).strip()
        if any(record.values()):
            records.append(record)

    return records


@app.route("/blood-bank")
@app.route("/blood-banks")
def blood_bank():
    blood_banks = load_blood_banks()
    filters = {
        "search": request.args.get("search", "").strip().lower(),
        "type": request.args.get("type", "").strip().lower(),
        "state": request.args.get("state", "").strip().lower(),
        "district": request.args.get("district", "").strip().lower(),
        "city": request.args.get("city", "").strip().lower()
    }

    def matches(bank):
        searchable_name = (bank.get("name", "") or "").lower()
        return (
            (not filters["search"] or filters["search"] in searchable_name)
            and (not filters["type"] or filters["type"] == (bank.get("type", "") or "").lower())
            and (not filters["state"] or filters["state"] in (bank.get("state", "") or "").lower())
            and (not filters["district"] or filters["district"] in (bank.get("district", "") or "").lower())
            and (not filters["city"] or filters["city"] in (bank.get("city", "") or "").lower())
        )

    return render_template(
        "blood_bank.html",
        blood_banks=[bank for bank in blood_banks if matches(bank)]
    )


# =========================
# HOSPITALS
# =========================

@app.route("/hospitals")
def hospitals():

    hospitals = [

        {
            "name": "Government General Hospital",
            "location": "Guntur",
            "phone": "0863-2222222"
        },

        {
            "name": "Apollo Hospital",
            "location": "Vijayawada",
            "phone": "0866-2222222"
        },

        {
            "name": "City Care Hospital",
            "location": "Tenali",
            "phone": "08644-222222"
        }

    ]

    return render_template(
        "hospitals.html",
        hospitals=hospitals
    )


# =========================
# AMBULANCE SERVICES
# =========================

@app.route("/ambulances")
def ambulances():
    ambulance_services = [
        {
            "name": "Emergency Ambulance",
            "location": "Guntur",
            "phone": "108",
            "availability": "24/7"
        },
        {
            "name": "Apollo Emergency Ambulance",
            "location": "Vijayawada",
            "phone": "0866-2333333",
            "availability": "24/7"
        },
        {
            "name": "City Care Ambulance",
            "location": "Tenali",
            "phone": "08644-222222",
            "availability": "Available"
        }
    ]

    return render_template(
        "ambulances.html",
        ambulance_services=ambulance_services
    )


# =========================
# FIRST AID AND HELPLINES
# =========================

@app.route("/first-aid")
def first_aid():
    helplines = [
        {
            "name": "Emergency Medical Helpline",
            "description": "For urgent medical emergencies and immediate assistance.",
            "phone": "108",
            "availability": "24/7"
        },
        {
            "name": "National Emergency Helpline",
            "description": "For police, fire, ambulance, and other emergency support.",
            "phone": "112",
            "availability": "24/7"
        },
        {
            "name": "Blood Support Desk",
            "description": "For help finding blood banks and coordinating urgent requests.",
            "phone": "1800-123-4567",
            "availability": "Available"
        }
    ]

    first_aid_steps = [
        "Call 108 or 112 for a serious or life-threatening emergency.",
        "Keep the person safe, still, and as comfortable as possible.",
        "For heavy bleeding, apply firm pressure with clean cloth or gauze.",
        "Do not give food or drink to an unconscious or seriously injured person.",
        "Follow the emergency operator's instructions until help arrives."
    ]

    return render_template(
        "first_aid.html",
        helplines=helplines,
        first_aid_steps=first_aid_steps
    )


# =========================
# NOTIFICATIONS
# =========================

@app.route("/notifications")
def notifications():
    if firestore_primary_enabled():
        requests = []
        for doc in firestore_client.collection("blood_requests").stream():
            item = doc.to_dict() or {}
            if str(item.get("status", "Open")).lower() in {"open", "pending"}:
                item["id"] = doc.id
                requests.append(item)
        requests = sorted(requests, key=lambda item: str(item.get("created_at") or ""), reverse=True)[:20]
    else:
        connection = get_database()
        requests = [dict(row) for row in connection.execute(
            """
            SELECT patient_name, blood_group, hospital_name, hospital_location,
                   emergency_level, created_at
            FROM blood_request
            WHERE lower(coalesce(status, 'Open')) IN ('open', 'pending')
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()]
        connection.close()

    notifications_list = [
        {
            "message": (
                f"New {request['blood_group']} blood request for "
                f"{request['patient_name']} at {request['hospital_name']}"
            ),
            "details": (
                f"{request['hospital_location'] or 'Location not provided'} "
                f"• {request['emergency_level']} urgency"
            ),
            "time": format_elapsed_time(request.get("created_at"))
        }
        for request in requests
    ]

    return render_template(
        "notifications.html",
        notifications=notifications_list
    )


# =========================
# INFORMATION PAGES
# =========================

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()

        if not name or not email or not subject or len(message) < 5:
            flash("Please complete all required contact fields.", "error")
            return render_template("contact.html")

        created_at = datetime.now()
        contact_payload = {
            "name": name,
            "email": email,
            "phone": phone,
            "subject": subject,
            "message": message,
            "created_at": created_at
        }

        if firestore_primary_enabled():
            sync_firestore_document("contact_messages", f"contact-{int(created_at.timestamp() * 1000)}", contact_payload)
        else:
            connection = get_database()
            cursor = connection.execute(
                """
                INSERT INTO contact_message
                    (name, email, phone, subject, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, email, phone, subject, message, created_at)
            )
            connection.commit()
            connection.close()
            sync_firestore_document(
                "contact_messages",
                cursor.lastrowid,
                contact_payload
            )
        flash("Your message was sent successfully.", "success")
        return redirect(url_for("contact"))

    return render_template(
        "contact.html",
        selected_subject=request.args.get("subject", ""),
        bank_name=request.args.get("bank", "").strip(),
        bank_city=request.args.get("city", "").strip(),
        bank_state=request.args.get("state", "").strip()
    )

@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/testimonials")
def testimonials():
    return render_template("testimonials.html")


@app.route("/feedback", methods=["GET", "POST"])
def feedback():
    if request.method == "POST":
        flash("Thank you for sharing your feedback.", "success")
        return redirect(url_for("feedback"))

    return render_template("feedback.html")


# =========================
# SETTINGS
# =========================

@app.route("/settings", methods=["GET", "POST"])
def settings():

    profile = session.get("profile", DEFAULT_PROFILE.copy())

    if request.method == "GET" and profile.get("email"):
        if firestore_primary_enabled():
            for doc in firestore_client.collection("donors").stream():
                donor = doc.to_dict() or {}
                if str(donor.get("email", "")).lower() == str(profile.get("email", "")).lower():
                    profile = {
                        "user_id": donor.get("user_id") or doc.id,
                        "name": donor.get("full_name") or donor.get("name"),
                        "email": donor.get("email"),
                        "phone": donor.get("mobile") or donor.get("phone"),
                        "blood_group": donor.get("blood_group"),
                        "location": donor.get("city") or donor.get("location")
                    }
                    session["profile"] = profile
                    break
        else:
            connection = get_database()
            user_row = connection.execute(
                """
                SELECT user_id, full_name, email, mobile, blood_group, city
                FROM `user`
                WHERE lower(email) = lower(?)
                LIMIT 1
                """,
                (profile["email"],)
            ).fetchone()
            connection.close()

            if user_row:
                if isinstance(user_row, dict):
                    profile = {
                        "user_id": user_row["user_id"],
                        "name": user_row["full_name"],
                        "email": user_row["email"],
                        "phone": user_row["mobile"],
                        "blood_group": user_row["blood_group"],
                        "location": user_row["city"]
                    }
                else:
                    profile = {
                        "user_id": user_row["user_id"],
                        "name": user_row["full_name"],
                        "email": user_row["email"],
                        "phone": user_row["mobile"],
                        "blood_group": user_row["blood_group"],
                        "location": user_row["city"]
                    }
                session["profile"] = profile

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        blood_group = request.form.get("blood_group", "").strip()
        location = request.form.get("location", "").strip()

        if not name or not email or not blood_group or not location:
            flash("Please fill in all required profile fields.", "error")
            return render_template("settings.html", profile=profile)

        updated_profile = {
            "full_name": name,
            "email": email,
            "mobile": phone,
            "blood_group": blood_group,
            "city": location,
            "user_id": profile.get("user_id") or profile.get("email"),
            "updated_at": datetime.now()
        }

        if firestore_primary_enabled():
            user_document_id = profile.get("user_id") or profile.get("email")
            sync_firestore_document("donors", user_document_id, updated_profile)
        else:
            connection = get_database()
            user_row = connection.execute(
                "SELECT id FROM `user` WHERE user_id = ? OR lower(email) = lower(?) LIMIT 1",
                (profile.get("user_id", ""), profile.get("email", ""))
            ).fetchone()
            cursor = connection.execute(
                """
                UPDATE `user`
                SET full_name = ?, email = ?, mobile = ?, blood_group = ?, city = ?
                WHERE user_id = ? OR lower(email) = lower(?)
                """,
                (
                    name,
                    email,
                    phone,
                    blood_group,
                    location,
                    profile.get("user_id", ""),
                    profile.get("email", "")
                )
            )
            connection.commit()
            connection.close()

            if cursor.rowcount == 0:
                session.clear()
                flash("Please log in with your registered donor email before editing your profile.", "error")
                return redirect(url_for("login"))

            user_document_id = (
                user_row["id"] if isinstance(user_row, dict) else user_row[0]
            ) if user_row else None
            sync_firestore_document(
                "donors",
                user_document_id,
                {
                    "full_name": name,
                    "email": email,
                    "mobile": phone,
                    "blood_group": blood_group,
                    "city": location,
                    "updated_at": datetime.now()
                }
            )

        session["profile"] = {
            "name": name,
            "email": email,
            "phone": phone,
            "blood_group": blood_group,
            "location": location
        }

        flash("Settings saved successfully.", "success")
        return redirect(url_for("settings"))

    return render_template("settings.html", profile=profile)


# =========================
# LOGOUT
# =========================

@app.route("/logout")
def logout():

    session.clear()
    flash("You have been logged out.", "success")

    return redirect(url_for("home"))


# =========================
# ERROR HANDLERS
# =========================

@app.errorhandler(404)
def page_not_found(error):

    return """
    <h1>404 - Page Not Found</h1>
    <p>The requested page does not exist.</p>
    <a href="/">Go Home</a>
    """, 404


@app.route("/blood-request/<int:request_id>/delete", methods=["POST"])
def delete_blood_request(request_id):
    if firestore_primary_enabled():
        delete_firestore_document("blood_requests", request_id)
        flash("Blood request deleted successfully.", "success")
        return redirect(url_for("dashboard"))

    connection = get_database()
    cursor = connection.execute(
        "DELETE FROM blood_request WHERE id = ?",
        (request_id,)
    )
    connection.commit()
    connection.close()

    if cursor.rowcount:
        delete_firestore_document("blood_requests", request_id)
        flash("Blood request deleted successfully.", "success")
    else:
        flash("Blood request was not found.", "error")

    return redirect(url_for("dashboard"))


# =========================
# RUN APPLICATION
# =========================

if __name__ == "__main__":
    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000
    )