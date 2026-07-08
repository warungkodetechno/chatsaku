import os

from flask import Blueprint,Flask, request, jsonify, render_template, send_file, redirect
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from models import db, Transaksi, Budget, Reminder, User, RequestDemo, TargetPembelian, HutangPiutang
import requests
import os
import time
import pandas as pd
import io
from zoneinfo import ZoneInfo
from itsdangerous import URLSafeTimedSerializer
from itsdangerous import BadSignature
from itsdangerous import SignatureExpired

from utils.duplicate import is_duplicate
from utils.helper import *

webhook_bp = Blueprint("webhook", __name__)

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

    if user.paket != "FREE" and user.akhir_langganan:

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

        if not has_feature(sender,"target"):

            kirim_wa(
                sender,
                "🔒 Target Tabungan Pembelian tersedia pada paket PREMIUM."
            )

            return jsonify(status=True)

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

        if not has_feature(sender,"tabung"):

            kirim_wa(
                sender,
                "🔒 Target Tabungan Pembelian tersedia pada paket PREMIUM."
            )

            return jsonify(status=True)

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

        if not has_feature(sender,"target"):

            kirim_wa(
                sender,
                "🔒 Target Tabungan Pembelian tersedia pada paket PREMIUM."
            )

            return jsonify(status=True)

        data = TargetPembelian.query.filter_by(

            nomor_wa=sender,
            aktif=True

        ).all()

        if not data:

            kirim_wa(
                sender,
                "Belum ada target."
            )

            return jsonify(status=True)

        text = "🎯 TARGET PEMBELIAN\n\n"

        for i,x in enumerate(data,1):

            persen = round(
                x.terkumpul /
                x.target * 100
            )

            if persen>100:
                persen=100

            sisa = x.target-x.terkumpul

            text += f"""{i}. {x.nama}

    Progress : {persen}%

    Rp {x.terkumpul:,.0f}
    /
    Rp {x.target:,.0f}

    Sisa Rp {sisa:,.0f}

    """

        kirim_wa(sender,text)

        return jsonify(status=True)

    # ======================================
    # DETAIL TARGET
    # ======================================
    if cmd.startswith("target "):

        if not has_feature(sender,"target"):

            kirim_wa(
                sender,
                "🔒 Target Tabungan Pembelian tersedia pada paket PREMIUM."
            )

            return jsonify(status=True)

        nama = message[7:]

        target = TargetPembelian.query.filter_by(

            nomor_wa=sender,
            nama=nama,
            aktif=True

        ).first()

        if target:

            persen = round(
                target.terkumpul /
                target.target *100
            )

            sisa = target.target-target.terkumpul

            kirim_wa(

                sender,

    f"""🎯 {target.nama}

    Target
    Rp {target.target:,.0f}

    Terkumpul
    Rp {target.terkumpul:,.0f}

    Sisa
    Rp {sisa:,.0f}

    Progress
    {persen}%"""

            )

            return jsonify(status=True)

    # ======================================
    # HAPUS TARGET
    # ======================================

    if cmd.startswith("hapustarget"):

        if not has_feature(sender,"hapustarget"):

            kirim_wa(
                sender,
                "🔒 Target Tabungan Pembelian tersedia pada paket PREMIUM."
            )

            return jsonify(status=True)

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

        link = generate_dashboard_link(sender)

        kirim_wa(
            sender,
            f"""💳 *Saldo Keuangan*
┌────────────────┐
📥 Masuk   : Rp {masuk:,.0f}
📤 Keluar  : Rp {keluar:,.0f}
 ─────────────────
💰 Saldo   : *Rp {saldo:,.0f}*
└────────────────┘

📊 Dashboard
{link}

🕒 Link Berlaku *30 Menit*

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

            link = generate_dashboard_link(sender)

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
┌────────────────────
💰 *KREDIT MASUK*

💵 Nominal      : Rp. {nominal:,.0f}
📝 Keterangan   : {keterangan}

🕒 {sekarang().strftime("%d %b %Y • %H:%M")}
└────────────────────

💳 *Saldo Saat Ini*
Rp {saldo:,.0f}

📊 Dashboard
{link}

⏳ Link Berlaku *30 Menit*

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

📈 Dashboard
{link}

⏳ Link Berlaku *30 Menit*

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

        today = sekarang().date()

        data = transaksi_user(sender).filter(
            db.func.date(Transaksi.tanggal) == today
        ).all()

        total = sum(x.nominal for x in data)
        link = generate_dashboard_link(sender)
        masuk_hari_ini = sum(
            x.nominal for x in data
            if x.tipe == "MASUK"
        )

        keluar_hari_ini = sum(
            x.nominal for x in data
            if x.tipe == "KELUAR"
        )

        kirim_wa(
            sender,
            f"""📊 *Ringkasan Hari Ini*
━━━━━━━━━━━━━━

🧾 *Jumlah Transaksi*
{len(data)}

📥 *Pemasukan*  : Rp. {masuk_hari_ini:,.0f}
📤 *Pengeluaran*: Rp. {keluar_hari_ini:,.0f}

━━━━━━━━━━━━━━

💰 *Total Aktivitas*
Rp {total:,.0f}

📊 *Dashboard*
{link}

🔒 Link berlaku selama *30 Menit*.

━━━━━━━━━━━━━━
🤖 *Finance Assistant*
"""
        )

        return jsonify({"status": True})

    # =========================
    # BUDGET
    # =========================
    if cmd.startswith("budget"):
        if not has_feature(sender, "budget"):

            kirim_wa(sender,
    """
    🔒 Fitur Budget hanya tersedia pada paket PRO.

    Upgrade sekarang agar dapat:

    ✅ Budget Bulanan
    ✅ Reminder
    ✅ AI Insight
    ✅ Dashboard Lengkap

    Ketik:
    upgrade
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
                    nomor_wa=sender,
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

                    now = datetime.now()

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
                    terpakai = transaksi_user(sender).filter(
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
        if not has_feature(sender,"ai"):

            kirim_wa(
                sender,
                "🔒 AI Insight tersedia pada paket PREMIUM."
            )

            return jsonify(status=True)

        try:

            periode = periode_sekarang()

            data = transaksi_user(sender).filter(
                Transaksi.tipe == "KELUAR"
            ).all()

            if not data:

                kirim_wa(
                    sender,
                    "📭 Belum ada transaksi yang dapat dianalisis."
                )

                return jsonify({"status": True})

            # =====================================
            # TOTAL
            # =====================================

            total = sum(x.nominal for x in data)

            # =====================================
            # KATEGORI
            # =====================================

            kategori = {}

            for trx in data:

                key = trx.kategori or "Lainnya"

                kategori[key] = kategori.get(key, 0) + trx.nominal

            kategori_terbesar = max(
                kategori,
                key=kategori.get
            )

            nominal_terbesar = kategori[kategori_terbesar]

            persen = nominal_terbesar / total * 100

            # =====================================
            # BUDGET
            # =====================================

            budget = Budget.query.filter_by(
                nomor_wa=sender,
                kategori=kategori_terbesar,
                periode=periode
            ).first()

            budget_info = ""

            if budget:

                persen_budget = (
                    nominal_terbesar /
                    budget.nominal
                ) * 100

                sisa = budget.nominal - nominal_terbesar

                budget_info = f"""

🎯 Budget {kategori_terbesar.title()}
Rp {budget.nominal:,.0f}

📉 Terpakai
Rp {nominal_terbesar:,.0f}

💵 Sisa
Rp {max(sisa,0):,.0f}

📊 {persen_budget:.1f}%
"""

            # =====================================
            # FINANCE SCORE
            # =====================================

            score = 100

            if persen > 50:
                score -= 20

            elif persen > 35:
                score -= 10

            if budget:

                if persen_budget > 100:
                    score -= 25

                elif persen_budget > 80:
                    score -= 10

            if score >= 90:
                status = "🟢 Sangat Sehat"

            elif score >= 75:
                status = "🟢 Sehat"

            elif score >= 60:
                status = "🟡 Cukup"

            elif score >= 40:
                status = "🟠 Perlu Perhatian"

            else:
                status = "🔴 Boros"

            # =====================================
            # REKOMENDASI
            # =====================================

            rekomendasi = []

            if persen > 50:

                rekomendasi.append(
                    f"• Kurangi pengeluaran {kategori_terbesar.title()}."
                )

            if budget and persen_budget > 100:

                rekomendasi.append(
                    "• Budget kategori sudah terlampaui."
                )

            elif budget and persen_budget > 80:

                rekomendasi.append(
                    "• Budget hampir habis."
                )

            if not rekomendasi:

                rekomendasi.append(
                    "• Pengeluaran masih terkendali."
                )

            kirim_wa(
                sender,
                f"""🏦 *AI Finance Insight*
──────────────────

📊 Analisis Pengeluaran

💸 Total Debit
Rp {total:,.0f}

🏷️ Kategori Terbesar
{kategori_terbesar.title()}

💰 Nominal
Rp {nominal_terbesar:,.0f}

📈 Kontribusi
{persen:.1f}% dari total

{budget_info}

──────────────────

💳 *Finance Score*

✨ {score}/100

{status}

──────────────────

🧠 *Rekomendasi AI*

{chr(10).join(rekomendasi)}

──────────────────

🤖 ChatSaku Finance Assistant
💚 AI Powered • WhatsApp Finance
"""
            )

        except Exception as e:

            print(e)

            kirim_wa(
                sender,
                f"Terjadi kesalahan\n\n{e}"
            )

        return jsonify({"status": True})

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

            parts = message.lower().split()

            # =========================
            # LIHAT REMINDER
            # =========================
            if len(parts) == 1:

                reminders = Reminder.query.filter_by(
                    nomor_wa=sender,
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

        print("=== DEBUG HUTANG ===")
        print("SENDER:", sender)


        daftar = HutangPiutang.query.filter(
            HutangPiutang.nomor_wa == sender,
            HutangPiutang.tipe == "HUTANG"
        ).all()


        print("JUMLAH:", len(daftar))

        for h in daftar:
            print(
                h.nomor_wa,
                h.tipe,
                h.nama,
                h.nominal,
                h.status
            )


        if not daftar:

            kirim_wa(
                sender,
                """💳 *Daftar Hutang*

    Tidak ada hutang 😊

    🤖 ChatSaku Finance"""
            )

            return jsonify(status=True)


        total = 0

        pesan = """💳 *Daftar Hutang*

    """


        for i, h in enumerate(daftar,1):

            jumlah = h.nominal

            pesan += f"""
        {i}. 👤 {h.nama}
        💰 Rp {jumlah:,.0f}
        📝 {h.keterangan or "-"}
        """

            total += jumlah


        pesan += f"""
    ━━━━━━━━━━━━━━
    Total:
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

            pesan += f"""
    {i}. 👤 {p.nama}
    💰 Rp {p.sisa:,.0f}
    📝 {p.keterangan or "-"}
    """

            total += p.sisa



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

            sisa=nominal,

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

    # ==========================
    # MENU
    # ==========================
    if cmd in ["menu", "fitur", "help"]:

        kirim_wa(
            sender,
    f"""🤖 *ChatSaku Finance Assistant*

Berikut menu yang tersedia:

━━━━━━━━━━━━━━

💰 *Pemasukan*

masuk 500000 gaji

Contoh:
masuk 2500000 gaji

━━━━━━━━━━━━━━

💸 *Pengeluaran*

keluar 25000 makan

Contoh:
keluar 150000 bensin

━━━━━━━━━━━━━━

💳 *Saldo*

saldo

Melihat total saldo saat ini.

━━━━━━━━━━━━━━

📊 *Ringkasan Hari Ini*

hariini

Menampilkan transaksi hari ini.

━━━━━━━━━━━━━━

🎯 *Budget Bulanan*

Lihat Budget:

budget

Tambah Budget:

budget makanan 1500000

━━━━━━━━━━━━━━

🎁 *Target Pembelian*

Lihat Semua Target:

target

Buat Target:

target laptop 12000000 31-12-2026

Lihat Detail Target:

target laptop

Tambah Tabungan:

tabung laptop 500000

Hapus Target:

hapustarget laptop

━━━━━━━━━━━━━━

🤖 *AI Finance Insight*

insight

Analisis otomatis kondisi keuangan.

━━━━━━━━━━━━━━

🔔 *Reminder Tagihan*

Lihat Reminder:

reminder

Tambah Reminder:

reminder listrik 20 500000

Hapus Reminder:

hapusreminder listrik

━━━━━━━━━━━━━━

🌐 Website

https://chatsaku.com

📊 Dashboard

Dashboard tersedia otomatis setelah Anda melakukan transaksi.

💚 *ChatSaku Finance Assistant*

100% WhatsApp • AI Powered
Kelola keuangan cukup melalui chat WhatsApp.
"""
        )

        return jsonify(status=True)

    # =========================
    # DEFAULT
    # =========================
    return jsonify({
        "status": True
    })
