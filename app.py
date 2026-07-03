from flask import Flask, request, jsonify, render_template, send_file, redirect
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from models import db, Transaksi, Budget, Reminder, User
from routes.webhook import webhook_bp
import requests
import os
import time
import pandas as pd
import io
from zoneinfo import ZoneInfo
from itsdangerous import URLSafeTimedSerializer
from itsdangerous import BadSignature
from itsdangerous import SignatureExpired
from utils.helper import *

app = Flask(__name__)

KATEGORI = {

    "makanan": [
        "bakso",
        "mie",
        "ayam",
        "geprek",
        "pizza",
        "burger",
        "kebab",
        "martabak",
        "sate",
        "nasi",
        "warteg",
        "padang",
        "seafood",
        "pempek",
        "siomay",
        "batagor",
        "snack",
        "cemilan"
    ],

    "minuman": [
        "kopi",
        "es kopi",
        "teh",
        "es teh",
        "jus",
        "boba",
        "chatime",
        "starbucks",
        "janji jiwa",
        "kopi kenangan",
        "air",
        "galon",
        "susu"
    ],

    "transport": [
        "grab",
        "gojek",
        "maxim",
        "gocar",
        "goride",
        "bensin",
        "pertalite",
        "pertamax",
        "solar",
        "parkir",
        "tol",
        "kereta",
        "bus"
    ],

    "belanja": [
        "indomaret",
        "alfamart",
        "superindo",
        "hypermart",
        "sayur",
        "buah",
        "beras",
        "telur",
        "daging",
        "sembako"
    ],

    "tagihan": [
        "pln",
        "listrik",
        "air",
        "pam",
        "wifi",
        "internet",
        "indihome",
        "biznet",
        "pulsa",
        "bpjs"
    ],

    "hiburan": [
        "netflix",
        "spotify",
        "bioskop",
        "xxi",
        "cgv",
        "steam",
        "game",
        "playstation"
    ],

    "kesehatan": [
        "dokter",
        "obat",
        "apotek",
        "vitamin",
        "rumah sakit",
        "lab"
    ],

    "pendidikan": [
        "sekolah",
        "kampus",
        "buku",
        "kursus",
        "sertifikasi"
    ],

    "investasi": [
        "saham",
        "reksadana",
        "emas",
        "crypto",
        "bitcoin",
        "obligasi"
    ]
}

def cari_kategori(keterangan):

    teks = keterangan.lower()

    for kategori, daftar in KATEGORI.items():

        for sub in daftar:

            if sub in teks:

                return kategori, sub

    return "lainnya", "lainnya"



# =========================
# CONFIG DB
# =========================
# app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///kas.db"
# ambil DATABASE_URL dari Railway
database_url = os.getenv("DATABASE_URL")

# FIX untuk Railway (postgres:// -> postgresql://)
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

# ❗ INI WAJIB ADA
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

app.register_blueprint(webhook_bp)

with app.app_context():
    db.create_all()

def sekarang():
    return datetime.now(ZoneInfo("Asia/Jakarta"))



@app.route("/admin/users")
def admin_users():

    search = request.args.get("search","")

    page = request.args.get(
        "page",
        1,
        type=int
    )

    query = User.query

    if search:

        query = query.filter(
            User.nomor_wa.contains(search)
        )

    users = query.order_by(
        User.created_at.desc()
    ).paginate(
        page=page,
        per_page=15
    )

    return render_template(
        "admin_users.html",
        users=users,
        search=search
    )

@app.route("/admin/users/add",methods=["POST"])
def add_user():

    nama=request.form["nama"]

    nomor=request.form["nomor"]

    if User.query.filter_by(
        nomor_wa=nomor
    ).first():

        return redirect("/admin/users")

    db.session.add(

        User(
            nama=nama,
            nomor_wa=nomor
        )

    )

    db.session.commit()

    return redirect("/admin/users")

@app.route("/admin/users/toggle/<int:id>")
def toggle_user(id):

    user=User.query.get_or_404(id)

    user.aktif=not user.aktif

    db.session.commit()

    return redirect("/admin/users")

@app.route("/admin/users/delete/<int:id>")
def delete_user(id):

    user=User.query.get_or_404(id)

    db.session.delete(user)

    db.session.commit()

    return redirect("/admin/users")

@app.route("/dashboard/<token>")
def dashboard(token):

    nomor = verify_token(token)

    if not nomor:
        return "Link sudah tidak berlaku.", 403

    # =========================
    # PARAMETER
    # =========================

    page = request.args.get("page", 1, type=int)
    per_page = 10

    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    # =========================
    # QUERY DASAR
    # =========================

    query = Transaksi.query.filter(
        Transaksi.nomor_wa == nomor
    )

    # =========================
    # FILTER TANGGAL
    # =========================

    try:
        if start_date:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            query = query.filter(
                Transaksi.tanggal >= start
            )

        if end_date:
            # +1 hari agar seluruh transaksi pada tanggal tersebut ikut terambil
            end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(
                Transaksi.tanggal < end
            )

    except ValueError:
        # Abaikan jika format tanggal tidak valid
        pass

    # =========================
    # PAGINATION
    # =========================

    data_paginated = query.order_by(
        Transaksi.tanggal.desc()
    ).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    # =========================
    # DATA UNTUK CHART & SUMMARY
    # =========================

    all_data = query.order_by(
        Transaksi.tanggal.desc()
    ).all()

    total_masuk = sum(
        x.nominal
        for x in all_data
        if x.tipe == "MASUK"
    )

    total_keluar = sum(
        x.nominal
        for x in all_data
        if x.tipe == "KELUAR"
    )

    saldo = total_masuk - total_keluar

    # ==========================================
    # BUDGET BULAN INI
    # ==========================================

    periode = periode_sekarang()

    budget_list = Budget.query.filter_by(
        nomor_wa=nomor,
        periode=periode
    ).all()

    budget_data = []

    for b in budget_list:

        terpakai = db.session.query(
            db.func.coalesce(db.func.sum(Transaksi.nominal), 0)
        ).filter(
            Transaksi.nomor_wa == nomor,
            Transaksi.tipe == "KELUAR",
            Transaksi.kategori == b.kategori,
            db.func.to_char(
                Transaksi.tanggal,
                "YYYY-MM"
            ) == periode
        ).scalar()

        sisa = b.nominal - terpakai

        persen = 0

        if b.nominal > 0:
            persen = round((terpakai / b.nominal) * 100)

        budget_data.append({

            "kategori": b.kategori,
            "budget": b.nominal,
            "terpakai": terpakai,
            "sisa": sisa,
            "persen": persen

        })

    # ==========================================
    # REMINDER TAGIHAN
    # ==========================================

    reminders = Reminder.query.filter_by(
        nomor_wa=nomor,
        aktif=True
    ).order_by(
        Reminder.tanggal.asc()
    ).all()

    # ==========================================
    # AI FINANCE INSIGHT
    # ==========================================

    insight = []

    # Kondisi saldo

    if saldo > 0:

        insight.append(
            f"👍 Saldo Anda masih positif sebesar Rp {saldo:,.0f}."
        )

    else:

        insight.append(
            "⚠ Pengeluaran lebih besar daripada pemasukan."
        )

    # Persentase pengeluaran

    if total_masuk > 0:

        rasio = (total_keluar / total_masuk) * 100

        if rasio >= 90:

            insight.append(
                "Pengeluaran sudah mencapai lebih dari 90% dari pemasukan."
            )

        elif rasio >= 70:

            insight.append(
                "Pengeluaran sudah melewati 70% dari pemasukan."
            )

        else:

            insight.append(
                "Arus kas masih cukup sehat."
            )

    # Budget

    for item in budget_data:

        if item["persen"] >= 100:

            insight.append(
                f"Budget kategori {item['kategori']} telah terlampaui."
            )

        elif item["persen"] >= 90:

            insight.append(
                f"Budget kategori {item['kategori']} hampir habis."
            )

    # Reminder

    hari_ini = datetime.now().day

    for r in reminders:

        selisih = r.tanggal - hari_ini

        if selisih == 0:

            insight.append(
                f"Hari ini jatuh tempo pembayaran {r.nama}."
            )

        elif selisih <= 3 and selisih > 0:

            insight.append(
                f"{r.nama} jatuh tempo dalam {selisih} hari."
            )

    # Pengeluaran terbesar

    kategori = {}

    for trx in all_data:

        if trx.tipe == "KELUAR":

            kategori.setdefault(trx.kategori, 0)

            kategori[trx.kategori] += trx.nominal

    if kategori:

        terbesar = max(kategori, key=kategori.get)

        insight.append(

            f"Kategori pengeluaran terbesar bulan ini adalah "
            f"{terbesar} sebesar Rp {kategori[terbesar]:,.0f}."

        )

    # =========================
    # RENDER
    # =========================

    return render_template(
        "index.html",
        data=all_data,
        data_paginated=data_paginated,
        total_masuk=total_masuk,
        total_keluar=total_keluar,
        saldo=saldo,
        start_date=start_date,
        end_date=end_date,
        token=token,
        budget_data=budget_data,
        reminders=reminders,
        now=datetime.now(),
        insight=insight
    )

# =========================
# LOG REQUEST
# =========================
@app.before_request
def log_all():
    print("=" * 80)
    print("PATH :", request.path)
    print("METHOD :", request.method)
    print("CONTENT TYPE :", request.content_type)
    print("=" * 80)

@app.route("/")
def home():
    return render_template("home.html")

# =========================
# EXPORT EXCEL
# =========================
@app.route("/export-excel")
def export_excel():

    # =========================
    # VERIFIKASI TOKEN
    # =========================
    token = request.args.get("token")

    nomor = verify_token(token)

    if not nomor:
        return "Unauthorized", 403

    # =========================
    # FILTER TANGGAL
    # =========================
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    query = Transaksi.query.filter(
        Transaksi.nomor_wa == nomor
    )

    if start_date:
        query = query.filter(
            db.func.date(Transaksi.tanggal) >= start_date
        )

    if end_date:
        query = query.filter(
            db.func.date(Transaksi.tanggal) <= end_date
        )

    # =========================
    # AMBIL DATA
    # =========================
    data = query.order_by(
        Transaksi.tanggal.desc()
    ).all()

    rows = []

    for row in data:

        rows.append({
            "Tanggal": row.tanggal.strftime("%d-%m-%Y %H:%M"),
            "Tipe": row.tipe,
            "Nominal": row.nominal,
            "Keterangan": row.keterangan,
            "Nomor WA": row.nomor_wa
        })

    df = pd.DataFrame(rows)

    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        df.to_excel(
            writer,
            index=False,
            sheet_name="Kas WhatsApp"
        )

        worksheet = writer.sheets["Kas WhatsApp"]

        for column in worksheet.columns:

            max_length = 0

            column_letter = column[0].column_letter

            for cell in column:

                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except Exception:
                    pass

            worksheet.column_dimensions[
                column_letter
            ].width = max_length + 3

    output.seek(0)

    filename = (
        f"Kas_WhatsApp_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    )

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )




# =========================
# TEST
# =========================
@app.route("/test-wa")
def test_wa():
    kirim_wa("6285871264448", "Test dari Railway berhasil")
    return {"status": True}


@app.route("/debug-token")
def debug_token():
    token = os.getenv("FONTE_TOKEN")
    return {
        "exists": bool(token),
        "length": len(token) if token else 0
    }


def periode_sekarang():
    return datetime.now().strftime("%Y-%m")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
