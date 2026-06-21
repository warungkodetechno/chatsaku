from flask import Flask
from flask import request
from flask import jsonify
from flask import render_template

from flask_sqlalchemy import SQLAlchemy

from datetime import datetime

from models import db
from models import Transaksi
import requests
import os

app = Flask(__name__)

@app.before_request
def log_all():

    print("=" * 80)
    print("PATH :", request.path)
    print("METHOD :", request.method)
    print("CONTENT TYPE :", request.content_type)
    print("=" * 80)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///kas.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

with app.app_context():
    db.create_all()


# ==================================
# DASHBOARD
# ==================================
@app.route("/")
def index():

    data = Transaksi.query.order_by(
        Transaksi.tanggal.desc()
    ).all()

    total_masuk = sum(
        x.nominal for x in data
        if x.tipe == "MASUK"
    )

    total_keluar = sum(
        x.nominal for x in data
        if x.tipe == "KELUAR"
    )

    saldo = total_masuk - total_keluar

    return render_template(
        "index.html",
        data=data,
        saldo=saldo,
        total_masuk=total_masuk,
        total_keluar=total_keluar
    )

# @app.route("/")
# def index():
#     return "Railway OK"

FONTE_TOKEN = os.getenv("FONTE_TOKEN")

def kirim_wa(nomor, pesan):

    try:

        response = requests.post(
            "https://api.fonnte.com/send",
            headers={
                "Authorization": FONTE_TOKEN
            },
            data={
                "target": nomor,
                "message": pesan
            },
            timeout=30
        )

        print("========== FONNTE ==========")
        print("STATUS :", response.status_code)
        print("BODY :", response.text)
        print("============================")

        return response.text

    except Exception as e:

        print("ERROR FONNTE :", str(e))

@app.route("/test-post", methods=["POST"])
def test_post():

    print("POST BERHASIL MASUK")

    print(request.form.to_dict())

    print(request.get_json(silent=True))

    return {
        "status": True
    }

# ==================================
# WEBHOOK FONNTE
# ==================================
# @app.route("/webhook", methods=["POST"])
# def webhook():

#     payload = request.get_json(silent=True) or {}

#     print("=" * 80)
#     print("WEBHOOK MASUK")
#     print(payload)
#     print("=" * 80)

#     # ==================================
#     # ABAIKAN STATUS DEVICE
#     # ==================================
#     if "state" in payload:
#         return jsonify({"status": True})

#     # ==================================
#     # ABAIKAN PESAN DARI BOT SENDIRI
#     # ==================================
#     if payload.get("fromMe") is True:
#         return jsonify({"status": True})

#     if payload.get("isFromMe") is True:
#         return jsonify({"status": True})

#     if payload.get("owner") is True:
#         return jsonify({"status": True})

#     # ==================================
#     # FORMAT FONNTE
#     # ==================================
#     sender = ""
#     message = ""

#     if "sender" in payload:

#         sender = payload.get("sender", "")
#         message = payload.get("message", "")

#     elif "data" in payload:

#         data = payload.get("data", {})

#         sender = data.get("sender", "")
#         message = data.get("message", "")

#     sender = str(sender).strip()
#     message = str(message).strip()

#     print("SENDER :", sender)
#     print("MESSAGE :", message)

#     if not sender:
#         return jsonify({"status": True})

#     if not message:
#         return jsonify({"status": True})

#     cmd = message.lower()

#     # ==================================
#     # SALDO
#     # ==================================
#     if cmd == "saldo":

#         masuk = db.session.query(
#             db.func.sum(Transaksi.nominal)
#         ).filter(
#             Transaksi.tipe == "MASUK"
#         ).scalar() or 0

#         keluar = db.session.query(
#             db.func.sum(Transaksi.nominal)
#         ).filter(
#             Transaksi.tipe == "KELUAR"
#         ).scalar() or 0

#         saldo = masuk - keluar

#         kirim_wa(
#             sender,
#             f"💰 SALDO\n\n"
#             f"Masuk : Rp {masuk:,.0f}\n"
#             f"Keluar : Rp {keluar:,.0f}\n"
#             f"Saldo : Rp {saldo:,.0f}"
#         )

#         return jsonify({"status": True})

#     # ==================================
#     # MASUK
#     # ==================================
#     elif cmd.startswith("masuk"):

#         try:

#             parts = message.split()

#             nominal = int(parts[1])

#             keterangan = " ".join(parts[2:])

#             trx = Transaksi(
#                 tanggal=datetime.now(),
#                 tipe="MASUK",
#                 nominal=nominal,
#                 keterangan=keterangan,
#                 nomor_wa=sender
#             )

#             db.session.add(trx)
#             db.session.commit()

#             kirim_wa(
#                 sender,
#                 f"✅ Pemasukan tersimpan\n\n"
#                 f"Rp {nominal:,.0f}\n"
#                 f"{keterangan}"
#             )

#         except Exception as e:

#             print(e)

#             kirim_wa(
#                 sender,
#                 "Format:\nmasuk 1000000 gaji"
#             )

#         return jsonify({"status": True})

#     # ==================================
#     # KELUAR
#     # ==================================
#     elif cmd.startswith("keluar"):

#         try:

#             parts = message.split()

#             nominal = int(parts[1])

#             keterangan = " ".join(parts[2:])

#             trx = Transaksi(
#                 tanggal=datetime.now(),
#                 tipe="KELUAR",
#                 nominal=nominal,
#                 keterangan=keterangan,
#                 nomor_wa=sender
#             )

#             db.session.add(trx)
#             db.session.commit()

#             kirim_wa(
#                 sender,
#                 f"✅ Pengeluaran tersimpan\n\n"
#                 f"Rp {nominal:,.0f}\n"
#                 f"{keterangan}"
#             )

#         except Exception as e:

#             print(e)

#             kirim_wa(
#                 sender,
#                 "Format:\nkeluar 25000 makan siang"
#             )

#         return jsonify({"status": True})

#     # ==================================
#     # HARI INI
#     # ==================================
#     elif cmd == "hariini":

#         today = datetime.now().date()

#         data = Transaksi.query.filter(
#             db.func.date(Transaksi.tanggal) == today
#         ).all()

#         total = sum(x.nominal for x in data)

#         kirim_wa(
#             sender,
#             f"📅 Hari Ini\n\n"
#             f"Jumlah Transaksi : {len(data)}\n"
#             f"Total : Rp {total:,.0f}"
#         )

#         return jsonify({"status": True})

#     # ==================================
#     # HELP
#     # ==================================
#     kirim_wa(
#         sender,
#         "Perintah:\n\n"
#         "masuk 100000 gaji\n"
#         "keluar 25000 makan\n"
#         "saldo\n"
#         "hariini"
#     )

#     return jsonify({"status": True})

@app.route("/webhook", methods=["POST"])
def webhook():

    payload = request.get_json(silent=True) or {}

    print("=" * 80)
    print("WEBHOOK MASUK")
    print(payload)
    print("=" * 80)

    # ==================================
    # ABAIKAN STATUS DEVICE
    # ==================================
    if "state" in payload:
        print("STATUS DEVICE")
        return jsonify({"status": True})

    # ==================================
    # AMBIL DATA PESAN
    # ==================================
    sender = str(payload.get("sender", "")).strip()
    message = str(payload.get("message", "")).strip()

    # fallback beberapa format webhook
    if not sender and "pengirim" in payload:
        sender = str(payload.get("pengirim", "")).strip()

    if not message and "pesan" in payload:
        message = str(payload.get("pesan", "")).strip()

    print("SENDER :", sender)
    print("MESSAGE :", message)

    # ==================================
    # CEGAH LOOPING PESAN BOT
    # ==================================
    if "_Sent via fonnte.com_" in message:
        print("PESAN BOT SENDIRI -> DIABAIKAN")
        return jsonify({"status": True})

    if payload.get("quick") is True:
        print("QUICK MESSAGE -> DIABAIKAN")
        return jsonify({"status": True})

    # ==================================
    # VALIDASI
    # ==================================
    if not sender:
        return jsonify({"status": True})

    if not message:
        return jsonify({"status": True})

    cmd = message.lower()

    # ==================================
    # SALDO
    # ==================================
    if cmd == "saldo":

        masuk = db.session.query(
            db.func.sum(Transaksi.nominal)
        ).filter(
            Transaksi.tipe == "MASUK"
        ).scalar() or 0

        keluar = db.session.query(
            db.func.sum(Transaksi.nominal)
        ).filter(
            Transaksi.tipe == "KELUAR"
        ).scalar() or 0

        saldo = masuk - keluar

        kirim_wa(
            sender,
            f"💰 SALDO\n\n"
            f"Masuk : Rp {masuk:,.0f}\n"
            f"Keluar : Rp {keluar:,.0f}\n"
            f"Saldo : Rp {saldo:,.0f}"
        )

        return jsonify({"status": True})

    # ==================================
    # MASUK
    # ==================================
    elif cmd.startswith("masuk"):

    print("MASUK COMMAND TERDETEKSI")

    try:

        parts = message.split()

        nominal = int(parts[1])

        keterangan = " ".join(parts[2:])

        print("NOMINAL :", nominal)
        print("KETERANGAN :", keterangan)

        trx = Transaksi(
            tanggal=datetime.now(),
            tipe="MASUK",
            nominal=nominal,
            keterangan=keterangan,
            nomor_wa=sender
        )

        db.session.add(trx)
        db.session.commit()

        print("DATA TERSIMPAN")

        kirim_wa(
            sender,
            f"✅ Pemasukan tersimpan\n\n"
            f"Rp {nominal:,.0f}\n"
            f"{keterangan}"
        )

        print("KIRIM WA SELESAI")

    except Exception as e:

        print("ERROR MASUK :", str(e))

    # ==================================
    # KELUAR
    # ==================================
    elif cmd.startswith("keluar"):

        try:

            parts = message.split()

            nominal = int(parts[1])
            keterangan = " ".join(parts[2:])

            trx = Transaksi(
                tanggal=datetime.now(),
                tipe="KELUAR",
                nominal=nominal,
                keterangan=keterangan,
                nomor_wa=sender
            )

            db.session.add(trx)
            db.session.commit()

            kirim_wa(
                sender,
                f"✅ Pengeluaran tersimpan\n\n"
                f"Rp {nominal:,.0f}\n"
                f"{keterangan}"
            )

        except Exception as e:

            print(e)

            kirim_wa(
                sender,
                "Format:\nkeluar 25000 makan siang"
            )

        return jsonify({"status": True})

    # ==================================
    # HARI INI
    # ==================================
    elif cmd == "hariini":

        today = datetime.now().date()

        data = Transaksi.query.filter(
            db.func.date(Transaksi.tanggal) == today
        ).all()

        total = sum(x.nominal for x in data)

        kirim_wa(
            sender,
            f"📅 Hari Ini\n\n"
            f"Jumlah Transaksi : {len(data)}\n"
            f"Total : Rp {total:,.0f}"
        )

        return jsonify({"status": True})

    # ==================================
    # HELP
    # ==================================
    kirim_wa(
        sender,
        "Perintah:\n\n"
        "masuk 100000 gaji\n"
        "keluar 25000 makan\n"
        "saldo\n"
        "hariini"
    )

    return jsonify({"status": True})

@app.route("/test-wa")
def test_wa():

    kirim_wa(
        "6285872362212",
        "Test dari Railway berhasil"
    )

    return {
        "status": True
    }

@app.route("/debug-token")
def debug_token():

    token = os.getenv("FONTE_TOKEN")

    return {
        "exists": bool(token),
        "length": len(token) if token else 0
    }

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
    )
