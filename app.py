from flask import Flask, request, jsonify, render_template, send_file, redirect
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, time as dt_time
import time
from models import db, Transaksi, Budget, Reminder, User, RequestDemo, HutangPiutang, TargetPembelian, PromoPaket, MonthlySummary, get_last_summary, get_saldo_akhir
from routes.webhook import webhook_bp
import requests
import os
import pandas as pd
import io
from zoneinfo import ZoneInfo
from itsdangerous import URLSafeTimedSerializer
from itsdangerous import BadSignature
from itsdangerous import SignatureExpired
from utils.helper import *

from sqlalchemy import func
from calendar import monthrange

from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo
import atexit

app = Flask(__name__)

# def scheduler_loop():

#     print("Scheduler Started")

#     while True:

#         try:

#             print("Server  :", sekarang())
#             print("Jakarta :", now_jakarta())

#             schedule.run_pending()

#         except Exception:
#             import traceback
#             traceback.print_exc()

#         time.sleep(1)
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

@app.route("/admin/users")
def admin_users():

    search = request.args.get("search", "")

    page = request.args.get(
        "page",
        1,
        type=int
    )

    page_request = request.args.get(
        "page_request",
        1,
        type=int
    )

    # =====================================
    # REGISTERED USER
    # =====================================

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

    # =====================================
    # REQUEST DEMO
    # =====================================

    request_users = RequestDemo.query.order_by(
        RequestDemo.created_at.desc()
    ).paginate(
        page=page_request,
        per_page=15
    )

    return render_template(
        "admin_users.html",
        users=users,
        request_users=request_users,
        search=search
    )

from datetime import date, timedelta

@app.route("/admin/users/add", methods=["POST"])
def add_user():

    nama = request.form["nama"].strip()

    nomor = request.form["nomor"].strip()

    durasi = int(request.form.get("durasi", 30))

    if User.query.filter_by(
        nomor_wa=nomor
    ).first():

        return redirect("/admin/users")

    mulai = date.today()

    akhir = mulai + timedelta(days=durasi)

    user = User(
        nama=nama,
        nomor_wa=nomor,
        aktif=True,
        mulai_langganan=mulai,
        akhir_langganan=akhir
    )

    db.session.add(user)

    db.session.commit()

    return redirect("/admin/users")

from datetime import date, timedelta

@app.route("/admin/users/toggle/<int:id>")
def toggle_user(id):

    user = User.query.get_or_404(id)

    if user.aktif:

        user.aktif = False

    else:

        user.aktif = True

        user.mulai_langganan = date.today()

        user.akhir_langganan = date.today() + timedelta(days=30)

    db.session.commit()

    return redirect("/admin/users")

@app.route("/admin/users/delete/<int:id>")
def delete_user(id):

    user = User.query.get_or_404(id)

    nomor = user.nomor_wa

    # Hapus semua transaksi
    Transaksi.query.filter_by(
        nomor_wa=nomor
    ).delete()

    # Hapus semua budget
    Budget.query.filter_by(
        nomor_wa=nomor
    ).delete()

    # Hapus semua reminder
    Reminder.query.filter_by(
        nomor_wa=nomor
    ).delete()

    # Hapus request demo
    RequestDemo.query.filter_by(
        nomor_wa=nomor
    ).delete()

    # Hapus user
    db.session.delete(user)

    db.session.commit()

    return redirect("/admin/users")

@app.route("/dashboard/<token>")
def dashboard(token):

    verify_monthly_summary(
        nomor
    )

    nomor = verify_token(token)

    if not nomor:
        return render_template(
            "link_expired.html"
        )

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
    # TARGET PEMBELIAN / TABUNGAN
    # ==========================================

    target_pembelian = TargetPembelian.query.filter(
        TargetPembelian.nomor_wa == nomor,
        TargetPembelian.aktif == True
    ).order_by(
        TargetPembelian.deadline.asc()
    ).first()

    target_data = None

    if target_pembelian:

        progress = 0

        if target_pembelian.target > 0:
            progress = round(
                (target_pembelian.terkumpul / target_pembelian.target) * 100,
                1
            )

        progress = min(progress, 100)

        sisa = max(
            target_pembelian.target - target_pembelian.terkumpul,
            0
        )

        sisa_hari = (
            target_pembelian.deadline - sekarang().date()
        ).days

        target_data = {

            "id": target_pembelian.id,

            "nama": target_pembelian.nama,

            "target": target_pembelian.target,

            "terkumpul": target_pembelian.terkumpul,

            "sisa": sisa,

            "progress": progress,

            "deadline": target_pembelian.deadline,

            "sisa_hari": sisa_hari,

            "selesai": progress >= 100

        }

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

    hari_ini = sekarang().day

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

    # ==========================
    # HUTANG PIUTANG SUMMARY
    # ==========================

    hutang_list = HutangPiutang.query.filter(
        HutangPiutang.nomor_wa == nomor,
        HutangPiutang.tipe == "HUTANG",
        HutangPiutang.status != "LUNAS"
    ).order_by(
        HutangPiutang.tanggal.desc()
    ).all()

    piutang_list = HutangPiutang.query.filter(
        HutangPiutang.nomor_wa == nomor,
        HutangPiutang.tipe == "PIUTANG",
        HutangPiutang.status != "LUNAS"
    ).order_by(
        HutangPiutang.tanggal.desc()
    ).all()

    total_hutang = sum(
        x.nominal for x in hutang_list
    )


    total_piutang = sum(
        x.nominal for x in piutang_list
    )


    net_balance = total_piutang - total_hutang

    # ==========================================
    # TARGET PEMBELIAN INSIGHT
    # ==========================================

    if target_data:

        if target_data["selesai"]:

            insight.append(
                f"🎉 Target '{target_data['nama']}' telah berhasil tercapai."
            )

        elif target_data["sisa_hari"] < 0:

            insight.append(
                f"⏰ Target '{target_data['nama']}' telah melewati deadline."
            )

        elif target_data["sisa_hari"] <= 7:

            insight.append(
                f"📅 Deadline target '{target_data['nama']}' tinggal {target_data['sisa_hari']} hari lagi."
            )

        elif target_data["progress"] >= 80:

            insight.append(
                f"🎯 Target '{target_data['nama']}' sudah mencapai {target_data['progress']}%."
            )

        else:

            insight.append(
                f"💰 Target '{target_data['nama']}' masih kurang Rp {target_data['sisa']:,.0f}."
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
        now=sekarang(),
        insight=insight,
        hutang_list=hutang_list,
        piutang_list=piutang_list,

        total_hutang=total_hutang,
        total_piutang=total_piutang,
        net_balance=net_balance,

        target_data=target_data
    )

@app.route("/expired")
def expired():

    return render_template(
        "link_expired.html"
    )

@app.route("/dashboard-data/<token>")
def dashboard_data(token):

    nomor = verify_token(token)

    if not nomor:
        return jsonify({"status": False}), 403

    transaksi = (
        Transaksi.query
        .filter_by(nomor_wa=nomor)
        .order_by(Transaksi.tanggal.desc())
        .limit(10)
        .all()
    )

    total_masuk = db.session.query(
        db.func.coalesce(db.func.sum(Transaksi.nominal),0)
    ).filter(
        Transaksi.nomor_wa==nomor,
        Transaksi.tipe=="MASUK"
    ).scalar()

    total_keluar = db.session.query(
        db.func.coalesce(db.func.sum(Transaksi.nominal),0)
    ).filter(
        Transaksi.nomor_wa==nomor,
        Transaksi.tipe=="KELUAR"
    ).scalar()

    saldo = total_masuk-total_keluar

    rows=[]

    for t in transaksi:

        rows.append({
            "id": t.id,
            "tanggal":t.tanggal.strftime("%d/%m/%Y %H:%M"),
            "tipe":t.tipe,
            "nominal":t.nominal,
            "kategori":t.kategori or "-",
            "keterangan":t.keterangan or "-"

        })

    return jsonify({

        "saldo":saldo,
        "masuk":total_masuk,
        "keluar":total_keluar,
        "rows":rows

    })

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
        f"{sekarang().strftime('%Y%m%d_%H%M%S')}.xlsx"
    )

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.route("/fitur")
def fitur():

    commands = [

        {
            "icon": "💰",
            "title": "Pemasukan",
            "command": "masuk",
            "example": "masuk 500000 gaji",
            "desc": "Mencatat pemasukan secara otomatis."
        },

        {
            "icon": "💸",
            "title": "Pengeluaran",
            "command": "keluar",
            "example": "keluar 25000 makan",
            "desc": "Mencatat pengeluaran beserta kategori otomatis."
        },

        {
            "icon": "💳",
            "title": "Saldo",
            "command": "saldo",
            "example": "saldo",
            "desc": "Menampilkan saldo terkini, total pemasukan, pengeluaran, dan link dashboard."
        },

        {
            "icon": "📊",
            "title": "Ringkasan Hari Ini",
            "command": "hariini",
            "example": "hariini",
            "desc": "Melihat total transaksi hari ini."
        },

        {
            "icon": "🎯",
            "title": "Budget Bulanan",
            "command": "budget",
            "example": "budget makanan 1500000",
            "desc": "Membuat dan memonitor budget tiap kategori."
        },

        {
            "icon": "🤖",
            "title": "AI Insight",
            "command": "insight",
            "example": "insight",
            "desc": "AI menganalisis pola pengeluaran dan memberikan rekomendasi."
        },

        {
            "icon": "🔔",
            "title": "Reminder Tagihan",
            "command": "reminder",
            "example": "reminder listrik 20 500000",
            "desc": "Mengingatkan tagihan bulanan."
        },

        {
            "icon": "🗑️",
            "title": "Hapus Reminder",
            "command": "hapusreminder",
            "example": "hapusreminder listrik",
            "desc": "Menghapus reminder yang sudah tidak digunakan."
        }

    ]

    return render_template(
        "fitur.html",
        commands=commands
    )

# =====================================
# LAPORAN HARIAN FINANCE
# =====================================

def generate_laporan_harian(nomor_wa):

    hari_ini = date.today()

    awal = datetime.combine(
        hari_ini,
        dt_time.min
    )

    akhir = datetime.combine(
        hari_ini,
        dt_time.max
    )

    # =========================
    # TRANSAKSI HARI INI
    # =========================

    transaksi = Transaksi.query.filter(
        Transaksi.nomor_wa == nomor_wa,
        Transaksi.tanggal.between(
            awal,
            akhir
        )
    ).all()

    total_masuk = 0
    total_keluar = 0

    detail_masuk = ""
    detail_keluar = ""

    for t in transaksi:

        if t.tipe == "MASUK":

            total_masuk += t.nominal

            detail_masuk += (
                f"\n• {t.keterangan}"
                f"\n  Rp {t.nominal:,.0f}"
            )

        else:

            total_keluar += t.nominal

            detail_keluar += (
                f"\n• {t.keterangan}"
                f"\n  Rp {t.nominal:,.0f}"
            )

    # =========================
    # HUTANG HARI INI
    # =========================

    hutang = HutangPiutang.query.filter(
        HutangPiutang.nomor_wa == nomor_wa,
        HutangPiutang.tipe == "HUTANG",
        HutangPiutang.tanggal.between(
            awal,
            akhir
        )
    ).all()

    total_hutang = sum(
        h.nominal for h in hutang
    )

    detail_hutang = ""

    for h in hutang:

        detail_hutang += (
            f"\n• {h.nama}"
            f"\n  Rp {h.nominal:,.0f}"
        )

    # =========================
    # PIUTANG HARI INI
    # =========================

    piutang = HutangPiutang.query.filter(
        HutangPiutang.nomor_wa == nomor_wa,
        HutangPiutang.tipe == "PIUTANG",
        HutangPiutang.tanggal.between(
            awal,
            akhir
        )
    ).all()

    total_piutang = sum(
        p.nominal for p in piutang
    )

    detail_piutang = ""

    for p in piutang:

        detail_piutang += (
            f"\n• {p.nama}"
            f"\n  Rp {p.nominal:,.0f}"
        )

    saldo = total_masuk - total_keluar

    # =========================
    # SALDO KESELURUHAN
    # =========================

    total_semua_masuk = db.session.query(
        db.func.coalesce(
            db.func.sum(Transaksi.nominal),
            0
        )
    ).filter(
        Transaksi.nomor_wa == nomor_wa,
        Transaksi.tipe == "MASUK"
    ).scalar()

    total_semua_keluar = db.session.query(
        db.func.coalesce(
            db.func.sum(Transaksi.nominal),
            0
        )
    ).filter(
        Transaksi.nomor_wa == nomor_wa,
        Transaksi.tipe == "KELUAR"
    ).scalar()

    saldo_total = total_semua_masuk - total_semua_keluar

    Link = generate_dashboard_link(nomor_wa)

    laporan = f"""📊 *Laporan Harian ChatSaku*
📅 {hari_ini.strftime("%d %B %Y")}

════════════════════

📈 *Ringkasan*

• 💰 Pemasukan      : Rp {total_masuk:,.0f}
• 💸 Pengeluaran    : Rp {total_keluar:,.0f}
• 💳 Hutang Baru    : Rp {total_hutang:,.0f}
• 📥 Piutang Baru   : Rp {total_piutang:,.0f}

━━━━━━━━━━━━━━━━━━━━

💼 *Saldo Hari Ini*
💵 *Rp {saldo:,.0f}*

💼 *Saldo Keseluruhan*
💵 *Rp {saldo_total:,.0f}*

━━━━━━━━━━━━━━━━━━━━

🟢 *Detail Pemasukan*
{detail_masuk or "• Tidak ada"}

🔴 *Detail Pengeluaran*
{detail_keluar or "• Tidak ada"}

🟠 *Detail Hutang*
{detail_hutang or "• Tidak ada"}

🔵 *Detail Piutang*
{detail_piutang or "• Tidak ada"}

════════════════════

Link Dashboard
{Link}
*Link aktif selama 30 menit

Terima kasih telah menggunakan *ChatSaku* 😊
Kelola keuangan lebih rapi, setiap hari.
"""
    return laporan

def generate_laporan_bulanan(nomor_wa, periode):

    summary = MonthlySummary.query.filter_by(
        nomor_wa=nomor_wa,
        periode=periode
    ).first()

    if not summary:
        return None

    transaksi = Transaksi.query.filter(
        Transaksi.nomor_wa == nomor_wa,
        func.to_char(
            Transaksi.tanggal,
            "YYYY-MM"
        ) == periode,
        Transaksi.tipe == "KELUAR"
    ).all()

    kategori = {}

    for trx in transaksi:
        key = trx.kategori or "-"
        kategori[key] = kategori.get(key, 0) + trx.nominal

    kategori_terbesar = "-"

    nominal_terbesar = 0

    if kategori:
        kategori_terbesar = max(
            kategori,
            key=kategori.get
        )
        nominal_terbesar = kategori[kategori_terbesar]

    return f"""
📊 *Laporan Bulanan ChatSaku*

📅 Periode : {periode}

═══════════════════

💵 Saldo Awal
Rp {summary.saldo_awal:,.0f}

📈 Total Masuk
Rp {summary.total_masuk:,.0f}

📉 Total Keluar
Rp {summary.total_keluar:,.0f}

💰 Saving
Rp {summary.saving:,.0f}

🏦 Saldo Akhir
Rp {summary.saldo_akhir:,.0f}

━━━━━━━━━━━━━━━━━━━

🏆 Pengeluaran Terbesar

{kategori_terbesar}
Rp {nominal_terbesar:,.0f}

━━━━━━━━━━━━━━━━━━━

Jumlah Transaksi
{summary.total_transaksi}

Terima kasih telah menggunakan ChatSaku 😊
"""

def kirim_laporan_bulanan(periode):

    users = User.query.filter_by(
        aktif=True
    ).all()

    for user in users:

        try:

            laporan = generate_laporan_bulanan(
                user.nomor_wa,
                periode
            )

            if laporan:

                kirim_wa(
                    user.nomor_wa,
                    laporan
                )

        except Exception as e:

            print(e)

def scheduler_closing():

    with app.app_context():

        periode = previous_period()

        if not validate_period(periode):

            print("Periode tidak valid")

            return

        hasil = closing_month(periode)

        print(hasil)
def kirim_laporan_harian():

    print("=" * 60)
    print("Scheduler dijalankan :", now_jakarta())
    print("=" * 60)

    with app.app_context():

        print("🚀 kirim_laporan_harian dijalankan")

        users = User.query.all()

        print("Jumlah user:", len(users))

        for user in users:

            print(
                "Proses user:",
                user.nomor_wa,
                "paket:",
                user.paket
            )

            try:

                if not has_feature(user.nomor_wa, "laporan_harian"):
                    print("Lewat:", user.nomor_wa)
                    continue

                laporan = generate_laporan_harian(user.nomor_wa)

                kirim_wa(
                    user.nomor_wa,
                    laporan
                )

                print("✅ Berhasil:", user.nomor_wa)

            except Exception as e:

                print("❌ Error:", e)

def previous_period():

    now = now_jakarta()

    if now.month == 1:
        return f"{now.year-1}-12"

    return f"{now.year}-{now.month-1:02d}"

def copy_budget_next_month(nomor_wa, periode):

    tujuan = next_period(periode)

    budget_list = Budget.query.filter_by(
        nomor_wa=nomor_wa,
        periode=periode,
        auto_repeat=True
    ).all()

    for item in budget_list:

        sudah = Budget.query.filter_by(
            nomor_wa=nomor_wa,
            periode=tujuan,
            kategori=item.kategori
        ).first()

        if sudah:
            continue

        db.session.add(

            Budget(
                nomor_wa=item.nomor_wa,
                kategori=item.kategori,
                nominal=item.nominal,
                periode=tujuan,
                auto_repeat=True
            )

        )

def closing_user(user, periode):

    tahun, bulan = map(int, periode.split("-"))

    awal = datetime(
        tahun,
        bulan,
        1,
        0,
        0,
        0
    )

    akhir = datetime(
        tahun,
        bulan,
        monthrange(tahun, bulan)[1],
        23,
        59,
        59
    )

    saldo_awal = calculate_opening_balance(
        user.nomor_wa,
        periode
    )

    # ==============================
    # TOTAL PEMASUKAN
    # ==============================

    masuk = (
        db.session.query(
            func.coalesce(
                func.sum(Transaksi.nominal),
                0
            )
        )
        .filter(
            Transaksi.nomor_wa == user.nomor_wa,
            Transaksi.tipe == "MASUK",
            Transaksi.tanggal >= awal,
            Transaksi.tanggal <= akhir
        )
        .scalar()
    )

    # ==============================
    # TOTAL PENGELUARAN
    # ==============================

    keluar = (
        db.session.query(
            func.coalesce(
                func.sum(Transaksi.nominal),
                0
            )
        )
        .filter(
            Transaksi.nomor_wa == user.nomor_wa,
            Transaksi.tipe == "KELUAR",
            Transaksi.tanggal >= awal,
            Transaksi.tanggal <= akhir
        )
        .scalar()
    )

    # ==============================
    # JUMLAH TRANSAKSI
    # ==============================

    jumlah = (
        db.session.query(
            func.count(Transaksi.id)
        )
        .filter(
            Transaksi.nomor_wa == user.nomor_wa,
            Transaksi.tanggal >= awal,
            Transaksi.tanggal <= akhir
        )
        .scalar()
    )

    saving = masuk - keluar

    saldo_akhir = saldo_awal + saving

    # ==============================
    # INSERT / UPDATE SUMMARY
    # ==============================

    summary = MonthlySummary.query.filter_by(
        nomor_wa=user.nomor_wa,
        periode=periode
    ).first()

    if summary is None:

        summary = MonthlySummary(
            nomor_wa=user.nomor_wa,
            periode=periode
        )

        db.session.add(summary)

    summary.saldo_awal = saldo_awal
    summary.total_masuk = masuk
    summary.total_keluar = keluar
    summary.saving = saving
    summary.saldo_akhir = saldo_akhir
    summary.total_transaksi = jumlah
    summary.status = "CLOSED"
    summary.closed_at = now_jakarta()

    copy_budget_next_month(
        user.nomor_wa,
        periode
    )

    return True

def closing_month(periode=None):

    if periode is None:
        periode = previous_period()

    # ==========================
    # VALIDASI PERIODE
    # ==========================

    if not validate_period(periode):

        return {
            "status": False,
            "message": "Periode tidak valid"
        }

    # ==========================
    # LOCK
    # ==========================

    if not acquire_lock():

        return {
            "status": False,
            "message": "Closing sedang berjalan."
        }

    berhasil = 0
    gagal = 0

    try:

        users = User.query.filter_by(
            aktif=True
        ).all()

        print("=" * 60)
        print("MONTHLY CLOSING")
        print("Periode :", periode)
        print("Jumlah User :", len(users))
        print("=" * 60)

        for user in users:

            try:

                closing_user(
                    user,
                    periode
                )

                berhasil += 1

                print(
                    "SUCCESS :",
                    user.nomor_wa
                )

            except Exception as e:

                gagal += 1

                db.session.rollback()

                import traceback

                traceback.print_exc()

                print(
                    "FAILED :",
                    user.nomor_wa,
                    str(e)
                )

        db.session.commit()

        print("=" * 60)
        print("Closing selesai")
        print("Success :", berhasil)
        print("Failed  :", gagal)
        print("=" * 60)

        return {

            "status": True,

            "periode": periode,

            "success": berhasil,

            "failed": gagal

        }

    except Exception as e:

        db.session.rollback()

        import traceback

        traceback.print_exc()

        print("FATAL ERROR :", str(e))

        return {

            "status": False,

            "message": str(e)

        }

    finally:

        release_lock()

def recalculate_summary(

    nomor_wa,

    periode

):

    closing_user(

        User.query.filter_by(

            nomor_wa=nomor_wa

        ).first(),

        periode

    )

def validate_period(periode):
    """
    Validasi format periode YYYY-MM
    Contoh valid:
        2026-01
        2026-12

    Contoh tidak valid:
        2026
        26-01
        2026-13
        2026-00
        abc
    """

    if not periode:
        return False

    periode = periode.strip()

    # Format harus YYYY-MM
    if not re.match(r"^\d{4}-\d{2}$", periode):
        return False

    try:

        tahun, bulan = map(int, periode.split("-"))

        if tahun < 2020:
            return False

        if bulan < 1 or bulan > 12:
            return False

        datetime(tahun, bulan, 1)

        return True

    except Exception:

        return False

@app.route("/admin/closing-month")
def admin_closing():

    periode = request.args.get("periode")

    if not periode:

        periode = previous_period()

    if not validate_period(periode):

        return jsonify({

            "status": False,

            "message": "Format periode harus YYYY-MM"

        }), 400

    hasil = closing_month(periode)

    return jsonify(hasil)

@app.route("/admin/monthly-summary")
def monthly_summary():

    data = MonthlySummary.query.order_by(

        MonthlySummary.periode.desc(),

        MonthlySummary.nomor_wa

    ).all()

    rows = []

    for item in data:

        rows.append({

            "periode": item.periode,

            "nomor": item.nomor_wa,

            "saldo_awal": item.saldo_awal,

            "masuk": item.total_masuk,

            "keluar": item.total_keluar,

            "saving": item.saving,

            "saldo_akhir": item.saldo_akhir,

            "status": item.status

        })

    return jsonify(rows)

@app.route("/admin/reclosing/<periode>")
def admin_reclosing(periode):

    if not validate_period(periode):

        return jsonify({

            "status":False,

            "message":"Periode salah"

        }),400

    cascade_reclosing(
        periode
    )

    return jsonify({

        "status":True,

        "message":"Cascade selesai"

    })

def next_period(periode):

    tahun, bulan = map(int, periode.split("-"))

    if bulan == 12:
        return f"{tahun+1}-01"

    return f"{tahun}-{bulan+1:02d}"

@app.route("/admin/test-closing")
def test_closing():

    with app.app_context():

        hasil = closing_month()

        kirim_laporan_bulanan(
            hasil["periode"]
        )

    return jsonify(hasil)

@app.route("/admin/test-laporan-bulanan")
def test_laporan():

    nomor = request.args.get("nomor")

    periode = request.args.get("periode")

    laporan = generate_laporan_bulanan(
        nomor,
        periode
    )

    kirim_wa(
        nomor,
        laporan
    )

    return jsonify({
        "status":True
    })

@app.route("/admin/verify-summary")
def admin_verify():

    users = User.query.filter_by(
        aktif=True
    ).all()

    for user in users:

        verify_monthly_summary(
            user.nomor_wa
        )

    return jsonify({

        "status":True,

        "message":"Verification selesai"

    })

def scheduler_verify():

    users = User.query.filter_by(
        aktif=True
    ).all()

    for user in users:

        verify_monthly_summary(
            user.nomor_wa
        )

scheduler = BackgroundScheduler(
    timezone=ZoneInfo("Asia/Jakarta")
)

scheduler.add_job(
    func=kirim_laporan_harian,
    trigger="cron",
    hour=17,
    minute=25,
    id="laporan_harian",
    replace_existing=True
)

scheduler.add_job(
    func=scheduler_closing,
    trigger="cron",
    day=1,
    hour=0,
    minute=10,
    id="closing_month",
    replace_existing=True
)

scheduler.add_job(

    func=scheduler_verify,
    trigger="cron",
    hour=1,
    minute=0,
    id="verify_summary",
    replace_existing=True

)

scheduler.start()

atexit.register(lambda: scheduler.shutdown())
# schedule.every().day.at(
#     "12:20"
# ).do(
#     kirim_laporan_harian
# )
# # schedule.every(1).minutes.do(kirim_laporan_harian)

# print(schedule.jobs)

# threading.Thread(
#     target=scheduler_loop,
#     daemon=True
# ).start()

@app.route("/promo")
def promo():

    hari_ini = sekarang().date()

    promos = PromoPaket.query.filter(
        PromoPaket.aktif == True,
        PromoPaket.tanggal_mulai <= hari_ini,
        PromoPaket.tanggal_selesai >= hari_ini
    ).order_by(PromoPaket.nama).all()

    hasil = {}

    for p in promos:

        if p.nama not in hasil:
            hasil[p.nama] = {
                "nama": p.nama,
                "mulai": p.tanggal_mulai.strftime("%d %b %Y"),
                "selesai": p.tanggal_selesai.strftime("%d %b %Y"),
                "paket": {}
            }

        hasil[p.nama]["paket"][p.paket] = p.harga_promo

    return jsonify(list(hasil.values()))

import os
import uuid
import midtransclient

snap = midtransclient.Snap(
    is_production=False,  # Sandbox
    server_key=os.getenv("MIDTRANS_SERVER_KEY")
)

import os
import uuid
import midtransclient
from datetime import datetime
from flask import request, jsonify

snap = midtransclient.Snap(
    is_production=False,
    server_key=os.getenv("MIDTRANS_SERVER_KEY")
)

@app.route("/api/payment/create", methods=["POST"])
def create_payment():

    try:

        data = request.get_json()

        paket = data.get("paket", "").upper()
        nomor_wa = data.get("nomor_wa")

        if not nomor_wa:
            return jsonify({
                "success": False,
                "message": "Nomor WhatsApp wajib diisi."
            }), 400

        user = User.query.filter_by(
            nomor_wa=nomor_wa
        ).first()

        if not user:
            return jsonify({
                "success": False,
                "message": "User tidak ditemukan."
            }), 404

        HARGA_PAKET = {
            "STARTER": 10000,
            "PRO": 25000,
            "PREMIUM": 55000
        }

        if paket not in HARGA_PAKET:
            return jsonify({
                "success": False,
                "message": "Paket tidak ditemukan."
            }), 400

        harga = HARGA_PAKET[paket]

        hari_ini = sekarang().date()

        promo = PromoPaket.query.filter(
            PromoPaket.paket == paket,
            PromoPaket.aktif == True,
            PromoPaket.tanggal_mulai <= hari_ini,
            PromoPaket.tanggal_selesai >= hari_ini
        ).first()

        if promo:
            harga = promo.harga_promo

        order_id = f"CHATSAKU-{uuid.uuid4().hex[:12]}"

        parameter = {

            "transaction_details": {
                "order_id": order_id,
                "gross_amount": int(harga)
            },

            "credit_card": {
                "secure": True
            },

            "customer_details": {
                "first_name": user.nama,
                "phone": user.nomor_wa
            },

            "item_details": [

                {
                    "id": paket,
                    "price": int(harga),
                    "quantity": 1,
                    "name": f"ChatSaku {paket}"
                }

            ]

        }

        transaction = snap.create_transaction(parameter)

        return jsonify({

            "success": True,

            "token": transaction["token"],

            "redirect_url": transaction["redirect_url"],

            "order_id": order_id,

            "harga": harga

        })

    except Exception as e:

        import traceback
        traceback.print_exc()

        return jsonify({

            "success": False,

            "error": str(e)

        }), 500

@app.route("/midtrans/notification", methods=["POST"])
def notification():

    notif = request.get_json()

    order_id = notif["order_id"]

    status = notif["transaction_status"]

    payment = Payment.query.filter_by(
        order_id=order_id
    ).first()

    if payment is None:
        return "Not Found",404

    if status == "settlement":

        payment.status = "PAID"

        user = User.query.filter_by(
            nomor_wa=payment.nomor_wa
        ).first()

        user.paket = payment.paket

        db.session.commit()

    return "OK"

def list_period_between(start_period, end_period):

    hasil = []

    tahun, bulan = map(int, start_period.split("-"))
    end_tahun, end_bulan = map(int, end_period.split("-"))

    while True:

        hasil.append(f"{tahun}-{bulan:02d}")

        if tahun == end_tahun and bulan == end_bulan:
            break

        bulan += 1

        if bulan == 13:
            bulan = 1
            tahun += 1

    return hasil

def last_summary_period():

    last = db.session.query(

        func.max(
            MonthlySummary.periode
        )

    ).scalar()

    return last

def cascade_reclosing(start_period):

    akhir = last_summary_period()

    if akhir is None:

        return

    daftar = list_period_between(
        start_period,
        akhir
    )

    print("="*60)
    print("CASCADE RECLOSING")
    print(daftar)
    print("="*60)

    for periode in daftar:

        print("RECALCULATE :", periode)

        closing_month(
            periode
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
