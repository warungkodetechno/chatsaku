import os

from flask import Blueprint,Flask, request, jsonify, render_template, send_file, redirect
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, time
from models import db, Transaksi, Budget, Reminder, User, RequestDemo, TargetPembelian, HutangPiutang, MonthlySummary, Transaksi, get_owner_number
import requests
import os
import pandas as pd
import io
from zoneinfo import ZoneInfo
from itsdangerous import URLSafeTimedSerializer
from itsdangerous import BadSignature
from itsdangerous import SignatureExpired

from utils.duplicate import is_duplicate
from utils.helper import *
from utils.ai_insight import *

webhook_bp = Blueprint("webhook", __name__)

def get_current_balance(nomor_wa):
    verify_monthly_summary(
        nomor_wa
    )
    """
    Menghitung saldo saat ini menggunakan snapshot MonthlySummary.

    Alur:
    1. Cari snapshot (closing) terakhir.
    2. Jika ada:
       saldo = saldo_akhir_snapshot
             + pemasukan setelah snapshot
             - pengeluaran setelah snapshot

    3. Jika belum ada snapshot:
       saldo = seluruh pemasukan
             - seluruh pengeluaran
    """

    last_summary = (
        MonthlySummary.query
        .filter_by(nomor_wa=nomor_wa)
        .order_by(MonthlySummary.periode.desc())
        .first()
    )

    # =====================================
    # BELUM ADA CLOSING
    # =====================================
    if last_summary is None:

        total_masuk = (
            db.session.query(
                func.coalesce(func.sum(Transaksi.nominal), 0)
            )
            .filter(
                Transaksi.nomor_wa == nomor_wa,
                Transaksi.tipe == "MASUK"
            )
            .scalar()
        )

        total_keluar = (
            db.session.query(
                func.coalesce(func.sum(Transaksi.nominal), 0)
            )
            .filter(
                Transaksi.nomor_wa == nomor_wa,
                Transaksi.tipe == "KELUAR"
            )
            .scalar()
        )

        return total_masuk - total_keluar

    # =====================================
    # ADA SNAPSHOT
    # =====================================

    tahun, bulan = map(int, last_summary.periode.split("-"))

    if bulan == 12:

        mulai = datetime(
            tahun + 1,
            1,
            1,
            0,
            0,
            0
        )

    else:

        mulai = datetime(
            tahun,
            bulan + 1,
            1,
            0,
            0,
            0
        )

    total_masuk = (
        db.session.query(
            func.coalesce(func.sum(Transaksi.nominal), 0)
        )
        .filter(
            Transaksi.nomor_wa == nomor_wa,
            Transaksi.tipe == "MASUK",
            Transaksi.tanggal >= mulai
        )
        .scalar()
    )

    total_keluar = (
        db.session.query(
            func.coalesce(func.sum(Transaksi.nominal), 0)
        )
        .filter(
            Transaksi.nomor_wa == nomor_wa,
            Transaksi.tipe == "KELUAR",
            Transaksi.tanggal >= mulai
        )
        .scalar()
    )

    saldo = (
        last_summary.saldo_akhir
        + total_masuk
        - total_keluar
    )

    return saldo

def refresh_summary_after_transaction(tanggal_transaksi):
    """
    Refresh MonthlySummary setelah transaksi ditambah,
    diubah atau dihapus.

    Hanya akan melakukan cascade jika bulan tersebut
    sudah pernah dilakukan closing.
    """

    periode = tanggal_transaksi.strftime("%Y-%m")

    summary = MonthlySummary.query.filter_by(
        periode=periode
    ).first()

    # Belum pernah closing
    if summary is None:
        return

    print("=" * 60)
    print("AUTO RECALCULATE")
    print("Periode :", periode)
    print("=" * 60)

    cascade_reclosing(periode)

# =========================
# WEBHOOK
# =========================
@webhook_bp.route("/webhook", methods=["POST"])
def webhook():

    payload = request.get_json(silent=True) or {}

    print("=" * 80)
    print("WEBHOOK INCOMING")
    print(payload)
    print("=" * 80)

    # ======================================
    # IGNORE STATUS EVENT
    # ======================================

    if payload.get("status") or payload.get("state"):
        return jsonify(status=True)

    if payload.get("event") in [
        "sent",
        "delivered",
        "read"
    ]:
        return jsonify(status=True)

    # ======================================
    # AMBIL DATA
    # ======================================

    sender = normalize_wa(
        payload.get("sender")
        or payload.get("pengirim")
        or payload.get("from")
        or ""
    )

    message = str(
        payload.get("message")
        or payload.get("pesan")
        or ""
    ).strip()

    msg_id = (
        payload.get("id")
        or payload.get("inboxid")
        or f"{sender}:{int(time.time())}"
    )

    if not sender:
        return jsonify(status=True)

    if not message:
        return jsonify(status=True)

    lower_msg = message.lower()

    print("Sender :", sender)
    print("Message:", message)

    pushname = str(payload.get("pushname") or "").strip()

    # ======================================
    # ANTI LOOP
    # ======================================

    # pesan dari bot sendiri
    if message.startswith("[BOT]"):
        return jsonify(status=True)

    # footer fonnte
    if "sent via fonnte" in lower_msg:
        return jsonify(status=True)

    # balasan bot
    if "chatsaku finance assistant" in lower_msg:
        return jsonify(status=True)

    if "nomor belum terdaftar" in lower_msg:
        return jsonify(status=True)

    # nomor bot sendiri
    bot_number = normalize_wa(
        os.getenv("BOT_NUMBER", "")
    )

    if sender == bot_number:
        return jsonify(status=True)

    # ======================================
    # DUPLICATE FILTER
    # ======================================

    if is_duplicate(msg_id):
        return jsonify(status=True)

    # ======================================
    # COMMAND
    # ======================================

    cmd = lower_msg.strip()

    print("CMD :", cmd)

    print("SENDER :", sender)
    print("MESSAGE:", message)
    print("CMD    :", cmd)

    # ======================================
    # REQUEST DEMO
    # ======================================

    if cmd.startswith("halo chatsaku, saya ingin mencoba versi gratis"):

        print("="*50)
        print("REQUEST DEMO")
        print("Sender :", sender)
        print("Pushname :", pushname)
        print("="*50)


        demo = RequestDemo.query.filter_by(
            nomor_wa=sender
        ).first()

        if demo is None:

            demo = RequestDemo(
                nomor_wa=sender,
                nama=pushname or ""
            )

            db.session.add(demo)
            db.session.commit()

            kirim_wa(
                sender,
                """🎉 Terima kasih telah mendaftar ChatSaku Free.

    Permintaan Anda berhasil diterima.

    Silakan mulai menggunakan ChatSaku dengan contoh berikut:

    • masuk 500000 gaji
    • keluar 25000 makan

    Selamat mencoba 😊"""
            )

        else:

            kirim_wa(
                sender,
                """✅ Anda sudah pernah mendaftar ChatSaku Free.

    Silakan langsung kirim transaksi, misalnya:

    • masuk 500000 gaji
    • keluar 25000 makan"""
            )

        return jsonify({"status": True})

    # ======================================
    # VALIDASI USER
    # ======================================

    user = user_terdaftar(sender)

    print("User :", user)

    if not user:

        print("UNREGISTERED :", sender)

        kirim_wa(
            sender,
            """🚫 *Nomor Belum Terdaftar*

Maaf, nomor WhatsApp Anda belum terdaftar pada sistem *ChatSaku Finance*.

Silakan hubungi Admin untuk mengaktifkan akun Anda.

https://www.chatsaku.com

💚 ChatSaku Finance Assistant
"""
        )

        return jsonify(
            status=True,
            registered=False
        )

    # ======================================
    # CEK MASA BERLANGGANAN
    # ======================================

    from datetime import date

    if user.paket != "STARTER" and user.akhir_langganan:

        if date.today() > user.akhir_langganan:

            if user.aktif:

                user.aktif = False
                db.session.commit()

            kirim_wa(
                sender,
                f"""🔒 *Langganan ChatSaku Telah Berakhir*

    Paket : {user.paket}

    Berakhir pada:
    {user.akhir_langganan.strftime("%d-%m-%Y")}

    Silakan lakukan perpanjangan agar seluruh fitur dapat digunakan kembali.

    https://www.chatsaku.com

    💚 ChatSaku Finance Assistant"""
            )

            return jsonify(status=True)

    # Jika admin menonaktifkan akun
    if not user.aktif:

        kirim_wa(
            sender,
            """🚫 *Akun Anda Nonaktif*

    Silakan hubungi Admin ChatSaku untuk mengaktifkan kembali akun Anda.

    https://www.chatsaku.com"""
        )

        return jsonify(status=True)

    # =====================================================
    # HANYA RESPON COMMAND YANG DIKENAL
    # =====================================================
    valid_command = (
        cmd == "saldo"
        or cmd == "hariini"
        or cmd == "insight"
        or cmd == "dashboard"
        or cmd == "share"
        or cmd.startswith("masuk")
        or cmd.startswith("keluar")
        or cmd.startswith("budget")
        or cmd.startswith("reminder")
        or cmd.startswith("hapusreminder")
        or cmd.startswith("halo chatsaku, saya ingin mencoba versi gratis")
        or cmd == "menu"
        or cmd == "fitur"
        or cmd == "help"
        or cmd == "target"
        or cmd.startswith("target ")
        or cmd.startswith("tabung")
        or cmd.startswith("hapustarget")
        or cmd=="hutang"
        or cmd.startswith("hutang ")
        or cmd=="piutang"
        or cmd.startswith("piutang ")
        or cmd.startswith("bayarhutang")
        or cmd.startswith("bayarpiutang")
    )

    if not valid_command:
        print("IGNORE NON COMMAND")
        return jsonify({
            "status": True,
            "ignored": True
        })

    # ======================================
    # TARGET BARU
    # ======================================

    if cmd.startswith("target "):

        if not has_feature(sender, "target"):

            kirim_wa(sender,
    """
    🔒 Fitur target tabungan hanya tersedia pada paket PREMIUM.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ Target Tabungan
    ✅ Hutang Piutang
    ✅ AI Insight
    ✅ Dashboard Lengkap

            """)

        bagian = message.split()

        if len(bagian) >= 4:

            try:

                deadline = datetime.strptime(
                    bagian[-1],
                    "%d-%m-%Y"
                ).date()

                nominal = int(
                    bagian[-2].replace(".","")
                )

                nama = " ".join(
                    bagian[1:-2]
                )

            except:

                kirim_wa(
                    sender,
                    "Format:\n\ntarget laptop 12000000 31-12-2026"
                )

                return jsonify(status=True)

            cek = TargetPembelian.query.filter_by(
                nomor_wa=sender,
                nama=nama,
                aktif=True
            ).first()

            if cek:

                kirim_wa(
                    sender,
                    "Target tersebut sudah ada."
                )

                return jsonify(status=True)

            target = TargetPembelian(

                nomor_wa=sender,
                nama=nama,
                target=nominal,
                deadline=deadline

            )

            db.session.add(target)

            db.session.commit()

            kirim_wa(

                sender,

    f"""🎯 Target berhasil dibuat

Nama        : {nama}
Target      : Rp. {nominal:,.0f}
Deadline    :{deadline.strftime("%d-%m-%Y")}

Selamat menabung 💚"""
            )

            return jsonify(status=True)

    # ======================================
    # TABUNG
    # ======================================

    if cmd.startswith("tabung "):

        if not has_feature(sender, "tabung"):

            kirim_wa(sender,
    """
    🔒 Fitur tabungan hanya tersedia pada paket PREMIUM.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ Target Tabungan
    ✅ Hutang Piutang
    ✅ AI Insight
    ✅ Dashboard Lengkap

            """)

        bagian = message.split()

        if len(bagian) < 3:

            kirim_wa(
                sender,
                "Format:\n\ntabung laptop 500000"
            )

            return jsonify(status=True)

        try:

            nominal = int(
                bagian[-1].replace(".","")
            )

        except:

            return jsonify(status=True)

        nama = " ".join(
            bagian[1:-1]
        )

        target = TargetPembelian.query.filter_by(

            nomor_wa=sender,
            nama=nama,
            aktif=True

        ).first()

        if not target:

            kirim_wa(
                sender,
                "Target tidak ditemukan."
            )

            return jsonify(status=True)

        target.terkumpul += nominal

        db.session.commit()

        persen = round(
            target.terkumpul /
            target.target * 100
        )

        if persen > 100:
            persen = 100

        kirim_wa(

            sender,

    f"""💚 Tabungan berhasil

    {target.nama}

    +Rp {nominal:,.0f}

    Terkumpul
    Rp {target.terkumpul:,.0f}

    Progress
    {persen}%"""

        )

        return jsonify(status=True)

    # ======================================
    # LIST TARGET
    # ======================================

    if cmd == "target":

        if not has_feature(sender, "target"):

            kirim_wa(
                sender,
                """
    🔒 Fitur Target Tabungan hanya tersedia pada paket PREMIUM.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ Target Tabungan
    ✅ Hutang Piutang
    ✅ AI Insight
    ✅ Dashboard Lengkap
    """
            )

            return jsonify(status=True)

        nomor = get_owner_number(sender)

        data = TargetPembelian.query.filter_by(
            nomor_wa=nomor,
            aktif=True
        ).all()

        if not data:

            kirim_wa(
                sender,
                "Belum ada target tabungan."
            )

            return jsonify(status=True)

        text = "🎯 *TARGET TABUNGAN*\n\n"

        if nomor != sender:
            text += "👁 Mode Viewer (Data Owner)\n\n"

        for i, x in enumerate(data, 1):

            persen = round(
                (x.terkumpul / x.target) * 100
            ) if x.target else 0

            persen = min(persen, 100)

            sisa = max(x.target - x.terkumpul, 0)

            text += f"""*{i}. {x.nama}*

    📊 Progress : {persen}%
    💰 Terkumpul : Rp {x.terkumpul:,.0f}
    🎯 Target    : Rp {x.target:,.0f}
    💵 Sisa      : Rp {sisa:,.0f}

    """

        kirim_wa(sender, text)

        return jsonify(status=True)

    # ======================================
    # DETAIL TARGET
    # ======================================
    if cmd.startswith("target "):

        if not has_feature(sender, "target"):

            kirim_wa(
                sender,
                """
    🔒 Fitur target tabungan hanya tersedia pada paket PREMIUM.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ Target Tabungan
    ✅ Hutang Piutang
    ✅ AI Insight
    ✅ Dashboard Lengkap
    """
            )

            return jsonify(status=True)

        nomor = get_owner_number(sender)

        nama = message[7:].strip()

        target = TargetPembelian.query.filter_by(
            nomor_wa=nomor,
            nama=nama,
            aktif=True
        ).first()

        if not target:

            kirim_wa(
                sender,
                "❌ Target tidak ditemukan."
            )

            return jsonify(status=True)

        persen = round(
            (target.terkumpul / target.target) * 100
        ) if target.target else 0

        persen = min(persen, 100)

        sisa = max(target.target - target.terkumpul, 0)

        viewer_info = ""

        if nomor != sender:
            viewer_info = "\n👁 Mode Viewer (Data Owner)\n"

        kirim_wa(
            sender,
            f"""🎯 *{target.nama}*

    🎯 Target
    Rp {target.target:,.0f}

    💰 Terkumpul
    Rp {target.terkumpul:,.0f}

    💵 Sisa
    Rp {sisa:,.0f}

    📊 Progress
    {persen}%
    {viewer_info}
    🤖 ChatSaku Finance Assistant"""
        )

        return jsonify(status=True)

    # ======================================
    # HAPUS TARGET
    # ======================================

    if cmd.startswith("hapustarget"):

        if not has_feature(sender, "target"):

            kirim_wa(sender,
    """
    🔒 Fitur target tabungan hanya tersedia pada paket PREMIUM.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ Target Tabungan
    ✅ Hutang Piutang
    ✅ AI Insight
    ✅ Dashboard Lengkap

            """)

        nama = message.replace(
            "hapustarget",
            ""
        ).strip()

        target = TargetPembelian.query.filter_by(

            nomor_wa=sender,
            nama=nama,
            aktif=True

        ).first()

        if target:

            db.session.delete(target)

            db.session.commit()

            kirim_wa(

                sender,

                "🗑 Target berhasil dihapus."

            )

        else:

            kirim_wa(

                sender,

                "Target tidak ditemukan."

            )

        return jsonify(status=True)

    # =========================
    # SALDO
    # =========================
    if cmd == "saldo":

        nomor = get_owner_number(sender)

        masuk = transaksi_user(nomor).filter(
            Transaksi.tipe == "MASUK"
        ).with_entities(
            db.func.sum(Transaksi.nominal)
        ).scalar() or 0

        keluar = transaksi_user(nomor).filter(
            Transaksi.tipe == "KELUAR"
        ).with_entities(
            db.func.sum(Transaksi.nominal)
        ).scalar() or 0

        saldo = get_current_balance(nomor)

        # link = generate_dashboard_link(sender)

        kirim_wa(
            sender,
            f"""💳 *Saldo Keuangan*
┌─────────────────────┐
📥 Masuk   : Rp {masuk:,.0f}
📤 Keluar  : Rp {keluar:,.0f}
 ──────────────────────
💰 Saldo   : *Rp {saldo:,.0f}*
└─────────────────────┘

🤖 ChatSaku Finance Assistant
"""
        )
        return jsonify({"status": True})

    # =========================
    # MASUK
    # =========================
    if cmd.startswith("masuk"):

        try:

            parts = message.split()

            nominal = int(parts[1])

            keterangan = (
                " ".join(parts[2:])
                if len(parts) > 2
                else "-"
            )

            # link = generate_dashboard_link(sender)

            trx = Transaksi(
                tanggal=sekarang(),
                tipe="MASUK",
                nominal=nominal,
                keterangan=keterangan,
                nomor_wa=sender
            )

            db.session.add(trx)
            db.session.commit()

            masuk = transaksi_user(sender).filter(
                Transaksi.tipe == "MASUK"
            ).with_entities(
                db.func.sum(Transaksi.nominal)
            ).scalar() or 0

            keluar = transaksi_user(sender).filter(
                Transaksi.tipe == "KELUAR"
            ).with_entities(
                db.func.sum(Transaksi.nominal)
            ).scalar() or 0

            saldo = masuk - keluar

            kirim_wa(
                sender,
                f"""✅ *Transaksi Berhasil*
┌────────────────────┐
💰 *KREDIT MASUK*

💵 Nominal      : Rp. {nominal:,.0f}
📝 Keterangan   : {keterangan}

🕒 {sekarang().strftime("%d %b %Y • %H:%M")}
└────────────────────┘

💳 *Saldo Saat Ini*
Rp {saldo:,.0f}

━━━━━━━━━━━━━━━━━━
🤖 ChatSaku Finance Assistant
"""
            )

        except Exception:

            kirim_wa(
                sender,
                "Format:\nmasuk 100000 gaji"
            )

        return jsonify({"status": True})

    # =========================
    # KELUAR
    # =========================
    if cmd.startswith("keluar"):

        try:

            parts = message.split()

            if len(parts) < 3:

                kirim_wa(
                    sender,
                    "Format:\nkeluar 25000 grab"
                )

                return jsonify({"status": True})

            nominal = int(
                parts[1]
                .replace(".", "")
                .replace(",", "")
            )

            keterangan = " ".join(parts[2:])

            kategori, subkategori = cari_kategori(keterangan)

            trx = Transaksi(
                tanggal=sekarang(),
                tipe="KELUAR",
                nominal=nominal,
                kategori=kategori,
                subkategori=subkategori,
                keterangan=keterangan,
                nomor_wa=sender
            )

            db.session.add(trx)
            db.session.commit()

            # ======================================
            # TOTAL SALDO
            # ======================================

            masuk = transaksi_user(sender).filter(
                Transaksi.tipe == "MASUK"
            ).with_entities(
                db.func.sum(Transaksi.nominal)
            ).scalar() or 0

            keluar = transaksi_user(sender).filter(
                Transaksi.tipe == "KELUAR"
            ).with_entities(
                db.func.sum(Transaksi.nominal)
            ).scalar() or 0

            saldo = masuk - keluar

            # ======================================
            # DASHBOARD
            # ======================================

            link = generate_dashboard_link(sender)

            # ======================================
            # BUDGET
            # ======================================

            periode = periode_sekarang()

            budget = Budget.query.filter_by(
                nomor_wa=sender,
                kategori=kategori,
                periode=periode
            ).first()

            budget_text = ""

            if budget:

                # awal & akhir bulan
                now = sekarang()

                awal_bulan = now.replace(
                    day=1,
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0
                )

                if now.month == 12:
                    akhir_bulan = now.replace(
                        year=now.year + 1,
                        month=1,
                        day=1,
                        hour=0,
                        minute=0,
                        second=0,
                        microsecond=0
                    )
                else:
                    akhir_bulan = now.replace(
                        month=now.month + 1,
                        day=1,
                        hour=0,
                        minute=0,
                        second=0,
                        microsecond=0
                    )

                total_keluar = transaksi_user(sender).filter(
                    Transaksi.tipe == "KELUAR",
                    Transaksi.kategori == kategori,
                    Transaksi.tanggal >= awal_bulan,
                    Transaksi.tanggal < akhir_bulan
                ).with_entities(
                    db.func.sum(Transaksi.nominal)
                ).scalar() or 0

                persen = (
                    (total_keluar / budget.nominal) * 100
                    if budget.nominal > 0 else 0
                )

                sisa = budget.nominal - total_keluar

                blok = min(10, int(persen / 10))
                bar = "🟩" * blok + "⬜" * (10 - blok)

                if persen <= 50:
                    status = "🟢 Budget Aman"

                elif persen <= 80:
                    status = "🟡 Perlu Perhatian"

                elif persen <= 100:
                    status = "🟠 Hampir Habis"

                else:
                    status = "🔴 Budget Terlampaui"

                budget_text = f"""
──────────────────
🏦 *Budget Bulan Ini*

🏷️ Kategori
{kategori.title()}

💰 Budget
Rp {budget.nominal:,.0f}

💸 Terpakai
Rp {total_keluar:,.0f}

💳 Sisa Budget
Rp {max(sisa,0):,.0f}

📊 Progress
{persen:.1f}%

{bar}

{status}
"""

                if persen > 100:

                    over = total_keluar - budget.nominal

                    budget_text += f"""

⚠️ Melebihi Budget
Rp {over:,.0f}
"""

            else:

                budget_text = """
 ──────────────────

🏦 *Budget Bulan Ini*

ℹ️ Belum ada budget untuk kategori ini.

Contoh:
budget transport 1000000
"""

            kirim_wa(
                sender,
                f"""🏦 *Notifikasi Transaksi*
──────────────────

✅ *Debit Berhasil*
💸 - Rp {nominal:,.0f}

🏷️ Kategori
{kategori.title()}

📂 Subkategori
{subkategori.title()}

📝 Keterangan
{keterangan}

🕒 {sekarang().strftime("%d %b %Y • %H:%M")}

{budget_text}

──────────────────

💳 Saldo Tersedia
*Rp {saldo:,.0f}*

──────────────────
🤖 ChatSaku Finance Assistant
"""
            )

        except Exception as e:

            print(e)

            kirim_wa(
                sender,
                f"""❌ Terjadi kesalahan

{e}

Format:
keluar 25000 grab
"""
            )

        return jsonify({"status": True})

    # =========================
    # HARI INI
    # =========================
    if cmd == "hariini":

        nomor = get_owner_number(sender)

        today = sekarang().date()

        data = transaksi_user(nomor).filter(
            db.func.date(Transaksi.tanggal) == today
        ).all()

        total = sum(x.nominal for x in data)

        masuk_hari_ini = sum(
            x.nominal
            for x in data
            if x.tipe == "MASUK"
        )

        keluar_hari_ini = sum(
            x.nominal
            for x in data
            if x.tipe == "KELUAR"
        )

        viewer_info = ""

        if nomor != sender:
            viewer_info = "\n👁 *Mode Viewer (Data Owner)*\n"

        kirim_wa(
            sender,
            f"""📊 *Ringkasan Hari Ini*
    ━━━━━━━━━━━━━━

    🧾 *Jumlah Transaksi*
    {len(data)}

    📥 *Pemasukan*
    Rp {masuk_hari_ini:,.0f}

    📤 *Pengeluaran*
    Rp {keluar_hari_ini:,.0f}

    ━━━━━━━━━━━━━━

    💰 *Total Aktivitas*
    Rp {total:,.0f}
    {viewer_info}
    ━━━━━━━━━━━━━━

    🤖 ChatSaku Finance Assistant
    """
        )

        return jsonify({"status": True})

    # =========================
    # BUDGET
    # =========================
    if cmd.startswith("budget"):

        nomor = get_owner_number(sender)

        if not has_feature(sender, "budget"):

            kirim_wa(sender,
    """
    🔒 Fitur Budget hanya tersedia pada paket PRO dan PREMIUM.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ AI Insight
    ✅ Dashboard Lengkap

            """)

            return jsonify(status=True)

        try:

            parts = message.lower().split()

            # =========================
            # LIHAT BUDGET
            # =========================
            if len(parts) == 1:

                periode = periode_sekarang()

                budgets = Budget.query.filter_by(
                    nomor_wa=nomor,
                    periode=periode
                ).order_by(
                    Budget.kategori.asc()
                ).all()

                if not budgets:

                    kirim_wa(
                        sender,
                        "📭 Belum ada budget bulan ini.\n\n"
                        "Contoh:\n"
                        "budget makanan 1500000"
                    )

                    return jsonify({"status": True})

                pesan = f"🎯 *Budget Bulan {periode}*\n"
                pesan += "━━━━━━━━━━━━━━\n\n"

                total_budget = 0
                total_terpakai = 0

                for b in budgets:

                    total_budget += b.nominal

                    now = sekarang()

                    awal_bulan = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

                    if now.month == 12:
                        akhir_bulan = now.replace(year=now.year + 1, month=1, day=1,
                                                hour=0, minute=0, second=0, microsecond=0)
                    else:
                        akhir_bulan = now.replace(month=now.month + 1, day=1,
                                                hour=0, minute=0, second=0, microsecond=0)

                    # =============================
                    # TOTAL PENGELUARAN KATEGORI BULAN INI
                    # =============================
                    terpakai = transaksi_user(nomor).filter(
                        Transaksi.tipe == "KELUAR",
                        Transaksi.kategori == b.kategori,
                        Transaksi.tanggal >= awal_bulan,
                        Transaksi.tanggal < akhir_bulan
                    ).with_entities(
                        db.func.sum(Transaksi.nominal)
                    ).scalar() or 0

                    total_terpakai += terpakai

                    persen = (
                        (terpakai / b.nominal) * 100
                        if b.nominal > 0 else 0
                    )

                    sisa = b.nominal - terpakai

                    blok = min(10, int(persen / 10))

                    progress = (
                        "🟩" * blok +
                        "⬜" * (10 - blok)
                    )

                    if persen < 50:
                        status = "🟢 Aman"

                    elif persen < 80:
                        status = "🟡 Waspada"

                    elif persen <= 100:
                        status = "🟠 Hampir Habis"

                    else:
                        status = "🔴 Terlampaui"

                    pesan += (
                        f"📂 *{b.kategori.title()}*\n"
                        f"💰 Budget   : Rp {b.nominal:,.0f}\n"
                        f"📉 Terpakai : Rp {terpakai:,.0f}\n"
                        f"💵 Sisa     : Rp {max(sisa,0):,.0f}\n"
                        f"📊 {persen:.1f}%\n"
                        f"{progress}\n"
                        f"{status}\n\n"
                    )

                pesan += "━━━━━━━━━━━━━━\n"

                total_persen = (
                    (total_terpakai / total_budget) * 100
                    if total_budget > 0 else 0
                )

                blok = min(10, int(total_persen / 10))

                progress = (
                    "🟩" * blok +
                    "⬜" * (10 - blok)
                )

                pesan += (
                    f"💼 *TOTAL BUDGET*\n\n"
                    f"💰 Budget   : Rp {total_budget:,.0f}\n"
                    f"📉 Terpakai : Rp {total_terpakai:,.0f}\n"
                    f"💵 Sisa     : Rp {max(total_budget-total_terpakai,0):,.0f}\n\n"
                    f"📊 {total_persen:.1f}%\n"
                    f"{progress}"
                )

                kirim_wa(sender, pesan)

                return jsonify({"status": True})

            if is_viewer(sender):

                kirim_wa(
                    sender,
                    """🔒 Mode Viewer

            Anda hanya dapat melihat Budget.

            Perubahan Budget hanya dapat dilakukan oleh Owner."""
                )

                return jsonify(status=True)

            # =========================
            # FORMAT
            # =========================
            if len(parts) < 3:

                kirim_wa(
                    sender,
                    "Format:\n"
                    "budget makanan 1500000"
                )

                return jsonify({"status":True})

            kategori = parts[1].lower()

            # =========================
            # VALIDASI KATEGORI
            # =========================
            if kategori not in KATEGORI.keys():

                daftar = "\n".join(
                    f"• {x.title()}"
                    for x in KATEGORI.keys()
                )

                kirim_wa(
                    sender,
                    f"""❌ Kategori tidak tersedia.

Kategori:

{daftar}

Contoh:
budget makanan 1500000
"""
                )

                return jsonify({"status":True})

            nominal = int(
                parts[2]
                .replace(".","")
                .replace(",","")
            )

            periode = periode_sekarang()

            # =========================
            # VALIDASI SALDO
            # =========================

            # Hitung saldo saat ini
            saldo = hitung_saldo(sender)

            # Budget yang sudah ada (jika edit)
            budget_lama = Budget.query.filter_by(
                nomor_wa=sender,
                kategori=kategori,
                periode=periode
            ).first()

            # Total budget bulan ini
            total_budget = db.session.query(
                db.func.coalesce(db.func.sum(Budget.nominal), 0)
            ).filter(
                Budget.nomor_wa == sender,
                Budget.periode == periode
            ).scalar()

            # Jika sedang mengubah budget,
            # kurangi budget lama agar tidak dihitung dua kali
            if budget_lama:
                total_budget -= budget_lama.nominal

            total_setelah = total_budget + nominal

            # Tidak boleh melebihi saldo
            if total_setelah > saldo:

                sisa = max(saldo - total_budget, 0)

                kirim_wa(
                    sender,
                    f"""⚠️ *Budget Tidak Dapat Dibuat*
━━━━━━━━━━━━━━

💰 Saldo Anda
Rp {saldo:,.0f}

📊 Total Budget Setelah Disimpan
Rp {total_setelah:,.0f}

❌ Budget melebihi saldo yang tersedia.

Sisa budget yang masih bisa dibuat:

💵 Rp {sisa:,.0f}

Silakan kurangi nominal budget atau tambahkan saldo terlebih dahulu.
"""
                )

                return jsonify({"status": True})

            budget = Budget.query.filter_by(
                nomor_wa=sender,
                kategori=kategori,
                periode=periode
            ).first()

            if budget:

                budget.nominal = nominal

                status = "Diperbarui"

            else:

                budget = Budget(
                    nomor_wa=sender,
                    kategori=kategori,
                    nominal=nominal,
                    periode=periode
                )

                db.session.add(budget)

                status = "Dibuat"

            db.session.commit()

            kirim_wa(
                sender,
                f"""🎯 *Budget {status}*
━━━━━━━━━━━━━━

📂 Kategori
{kategori.title()}

💰 Budget
Rp {nominal:,.0f}

📅 Periode
{periode}

━━━━━━━━━━━━━━

Ketik *budget*
untuk melihat semua budget.
"""
            )

        except Exception as e:

            print(e)

            kirim_wa(
                sender,
                f"Terjadi kesalahan.\n\n{e}"
            )

        return jsonify({"status":True})

    # =========================
    # AI INSIGHT
    # =========================
    if cmd == "insight":

        from utils.ai_insight import generate_ai_insight

        if not has_feature(sender, "ai"):

            kirim_wa(
                sender,
                """🔒 *Fitur AI Insight* hanya tersedia pada paket PREMIUM.

    Upgrade sekarang untuk menikmati:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ Target Tabungan
    ✅ Hutang Piutang
    ✅ AI Finance Insight
    ✅ Dashboard Lengkap
    """
            )

            return jsonify(status=True)

        try:

            nomor = get_owner_number(sender)

            insight = generate_ai_insight(nomor)

            viewer_info = ""

            if nomor != sender:
                viewer_info = (
                    "👁 *Mode Viewer*\n"
                    "Analisis ini menggunakan data Owner.\n\n"
                )

            pesan = f"""🏦 *AI Finance Insight*
    ──────────────────

    {viewer_info}🧠 *Analisis AI*

    """

            for item in insight:
                pesan += f"• {item}\n"

            pesan += """

    ──────────────────
    🤖 ChatSaku Finance Assistant
    💚 AI Powered • WhatsApp Finance
    """

            kirim_wa(sender, pesan)

        except Exception as e:

            print(e)

            kirim_wa(
                sender,
                f"Terjadi kesalahan\n\n{e}"
            )

        return jsonify(status=True)

    # =========================
    # REMINDER
    # =========================
    if cmd.startswith("reminder"):
        if not has_feature(sender, "reminder"):

            kirim_wa(
                sender,
                "🔒 Reminder tersedia di paket PRO."
            )

            return jsonify(status=True)

        try:
            nomor = get_owner_number(sender)

            parts = message.lower().split()

            # =========================
            # LIHAT REMINDER
            # =========================
            if len(parts) == 1:

                reminders = Reminder.query.filter_by(
                    nomor_wa=nomor,
                    aktif=True
                ).order_by(
                    Reminder.tanggal.asc()
                ).all()

                if not reminders:

                    kirim_wa(
                        sender,
                        "📭 Belum ada reminder.\n\n"
                        "Contoh:\n"
                        "reminder listrik 20 500000"
                    )

                    return jsonify({"status": True})

                pesan = "🔔 *Reminder Tagihan*\n"
                pesan += "━━━━━━━━━━━━━━\n\n"

                total = 0

                for r in reminders:

                    total += r.nominal

                    pesan += (
                        f"📄 {r.nama.title()}\n"
                        f"📅 Tanggal : {r.tanggal}\n"
                        f"💰 Rp {r.nominal:,.0f}\n\n"
                    )

                pesan += "━━━━━━━━━━━━━━\n"
                pesan += f"💵 Total Tagihan\nRp {total:,.0f}"

                kirim_wa(sender, pesan)

                return jsonify({"status": True})

            if is_viewer(sender):

                kirim_wa(
                    sender,
                    """🔒 Mode Viewer

            Anda hanya dapat melihat Reminder.

            Perubahan Reminder hanya dapat dilakukan oleh Owner."""
                )

                return jsonify(status=True)

            # =========================
            # FORMAT
            # =========================
            if len(parts) < 4:

                kirim_wa(
                    sender,
                    "Format:\n"
                    "reminder listrik 20 500000"
                )

                return jsonify({"status": True})

            nama = parts[1]

            tanggal = int(parts[2])

            nominal = int(
                parts[3]
                .replace(".", "")
                .replace(",", "")
            )

            if tanggal < 1 or tanggal > 31:

                kirim_wa(
                    sender,
                    "Tanggal harus antara 1-31."
                )

                return jsonify({"status": True})

            reminder = Reminder.query.filter_by(
                nomor_wa=sender,
                nama=nama
            ).first()

            if reminder:

                reminder.tanggal = tanggal
                reminder.nominal = nominal
                reminder.aktif = True

                status = "Diperbarui"

            else:

                reminder = Reminder(

                    nomor_wa=sender,

                    nama=nama,

                    tanggal=tanggal,

                    nominal=nominal

                )

                db.session.add(reminder)

                status = "Dibuat"

            db.session.commit()

            kirim_wa(
                sender,
                f"""🔔 *Reminder {status}*
━━━━━━━━━━━━━━

📄 Tagihan
{nama.title()}

📅 Jatuh Tempo
Tanggal {tanggal}

💰 Estimasi
Rp {nominal:,.0f}

━━━━━━━━━━━━━━

Ketik *reminder*
untuk melihat seluruh reminder.
"""
            )

        except Exception as e:

            print(e)

            kirim_wa(
                sender,
                str(e)
            )

        return jsonify({"status": True})

    # =========================
    # HAPUS REMINDER
    # =========================
    if cmd.startswith("hapusreminder"):

        if not has_feature(sender, "hapusreminder"):

            kirim_wa(sender,
    """
    🔒 Fitur Reminder hanya tersedia pada paket PRO dan PREMIUM.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ Target Tabungan
    ✅ Hutang Piutang
    ✅ AI Insight
    ✅ Dashboard Lengkap

            """)

        try:

            parts = message.lower().split()

            if len(parts) < 2:

                kirim_wa(
                    sender,
                    "Format:\n"
                    "hapusreminder listrik"
                )

                return jsonify({"status": True})

            nama = parts[1]

            reminder = Reminder.query.filter_by(
                nomor_wa=sender,
                nama=nama
            ).first()

            if not reminder:

                kirim_wa(
                    sender,
                    "Reminder tidak ditemukan."
                )

                return jsonify({"status": True})

            db.session.delete(reminder)

            db.session.commit()

            kirim_wa(
                sender,
                f"""🗑️ Reminder berhasil dihapus

    📄 {nama.title()}
    """
            )

        except Exception as e:

            print(e)

            kirim_wa(
                sender,
                str(e)
            )

        return jsonify({"status": True})

    # =========================
    # HUTANG LIHAT
    # =========================

    if cmd == "hutang":

        if not has_feature(sender, "hutang"):

            kirim_wa(sender,
    """
    🔒 Fitur hutang hanya tersedia pada paket PREMIUM.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ Target Tabungan
    ✅ Hutang Piutang
    ✅ AI Insight
    ✅ Dashboard Lengkap

            """)

        daftar = HutangPiutang.query.filter(
            HutangPiutang.nomor_wa == sender,
            HutangPiutang.tipe == "HUTANG"
        ).order_by(
            HutangPiutang.tanggal.desc()
        ).all()


        if not daftar:

            kirim_wa(
                sender,
                """💳 *Daftar Hutang*

    Belum ada data hutang.

    🤖 ChatSaku Finance"""
            )

            return jsonify(status=True)



        total = 0

        pesan = """💳 *Daftar Hutang*

    """


        for i, h in enumerate(daftar,1):

            status = (
                "✅ LUNAS"
                if h.status == "LUNAS"
                else "⏳ BELUM LUNAS"
            )


            pesan += f"""
    {i}. 👤 {h.nama}
    💰 Rp {h.nominal:,.0f}
    📌 {status}
    📝 {h.keterangan or "-"}

    """


            if h.status != "LUNAS":
                total += h.nominal



        pesan += f"""
    ━━━━━━━━━━━━━━
    Total Aktif:
    💰 Rp {total:,.0f}

    🤖 ChatSaku Finance
    """


        kirim_wa(
            sender,
            pesan
        )

        return jsonify(status=True)


    # =========================
    # TAMBAH HUTANG
    # =========================

    if cmd.startswith("hutang "):

        if not has_feature(sender, "hutang"):

            kirim_wa(sender,
    """
    🔒 Fitur hutang hanya tersedia pada paket PREMIUM.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ Target Tabungan
    ✅ Hutang Piutang
    ✅ AI Insight
    ✅ Dashboard Lengkap

            """)

        data = message.split(" ", 3)

        if len(data) < 3:

            kirim_wa(
                sender,
                """❌ Format salah

    Contoh:

    hutang budi 500000 pinjam uang"""
            )

            return jsonify(status=True)


        nama = data[1]


        try:

            nominal = int(data[2])

        except:

            kirim_wa(
                sender,
                "❌ Nominal harus angka"
            )

            return jsonify(status=True)



        keterangan = ""

        if len(data) == 4:
            keterangan = data[3]



        hp = HutangPiutang(

            nomor_wa = sender,

            tipe = "HUTANG",

            nama = nama,

            nominal = nominal,

            status = "AKTIF",

            keterangan = keterangan

        )


        db.session.add(hp)

        db.session.commit()



        kirim_wa(
            sender,
            f"""✅ *Hutang Dicatat*

    👤 {nama}
    💰 Rp {nominal:,.0f}

    📝 {keterangan or "-"}

    Status:
    ⏳ BELUM LUNAS

    🤖 ChatSaku Finance"""
        )


        return jsonify(status=True)



    # =========================
    # LIHAT PIUTANG
    # =========================

    if cmd == "piutang":

        if not has_feature(sender, "piutang"):

            kirim_wa(sender,
    """
    🔒 Fitur Piutang hanya tersedia pada paket PREMIUM.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ Target Tabungan
    ✅ Hutang Piutang
    ✅ AI Insight
    ✅ Dashboard Lengkap

            """)

        daftar = HutangPiutang.query.filter(
            HutangPiutang.nomor_wa == sender,
            HutangPiutang.tipe == "PIUTANG",
            HutangPiutang.status != "LUNAS"
        ).all()


        if not daftar:

            kirim_wa(
                sender,
                """📥 *Daftar Piutang*

    Tidak ada piutang aktif 😊

    🤖 ChatSaku Finance"""
            )

            return jsonify(status=True)



        total = 0


        pesan = """📥 *Daftar Piutang Aktif*

    """


        for i, p in enumerate(daftar, 1):

            jumlah = p.nominal or 0

            pesan += f"""
    {i}. 👤 {p.nama}
    💰 Rp {jumlah:,.0f}
    📝 {p.keterangan or "-"}
    """


            total += jumlah



        pesan += f"""
    ━━━━━━━━━━━━━━
    Total Piutang:
    💰 Rp {total:,.0f}

    🤖 ChatSaku Finance
    """


        kirim_wa(
            sender,
            pesan
        )


        return jsonify(status=True)

    # =========================
    # TAMBAH PIUTANG
    # =========================

    if cmd.startswith("piutang "):

        if not has_feature(sender, "piutang"):

            kirim_wa(sender,
    """
    🔒 Fitur Piutang hanya tersedia pada paket PREMIUM.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ Target Tabungan
    ✅ Hutang Piutang
    ✅ AI Insight
    ✅ Dashboard Lengkap

            """)

        data = message.split(" ", 3)


        if len(data) < 3:

            kirim_wa(
                sender,
                """❌ Format Piutang salah

    Gunakan:

    piutang nama nominal keterangan

    Contoh:
    piutang agus 300000 makan bersama"""
            )

            return jsonify(status=True)



        nama = data[1]


        try:

            nominal = int(data[2])

        except:

            kirim_wa(
                sender,
                "❌ Nominal harus berupa angka."
            )

            return jsonify(status=True)



        keterangan = ""

        if len(data) == 4:
            keterangan = data[3]



        hp = HutangPiutang(

            nomor_wa=sender,

            tipe="PIUTANG",

            nama=nama,

            nominal=nominal,

            status="AKTIF",

            keterangan=keterangan

        )


        db.session.add(hp)

        db.session.commit()



        kirim_wa(
            sender,
            f"""✅ *Piutang Dicatat*

    👤 {nama}
    💰 Rp {nominal:,.0f}

    📝 {keterangan or "-"}

    Status:
    ⏳ BELUM DIBAYAR

    🤖 ChatSaku Finance"""
        )


        return jsonify(status=True)

    # =========================
    # BAYAR PIUTANG
    # =========================

    if cmd.startswith("bayarpiutang"):

        if not has_feature(sender, "piutang"):

            kirim_wa(sender,
    """
    🔒 Fitur Piutang hanya tersedia pada paket PREMIUM.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ Target Tabungan
    ✅ Hutang Piutang
    ✅ AI Insight
    ✅ Dashboard Lengkap

            """)

        data = message.split(" ",1)


        if len(data)<2:

            kirim_wa(
                sender,
                """❌ Format salah

    Gunakan:

    bayarpiutang nama

    Contoh:
    bayarpiutang agus"""
            )

            return jsonify(status=True)



        nama=data[1].strip()



        piutang = HutangPiutang.query.filter(
            HutangPiutang.nomor_wa == sender,
            HutangPiutang.tipe == "PIUTANG",
            HutangPiutang.nama.ilike(nama),
            HutangPiutang.status != "LUNAS"
        ).first()



        if not piutang:

            kirim_wa(
                sender,
                f"""❌ Piutang {nama} tidak ditemukan"""
            )

            return jsonify(status=True)



        piutang.status="LUNAS"

        piutang.lunas_tanggal=sekarang()


        db.session.commit()



        kirim_wa(
            sender,
            f"""✅ *Piutang Diterima*

    👤 {piutang.nama}

    💰 Rp {piutang.nominal:,.0f}

    Status:
    ✅ SUDAH DIBAYAR

    🕒 {sekarang().strftime("%d %b %Y %H:%M")}

    🤖 ChatSaku Finance"""
        )


        return jsonify(status=True)

    # =========================
    # BAYAR HUTANG
    # =========================

    if cmd.startswith("bayarhutang"):

        if not has_feature(sender, "hutang"):

            kirim_wa(sender,
    """
    🔒 Fitur hutang hanya tersedia pada paket PREMIUM.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ Target Tabungan
    ✅ Hutang Piutang
    ✅ AI Insight
    ✅ Dashboard Lengkap

            """)

        data = message.split(" ", 1)


        if len(data) < 2:

            kirim_wa(
                sender,
                """❌ Format salah

    Gunakan:

    bayarhutang nama

    Contoh:
    bayarhutang budi"""
            )

            return jsonify(status=True)



        nama = data[1].strip()



        hutang = HutangPiutang.query.filter(
            HutangPiutang.nomor_wa == sender,
            HutangPiutang.tipe == "HUTANG",
            HutangPiutang.nama.ilike(nama),
            HutangPiutang.status != "LUNAS"
        ).first()



        if not hutang:

            kirim_wa(
                sender,
                f"""❌ Hutang {nama} tidak ditemukan

    Pastikan nama sesuai."""
            )

            return jsonify(status=True)



        hutang.status = "LUNAS"
        hutang.lunas_tanggal = sekarang()


        db.session.commit()



        kirim_wa(
            sender,
            f"""✅ *Hutang Lunas*

    👤 {hutang.nama}

    💰 Rp {hutang.nominal:,.0f}

    Status:
    ✅ SUDAH LUNAS

    🕒 {sekarang().strftime("%d %b %Y %H:%M")}

    🤖 ChatSaku Finance"""
        )


        return jsonify(status=True)

    # =========================
    # DASHBOARD
    # =========================

    if cmd == "dashboard":

        nomor = get_owner_number(sender)

        link = generate_dashboard_link(nomor)

        mode = ""

        if nomor != sender:
            mode = (
                "\n👁 *Mode Viewer*\n"
                "Anda sedang melihat dashboard milik Owner.\n"
            )

        kirim_wa(
            sender,
            f"""📊 *Dashboard ChatSaku*

    Akses Dashboard:

    {link}

    {mode}
    Fitur:

    💰 Saldo
    📥 Pemasukan
    📤 Pengeluaran
    📈 Grafik
    💳 Hutang Piutang
    🎯 Target Tabungan
    🤖 AI Insight

    ⏳ Link berlaku 30 menit.

    💚 ChatSaku Finance Assistant"""
        )

        return jsonify(status=True)

    # ==========================
    # MENU
    # ==========================
    if cmd in ["menu", "fitur", "help"]:

        kirim_wa(
            sender,
    f"""🤖 *ChatSaku Finance Assistant*

Kelola seluruh keuangan langsung dari WhatsApp.

━━━━━━━━━━━━━━━━━━
💰 *TRANSAKSI*

➕ masuk 500000 gaji
➖ keluar 25000 makan

━━━━━━━━━━━━━━━━━━
💳 *KEUANGAN*

• saldo
   Melihat saldo saat ini.

• hariini
   Ringkasan transaksi hari ini.

• dashboard
   Membuka dashboard keuangan.

━━━━━━━━━━━━━━━━━━
🎯 *BUDGET*

• budget
   Melihat seluruh budget.

• budget makanan 1500000
   Membuat / mengubah budget.

━━━━━━━━━━━━━━━━━━
🎁 *TARGET TABUNGAN*

• target
   Melihat semua target.

• target laptop 12000000 31-12-2026
   Membuat target baru.

• target laptop
   Detail target.

• tabung laptop 500000
   Menambah tabungan.

• hapustarget laptop
   Menghapus target.

━━━━━━━━━━━━━━━━━━
🔔 *REMINDER*

• reminder
   Daftar reminder.

• reminder listrik 20 500000
   Membuat reminder.

• hapusreminder listrik
   Menghapus reminder.

━━━━━━━━━━━━━━━━━━
📊 *LAPORAN & ANALISIS*

• insight
   AI Finance Insight.

• statistik
   Statistik keuangan.

• excel
   Export ke Excel.

• pdf
   Export ke PDF.

━━━━━━━━━━━━━━━━━━
👥 *MULTI USER*

• share 08123456789
   Tambah Viewer.

• viewer
   Daftar Viewer.

• unshare 08123456789
   Hapus Viewer.

━━━━━━━━━━━━━━━━━━
⚡ *FITUR PREMIUM*

✨ AI Finance Insight
✨ Budget Bulanan
✨ Target Tabungan
✨ Reminder Tagihan
✨ Export Excel & PDF
✨ Laporan Harian Otomatis
✨ Multi User (Viewer)

━━━━━━━━━━━━━━━━━━
🌐 Website
https://chatsaku.com

📈 Dashboard Web
Tersedia otomatis setelah Anda melakukan transaksi.

💚 *ChatSaku Finance Assistant*
100% WhatsApp • AI Powered
Kelola keuangan lebih mudah, cepat, dan praktis.
"""
        )

        return jsonify(status=True)

    if cmd == "share":

        if not has_feature(sender, "share"):

            kirim_wa(
                sender,
                "🔒 Fitur Multi User tersedia pada paket PREMIUM."
            )

            return jsonify(status=True)

        if len(args) < 1:

            kirim_wa(
                sender,
                "Format:\n\nshare 081234567890"
            )

            return jsonify(status=True)

        nomor = normalize_nomor(args[0])

        if nomor == sender:

            kirim_wa(
                sender,
                "❌ Tidak bisa membagikan akun ke nomor sendiri."
            )

            return jsonify(status=True)

        cek = SharedAccess.query.filter_by(
            owner=sender,
            member=nomor,
            aktif=True
        ).first()

        if cek:

            kirim_wa(
                sender,
                "Nomor tersebut sudah menjadi viewer."
            )

            return jsonify(status=True)

        db.session.add(

            SharedAccess(

                owner=sender,

                member=nomor

            )

        )

        db.session.commit()

        kirim_wa(
            sender,
            f"""✅ Viewer berhasil ditambahkan

    👤 {nomor}

    Nomor tersebut sekarang dapat melihat dashboard dan laporan Anda."""
        )

        return jsonify(status=True)

    # =========================
    # DEFAULT
    # =========================
    return jsonify({
        "status": True
    })
