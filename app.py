import os
import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify
)

from werkzeug.security import generate_password_hash, check_password_hash


# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "smart-blood-donor-dev-secret-key"
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# ============================================================
# DEFAULT PROFILE
# ============================================================

DEFAULT_PROFILE = {
    "name": "Maruthi",
    "email": "maruthi@example.com",
    "phone": "",
    "blood_group": "O+",
    "location": "Guntur"
}


# ============================================================
# FILE PATHS
# ============================================================

# Local Excel file.
# If this file does not exist, sample blood-bank data will be used.
BLOOD_BANK_FILE = Path(
    r"C:\Users\cheru\OneDrive\Documents\India_Blood_Banks.xlsx"
)


# ============================================================
# DATABASE PATH
# ============================================================

# IMPORTANT:
# On Render, use /tmp because it is writable.
#
# On your own computer, use the normal Flask instance folder.

if os.getenv("RENDER"):
    DATABASE_FILE = Path("/tmp/smart_blood_donor.db")
else:
    DATABASE_FILE = Path(app.instance_path) / "smart_blood_donor.db"


# Make sure the database directory exists.
DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)


# ============================================================
# FIREBASE / FIRESTORE
# ============================================================

firestore_client = None


def initialize_firestore():
    """
    Initialize Firebase Firestore.

    Supported environment variables:

    FIREBASE_PROJECT_ID
    FIREBASE_SERVICE_ACCOUNT
    FIREBASE_SERVICE_ACCOUNT_JSON
    """

    global firestore_client

    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()

    if not project_id:
        logger.info("Firebase Project ID not configured.")
        return None

    try:

        service_account_path = os.getenv(
            "FIREBASE_SERVICE_ACCOUNT",
            ""
        ).strip()

        service_account_json = os.getenv(
            "FIREBASE_SERVICE_ACCOUNT_JSON",
            ""
        ).strip()

        credential = None

        # ----------------------------------------------------
        # Option 1: Service account JSON file
        # ----------------------------------------------------

        if service_account_path:

            path = Path(service_account_path)

            if path.is_file():

                credential = credentials.Certificate(
                    str(path)
                )

        # ----------------------------------------------------
        # Option 2: Service account JSON stored in environment
        # ----------------------------------------------------

        if credential is None and service_account_json:

            service_account_data = json.loads(
                service_account_json
            )

            credential = credentials.Certificate(
                service_account_data
            )

        # ----------------------------------------------------
        # Option 3: Google Application Default Credentials
        # ----------------------------------------------------

        if credential is None:

            credential = credentials.ApplicationDefault()

        # ----------------------------------------------------
        # Initialize Firebase app
        # ----------------------------------------------------

        try:

            firebase_app = firebase_admin.get_app()

        except ValueError:

            firebase_app = firebase_admin.initialize_app(
                credential,
                {
                    "projectId": project_id
                }
            )

        firestore_client = firestore.client(
            app=firebase_app
        )

        logger.info("Firebase Firestore initialized successfully.")

        return firestore_client

    except Exception:

        logger.exception(
            "Firebase initialization failed. "
            "Continuing with SQL storage."
        )

        return None


firestore_client = initialize_firestore()


def firestore_primary_enabled():
    """
    Firestore is used as the primary database only when:
    1. Firebase is configured
    2. USE_FIRESTORE_PRIMARY is not disabled
    """

    return (
        firestore_client is not None
        and
        os.getenv(
            "USE_FIRESTORE_PRIMARY",
            "true"
        ).strip().lower()
        not in {
            "0",
            "false",
            "no",
            "off"
        }
    )


# ============================================================
# FIRESTORE HELPERS
# ============================================================

def convert_firestore_value(value):
    """
    Convert Firestore timestamp / datetime values
    into a string when necessary.
    """

    if isinstance(value, datetime):
        return value.isoformat()

    try:

        if hasattr(value, "isoformat"):
            return value.isoformat()

    except Exception:
        pass

    return value


def clean_firestore_data(data):
    """
    Make data safe for Firestore.
    """

    cleaned = {}

    for key, value in data.items():

        if isinstance(value, datetime):

            cleaned[key] = value

        elif isinstance(value, dict):

            cleaned[key] = clean_firestore_data(value)

        elif isinstance(value, list):

            cleaned[key] = [
                clean_firestore_data(item)
                if isinstance(item, dict)
                else item
                for item in value
            ]

        else:

            cleaned[key] = value

    return cleaned


def sync_firestore_document(
    collection_name,
    document_id,
    data
):
    """
    Save or update a Firestore document.

    Returns True if successful.
    """

    if firestore_client is None:
        return False

    try:

        cleaned_data = clean_firestore_data(data)

        firestore_client \
            .collection(collection_name) \
            .document(str(document_id)) \
            .set(
                cleaned_data,
                merge=True
            )

        return True

    except Exception:

        logger.exception(
            "Firestore sync failed for %s/%s",
            collection_name,
            document_id
        )

        return False


# ============================================================
# DATABASE CONNECTION
# ============================================================

class DatabaseConnection:

    def __init__(self):

        self.use_mysql = bool(
            os.getenv("MYSQL_DATABASE")
        )

        if self.use_mysql:

            import pymysql

            self.connection = pymysql.connect(

                host=os.getenv(
                    "MYSQL_HOST",
                    "127.0.0.1"
                ),

                port=int(
                    os.getenv(
                        "MYSQL_PORT",
                        "3306"
                    )
                ),

                user=os.getenv(
                    "MYSQL_USER",
                    "root"
                ),

                password=os.getenv(
                    "MYSQL_PASSWORD",
                    ""
                ),

                database=os.environ[
                    "MYSQL_DATABASE"
                ],

                cursorclass=pymysql.cursors.DictCursor,

                autocommit=False
            )

            self.initialize_mysql_schema()

        else:

            self.connection = sqlite3.connect(
                str(DATABASE_FILE),
                timeout=30
            )

            self.connection.row_factory = sqlite3.Row

            self.initialize_sqlite_schema()


    # ========================================================
    # SQLITE SCHEMA
    # ========================================================

    def initialize_sqlite_schema(self):

        cursor = self.connection.cursor()

        # ----------------------------------------------------
        # USER TABLE
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                full_name TEXT NOT NULL,

                blood_group TEXT NOT NULL,

                mobile TEXT,

                email TEXT NOT NULL,

                country TEXT,

                state TEXT,

                district TEXT,

                city TEXT,

                user_id TEXT NOT NULL,

                password_hash TEXT,

                availability TEXT DEFAULT 'Available',

                last_donation_date TEXT,

                created_at DATETIME

            )
            """
        )


        # ----------------------------------------------------
        # BLOOD REQUEST TABLE
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS blood_request (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                requester_id TEXT,

                patient_name TEXT,

                blood_group TEXT,

                units INTEGER,

                required_date TEXT,

                emergency_level TEXT,

                hospital_name TEXT,

                hospital_address TEXT,

                city TEXT,

                district TEXT,

                state TEXT,

                contact_phone TEXT,

                message TEXT,

                status TEXT DEFAULT 'Pending',

                created_at DATETIME

            )
            """
        )


        # ----------------------------------------------------
        # CONTACT MESSAGE TABLE
        # ----------------------------------------------------

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS contact_message (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                name TEXT NOT NULL,

                email TEXT NOT NULL,

                phone TEXT,

                subject TEXT NOT NULL,

                message TEXT NOT NULL,

                created_at DATETIME

            )
            """
        )


        self.connection.commit()


    # ========================================================
    # MYSQL SCHEMA
    # ========================================================

    def initialize_mysql_schema(self):

        cursor = self.connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS `user` (

                id INT AUTO_INCREMENT PRIMARY KEY,

                full_name VARCHAR(120) NOT NULL,

                blood_group VARCHAR(10) NOT NULL,

                mobile VARCHAR(20),

                email VARCHAR(120) NOT NULL,

                country VARCHAR(100),

                state VARCHAR(100),

                district VARCHAR(100),

                city VARCHAR(100),

                user_id VARCHAR(120) NOT NULL,

                password_hash VARCHAR(255),

                availability VARCHAR(50)
                    DEFAULT 'Available',

                last_donation_date DATE NULL,

                created_at DATETIME

            )
            """
        )


        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS blood_request (

                id INT AUTO_INCREMENT PRIMARY KEY,

                requester_id VARCHAR(120),

                patient_name VARCHAR(120),

                blood_group VARCHAR(10),

                units INT,

                required_date VARCHAR(30),

                emergency_level VARCHAR(50),

                hospital_name VARCHAR(200),

                hospital_address TEXT,

                city VARCHAR(100),

                district VARCHAR(100),

                state VARCHAR(100),

                contact_phone VARCHAR(30),

                message TEXT,

                status VARCHAR(50)
                    DEFAULT 'Pending',

                created_at DATETIME

            )
            """
        )


        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS contact_message (

                id INT AUTO_INCREMENT PRIMARY KEY,

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


    # ========================================================
    # EXECUTE
    # ========================================================

    def execute(
        self,
        query,
        params=()
    ):

        if self.use_mysql:

            query = query.replace(
                "?",
                "%s"
            )

            cursor = self.connection.cursor()

            cursor.execute(
                query,
                params
            )

            return cursor

        return self.connection.execute(
            query,
            params
        )


    # ========================================================
    # COMMIT
    # ========================================================

    def commit(self):

        self.connection.commit()


    # ========================================================
    # CLOSE
    # ========================================================

    def close(self):

        try:

            self.connection.close()

        except Exception:
            pass


def get_database():

    return DatabaseConnection()


# ============================================================
# GENERAL HELPERS
# ============================================================

def row_to_dict(row):

    if row is None:
        return None

    if isinstance(row, dict):
        return dict(row)

    return {
        key: row[key]
        for key in row.keys()
    }


def rows_to_dicts(rows):

    return [
        row_to_dict(row)
        for row in rows
    ]


def get_logged_in_profile():

    return session.get(
        "profile",
        DEFAULT_PROFILE.copy()
    )


def save_session_profile(user):

    if not user:
        return

    if isinstance(user, dict):

        session["profile"] = {

            "user_id": (
                user.get("user_id")
                or user.get("id")
            ),

            "name": (
                user.get("full_name")
                or user.get("name")
                or ""
            ),

            "email": user.get(
                "email",
                ""
            ),

            "phone": (
                user.get("mobile")
                or user.get("phone")
                or ""
            ),

            "blood_group": user.get(
                "blood_group",
                ""
            ),

            "location": (
                user.get("city")
                or user.get("location")
                or ""
            )

        }


def find_sql_user_by_email_or_id(
    email_or_id
):

    connection = get_database()

    try:

        row = connection.execute(
            """
            SELECT
                id,
                full_name,
                blood_group,
                mobile,
                email,
                country,
                state,
                district,
                city,
                user_id,
                password_hash,
                availability,
                last_donation_date,
                created_at
            FROM `user`
            WHERE lower(email) = lower(?)
               OR lower(user_id) = lower(?)
            LIMIT 1
            """,
            (
                email_or_id,
                email_or_id
            )
        ).fetchone()

        return row_to_dict(row)

    finally:

        connection.close()


def firestore_user_by_email_or_id(
    email_or_id
):

    if firestore_client is None:
        return None

    search_value = str(
        email_or_id
    ).strip().lower()

    try:

        # First try document ID.
        document = (
            firestore_client
            .collection("donors")
            .document(str(email_or_id))
            .get()
        )

        if document.exists:

            data = document.to_dict() or {}

            data["id"] = document.id

            return data


        # Search email.
        documents = (
            firestore_client
            .collection("donors")
            .stream()
        )

        for document in documents:

            data = document.to_dict() or {}

            donor_email = str(
                data.get("email", "")
                or ""
            ).strip().lower()

            donor_user_id = str(
                data.get("user_id", "")
                or ""
            ).strip().lower()

            if (
                donor_email == search_value
                or
                donor_user_id == search_value
            ):

                data["id"] = document.id

                return data

    except Exception:

        logger.exception(
            "Firestore user lookup failed."
        )

    return None


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    profile = get_logged_in_profile()

    return render_template(
        "index.html",
        profile=profile
    )


# ============================================================
# ABOUT
# ============================================================

@app.route("/about")
def about():

    return render_template(
        "about.html"
    )


# ============================================================
# REGISTER
# ============================================================

@app.route(
    "/register",
    methods=["GET", "POST"]
)
def register():

    if request.method == "POST":

        name = request.form.get(
            "full_name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        blood_group = request.form.get(
            "blood_group",
            ""
        ).strip()

        user_id = request.form.get(
            "user_id",
            ""
        ).strip()

        if not user_id:
            user_id = email


        if not name or not email or not password or not blood_group:

            flash(
                "Please fill all required fields.",
                "error"
            )

            return render_template(
                "register.html"
            )


        password_hash = generate_password_hash(
            password
        )

        created_at = datetime.now()


        # ====================================================
        # FIRESTORE PRIMARY
        # ====================================================

        if firestore_primary_enabled():

            existing_user = firestore_user_by_email_or_id(
                email
            )

            if existing_user:

                flash(
                    "An account with this email or user ID already exists.",
                    "error"
                )

                return render_template(
                    "register.html"
                )


            donor_data = {

                "full_name": name,

                "blood_group": blood_group,

                "mobile": request.form.get(
                    "mobile",
                    ""
                ),

                "email": email,

                "country": request.form.get(
                    "country",
                    "India"
                ),

                "state": request.form.get(
                    "state",
                    ""
                ),

                "district": request.form.get(
                    "district",
                    ""
                ),

                "city": request.form.get(
                    "city",
                    ""
                ),

                "user_id": user_id,

                # Password is HASHED.
                "password_hash": password_hash,

                "availability": request.form.get(
                    "availability",
                    "Available"
                ),

                "created_at": created_at

            }


            if sync_firestore_document(
                "donors",
                user_id,
                donor_data
            ):

                flash(
                    "Registration successful! Please login.",
                    "success"
                )

                return redirect(
                    url_for("login")
                )


            flash(
                "Registration failed. Please try again.",
                "error"
            )

            return render_template(
                "register.html"
            )


        # ====================================================
        # SQLITE / MYSQL PRIMARY
        # ====================================================

        connection = get_database()

        try:

            existing_email = connection.execute(
                """
                SELECT id
                FROM `user`
                WHERE lower(email) = lower(?)
                LIMIT 1
                """,
                (email,)
            ).fetchone()

            existing_user_id = connection.execute(
                """
                SELECT id
                FROM `user`
                WHERE lower(user_id) = lower(?)
                LIMIT 1
                """,
                (user_id,)
            ).fetchone()


            if existing_email or existing_user_id:

                flash(
                    "An account with this email or user ID already exists.",
                    "error"
                )

                return render_template(
                    "register.html"
                )


            connection.execute(
                """
                INSERT INTO `user` (
                    full_name,
                    blood_group,
                    mobile,
                    email,
                    country,
                    state,
                    district,
                    city,
                    user_id,
                    password_hash,
                    availability,
                    created_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    name,

                    blood_group,

                    request.form.get(
                        "mobile",
                        ""
                    ),

                    email,

                    request.form.get(
                        "country",
                        "India"
                    ),

                    request.form.get(
                        "state",
                        ""
                    ),

                    request.form.get(
                        "district",
                        ""
                    ),

                    request.form.get(
                        "city",
                        ""
                    ),

                    user_id,

                    password_hash,

                    request.form.get(
                        "availability",
                        "Available"
                    ),

                    created_at
                )
            )


            connection.commit()


        except Exception:

            connection.connection.rollback()

            logger.exception(
                "Registration failed."
            )

            flash(
                "Registration failed. Please try again.",
                "error"
            )

            return render_template(
                "register.html"
            )

        finally:

            connection.close()


        # ----------------------------------------------------
        # Optional Firestore secondary sync
        # ----------------------------------------------------

        sync_firestore_document(
            "donors",
            user_id,
            {
                "full_name": name,

                "blood_group": blood_group,

                "mobile": request.form.get(
                    "mobile",
                    ""
                ),

                "email": email,

                "country": request.form.get(
                    "country",
                    "India"
                ),

                "state": request.form.get(
                    "state",
                    ""
                ),

                "district": request.form.get(
                    "district",
                    ""
                ),

                "city": request.form.get(
                    "city",
                    ""
                ),

                "user_id": user_id,

                "password_hash": password_hash,

                "availability": request.form.get(
                    "availability",
                    "Available"
                ),

                "created_at": created_at
            }
        )


        flash(
            "Registration successful! Please login.",
            "success"
        )

        return redirect(
            url_for("login")
        )


    return render_template(
        "register.html"
    )


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )


        if not email or not password:

            flash(
                "Please enter email and password.",
                "error"
            )

            return render_template(
                "login.html"
            )


        # ====================================================
        # FIRESTORE LOGIN
        # ====================================================

        if firestore_primary_enabled():

            user_row = firestore_user_by_email_or_id(
                email
            )

            if not user_row:

                flash(
                    "No registered account found for this email. Please register first.",
                    "error"
                )

                return render_template(
                    "login.html"
                )


            stored_hash = user_row.get(
                "password_hash"
            )


            if not stored_hash:

                flash(
                    "This account was created before password security was enabled. Please register a new account.",
                    "error"
                )

                return render_template(
                    "login.html"
                )


            try:

                password_valid = check_password_hash(
                    stored_hash,
                    password
                )

            except Exception:

                password_valid = False


            if not password_valid:

                flash(
                    "Invalid email/user ID or password.",
                    "error"
                )

                return render_template(
                    "login.html"
                )


            save_session_profile(
                user_row
            )

            flash(
                "Login successful!",
                "success"
            )

            return redirect(
                url_for("dashboard")
            )


        # ====================================================
        # SQLITE / MYSQL LOGIN
        # ====================================================

        user_row = find_sql_user_by_email_or_id(
            email
        )


        if not user_row:

            flash(
                "No registered account found for this email. Please register first.",
                "error"
            )

            return render_template(
                "login.html"
            )


        stored_hash = user_row.get(
            "password_hash"
        )


        if not stored_hash:

            flash(
                "This account does not have a valid password. Please register again.",
                "error"
            )

            return render_template(
                "login.html"
            )


        try:

            password_valid = check_password_hash(
                stored_hash,
                password
            )

        except Exception:

            password_valid = False


        if not password_valid:

            flash(
                "Invalid email/user ID or password.",
                "error"
            )

            return render_template(
                "login.html"
            )


        save_session_profile(
            user_row
        )


        flash(
            "Login successful!",
            "success"
        )

        return redirect(
            url_for("dashboard")
        )


    return render_template(
        "login.html"
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    flash(
        "You have been logged out.",
        "success"
    )

    return redirect(
        url_for("home")
    )


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/dashboard")
def dashboard():

    profile = get_logged_in_profile()

    requests_data = []
    donors = []

    # ========================================================
    # FIRESTORE
    # ========================================================

    if firestore_primary_enabled():

        try:

            request_documents = (
                firestore_client
                .collection("blood_requests")
                .stream()
            )

            for document in request_documents:

                data = document.to_dict() or {}

                data["id"] = document.id

                requests_data.append(
                    data
                )


            donor_documents = (
                firestore_client
                .collection("donors")
                .stream()
            )

            for document in donor_documents:

                data = document.to_dict() or {}

                data["id"] = document.id

                donors.append(
                    data
                )

        except Exception:

            logger.exception(
                "Dashboard Firestore loading failed."
            )


    # ========================================================
    # SQL
    # ========================================================

    else:

        connection = get_database()

        try:

            request_rows = connection.execute(
                """
                SELECT *
                FROM blood_request
                ORDER BY id DESC
                """
            ).fetchall()

            requests_data = rows_to_dicts(
                request_rows
            )


            donor_rows = connection.execute(
                """
                SELECT
                    id,
                    full_name,
                    blood_group,
                    mobile,
                    email,
                    country,
                    state,
                    district,
                    city,
                    user_id,
                    availability,
                    last_donation_date,
                    created_at
                FROM `user`
                ORDER BY id DESC
                """
            ).fetchall()

            donors = rows_to_dicts(
                donor_rows
            )

        finally:

            connection.close()


    return render_template(
        "dashboard.html",

        profile=profile,

        requests=requests_data,

        blood_requests=requests_data,

        donors=donors,

        donor_count=len(donors),

        request_count=len(requests_data)
    )


# ============================================================
# FIND DONOR
# ============================================================

@app.route(
    "/find-donor",
    methods=["GET", "POST"]
)
def find_donor():

    donors = []

    selected_blood_group = ""
    selected_city = ""

    if request.method == "POST":

        selected_blood_group = request.form.get(
            "blood_group",
            ""
        ).strip()

        selected_city = request.form.get(
            "city",
            ""
        ).strip()


    else:

        selected_blood_group = request.args.get(
            "blood_group",
            ""
        ).strip()

        selected_city = request.args.get(
            "city",
            ""
        ).strip()


    # ========================================================
    # FIRESTORE
    # ========================================================

    if firestore_primary_enabled():

        try:

            documents = (
                firestore_client
                .collection("donors")
                .stream()
            )

            for document in documents:

                donor = document.to_dict() or {}

                donor["id"] = document.id

                if selected_blood_group:

                    if str(
                        donor.get(
                            "blood_group",
                            ""
                        )
                    ).upper() != selected_blood_group.upper():

                        continue


                if selected_city:

                    donor_city = str(
                        donor.get(
                            "city",
                            ""
                        )
                    ).lower()

                    if selected_city.lower() not in donor_city:

                        continue


                donors.append(
                    donor
                )

        except Exception:

            logger.exception(
                "Firestore donor search failed."
            )


    # ========================================================
    # SQL
    # ========================================================

    else:

        connection = get_database()

        try:

            query = """
                SELECT
                    id,
                    full_name,
                    blood_group,
                    mobile,
                    email,
                    country,
                    state,
                    district,
                    city,
                    user_id,
                    availability,
                    last_donation_date,
                    created_at
                FROM `user`
                WHERE 1 = 1
            """

            params = []


            if selected_blood_group:

                query += """
                    AND lower(blood_group) = lower(?)
                """

                params.append(
                    selected_blood_group
                )


            if selected_city:

                query += """
                    AND lower(city) LIKE lower(?)
                """

                params.append(
                    f"%{selected_city}%"
                )


            query += """
                ORDER BY id DESC
            """


            rows = connection.execute(
                query,
                tuple(params)
            ).fetchall()


            donors = rows_to_dicts(
                rows
            )

        finally:

            connection.close()


    return render_template(
        "find_donor.html",

        donors=donors,

        results=donors,

        blood_group=selected_blood_group,

        selected_blood_group=selected_blood_group,

        city=selected_city,

        selected_city=selected_city
    )


# ============================================================
# BLOOD REQUEST
# ============================================================

@app.route(
    "/blood-request",
    methods=["GET", "POST"]
)
def blood_request():

    if request.method == "POST":

        patient_name = request.form.get(
            "patient_name",
            ""
        ).strip()

        blood_group = request.form.get(
            "blood_group",
            ""
        ).strip()

        units = request.form.get(
            "units",
            "1"
        ).strip()

        required_date = request.form.get(
            "required_date",
            ""
        ).strip()

        emergency_level = request.form.get(
            "emergency_level",
            "Normal"
        ).strip()

        hospital_name = request.form.get(
            "hospital_name",
            ""
        ).strip()

        hospital_address = request.form.get(
            "hospital_address",
            ""
        ).strip()

        city = request.form.get(
            "city",
            ""
        ).strip()

        district = request.form.get(
            "district",
            ""
        ).strip()

        state = request.form.get(
            "state",
            ""
        ).strip()

        contact_phone = request.form.get(
            "phone",
            request.form.get(
                "contact_phone",
                ""
            )
        ).strip()

        message = request.form.get(
            "message",
            ""
        ).strip()


        try:

            units_value = int(units)

        except ValueError:

            units_value = 1


        profile = get_logged_in_profile()

        requester_id = profile.get(
            "user_id",
            ""
        )


        created_at = datetime.now()


        request_data = {

            "requester_id": requester_id,

            "patient_name": patient_name,

            "blood_group": blood_group,

            "units": units_value,

            "required_date": required_date,

            "emergency_level": emergency_level,

            "hospital_name": hospital_name,

            "hospital_address": hospital_address,

            "city": city,

            "district": district,

            "state": state,

            "contact_phone": contact_phone,

            "message": message,

            "status": "Pending",

            "created_at": created_at
        }


        # ====================================================
        # FIRESTORE
        # ====================================================

        if firestore_primary_enabled():

            document_id = (
                f"{requester_id or 'guest'}_"
                f"{int(datetime.now().timestamp() * 1000)}"
            )

            if sync_firestore_document(
                "blood_requests",
                document_id,
                request_data
            ):

                flash(
                    "Blood request submitted successfully.",
                    "success"
                )

                return redirect(
                    url_for("dashboard")
                )


            flash(
                "Unable to submit blood request. Please try again.",
                "error"
            )

            return render_template(
                "request_details.html"
            )


        # ====================================================
        # SQL
        # ====================================================

        connection = get_database()

        try:

            connection.execute(
                """
                INSERT INTO blood_request (
                    requester_id,
                    patient_name,
                    blood_group,
                    units,
                    required_date,
                    emergency_level,
                    hospital_name,
                    hospital_address,
                    city,
                    district,
                    state,
                    contact_phone,
                    message,
                    status,
                    created_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    requester_id,

                    patient_name,

                    blood_group,

                    units_value,

                    required_date,

                    emergency_level,

                    hospital_name,

                    hospital_address,

                    city,

                    district,

                    state,

                    contact_phone,

                    message,

                    "Pending",

                    created_at
                )
            )

            connection.commit()

        except Exception:

            connection.connection.rollback()

            logger.exception(
                "Blood request failed."
            )

            flash(
                "Unable to submit blood request.",
                "error"
            )

            return render_template(
                "request_details.html"
            )

        finally:

            connection.close()


        flash(
            "Blood request submitted successfully.",
            "success"
        )

        return redirect(
            url_for("dashboard")
        )


    return render_template(
        "request_details.html"
    )


# ============================================================
# DELETE BLOOD REQUEST
# ============================================================

@app.route(
    "/blood-request/<int:request_id>/delete",
    methods=["POST"]
)
def delete_blood_request(request_id):

    # ========================================================
    # FIRESTORE
    # ========================================================

    if firestore_primary_enabled():

        try:

            (
                firestore_client
                .collection("blood_requests")
                .document(str(request_id))
                .delete()
            )

            flash(
                "Blood request deleted.",
                "success"
            )

        except Exception:

            logger.exception(
                "Firestore blood request delete failed."
            )

            flash(
                "Unable to delete blood request.",
                "error"
            )

        return redirect(
            url_for("dashboard")
        )


    # ========================================================
    # SQL
    # ========================================================

    connection = get_database()

    try:

        connection.execute(
            """
            DELETE FROM blood_request
            WHERE id = ?
            """,
            (request_id,)
        )

        connection.commit()

        flash(
            "Blood request deleted.",
            "success"
        )

    except Exception:

        connection.connection.rollback()

        logger.exception(
            "Blood request delete failed."
        )

        flash(
            "Unable to delete blood request.",
            "error"
        )

    finally:

        connection.close()


    return redirect(
        url_for("dashboard")
    )


# ============================================================
# CONTACT
# ============================================================

@app.route(
    "/contact",
    methods=["GET", "POST"]
)
def contact():

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip()

        phone = request.form.get(
            "phone",
            ""
        ).strip()

        subject = request.form.get(
            "subject",
            ""
        ).strip()

        message = request.form.get(
            "message",
            ""
        ).strip()


        created_at = datetime.now()


        if not name or not email or not subject or not message:

            flash(
                "Please fill all required fields.",
                "error"
            )

            return render_template(
                "contact.html"
            )


        contact_data = {

            "name": name,

            "email": email,

            "phone": phone,

            "subject": subject,

            "message": message,

            "created_at": created_at

        }


        # ====================================================
        # FIRESTORE
        # ====================================================

        if firestore_primary_enabled():

            document_id = (
                f"contact_"
                f"{int(datetime.now().timestamp() * 1000)}"
            )

            if sync_firestore_document(
                "contact_messages",
                document_id,
                contact_data
            ):

                flash(
                    "Your message has been sent successfully.",
                    "success"
                )

            else:

                flash(
                    "Unable to send your message.",
                    "error"
                )

            return render_template(
                "contact.html"
            )


        # ====================================================
        # SQL
        # ====================================================

        connection = get_database()

        try:

            connection.execute(
                """
                INSERT INTO contact_message (
                    name,
                    email,
                    phone,
                    subject,
                    message,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    email,
                    phone,
                    subject,
                    message,
                    created_at
                )
            )

            connection.commit()

            flash(
                "Your message has been sent successfully.",
                "success"
            )

        except Exception:

            connection.connection.rollback()

            logger.exception(
                "Contact message failed."
            )

            flash(
                "Unable to send your message.",
                "error"
            )

        finally:

            connection.close()


    return render_template(
        "contact.html"
    )


# ============================================================
# FEEDBACK
# ============================================================

@app.route(
    "/feedback",
    methods=["GET", "POST"]
)
def feedback():

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip()

        message = request.form.get(
            "message",
            ""
        ).strip()

        rating = request.form.get(
            "rating",
            ""
        ).strip()


        if not message:

            flash(
                "Please enter your feedback.",
                "error"
            )

            return render_template(
                "feedback.html"
            )


        feedback_data = {

            "name": name,

            "email": email,

            "message": message,

            "rating": rating,

            "created_at": datetime.now()

        }


        if firestore_primary_enabled():

            document_id = (
                f"feedback_"
                f"{int(datetime.now().timestamp() * 1000)}"
            )

            sync_firestore_document(
                "feedback",
                document_id,
                feedback_data
            )

        else:

            # Store feedback using contact table
            # when only SQL storage is available.

            connection = get_database()

            try:

                connection.execute(
                    """
                    INSERT INTO contact_message (
                        name,
                        email,
                        phone,
                        subject,
                        message,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name or "Anonymous",

                        email or "",

                        "",

                        f"Feedback Rating: {rating}",

                        message,

                        datetime.now()
                    )
                )

                connection.commit()

            except Exception:

                connection.connection.rollback()

                logger.exception(
                    "Feedback save failed."
                )

            finally:

                connection.close()


        flash(
            "Thank you for your feedback!",
            "success"
        )

        return redirect(
            url_for("feedback")
        )


    return render_template(
        "feedback.html"
    )


# ============================================================
# SETTINGS
# ============================================================

@app.route(
    "/settings",
    methods=["GET", "POST"]
)
def settings():

    profile = get_logged_in_profile()

    if request.method == "POST":

        name = request.form.get(
            "full_name",
            profile.get("name", "")
        ).strip()

        phone = request.form.get(
            "phone",
            profile.get("phone", "")
        ).strip()

        blood_group = request.form.get(
            "blood_group",
            profile.get("blood_group", "")
        ).strip()

        city = request.form.get(
            "city",
            profile.get("location", "")
        ).strip()

        user_id = profile.get(
            "user_id"
        )

        # ====================================================
        # FIRESTORE
        # ====================================================

        if firestore_primary_enabled():

            if user_id:

                sync_firestore_document(
                    "donors",
                    user_id,
                    {
                        "full_name": name,
                        "mobile": phone,
                        "blood_group": blood_group,
                        "city": city
                    }
                )


        # ====================================================
        # SQL
        # ====================================================

        else:

            connection = get_database()

            try:

                if user_id:

                    connection.execute(
                        """
                        UPDATE `user`
                        SET
                            full_name = ?,
                            mobile = ?,
                            blood_group = ?,
                            city = ?
                        WHERE user_id = ?
                        """,
                        (
                            name,
                            phone,
                            blood_group,
                            city,
                            user_id
                        )
                    )

                    connection.commit()

            except Exception:

                connection.connection.rollback()

                logger.exception(
                    "Settings update failed."
                )

            finally:

                connection.close()


        session["profile"] = {

            "user_id": user_id,

            "name": name,

            "email": profile.get(
                "email",
                ""
            ),

            "phone": phone,

            "blood_group": blood_group,

            "location": city

        }


        flash(
            "Settings updated successfully.",
            "success"
        )

        return redirect(
            url_for("settings")
        )


    return render_template(
        "settings.html",
        profile=profile
    )


# ============================================================
# REGISTER PROFILE
# ============================================================

@app.route(
    "/register-profile",
    methods=["POST"]
)
def register_profile():

    data = request.get_json(
        silent=True
    )

    if not data:

        data = request.form.to_dict()


    name = data.get(
        "full_name",
        data.get("name", "")
    )

    email = data.get(
        "email",
        ""
    )

    blood_group = data.get(
        "blood_group",
        ""
    )

    user_id = data.get(
        "user_id",
        email
    )


    password = data.get(
        "password",
        ""
    )


    password_hash = None

    if password:

        password_hash = generate_password_hash(
            password
        )


    profile_data = {

        "full_name": name,

        "blood_group": blood_group,

        "mobile": data.get(
            "mobile",
            data.get("phone", "")
        ),

        "email": email,

        "country": data.get(
            "country",
            "India"
        ),

        "state": data.get(
            "state",
            ""
        ),

        "district": data.get(
            "district",
            ""
        ),

        "city": data.get(
            "city",
            ""
        ),

        "user_id": user_id,

        "availability": data.get(
            "availability",
            "Available"
        ),

        "created_at": datetime.now()

    }


    if password_hash:

        profile_data[
            "password_hash"
        ] = password_hash


    # ========================================================
    # FIRESTORE
    # ========================================================

    if firestore_primary_enabled():

        success = sync_firestore_document(
            "donors",
            user_id,
            profile_data
        )

        if request.is_json:

            return jsonify(
                {
                    "success": success
                }
            )

        if success:

            flash(
                "Profile registered successfully.",
                "success"
            )

        else:

            flash(
                "Unable to register profile.",
                "error"
            )

        return redirect(
            url_for("dashboard")
        )


    # ========================================================
    # SQL
    # ========================================================

    connection = get_database()

    try:

        existing = connection.execute(
            """
            SELECT id
            FROM `user`
            WHERE lower(user_id) = lower(?)
            LIMIT 1
            """,
            (user_id,)
        ).fetchone()


        if existing:

            if request.is_json:

                return jsonify(
                    {
                        "success": False,
                        "message": "User already exists."
                    }
                ), 409


            flash(
                "User already exists.",
                "error"
            )

            return redirect(
                url_for("dashboard")
            )


        connection.execute(
            """
            INSERT INTO `user` (
                full_name,
                blood_group,
                mobile,
                email,
                country,
                state,
                district,
                city,
                user_id,
                password_hash,
                availability,
                created_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                profile_data["full_name"],

                profile_data["blood_group"],

                profile_data["mobile"],

                profile_data["email"],

                profile_data["country"],

                profile_data["state"],

                profile_data["district"],

                profile_data["city"],

                profile_data["user_id"],

                profile_data.get(
                    "password_hash"
                ),

                profile_data["availability"],

                profile_data["created_at"]
            )
        )

        connection.commit()

    except Exception:

        connection.connection.rollback()

        logger.exception(
            "Profile registration failed."
        )

        if request.is_json:

            return jsonify(
                {
                    "success": False
                }
            ), 500

    finally:

        connection.close()


    if request.is_json:

        return jsonify(
            {
                "success": True
            }
        )


    flash(
        "Profile registered successfully.",
        "success"
    )

    return redirect(
        url_for("dashboard")
    )


# ============================================================
# BLOOD BANK DATA
# ============================================================

def load_blood_banks():

    # --------------------------------------------------------
    # Try Excel file first
    # --------------------------------------------------------

    possible_files = [

        BLOOD_BANK_FILE,

        Path("India_Blood_Banks.xlsx"),

        Path("data/India_Blood_Banks.xlsx"),

        Path(app.instance_path)
        / "India_Blood_Banks.xlsx"

    ]


    for file_path in possible_files:

        try:

            if not file_path.is_file():
                continue


            dataframe = pd.read_excel(
                file_path
            )


            records = dataframe.fillna(
                ""
            ).to_dict(
                orient="records"
            )


            return records

        except Exception:

            logger.exception(
                "Unable to load blood bank Excel file."
            )


    # --------------------------------------------------------
    # Sample fallback data
    # --------------------------------------------------------

    return [

        {
            "name": "Government General Hospital Blood Bank",
            "hospital": "Government General Hospital",
            "city": "Guntur",
            "district": "Guntur",
            "state": "Andhra Pradesh",
            "phone": "0863-2221000",
            "address": "Guntur, Andhra Pradesh"
        },

        {
            "name": "Red Cross Blood Bank",
            "hospital": "Indian Red Cross Society",
            "city": "Vijayawada",
            "district": "NTR",
            "state": "Andhra Pradesh",
            "phone": "0866-2422000",
            "address": "Vijayawada, Andhra Pradesh"
        },

        {
            "name": "Government Hospital Blood Bank",
            "hospital": "Government Hospital",
            "city": "Visakhapatnam",
            "district": "Visakhapatnam",
            "state": "Andhra Pradesh",
            "phone": "0891-2564000",
            "address": "Visakhapatnam, Andhra Pradesh"
        }

    ]


# ============================================================
# BLOOD BANK
# ============================================================

@app.route("/blood-banks")
def blood_bank():

    blood_banks = load_blood_banks()

    return render_template(
        "blood_bank.html",
        blood_banks=blood_banks
    )


# Also support the old singular URL.
@app.route("/blood-bank")
def blood_bank_old():

    return redirect(
        url_for("blood_bank")
    )


# ============================================================
# HOSPITALS
# ============================================================

@app.route("/hospitals")
def hospitals():

    blood_banks = load_blood_banks()

    return render_template(
        "hospitals.html",
        blood_banks=blood_banks
    )


# ============================================================
# AMBULANCES
# ============================================================

@app.route("/ambulances")
def ambulances():

    return render_template(
        "ambulances.html"
    )


# ============================================================
# FIRST AID
# ============================================================

@app.route("/first-aid")
def first_aid():

    return render_template(
        "first_aid.html"
    )


# ============================================================
# NOTIFICATIONS
# ============================================================

@app.route("/notifications")
def notifications():

    notifications_data = []


    profile = get_logged_in_profile()

    user_id = profile.get(
        "user_id"
    )


    # ========================================================
    # FIRESTORE
    # ========================================================

    if firestore_primary_enabled():

        try:

            documents = (
                firestore_client
                .collection("blood_requests")
                .stream()
            )

            for document in documents:

                data = document.to_dict() or {}

                if (
                    not user_id
                    or
                    data.get("requester_id")
                    == user_id
                ):

                    data["id"] = document.id

                    notifications_data.append(
                        data
                    )

        except Exception:

            logger.exception(
                "Notification loading failed."
            )


    # ========================================================
    # SQL
    # ========================================================

    else:

        connection = get_database()

        try:

            rows = connection.execute(
                """
                SELECT *
                FROM blood_request
                ORDER BY id DESC
                """
            ).fetchall()

            notifications_data = rows_to_dicts(
                rows
            )

        finally:

            connection.close()


    return render_template(
        "notifications.html",
        notifications=notifications_data
    )


# ============================================================
# FIREBASE STATUS
# ============================================================

@app.route("/firebase-status")
def firebase_status():

    return jsonify(
        {
            "firebase_configured":
                firestore_client is not None,

            "firestore_primary":
                firestore_primary_enabled()
        }
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@app.errorhandler(500)
def internal_server_error(error):

    logger.exception(
        "Internal server error: %s",
        error
    )

    return render_template(
        "index.html",
        profile=get_logged_in_profile()
    ), 500


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "5000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )