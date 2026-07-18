from collections import defaultdict
from datetime import date

from models import (
    Transaksi,
    Budget,
    Reminder,
    TargetPembelian,
)

from app import transaksi_user, periode_sekarang

def generate_ai_insight(nomor):

    periode = periode_sekarang()

    all_data = transaksi_user(nomor).all()

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

    insight = []

    # =====================
    # SALDO
    # =====================

    if saldo > 0:

        insight.append(
            f"👍 Saldo Anda masih positif sebesar Rp {saldo:,.0f}."
        )

    else:

        insight.append(
            "⚠️ Pengeluaran lebih besar daripada pemasukan."
        )

    # =====================
    # CASHFLOW
    # =====================

    if total_masuk > 0:

        rasio = total_keluar / total_masuk * 100

        if rasio >= 90:

            insight.append(
                "🚨 Pengeluaran sudah mencapai lebih dari 90% dari pemasukan."
            )

        elif rasio >= 70:

            insight.append(
                "🟡 Pengeluaran sudah melewati 70% dari pemasukan."
            )

        else:

            insight.append(
                "🟢 Arus kas masih cukup sehat."
            )

    # =====================
    # KATEGORI TERBESAR
    # =====================

    kategori = defaultdict(int)

    for trx in all_data:

        if trx.tipe == "KELUAR":

            kategori[trx.kategori or "Lainnya"] += trx.nominal

    if kategori:

        terbesar = max(
            kategori,
            key=kategori.get
        )

        insight.append(

            f"🍔 Pengeluaran terbesar ada pada kategori "
            f"{terbesar} sebesar Rp {kategori[terbesar]:,.0f}."

        )

    # =====================
    # BUDGET
    # =====================

    for nama, nominal in kategori.items():

        budget = Budget.query.filter_by(
            nomor_wa=nomor,
            kategori=nama,
            periode=periode
        ).first()

        if not budget:
            continue

        persen = nominal / budget.nominal * 100

        if persen >= 100:

            insight.append(
                f"💸 Budget {nama} telah terlampaui."
            )

        elif persen >= 90:

            insight.append(
                f"⚠️ Budget {nama} hampir habis."
            )

    # =====================
    # REMINDER
    # =====================

    hari = date.today().day

    reminders = Reminder.query.filter_by(
        nomor_wa=nomor
    ).all()

    for r in reminders:

        selisih = r.tanggal - hari

        if selisih == 0:

            insight.append(
                f"📅 Hari ini jatuh tempo {r.nama}."
            )

        elif 0 < selisih <= 3:

            insight.append(
                f"⏰ {r.nama} jatuh tempo dalam {selisih} hari."
            )

    # =====================
    # TARGET
    # =====================

    target = TargetPembelian.query.filter_by(
        nomor_wa=nomor,
        aktif=True
    ).first()

    if target:

        progress = (
            target.terkumpul /
            target.target
        ) * 100

        sisa = target.target - target.terkumpul

        sisa_hari = (
            target.deadline -
            date.today()
        ).days

        if progress >= 100:

            insight.append(
                f"🎉 Target '{target.nama}' telah tercapai."
            )

        elif sisa_hari < 0:

            insight.append(
                f"⌛ Target '{target.nama}' melewati deadline."
            )

        elif sisa_hari <= 7:

            insight.append(
                f"📅 Target '{target.nama}' tinggal {sisa_hari} hari lagi."
            )

        else:

            insight.append(
                f"💰 Target '{target.nama}' masih kurang Rp {sisa:,.0f}."
            )

    if not insight:

        insight.append(
            "Belum cukup data untuk dianalisis."
        )

    return insight
