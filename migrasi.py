import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from supabase import create_client

st.set_page_config(page_title="Migrasi Firestore ke Supabase", page_icon="🚀")
st.title("🚀 Migrasi Data Stok (Firestore ➔ Supabase)")
st.info("Jalankan proses ini 1 kali saja setelah tabel `stok_rak` dibuat di Supabase.")

if st.button("▶️ Mulai Migrasi Sekarang", type="primary"):
    with st.spinner("Menghubungkan ke Firestore & Supabase..."):
        if not firebase_admin._apps:
            cred = credentials.Certificate(dict(st.secrets["firebase"]))
            firebase_admin.initialize_app(cred)
        db_fs = firestore.client()
        sb = create_client(st.secrets["supabase"]["url"], st.secrets["supabase"]["key"])

        docs = db_fs.collection("uji_coba_rak").stream()
        semua_data = []
        for doc in docs:
            d = doc.to_dict() or {}
            if "data" in d and isinstance(d["data"], list):
                semua_data.extend(d["data"])

        rows_map = {}
        for item in semua_data:
            kol = str(item.get("Kolom", "")).strip().upper()
            bar = int(pd.to_numeric(item.get("Baris", 0), errors="coerce") or 0)
            plt_raw = item.get("Pallet Ke", item.get("Palet Ke", 0))
            plt_ke = int(pd.to_numeric(plt_raw, errors="coerce") or 0)

            if kol not in ["A", "B", "C", "D", "E", "F", "G"] or bar <= 0 or plt_ke <= 0:
                continue

            def clean_str(val):
                s = str(val or "").strip()
                return "" if s.lower() in ["nan", "none", "<na>"] else s

            rows_map[(kol, bar, plt_ke)] = {
                "kolom": kol,
                "baris": bar,
                "pallet_ke": plt_ke,
                "part_number": clean_str(item.get("Part Number")).upper(),
                "nama_produk": clean_str(item.get("Nama Produk") or item.get("Nama Barang")),
                "lot_number": clean_str(item.get("Lot Number")),
                "keterangan": clean_str(item.get("Keterangan")),
                "catatan": clean_str(item.get("Catatan")),
            }

        payload = list(rows_map.values())
        st.write(f"Ditemukan **{len(payload)}** slot palet unik. Mengunggah ke Supabase...")

        progress = st.progress(0)
        BATCH_SIZE = 500
        for i in range(0, len(payload), BATCH_SIZE):
            batch = payload[i : i + BATCH_SIZE]
            sb.table("stok_rak").upsert(batch, on_conflict="kolom,baris,pallet_ke").execute()
            progress.progress(min(1.0, (i + BATCH_SIZE) / max(1, len(payload))))

    st.success(f"✅ Berhasil memindahkan {len(payload)} baris ke tabel `stok_rak` di Supabase!")

progress = st.progress(0)
        BATCH_SIZE = 500
        try:
            for i in range(0, len(payload), BATCH_SIZE):
                batch = payload[i : i + BATCH_SIZE]
                sb.table("stok_rak").upsert(batch, on_conflict="kolom,baris,pallet_ke").execute()
                progress.progress(min(1.0, (i + BATCH_SIZE) / max(1, len(payload))))
            st.success(f"✅ Berhasil memindahkan {len(payload)} baris ke tabel `stok_rak` di Supabase!")
        except Exception as e:
            st.error(f"Detail Error Supabase: {getattr(e, 'message', str(e))}")
            st.code(repr(e))
