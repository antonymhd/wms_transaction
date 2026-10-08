import streamlit as st
import pandas as pd
from datetime import datetime
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

# ==========================================================
# 1. KONFIGURASI HALAMAN & INISIALISASI FIREBASE
# ==========================================================
st.set_page_config(page_title="WMS - Form Transaksi", page_icon="⚡", layout="centered")

# Inisialisasi Firebase menggunakan kunci rahasia dari Streamlit Secrets
if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ==========================================================
# 2. LOAD DATA (PRODUK DARI SHEETS, STOK DARI FIREBASE)
# ==========================================================
# Data produk tetap dari Sheets agar mudah diedit (Cache 1 Jam)
@st.cache_data(ttl=3600)
def load_product_mapping():
    try:
        sheet_url = "https://docs.google.com/spreadsheets/d/1rPODgznxi5QxPWk6paIK0-SsTPwIByJPcGYZ8YAUEwc/export?format=csv&gid=0"
        df = pd.read_csv(sheet_url)
        df.columns = df.columns.astype(str).str.strip()
        product_col = 'Nama Produk' if 'Nama Produk' in df.columns else 'Nama Barang'
        part_col = 'Part Number' if 'Part Number' in df.columns else 'Part Number'
        return df[[product_col, part_col]].dropna().drop_duplicates()
    except Exception as e:
        st.error("Gagal memuat daftar produk.")
        return pd.DataFrame({'Nama Produk': ['-'], 'Part Number': ['-']})

product_mapping = load_product_mapping()
product_col = product_mapping.columns[0]
part_col = product_mapping.columns[1]
list_nama_produk = sorted(product_mapping[product_col].astype(str).tolist())
baris_list = list(range(1, 29))

# ==========================================================
# 3. TAMPILAN UI FORM
# ==========================================================
st.title("⚡ Form Transaksi Gudang (Firebase)")
st.caption("Didukung oleh Firebase Firestore. Sinkronisasi Real-time di bawah 1 detik.")
st.divider()

tipe_trx = st.selectbox("Jenis Pergerakan:", ["INBOUND (Barang Masuk) ⬇️", "OUTBOUND (Barang Keluar) ⬆️"])
is_inbound = "INBOUND" in tipe_trx

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

# ==========================================================
# 4. VALIDASI SMART REAL-TIME (LANGSUNG DARI FIREBASE)
# ==========================================================
# Tarik data rak yang dipilih langsung dari Firebase (Seketika/Tanpa Cache)
docs = db.collection('master_rak').where('Kolom', '==', pilih_kolom).where('Baris', '==', pilih_baris).stream()
semua_pallet = [doc.to_dict() for doc in docs]
df_lokasi = pd.DataFrame(semua_pallet)

status_terisi = 0
acuan_in = acuan_out = acuan_sisa = 0

# Filter rentang pallet di Python agar tidak kena error Index Firebase
if not df_lokasi.empty:
    df_lokasi['Pallet_Ke'] = pd.to_numeric(df_lokasi['Pallet_Ke'], errors='coerce')
    df_lokasi = df_lokasi[(df_lokasi['Pallet_Ke'] >= pallet_dari) & (df_lokasi['Pallet_Ke'] <= pallet_sampai)]
    
    if not df_lokasi.empty:
        status_terisi = (df_lokasi['Status'] == 'Terisi').sum()
        acuan_in = pd.to_numeric(df_lokasi['In'], errors='coerce').fillna(0).max()
        acuan_out = pd.to_numeric(df_lokasi['Out'], errors='coerce').fillna(0).max()
        sisa_di_db = pd.to_numeric(df_lokasi['Sisa'], errors='coerce').fillna(0).max()
        acuan_sisa = sisa_di_db if sisa_di_db > 0 else (acuan_in - acuan_out)

st.divider()

# ==========================================================
# 5. INPUT IDENTITAS & KUANTITAS
# ==========================================================
st.markdown("### 2️⃣ Kuantitas & Identitas Produk")

valid_to_submit = True
qty_in_final = qty_out_final = sisa_stok_final = 0

if is_inbound:
    if status_terisi > 0:
        st.error(f"🚨 **TIDAK BISA DIPROSES:** Ada **{status_terisi} pallet** di lokasi ini yang masih Terisi. Pilih slot yang kosong!")
        valid_to_submit = False
    else:
        st.success("✅ Lokasi Kosong, siap untuk Inbound.")
        selected_product = st.selectbox("Pilih Nama Produk (Wajib):", options=list_nama_produk)
        matched_row = product_mapping[product_mapping[product_col].astype(str) == selected_product]
        part_number = matched_row[part_col].values[0] if not matched_row.empty else ""
        
        st.text_input("Part Number (Auto):", value=part_number, disabled=True)
        lot_number = st.text_input("Lot Number / No. Lot (Wajib):")
        
        qty_in_final = st.number_input("Input Qty IN (Masuk):", min_value=1, step=1, value=1)
        qty_out_final = 0
        sisa_stok_final = qty_in_final

else:
    if status_terisi == 0 and acuan_sisa <= 0:
        st.error("🚨 **TIDAK BISA DIPROSES:** Lokasi ini **KOSONG** (Sisa: 0).")
        valid_to_submit = False
    else:
        st.info(f"📊 **Data Stok Real-time:** In Awal = {int(acuan_in)} | Out Terdahulu = {int(acuan_out)} | **SISA = {int(acuan_sisa)}**")
        
        produk_lama = df_lokasi['Nama_Produk'].iloc[0] if 'Nama_Produk' in df_lokasi.columns else list_nama_produk[0]
        selected_product = st.selectbox("Pilih Nama Produk (Wajib):", options=list_nama_produk, index=list_nama_produk.index(produk_lama) if produk_lama in list_nama_produk else 0)
        
        matched_row = product_mapping[product_mapping[product_col].astype(str) == selected_product]
        part_number = matched_row[part_col].values[0] if not matched_row.empty else ""
        st.text_input("Part Number (Auto):", value=part_number, disabled=True)
        
        lot_lama = df_lokasi['Lot_Number'].iloc[0] if 'Lot_Number' in df_lokasi.columns else ""
        lot_number = st.text_input("Lot Number / No. Lot (Wajib):", value=lot_lama)
        
        qty_out_input = st.number_input(f"Input Qty OUT (Maks: {int(acuan_sisa)}):", min_value=1, max_value=int(acuan_sisa) if int(acuan_sisa) > 0 else 1, step=1, value=1)
        
        qty_in_final = acuan_in
        qty_out_final = acuan_out + qty_out_input
        sisa_stok_final = qty_in_final - qty_out_final

keterangan = st.text_area("Keterangan Tambahan (Opsional):")
submitted = st.button("🚀 Simpan Transaksi Instan", type="primary", use_container_width=True, disabled=not valid_to_submit)

# ==========================================================
# 6. PENYIMPANAN KE FIREBASE (SUPER CEPAT)
# ==========================================================
if submitted and valid_to_submit:
    if not lot_number.strip():
        st.error("🚨 Gagal: **Lot Number** wajib diisi!")
    elif pallet_sampai < pallet_dari:
        st.error("🚨 Gagal: Rentang pallet tidak logis.")
    else:
        with st.spinner("Menulis ke Firebase... ⚡"):
            try:
                # Menggunakan sistem Batch agar puluhan pallet ter-update dalam 1 kedipan mata
                batch = db.batch()
                
                # A. Menulis ke Log Transaksi
                log_ref = db.collection('log_transaksi').document()
                batch.set(log_ref, {
                    "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Tipe_Transaksi": "INBOUND" if is_inbound else "OUTBOUND",
                    "Part_Number": part_number,
                    "Nama_Produk": selected_product,
                    "Lot_Number": lot_number,
                    "Lokasi_Rak": f"{pilih_kolom}{pilih_baris}",
                    "Rentang_Pallet": f"{pallet_dari} - {pallet_sampai}",
                    "Qty_In": qty_in_final,
                    "Qty_Out": qty_out_final,
                    "Sisa_Stok": sisa_stok_final,
                    "Keterangan": keterangan
                })
                
                # B. Mengubah Status Fisik Rak
                is_terisi = (sisa_stok_final > 0)
                for pallet in range(pallet_dari, pallet_sampai + 1):
                    doc_id = f"{pilih_kolom}{pilih_baris}-{pallet}" # Format ID unik: A1-1, A1-2, dst.
                    rak_ref = db.collection('master_rak').document(doc_id)
                    batch.set(rak_ref, {
                        "Kolom": pilih_kolom,
                        "Baris": pilih_baris,
                        "Pallet_Ke": pallet,
                        "Part_Number": part_number if is_terisi else "",
                        "Lot_Number": lot_number if is_terisi else "",
                        "Nama_Produk": selected_product if is_terisi else "",
                        "In": qty_in_final if is_terisi else 0,
                        "Out": qty_out_final if is_terisi else 0,
                        "Sisa": sisa_stok_final if is_terisi else 0,
                        "Status": "Terisi" if is_terisi else "Kosong",
                        "Keterangan": keterangan if is_terisi else ""
                    }, merge=True)
                
                # Eksekusi semua perintah sekaligus
                batch.commit()
                st.success(f"✅ Transaksi Berhasil dalam Sekejap! Sisa Stok sekarang: **{sisa_stok_final}**.")
                
            except Exception as e:
                st.error(f"❌ Terjadi kesalahan jaringan Firebase: {e}")

# ==========================================================
# 7. MENAMPILKAN RIWAYAT TRANSAKSI DARI FIREBASE
# ==========================================================
st.divider()
st.subheader("📜 Riwayat Log Terakhir (Firebase)")
try:
    log_docs = db.collection('log_transaksi').order_by('Timestamp', direction=firestore.Query.DESCENDING).limit(5).stream()
    log_data = [doc.to_dict() for doc in log_docs]
    
    if log_data:
        df_log = pd.DataFrame(log_data)
        # Susun ulang urutan kolom agar enak dibaca
        kolom_rapi = ['Timestamp', 'Tipe_Transaksi', 'Lokasi_Rak', 'Rentang_Pallet', 'Nama_Produk', 'Lot_Number', 'Qty_In', 'Qty_Out', 'Sisa_Stok']
        st.dataframe(df_log[kolom_rapi], use_container_width=True, hide_index=True)
    else:
        st.info("Belum ada log transaksi. Coba input satu data Inbound!")
except Exception as e:
    st.warning("Menunggu data transaksi pertama masuk...")
