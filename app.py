import streamlit as st
import pandas as pd
from datetime import datetime
import requests

# Konfigurasi Halaman Khusus Transaksi
st.set_page_config(page_title="WMS - Form Transaksi", page_icon="📝", layout="centered")

# URL WEB APP APPS SCRIPT ANDA
WEB_APP_URL = "https://script.google.com/macros/s/AKfycbxt87dT2QAotNQyU7kQlBxFObxH37eH6uqqWwjJTBNgowk-pcNON4B6BOTWpwvkr2PYfg/exec"

@st.cache_data(ttl=30)
def load_data():
    try:
        sheet_url = "https://docs.google.com/spreadsheets/d/1rPODgznxi5QxPWk6paIK0-SsTPwIByJPcGYZ8YAUEwc/export?format=csv&gid=0"
        df = pd.read_csv(sheet_url)
        df.columns = df.columns.astype(str).str.strip()
        return df
    except Exception as e:
        st.error(f"⚠ Gagal memuat data master! Error: {e}")
        st.stop()

@st.cache_data(ttl=5)
def load_log():
    try:
        log_url = "https://docs.google.com/spreadsheets/d/1rPODgznxi5QxPWk6paIK0-SsTPwIByJPcGYZ8YAUEwc/export?format=csv&sheet=Log%20Transaksi"
        return pd.read_csv(log_url)
    except Exception:
        return pd.DataFrame(columns=["Timestamp", "Tipe Transaksi", "Part Number", "Nama Produk", "Lot Number", "Rak", "Rentang Pallet", "Qty In", "Qty Out", "Sisa Stok", "Keterangan"])

df = load_data()
product_col = 'Nama Produk' if 'Nama Produk' in df.columns else 'Nama Barang'
part_col = 'Part Number' if 'Part Number' in df.columns else 'Part Number'

product_mapping = df[[product_col, part_col]].dropna().drop_duplicates()
list_nama_produk = sorted(product_mapping[product_col].astype(str).tolist())
baris_list = list(range(1, 29))

# =========================================================================
# TAMPILAN FORM TRANSAKSI
# =========================================================================
st.title("📝 Form Transaksi Gudang")
st.caption("Pencatatan pergerakan Inbound/Outbound. Data akan update di Log dan Visualisasi 2D.")
st.divider()

tipe_trx = st.selectbox("Jenis Pergerakan:", ["INBOUND (Barang Masuk) ⬇️", "OUTBOUND (Barang Keluar) ⬆️"])
is_inbound = "INBOUND" in tipe_trx

selected_product = st.selectbox("Pilih Nama Produk (Wajib):", options=list_nama_produk)
matched_row = product_mapping[product_mapping[product_col].astype(str) == selected_product]
part_number = matched_row[part_col].values[0] if not matched_row.empty else ""

st.text_input("Part Number (Auto-filled):", value=part_number, disabled=True)
lot_number = st.text_input("No. Lot / Lot Number (Wajib):", placeholder="Ketik No. Lot di sini...")

st.markdown("### 📍 Lokasi Rak & Pallet")
pilih_baris = st.selectbox("Pilih Baris Rak (1 - 28):", options=baris_list)

if pilih_baris <= 4:
    kolom_tersedia, info_kolom = ['A', 'B', 'C', 'D', 'E'], "Baris 1–4 (Kolom A - E)"
elif pilih_baris <= 16:
    kolom_tersedia, info_kolom = ['A', 'B', 'C', 'D', 'E', 'F'], "Baris 5–16 (Kolom A - F)"
else:
    kolom_tersedia, info_kolom = ['A', 'B', 'C', 'D', 'E', 'F', 'G'], "Baris 17–28 (Kolom A - G)"
    
pilih_kolom = st.selectbox(f"Pilih Kolom Rak ({info_kolom}):", options=kolom_tersedia)

max_pallet = 31 if pilih_baris <= 16 else 44
st.info(f"ℹ️ Kapasitas maksimal Baris {pilih_baris} = **{max_pallet} Pallet**.")

c3, c4 = st.columns(2)
with c3:
    pallet_dari = st.number_input("Dari Pallet ke-:", min_value=1, max_value=max_pallet, step=1, value=1)
with c4:
    pallet_sampai = st.number_input("Sampai Pallet ke-:", min_value=1, max_value=max_pallet, step=1, value=1)

st.divider()
st.markdown("### 📦 Kuantitas (Wajib Isi)")

if is_inbound:
    qty_in = st.number_input("Quantity In (Qty Masuk):", min_value=0, step=1, value=0)
    qty_out = 0
else:
    qty_in = st.number_input("Acuan Stok Awal (Qty In sebelumnya):", min_value=0, step=1, value=0)
    qty_out = st.number_input("Quantity Out (Qty Keluar):", min_value=0, step=1, value=0)

keterangan = st.text_area("Keterangan Tambahan (Opsional):")

submitted = st.button("💾 Simpan Transaksi ke Sistem", type="primary", use_container_width=True)

# LOGIKA VALIDASI WAJIB ISI SEBELUM SUBMIT
if submitted:
    if not lot_number.strip():
        st.error("🚨 Gagal: Kolom **No. Lot** wajib diisi!")
    elif is_inbound and qty_in <= 0:
        st.error("🚨 Gagal: **Quantity In** harus lebih dari 0!")
    elif not is_inbound and qty_out <= 0:
        st.error("🚨 Gagal: **Quantity Out** harus lebih dari 0!")
    elif not is_inbound and qty_out > qty_in:
        st.error("🚨 Gagal: **Quantity Out** tidak boleh melebihi stok awal!")
    elif pallet_sampai < pallet_dari:
        st.error("🚨 Gagal: Rentang pallet tidak logis.")
    else:
        sisa_stok = qty_in if is_inbound else (qty_in - qty_out)
        
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
            "qty_in": qty_in,
            "qty_out": qty_out,
            "sisa_stok": sisa_stok,
            "keterangan": keterangan
        }
        
        try:
            with st.spinner('Menyimpan ke Sheet 1 dan Log Transaksi...'):
                response = requests.post(WEB_APP_URL, json=payload)
            if response.status_code == 200:
                st.success(f"✅ Berhasil! Transaksi tersimpan. Sisa Stok akhir: {sisa_stok}")
                st.cache_data.clear()
            else:
                st.error("❌ Gagal terhubung ke Google Sheets.")
        except Exception as e:
            st.error(f"Koneksi bermasalah: {e}")

st.divider()
st.subheader("📜 Riwayat Log Terakhir")
log_df = load_log()
if not log_df.empty:
    st.dataframe(log_df.tail(5).iloc[::-1], use_container_width=True, hide_index=True)
else:
    st.info("Belum ada log transaksi.")
