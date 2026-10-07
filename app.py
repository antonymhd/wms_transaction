import streamlit as st
import pandas as pd
from datetime import datetime
import requests
import time # Tambahkan modul time

# Konfigurasi Halaman
st.set_page_config(page_title="WMS - Form Transaksi", page_icon="📝", layout="centered")

WEB_APP_URL = "https://script.google.com/macros/s/AKfycbyUDkB585uFQqq9yVYPRFTUSmHK0bocAn0Ky7wz5a1HRuoDuAO125iq6etbG-Jc5SqsNg/exec"

# ==========================================================
# SOLUSI ANTI-LEMOT (CACHE BUSTING)
# ==========================================================
# Ubah TTL menjadi sangat singkat (1 detik)
@st.cache_data(ttl=1)
def load_data():
    try:
        # Tambahkan "&t=waktu_saat_ini" di ujung URL agar Google mengira ini link baru
        cache_buster = int(time.time())
        sheet_url = f"https://docs.google.com/spreadsheets/d/1rPODgznxi5QxPWk6paIK0-SsTPwIByJPcGYZ8YAUEwc/export?format=csv&gid=0&t={cache_buster}"
        df = pd.read_csv(sheet_url)
        df.columns = df.columns.astype(str).str.strip()
        return df
    except Exception as e:
        st.error(f"⚠ Gagal memuat data master! Error: {e}")
        st.stop()

@st.cache_data(ttl=1)
def load_log():
    try:
        cache_buster = int(time.time())
        log_url = f"https://docs.google.com/spreadsheets/d/1rPODgznxi5QxPWk6paIK0-SsTPwIByJPcGYZ8YAUEwc/export?format=csv&sheet=Log%20Transaksi&t={cache_buster}"
        return pd.read_csv(log_url)
    except Exception:
        return pd.DataFrame(columns=["Timestamp", "Tipe Transaksi", "Part Number", "Nama Produk", "Lot Number", "Lokasi Rak", "Rentang Pallet", "Qty In", "Qty Out", "Sisa Stok", "Keterangan"])

# (Sisa kode di bawahnya tetap persis sama seperti sebelumnya)
df = load_data()
product_col = 'Nama Produk' if 'Nama Produk' in df.columns else 'Nama Barang'
part_col = 'Part Number' if 'Part Number' in df.columns else 'Part Number'

product_mapping = df[[product_col, part_col]].dropna().drop_duplicates()
list_nama_produk = sorted(product_mapping[product_col].astype(str).tolist())
baris_list = list(range(1, 29))

st.title("📝 Form Transaksi Gudang")
st.caption("Validasi otomatis terhubung ke Sheet 1. Sisa stok dihitung secara real-time.")
st.divider()

tipe_trx = st.selectbox("Jenis Pergerakan:", ["INBOUND (Barang Masuk) ⬇️", "OUTBOUND (Barang Keluar) ⬆️"])
is_inbound = "INBOUND" in tipe_trx

# ---------------------------------------------------------
# LANGKAH 1: PILIH BARIS, KOLOM, DAN PALLET KE-
# ---------------------------------------------------------
st.markdown("### 1️⃣ Tentukan Lokasi Rak")
pilih_baris = st.selectbox("Pilih Baris Rak (1 - 28):", options=baris_list)

if pilih_baris <= 4:
    kolom_tersedia, info_kolom = ['A', 'B', 'C', 'D', 'E'], "Baris 1–4 (Kolom A - E)"
elif pilih_baris <= 16:
    kolom_tersedia, info_kolom = ['A', 'B', 'C', 'D', 'E', 'F'], "Baris 5–16 (Kolom A - F)"
else:
    kolom_tersedia, info_kolom = ['A', 'B', 'C', 'D', 'E', 'F', 'G'], "Baris 17–28 (Kolom A - G)"
    
pilih_kolom = st.selectbox(f"Pilih Kolom Rak ({info_kolom}):", options=kolom_tersedia)

max_pallet = 31 if pilih_baris <= 16 else 44
st.caption(f"ℹ️ Kapasitas maksimal Baris {pilih_baris} = **{max_pallet} Pallet**.")

c1, c2 = st.columns(2)
with c1:
    pallet_dari = st.number_input("Dari Pallet ke-:", min_value=1, max_value=max_pallet, step=1, value=1)
with c2:
    pallet_sampai = st.number_input("Sampai Pallet ke-:", min_value=1, max_value=max_pallet, step=1, value=1)

# --- DETEKSI KONDISI RAK (REAL-TIME DARI SHEET 1) ---
df_lokasi = df[(df['Kolom'].astype(str) == pilih_kolom) & 
               (df['Baris'].astype(str) == str(pilih_baris)) & 
               (df['Pallet Ke'] >= pallet_dari) & 
               (df['Pallet Ke'] <= pallet_sampai)]

status_terisi = (df_lokasi['Status'] == 'Terisi').sum()

# PERBAIKAN LOGIKA: Jika Sisa kosong, otomatis hitung In - Out
if not df_lokasi.empty:
    acuan_in = pd.to_numeric(df_lokasi['In'], errors='coerce').fillna(0).max()
    acuan_out = pd.to_numeric(df_lokasi['Out'], errors='coerce').fillna(0).max()
    
    # Ambil nilai Sisa. Jika 0 (karena kosong di Sheet), paksa hitung secara matematika
    sisa_di_sheet = pd.to_numeric(df_lokasi['Sisa'], errors='coerce').fillna(0).max()
    acuan_sisa = sisa_di_sheet if sisa_di_sheet > 0 else (acuan_in - acuan_out)
else:
    acuan_in = acuan_out = acuan_sisa = 0

st.divider()

# ---------------------------------------------------------
# LANGKAH 2: IDENTITAS & KUANTITAS BERDASARKAN VALIDASI
# ---------------------------------------------------------
st.markdown("### 2️⃣ Kuantitas & Identitas Produk")

valid_to_submit = True
qty_in_final = 0
qty_out_final = 0
sisa_stok_final = 0

if is_inbound:
    # VALIDASI INBOUND: Rak harus kosong
    if status_terisi > 0:
        st.error(f"🚨 **TIDAK BISA DIPROSES:** Ada **{status_terisi} pallet** di lokasi ini yang masih ada barangnya (Status: Terisi). Harap pilih lokasi pallet yang kosong!")
        valid_to_submit = False
    else:
        st.success("✅ Lokasi Kosong, siap untuk Inbound.")
        selected_product = st.selectbox("Pilih Nama Produk (Wajib):", options=list_nama_produk)
        
        matched_row = product_mapping[product_mapping[product_col].astype(str) == selected_product]
        part_number = matched_row[part_col].values[0] if not matched_row.empty else ""
        st.text_input("Part Number (Auto):", value=part_number, disabled=True)
        lot_number = st.text_input("Lot Number / No. Lot (Wajib):")
        
        qty_in_final = st.number_input("Input Qty IN (Barang Masuk per Pallet):", min_value=1, step=1, value=1)
        qty_out_final = 0
        sisa_stok_final = qty_in_final

else: # OUTBOUND
    # VALIDASI OUTBOUND: Hanya ditolak jika benar-benar tidak ada barang (Sisa murni <= 0 dan Status bukan Terisi)
    if status_terisi == 0 and acuan_sisa <= 0:
        st.error("🚨 **TIDAK BISA DIPROSES:** Lokasi ini **KOSONG** (Sisa: 0). Tidak ada barang yang bisa dikeluarkan dari pallet ini.")
        valid_to_submit = False
    else:
        st.info(f"📊 **Data Stok Ditemukan:** In Awal = {int(acuan_in)} | Out Terdahulu = {int(acuan_out)} | **SISA STOK = {int(acuan_sisa)}**")
        
        produk_lama = df_lokasi['Nama Produk'].iloc[0] if not pd.isna(df_lokasi['Nama Produk'].iloc[0]) else list_nama_produk[0]
        selected_product = st.selectbox("Pilih Nama Produk (Wajib):", options=list_nama_produk, index=list_nama_produk.index(produk_lama) if produk_lama in list_nama_produk else 0)
        
        matched_row = product_mapping[product_mapping[product_col].astype(str) == selected_product]
        part_number = matched_row[part_col].values[0] if not matched_row.empty else ""
        st.text_input("Part Number (Auto):", value=part_number, disabled=True)
        
        lot_lama = df_lokasi['Lot Number'].iloc[0] if not pd.isna(df_lokasi['Lot Number'].iloc[0]) else ""
        lot_number = st.text_input("Lot Number / No. Lot (Wajib):", value=lot_lama)
        
        # Batasi Qty Out sesuai stok riil yang ada (acuan_sisa)
        qty_out_input = st.number_input(f"Input Qty OUT (Maksimal ditarik: {int(acuan_sisa)}):", min_value=1, max_value=int(acuan_sisa) if int(acuan_sisa) > 0 else 1, step=1, value=1)
        
        qty_in_final = acuan_in
        qty_out_final = acuan_out + qty_out_input
        sisa_stok_final = qty_in_final - qty_out_final

keterangan = st.text_area("Keterangan Tambahan (Opsional):")
submitted = st.button("💾 Simpan Transaksi ke Sistem", type="primary", use_container_width=True, disabled=not valid_to_submit)

# ---------------------------------------------------------
# PENGIRIMAN DATA KE GOOGLE SHEETS
# ---------------------------------------------------------
if submitted and valid_to_submit:
    if not lot_number.strip():
        st.error("🚨 Gagal: **Lot Number** wajib diisi!")
    elif pallet_sampai < pallet_dari:
        st.error("🚨 Gagal: Rentang pallet tidak logis.")
    else:
        payload = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tipe": "INBOUND" if is_inbound else "OUTBOUND",
            "part_number": part_number,
            "nama_produk": selected_product,
            "lot_number": lot_number,
            "kolom": pilih_kolom,
            "baris": pilih_baris,
            "pallet_dari": pallet_dari,
            "pallet_sampai": pallet_sampai,
            "qty_in": qty_in_final,
            "qty_out": qty_out_final,
            "sisa_stok": sisa_stok_final,
            "keterangan": keterangan
        }
        
        try:
            with st.spinner('Menyimpan data ke Sheet 1 dan Log Transaksi...'):
                response = requests.post(WEB_APP_URL, json=payload)
            
            if response.status_code == 200:
                result = response.json()
                if result.get("status") == "success":
                    st.success(f"✅ Transaksi Berhasil! Sisa Stok sekarang: **{sisa_stok_final}**.")
                    st.cache_data.clear() # Paksa reload
                else:
                    st.error(f"❌ Error dari server: {result.get('message')}")
            else:
                st.error("❌ Gagal terhubung ke Google Sheets (HTTP Error).")
        except Exception as e:
            st.error(f"Koneksi bermasalah: {e}")

st.divider()
st.subheader("📜 Riwayat Log Terakhir")
log_df = load_log()
if not log_df.empty:
    st.dataframe(log_df.tail(5).iloc[::-1], use_container_width=True, hide_index=True)
else:
    st.info("Belum ada log transaksi.")
