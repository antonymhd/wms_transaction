import tomllib
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from supabase import create_client

# 1. Baca secrets.toml lokal
with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)

# 2. Koneksi Firestore (Sumber)
if not firebase_admin._apps:
    cred = credentials.Certificate(dict(secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db_fs = firestore.client()

# 3. Koneksi Supabase (Tujuan)
sb = create_client(secrets["supabase"]["url"], secrets["supabase"]["key"])

print("1. Mengambil data dari Firestore 'uji_coba_rak'...")
docs = db_fs.collection("uji_coba_rak").stream()
semua_data = []
for doc in docs:
    d = doc.to_dict() or {}
    if "data" in d and isinstance(d["data"], list):
        semua_data.extend(d["data"])

print(f"Total baris ditemukan: {len(semua_data)}")

# 4. Normalisasi format kolom & deduplikasi (kolom, baris, pallet_ke)
rows_map = {}
for item in semua_data:
    kol = str(item.get("Kolom", "")).strip().upper()
    bar = int(pd.to_numeric(item.get("Baris", 0), errors="coerce") or 0)
    plt_raw = item.get("Pallet Ke", item.get("Palet Ke", 0))
    plt_ke = int(pd.to_numeric(plt_raw, errors="coerce") or 0)

    if kol not in ["A", "B", "C", "D", "E", "F", "G"] or bar <= 0 or plt_ke <= 0:
        continue

    pn = str(item.get("Part Number", "") or "").strip().upper()
    if pn.lower() in ["nan", "none", "<na>"]:
        pn = ""

    nm = str(item.get("Nama Produk") or item.get("Nama Barang") or "").strip()
    if nm.lower() in ["nan", "none", "<na>"]:
        nm = ""

    lot = str(item.get("Lot Number", "") or "").strip()
    if lot.lower() in ["nan", "none", "<na>"]:
        lot = ""

    ket = str(item.get("Keterangan", "") or "").strip()
    if ket.lower() in ["nan", "none", "<na>"]:
        ket = ""

    cat = str(item.get("Catatan", "") or "").strip()
    if cat.lower() in ["nan", "none", "<na>"]:
        cat = ""

    rows_map[(kol, bar, plt_ke)] = {
        "kolom": kol,
        "baris": bar,
        "pallet_ke": plt_ke,
        "part_number": pn,
        "nama_produk": nm,
        "lot_number": lot,
        "keterangan": ket,
        "catatan": cat,
    }

payload = list(rows_map.values())
print(f"2. Mengunggah {len(payload)} baris unik ke Supabase 'stok_rak'...")

# 5. Upsert per batch 500 baris agar cepat & aman
BATCH_SIZE = 500
for i in range(0, len(payload), BATCH_SIZE):
    batch = payload[i : i + BATCH_SIZE]
    sb.table("stok_rak").upsert(batch, on_conflict="kolom,baris,pallet_ke").execute()
    print(f"   Terkirim {min(i + BATCH_SIZE, len(payload))} / {len(payload)} baris")

print("✅ Migrasi selesai! Silakan cek menu Table Editor -> stok_rak di Supabase.")
