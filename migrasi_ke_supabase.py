import tomllib
import math
import firebase_admin
from firebase_admin import credentials, firestore
from supabase import create_client

# 1. Baca kredensial langsung dari .streamlit/secrets.toml
with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)

# 2. Koneksi ke Firebase Firestore
if not firebase_admin._apps:
    cred = credentials.Certificate(dict(secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db_firebase = firestore.client()

# 3. Koneksi ke Supabase
sb_url = secrets["supabase"]["url"]
sb_key = secrets["supabase"]["key"]
supabase = create_client(sb_url, sb_key)

NAMA_KOLEKSI = "uji_coba_rak"
NAMA_TABEL = "uji_coba_rak"

def clean_str(val):
    if val is None:
        return ""
    if isinstance(val, float) and math.isnan(val):
        return ""
    s = str(val).strip()
    return "" if s.lower() in ["nan", "none", "<na>"] else s

def clean_int(val):
    try:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return 0
        return int(float(str(val).strip()))
    except Exception:
        return 0

def main():
    print(f"📥 Menarik seluruh dokumen dari Firestore '{NAMA_KOLEKSI}'...")
    docs = db_firebase.collection(NAMA_KOLEKSI).stream()

    raw_rows = []
    doc_count = 0
    for doc in docs:
        doc_count += 1
        doc_data = doc.to_dict()
        if "data" in doc_data and isinstance(doc_data["data"], list):
            raw_rows.extend(doc_data["data"])

    print(f"✅ Ditemukan {doc_count} dokumen dengan total {len(raw_rows)} baris palet.")

    if not raw_rows:
        print("⚠️ Tidak ada data untuk dimigrasi.")
        return

    # Normalisasi struktur kolom agar seragam
    payload = []
    for r in raw_rows:
        # Handle variasi penamaan kolom di Firebase
        pallet_val = r.get("Pallet Ke", r.get("Palet Ke", r.get("pallet_ke", 0)))
        nama_val = r.get("Nama Produk", r.get("Nama Barang", r.get("nama_produk", "")))

        kolom_clean = clean_str(r.get("Kolom", r.get("kolom", ""))).upper()
        baris_clean = clean_int(r.get("Baris", r.get("baris", 0)))

        if not kolom_clean or baris_clean == 0:
            continue

        payload.append({
            "kolom": kolom_clean,
            "baris": baris_clean,
            "pallet_ke": clean_int(pallet_val),
            "part_number": clean_str(r.get("Part Number", r.get("part_number", ""))).upper(),
            "nama_produk": clean_str(nama_val),
            "lot_number": clean_str(r.get("Lot Number", r.get("lot_number", ""))),
            "keterangan": clean_str(r.get("Keterangan", r.get("keterangan", ""))),
            "catatan": clean_str(r.get("Catatan", r.get("catatan", "")))
        })

    print(f"🧹 Membersihkan tabel '{NAMA_TABEL}' di Supabase sebelum insert ulang...")
    supabase.table(NAMA_TABEL).delete().neq("id", 0).execute()

    # Upload bertahap per 500 baris agar stabil & cepat
    batch_size = 500
    total_inserted = 0
    for i in range(0, len(payload), batch_size):
        chunk = payload[i : i + batch_size]
        supabase.table(NAMA_TABEL).insert(chunk).execute()
        total_inserted += len(chunk)
        print(f"🚀 Terkirim: {total_inserted} / {len(payload)} baris...")

    print("🎉 Re-migrasi selesai 100%!")

if __name__ == "__main__":
    main()
