from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from zoneinfo import ZoneInfo

db = SQLAlchemy()

JAKARTA = ZoneInfo("Asia/Jakarta")


def now_jakarta():
    return datetime.now(JAKARTA)

class User(db.Model):

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)

    nama = db.Column(db.String(100), nullable=False)

    nomor_wa = db.Column(
        db.String(30),
        unique=True,
        nullable=False,
        index=True
    )

    aktif = db.Column(
        db.Boolean,
        default=True
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=now_jakarta
    )
    mulai_langganan = db.Column(db.Date)

    akhir_langganan = db.Column(db.Date)

    paket = db.Column(db.String(30), default="FREE")

class Transaksi(db.Model):

    __tablename__ = "transaksi"

    id = db.Column(db.Integer, primary_key=True)

    tanggal = db.Column(
        db.DateTime(timezone=True),
        default=now_jakarta
    )

    tipe = db.Column(db.String(20))

    nominal = db.Column(db.Integer)

    kategori = db.Column(db.String(50))

    subkategori = db.Column(db.String(100))

    keterangan = db.Column(db.String(255))

    nomor_wa = db.Column(db.String(30))

class Budget(db.Model):

    __tablename__ = "budget"

    id = db.Column(db.Integer, primary_key=True)

    nomor_wa = db.Column(db.String(30), nullable=False, index=True)

    kategori = db.Column(db.String(100), nullable=False)

    nominal = db.Column(db.Integer, nullable=False)

    periode = db.Column(db.String(7), nullable=False)   # contoh: 2026-06

    dibuat = db.Column(
        db.DateTime(timezone=True),
        default=now_jakarta
    )

    __table_args__ = (
        db.UniqueConstraint(
            "nomor_wa",
            "kategori",
            "periode",
            name="uq_budget"
        ),
    )

class Reminder(db.Model):

    __tablename__ = "reminder"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nomor_wa = db.Column(
        db.String(30),
        nullable=False,
        index=True
    )

    nama = db.Column(
        db.String(100),
        nullable=False
    )

    nominal = db.Column(
        db.Integer,
        default=0
    )

    tanggal = db.Column(
        db.Integer,
        nullable=False
    )

    aktif = db.Column(
        db.Boolean,
        default=True
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=now_jakarta
    )

class RequestDemo(db.Model):

    __tablename__ = "request_demo"

    id = db.Column(db.Integer, primary_key=True)

    nomor_wa = db.Column(db.String(30), nullable=False, unique=True)

    nama = db.Column(db.String(150))

    status = db.Column(db.String(20), default="BARU")

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=now_jakarta
    )

class TargetPembelian(db.Model):

    __tablename__ = "target_pembelian"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nomor_wa = db.Column(
        db.String(30),
        nullable=False,
        index=True
    )

    nama = db.Column(
        db.String(150),
        nullable=False
    )

    target = db.Column(
        db.Integer,
        nullable=False
    )

    terkumpul = db.Column(
        db.Integer,
        default=0
    )

    deadline = db.Column(
        db.Date,
        nullable=False
    )

    aktif = db.Column(
        db.Boolean,
        default=True
    )

    created_at = db.Column(
        db.DateTime,
        default=now_jakarta
    )

class HutangPiutang(db.Model):

    __tablename__ = "hutang_piutang"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    nomor_wa = db.Column(
        db.String(30),
        nullable=False,
        index=True
    )

    tipe = db.Column(
        db.String(20),
        nullable=False
    )
    # HUTANG = kita punya kewajiban
    # PIUTANG = orang lain punya kewajiban ke kita


    nama = db.Column(
        db.String(100),
        nullable=False
    )


    nominal = db.Column(
        db.Integer,
        nullable=False
    )


    keterangan = db.Column(
        db.String(255)
    )


    status = db.Column(
        db.String(20),
        default="BELUM_LUNAS"
    )


    tanggal = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


    lunas_tanggal = db.Column(
        db.DateTime,
        nullable=True
    )
