import json
import math
import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from supabase import create_client, Client
from postgrest.exceptions import APIError

st.set_page_config(page_title="Migrasi Full Firestore -> Supabase", page_icon="🚀", layout="centered")

# ==========================================================
# 1. INISIALISASI KONEKSI (CACHED)
# ==========================================================
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        cred = credentials.Certificate(dict(st.secrets["firebase"]))
        firebase_admin.initialize_app(cred)
    return firestore.client()

@st.cache_resource
def init_supabase() -> Client:
    if "supabase" in st.secrets:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
    elif "connections" in st.secrets and "supabase" in st.secrets["connections"]:
        url = st.secrets["connections"]["supabase"]["SUPABASE_URL"]
        key = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
    else:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

KOLEKSI_JSONB = [
    "log_mutasi_rak",
    "log_transaksi",
    "master_rak",
    "visualisasi_rak",
    "wms_inbound_drafts",
    "wms_undo_state",
]

# ==========================================================
# 2. HELPER PEMBERSIH DATA
# ==========================================================
def sanitize_json(obj):
    return json.loads(json.dumps(obj, default=str))

def clean_str(val):
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return ""
    s = str(val).strip()
    return "" if s.lower() in ["nan", "none", "<na>"] else s

# ==========================================================
# 3. ANTARMUKA MIGRASI STREAMLIT
# ==========================================================
st.title("🚀 Migrasi Full Database (Firestore ➔ Supabase)")
st.info("Memindahkan **7 koleksi** (`uji_coba_rak` dengan filter slot unik + 6 koleksi pendamping) secara utuh.")

if st.button("▶️ Mulai Migrasi 7 Koleksi Sekarang", type="primary"):
    progress_bar = st.progress(0)
    status_box = st.empty()

    try:
        db_firebase = init_firebase()
        supabase = init_supabase()

        # --- TAHAP 1: MIGRASI UJI_COBA_RAK (DEDUPLIKASI SLOT UNIK) ---
        status_box.write("📥 Menarik koleksi **`uji_coba_rak`** dari Firestore...")
        docs_rak = db_firebase.collection("uji_coba_rak").stream()
        rows_map = {}

        for doc in docs_rak:
            doc_dict = doc.to_dict() or {}
            items = doc_dict.get("data") if isinstance(doc_dict.get("data"), list) else [doc_dict]

            for r in items:
                if not isinstance(r, dict):
                    continue
                kol = clean_str(r.get("Kolom", r.get("kolom", ""))).upper()
                bar = int(pd.to_numeric(r.get("Baris", r.get("baris", 0)), errors="coerce") or 0)
                plt_raw = r.get("Pallet Ke", r.get("Palet Ke", r.get("pallet_ke", 0)))
                plt_ke = int(pd.to_numeric(plt_raw, errors="coerce") or 0)

                if kol not in ["A", "B", "C", "D", "E", "F", "G"] or bar <= 0 or plt_ke <= 0:
                    continue

                rows_map[(kol, bar, plt_ke)] = {
                    "doc_id": doc.id,
                    "kolom": kol,
                    "baris": bar,
                    "pallet_ke": plt_ke,
                    "part_number": clean_str(r.get("Part Number", r.get("part_number", ""))).upper(),
                    "nama_produk": clean_str(r.get("Nama Produk") or r.get("Nama Barang") or r.get("nama_produk")),
                    "lot_number": clean_str(r.get("Lot Number", r.get("lot_number", ""))),
                    "keterangan": clean_str(r.get("Keterangan", r.get("keterangan", ""))),
                    "catatan": clean_str(r.get("Catatan", r.get("catatan", ""))),
                }

        payload_rak = list(rows_map.values())

        # Bersihkan isi lama uji_coba_rak sebelum insert ulang
        supabase.table("uji_coba_rak").delete().neq("id", 0).execute()

        batch_size = 500
        for i in range(0, len(payload_rak), batch_size):
            supabase.table("uji_coba_rak").insert(payload_rak[i : i + batch_size]).execute()

        st.write(f"✅ **`uji_coba_rak`**: Berhasil memindahkan **{len(payload_rak)}** slot palet unik.")
        progress_bar.progress(1 / 7)

        # --- TAHAP 2: MIGRASI 6 KOLEKSI LAINNYA ---
        for idx, nama_koleksi in enumerate(KOLEKSI_JSONB, start=2):
            status_box.write(f"📥 Menarik koleksi **`{nama_koleksi}`** dari Firestore...")
            docs = db_firebase.collection(nama_koleksi).stream()
            payload_json = [
                {"doc_id": doc.id, "data": sanitize_json(doc.to_dict() or {})}
                for doc in docs
            ]

            if payload_json:
                for i in range(0, len(payload_json), batch_size):
                    supabase.table(nama_koleksi).upsert(payload_json[i : i + batch_size]).execute()

            st.write(f"✅ **`{nama_koleksi}`**: Berhasil memindahkan **{len(payload_json)}** dokumen.")
            progress_bar.progress(idx / 7)

        status_box.empty()
        st.success("🎉 Seluruh 7 koleksi berhasil dimigrasi 100% ke Supabase!")

    except APIError as e:
        st.error(f"❌ **Detail Error Supabase:** {e.message}\n\n- **Code:** `{e.code}`\n- **Hint:** `{e.hint}`")
    except Exception as e:
        st.error(f"❌ **Terjadi kesalahan:** {e}")
