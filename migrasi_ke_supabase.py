import json
import math
import tomllib
import firebase_admin
from firebase_admin import credentials, firestore
from supabase import create_client

# 1. Baca kredensial dari .streamlit/secrets.toml
with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db_firebase = firestore.client()

sb_url = secrets["supabase"]["url"] if "supabase" in secrets else secrets["SUPABASE_URL"]
sb_key = secrets["supabase"]["key"] if "supabase" in secrets else secrets["SUPABASE_KEY"]
supabase = create_client(sb_url, sb_key)

KOLEKSI_JSONB = [
    "log_mutasi_rak",
    "log_transaksi",
    "master_rak",
    "visualisasi_rak",
    "wms_inbound_drafts",
    "wms_undo_state",
]

def sanitize_json(obj):
    """Konversi tipe data khusus Firestore (misal DatetimeWithNanoseconds) agar aman masuk JSONB."""
    return json.loads(json.dumps(obj, default=str))

def clean_str(val):
    if val is None or (isinstance(val, float) and math.isnan(val)):
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

def migrate_uji_coba_rak():
    print("\n📦 Memigrasi koleksi 'uji_coba_rak' (format tabular per palet)...")
    docs = db_firebase.collection("uji_coba_rak").stream()
    payload = []

    for doc in docs:
        doc_dict = doc.to_dict() or {}
        items = doc_dict.get("data") if isinstance(doc_dict.get("data"), list) else [doc_dict]

        for r in items:
            if not isinstance(r, dict):
                continue
            kolom_val = clean_str(r.get("Kolom", r.get("kolom", ""))).upper()
            baris_val = clean_int(r.get("Baris", r.get("baris", 0)))
            if not kolom_val or baris_val == 0:
                continue

            pallet_val = r.get("Pallet Ke", r.get("Palet Ke", r.get("pallet_ke", 0)))
            nama_val = r.get("Nama Produk", r.get("Nama Barang", r.get("nama_produk", "")))

            payload.append({
                "doc_id": doc.id,
                "kolom": kolom_val,
                "baris": baris_val,
                "pallet_ke": clean_int(pallet_val),
                "part_number": clean_str(r.get("Part Number", r.get("part_number", ""))).upper(),
                "nama_produk": clean_str(nama_val),
                "lot_number": clean_str(r.get("Lot Number", r.get("lot_number", ""))),
                "keterangan": clean_str(r.get("Keterangan", r.get("keterangan", ""))),
                "catatan": clean_str(r.get("Catatan", r.get("catatan", ""))),
            })

    if not payload:
        print("   ⚠️ Koleksi 'uji_coba_rak' kosong di Firebase.")
        return

    batch_size = 500
    for i in range(0, len(payload), batch_size):
        supabase.table("uji_coba_rak").insert(payload[i : i + batch_size]).execute()
    print(f"   ✅ Selesai: {len(payload)} baris palet masuk ke 'uji_coba_rak'.")

def migrate_collection_jsonb(nama_koleksi):
    print(f"\n📂 Memigrasi koleksi '{nama_koleksi}'...")
    docs = db_firebase.collection(nama_koleksi).stream()
    payload = []

    for doc in docs:
        payload.append({
            "doc_id": doc.id,
            "data": sanitize_json(doc.to_dict() or {})
        })

    if not payload:
        print(f"   ℹ️ Koleksi '{nama_koleksi}' kosong (0 dokumen).")
        return

    batch_size = 500
    for i in range(0, len(payload), batch_size):
        supabase.table(nama_koleksi).upsert(payload[i : i + batch_size]).execute()
    print(f"   ✅ Selesai: {len(payload)} dokumen masuk ke '{nama_koleksi}'.")

if __name__ == "__main__":
    print("🚀 Memulai migrasi penuh 7 koleksi Firestore -> Supabase...")
    migrate_uji_coba_rak()
    for col_name in KOLEKSI_JSONB:
        migrate_collection_jsonb(col_name)
    print("\n🎉 Seluruh database berhasil dimigrasi 100% ke Supabase!")
