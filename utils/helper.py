from models import db, Transaksi, Budget, Reminder, User
import os

from itsdangerous import URLSafeTimedSerializer

SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY belum diset.")

serializer = URLSafeTimedSerializer(SECRET_KEY)

BASE_URL = os.getenv(
    "BASE_URL",
    "https://inout-production-88e5.up.railway.app"
)


def generate_dashboard_link(nomor_wa):

    token = serializer.dumps(nomor_wa)

    return f"{BASE_URL}/dashboard/{token}"

def verify_token(token):

    try:

        nomor = serializer.loads(
            token,
            max_age=60 * 60 * 24  # 1 hari
        )

        return nomor

    except (BadSignature, SignatureExpired):

        return None

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

def transaksi_user(sender):
    return Transaksi.query.filter(
        Transaksi.nomor_wa == sender
    )


# =========================
# FONNTE SEND MESSAGE
# =========================
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
                "message": pesan,
                "delay": 2
            },
            timeout=30
        )

        print("=" * 60)
        print("FONNTE SEND")
        print("TO     :", nomor)
        print("STATUS :", response.status_code)
        print("BODY   :", response.text)
        print("=" * 60)

    except Exception as e:
        print("ERROR FONNTE :", str(e))

def hitung_saldo(nomor_wa):

    total_masuk = db.session.query(
        db.func.coalesce(db.func.sum(Transaksi.nominal), 0)
    ).filter(
        Transaksi.nomor_wa == nomor_wa,
        Transaksi.tipe == "MASUK"
    ).scalar()

    total_keluar = db.session.query(
        db.func.coalesce(db.func.sum(Transaksi.nominal), 0)
    ).filter(
        Transaksi.nomor_wa == nomor_wa,
        Transaksi.tipe == "KELUAR"
    ).scalar()

    return total_masuk - total_keluar



def progress_bar(persen):

    penuh = int(persen / 10)

    return "🟩" * penuh + "⬜" * (10 - penuh)

def ai_insight(sender):

    bulan = periode_sekarang()

    data = transaksi_user(sender).filter(
        Transaksi.tipe == "KELUAR"
    ).all()

    if not data:
        return "Belum ada data untuk dianalisis."

    total = sum(x.nominal for x in data)

    kategori = {}

    for trx in data:

        kategori.setdefault(trx.kategori, 0)

        kategori[trx.kategori] += trx.nominal

    kategori_terbesar = max(
        kategori,
        key=kategori.get
    )

    nominal = kategori[kategori_terbesar]

    persen = nominal / total * 100

    insight = []

    insight.append(
        f"📊 Pengeluaran terbesar berada pada kategori *{kategori_terbesar.title()}* ({persen:.1f}%)."
    )

    if persen > 50:

        insight.append(
            f"⚠️ Lebih dari setengah pengeluaran berasal dari {kategori_terbesar}."
        )

    elif persen > 30:

        insight.append(
            f"💡 Pengeluaran {kategori_terbesar} mulai mendominasi bulan ini."
        )

    else:

        insight.append(
            "✅ Pengeluaran masih cukup seimbang antar kategori."
        )

    return "\n".join(insight)
