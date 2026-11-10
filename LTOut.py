import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

st.set_page_config(page_title="Operator Outbound", layout="centered", initial_sidebar_state="collapsed")

# (Asumsikan Firebase sudah diinisialisasi di entry point)
if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

st.title("🚜 Outbound Cepat")
st.caption("Pilih item, ikuti lokasi FIFO, tekan keluarkan.")

# 1. Pilih Jenis Produk (Radio Button besar untuk layar sentuh)
jenis_produk = st.radio(
    "1. Pilih Jenis Produk",
    ["Cup", "Printing", "Preform"],
    horizontal=True
)

# 2. Filter Gramasi / Varian berdasarkan Jenis Produk
# (Gunakan @st.cache_data untuk membaca Master Produk CSV agar tidak boros read)
@st.cache_data(ttl=600)
def get_varian(jenis):
    # Dummy data: Ganti dengan logika filter df_master Anda
    if jenis == "Cup": return ["Cup 12oz", "Cup 16oz"]
    elif jenis == "Printing": return ["Print Motif A", "Print Motif B"]
    else: return ["Preform 15gr", "Preform 18gr"]

varian_pilihan = st.selectbox("2. Pilih Gramasi / Varian", ["- Pilih -"] + get_varian(jenis_produk))

if varian_pilihan != "- Pilih -":
    st.divider()
    
    # 3. Urutan FIFO & Detail Lokasi
    # Idealnya, query ke 1 dokumen 'rekomendasi_fifo' untuk hemat kuota Read Firebase.
    # Disini kita simulasikan sistem menemukan lokasi tertua.
    lokasi_fifo_kolom = "A"
    lokasi_fifo_baris = 15
    stok_tersedia_di_lorong = 20
    lot_tertua = "LOT-0991"
    
    st.markdown("### 📍 Lokasi Ambil (FIFO)")
    st.info(f"**Rak {lokasi_fifo_kolom} - Baris {lokasi_fifo_baris}**\n\nLot Target: {lot_tertua} | Stok Tersedia: {stok_tersedia_di_lorong} Pallet")
    
    # 4. Quantity Input
    qty_out = st.number_input("3. Jumlah Pallet yang Diambil", min_value=1, max_value=stok_tersedia_di_lorong, step=1, value=1)
    
    # 5. Eksekusi Keluarkan
    st.write("")
    if st.button("🚀 KELUARKAN BARANG", type="primary", use_container_width=True):
        with st.spinner("Memproses ke database..."):
            # Proses Update Firebase (Targeted Write ke dokumen Kolom_A saja)
            doc_ref = db.collection('uji_coba_rak').document(f"Kolom_{lokasi_fifo_kolom}")
            
            # Logic menarik pallet dari array (dibuka di sisi backend, pastikan hanya potong sesuai qty_out)
            # db.transaction() atau pembaruan set() spesifik dilakukan di sini.
            
            st.success(f"✅ Berhasil mengeluarkan {qty_out} Pallet {varian_pilihan} dari Rak {lokasi_fifo_kolom}-{lokasi_fifo_baris}!")
            # st.rerun() untuk mereset form
