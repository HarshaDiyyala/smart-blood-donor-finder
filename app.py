import os
import sqlite3
import hashlib
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify
)

from werkzeug.security import generate_password_hash, check_password_hash


# =========================================================
# APP CONFIGURATION
# =========================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "lifelink-secret-key-change-this"
)

# Render uses a temporary writable directory
if os.getenv("RENDER"):
    DATABASE_FILE = Path("/tmp/smart_blood_donor.db")
else:
    DATABASE_FILE = Path(app.instance_path) / "smart_blood_donor.db"

DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)


# =========================================================
# DATABASE
# =========================================================

def get_database():
    connection = sqlite3.connect(str(DATABASE_FILE))
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    connection = get_database()
    cursor = connection.cursor()

    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT,
            blood_group TEXT,
            location TEXT,
            password_hash TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Blood requests table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blood_request (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name TEXT NOT NULL,
            blood_group TEXT NOT NULL,
            units INTEGER DEFAULT 1,
            hospital TEXT,
            location TEXT,
            contact TEXT,
            urgency TEXT,
            message TEXT,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Contact messages
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contact_message (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Feedback
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT,
            rating INTEGER,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.commit()
    connection.close()


initialize_database()


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return render_template("index.html")


# =========================================================
# ABOUT US
# =========================================================

@app.route("/about")
def about():
    return render_template("about.html")


# =========================================================
# TESTIMONIALS
# =========================================================

@app.route("/testimonials")
def testimonials():
    return render_template("testimonials.html")


# =========================================================
# REGISTER
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        blood_group = request.form.get("blood_group", "").strip()
        location = request.form.get("location", "").strip()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Please fill all required fields.", "error")
            return redirect(url_for("register"))

        password_hash = generate_password_hash(password)

        connection = get_database()
        cursor = connection.cursor()

        try:
            cursor.execute("""
                INSERT INTO user
                (name, email, phone, blood_group, location, password_hash)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                name,
                email,
                phone,
                blood_group,
                location,
                password_hash
            ))

            connection.commit()

            flash("Registration successful. Please login.", "success")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            flash("Email already registered.", "error")

        finally:
            connection.close()

    return render_template("register.html")


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        connection = get_database()
        user = connection.execute(
            "SELECT * FROM user WHERE email = ?",
            (email,)
        ).fetchone()
        connection.close()

        if user and user["password_hash"]:

            if check_password_hash(
                user["password_hash"],
                password
            ):
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                session["user_email"] = user["email"]

                flash("Login successful.", "success")
                return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    flash("You have been logged out.", "success")

    return redirect(url_for("home"))


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_database()

    requests = connection.execute("""
        SELECT *
        FROM blood_request
        ORDER BY created_at DESC
    """).fetchall()

    connection.close()

    return render_template(
        "dashboard.html",
        requests=requests
    )


# =========================================================
# FIND DONOR
# =========================================================

@app.route("/find-donor", methods=["GET", "POST"])
def find_donor():

    donors = []

    if request.method == "POST":

        blood_group = request.form.get(
            "blood_group",
            ""
        ).strip()

        location = request.form.get(
            "location",
            ""
        ).strip()

        connection = get_database()

        query = """
            SELECT *
            FROM user
            WHERE blood_group = ?
        """

        params = [blood_group]

        if location:
            query += " AND location LIKE ?"
            params.append(f"%{location}%")

        donors = connection.execute(
            query,
            params
        ).fetchall()

        connection.close()

    return render_template(
        "find_donor.html",
        donors=donors
    )


# =========================================================
# BLOOD REQUEST
# =========================================================

@app.route("/blood-request", methods=["GET", "POST"])
@app.route("/request-details", methods=["GET", "POST"])
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

        hospital = request.form.get(
            "hospital",
            ""
        ).strip()

        location = request.form.get(
            "location",
            ""
        ).strip()

        contact = request.form.get(
            "contact",
            ""
        ).strip()

        urgency = request.form.get(
            "urgency",
            ""
        ).strip()

        message = request.form.get(
            "message",
            ""
        ).strip()

        connection = get_database()

        connection.execute("""
            INSERT INTO blood_request
            (
                patient_name,
                blood_group,
                units,
                hospital,
                location,
                contact,
                urgency,
                message,
                created_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            patient_name,
            blood_group,
            units,
            hospital,
            location,
            contact,
            urgency,
            message,
            session.get("user_id")
        ))

        connection.commit()
        connection.close()

        flash(
            "Blood request submitted successfully.",
            "success"
        )

        return redirect(url_for("blood_request"))

    return render_template("request_details.html")


# =========================================================
# DELETE BLOOD REQUEST
# =========================================================

@app.route(
    "/blood-request/<int:request_id>/delete",
    methods=["POST"]
)
def delete_blood_request(request_id):

    connection = get_database()

    connection.execute(
        "DELETE FROM blood_request WHERE id = ?",
        (request_id,)
    )

    connection.commit()
    connection.close()

    flash(
        "Blood request deleted successfully.",
        "success"
    )

    return redirect(url_for("dashboard"))


# =========================================================
# CONTACT
# =========================================================

@app.route("/contact", methods=["GET", "POST"])
def contact():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        message = request.form.get("message", "").strip()

        connection = get_database()

        connection.execute("""
            INSERT INTO contact_message
            (name, email, message)
            VALUES (?, ?, ?)
        """, (
            name,
            email,
            message
        ))

        connection.commit()
        connection.close()

        flash(
            "Your message has been sent.",
            "success"
        )

        return redirect(url_for("contact"))

    return render_template("contact.html")


# =========================================================
# FEEDBACK
# =========================================================

@app.route("/feedback", methods=["GET", "POST"])
def feedback():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        rating = request.form.get("rating", "5").strip()
        message = request.form.get("message", "").strip()

        connection = get_database()

        connection.execute("""
            INSERT INTO feedback
            (name, email, rating, message)
            VALUES (?, ?, ?, ?)
        """, (
            name,
            email,
            rating,
            message
        ))

        connection.commit()
        connection.close()

        flash(
            "Thank you for your feedback!",
            "success"
        )

        return redirect(url_for("feedback"))

    return render_template("feedback.html")


# =========================================================
# SETTINGS
# =========================================================

@app.route("/settings", methods=["GET", "POST"])
def settings():

    if "user_id" not in session:
        return redirect(url_for("login"))

    return render_template("settings.html")


# =========================================================
# REGISTER PROFILE
# =========================================================

@app.route("/register-profile", methods=["POST"])
def register_profile():

    if "user_id" not in session:
        return redirect(url_for("login"))

    blood_group = request.form.get(
        "blood_group",
        ""
    ).strip()

    location = request.form.get(
        "location",
        ""
    ).strip()

    phone = request.form.get(
        "phone",
        ""
    ).strip()

    connection = get_database()

    connection.execute("""
        UPDATE user
        SET blood_group = ?,
            location = ?,
            phone = ?
        WHERE id = ?
    """, (
        blood_group,
        location,
        phone,
        session["user_id"]
    ))

    connection.commit()
    connection.close()

    flash(
        "Profile updated successfully.",
        "success"
    )

    return redirect(url_for("settings"))


# =========================================================
# BLOOD BANKS
# =========================================================

@app.route("/blood-banks")
@app.route("/blood-bank")
def blood_bank():

    return render_template("blood_bank.html")


# =========================================================
# HOSPITALS
# =========================================================

@app.route("/hospitals")
def hospitals():

    return render_template("hospitals.html")


# =========================================================
# AMBULANCES
# =========================================================

@app.route("/ambulances")
def ambulances():

    return render_template("ambulances.html")


# =========================================================
# FIRST AID
# =========================================================

@app.route("/first-aid")
def first_aid():

    return render_template("first_aid.html")


# =========================================================
# NOTIFICATIONS
# =========================================================

@app.route("/notifications")
def notifications():

    return render_template("notifications.html")


# =========================================================
# FIREBASE STATUS
# =========================================================

@app.route("/firebase-status")
def firebase_status():

    return jsonify({
        "firebase": "configured"
    })


# =========================================================
# HEALTH CHECK
# =========================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "application": "LifeLink - Blood Donor Finder"
    })


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.errorhandler(404)
def page_not_found(error):

    return """
    <h1>404 - Page Not Found</h1>
    <p>The requested page does not exist.</p>
    <a href="/">Go to Home</a>
    """, 404


@app.errorhandler(500)
def internal_server_error(error):

    return """
    <h1>500 - Internal Server Error</h1>
    <p>Something went wrong on the server.</p>
    <a href="/">Go to Home</a>
    """, 500


# =========================================================
# RUN APP
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )