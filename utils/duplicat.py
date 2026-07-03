import time

# =========================
# GLOBAL ANTI DUPLICATE
# =========================
PROCESSED = {}

def is_duplicate(msg_id):
    """Simple in-memory anti duplicate"""
    now = time.time()

    # bersihkan cache lama (10 menit)
    expired = [k for k, v in PROCESSED.items() if now - v > 600]
    for k in expired:
        del PROCESSED[k]

    if msg_id in PROCESSED:
        return True

    PROCESSED[msg_id] = now
    return False
