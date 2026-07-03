from models import db, Transaksi, Budget, Reminder, User

# =========================
# VALIDASI FORMAT PENOMORAN
# =========================
def normalize_wa(nomor):

    nomor = str(nomor).strip()

    nomor = nomor.replace("@s.whatsapp.net", "")
    nomor = nomor.replace("+", "")
    nomor = nomor.replace(" ", "")
    nomor = nomor.replace("-", "")

    if nomor.startswith("08"):
        nomor = "62" + nomor[1:]

    return nomor


# =========================
# VALIDASI USER
# =========================
def user_terdaftar(nomor):

    nomor = normalize_wa(nomor)

    return User.query.filter(
        User.nomor_wa == nomor,
        User.aktif.is_(True)
    ).first()
