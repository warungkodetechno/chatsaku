from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Transaksi(db.Model):

    __tablename__ = "transaksi"

    id = db.Column(db.Integer, primary_key=True)

    tanggal = db.Column(db.DateTime)

    tipe = db.Column(db.String(20))

    nominal = db.Column(db.Integer)

    keterangan = db.Column(db.String(255))

    nomor_wa = db.Column(db.String(30))
