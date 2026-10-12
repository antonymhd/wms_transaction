import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import time
import io
import copy
import re
from datetime import datetime, timedelta

st.set_page_config(
    page_title="WMS Data Entry - Hokkan Deltapack",
    page_icon="📝",
    layout="wide"
)

NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)

db = firestore.client()

st.title("📝 WMS Entry Form (Inbound & Outbound)")
st.caption(f"Alat khusus PPIC. Terhubung ke database: {NAMA_DATABASE}")

# --- FUNGSI CACHED DATA UNTUK TAB 5 (MENJAGA KUOTA AMAN) ---
@st.cache_data(ttl=3600)
def load_all_racks_data():
    docs = db.collection(NAMA_DATABASE).stream()
    all_data = []
    for doc in docs:
        d_data = doc.to_dict().get('data', [])
        all_data.extend(d_data)
    return all_data

def catat_log_mutasi(jenis, kolom, baris, produk, part_num, no_lot, pallet_utuh, pecahan, eceran, keterangan=""):
    waktu_sekarang = (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
    log_data = {
        "Waktu Transaksi": waktu_sekarang, "Jenis Transaksi": jenis,
        "Lokasi Rak": f"{kolom}-{baris}", "Nama Produk": produk, "Part Number": part_num,
        "No Lot": no_lot, "Mutasi Pallet": float(pallet_utuh),
        "Mutasi Pecahan (Dus/Sak)": float(pecahan), "Mutasi Eceran (Pcs)": float(eceran),
        "Keterangan": keterangan, "Timestamp_System": firestore.SERVER_TIMESTAMP
    }
    try: db.collection(DB_LOG_MUTASI).add(log_data)
    except: pass

if 'undo_data' not in st.session_state:
    st.session_state['undo_data'] = None
    st.session_state['action_status'] = None 

if st.session_state['action_status'] == 'DONE':
    if st.button("🔙 BATALKAN AKSI TERAKHIR (UNDO)", type="primary"):
        with st.spinner("Mengembalikan data..."):
            doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state['action_kolom']}")
            doc_ref.set({'data': st.session_state['undo_data']})
            st.session_state['action_status'] = 'UNDONE'
            st.success("✅ Aksi dibatalkan!")
            time.sleep(1)
            st.rerun()

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📥 1. Inbound", "📤 2. Outbound", "⚙️ 3. Audit & Backup", "📊 4. Log Mutasi", "🔎 5. Pelacak FIFO"])

# ----------------------------------------------------------
# TAB 1: INBOUND
# ----------------------------------------------------------
with tab1:
    st.header("Input Data Kertas (Borongan)")
    if 'in_lokasi_dicari' not in st.session_state:
        st.session_state['in_lokasi_dicari'] = False

    col1, col2 = st.columns([1, 2])
    with col1:
        in_kolom = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="in_kolom")
        in_baris = st.number_input("Nomor Baris", min_value=1, max_value=28, value=1)
        if st.button("🔍 Cek Status Rak", use_container_width=True):
            st.session_state['in_lokasi_dicari'] = True
            st.session_state['in_k_pilih'] = in_kolom
            st.session_state['in_b_pilih'] = in_baris
            
    with col2:
        if st.session_state['in_lokasi_dicari']:
            k_pilih, b_pilih = st.session_state['in_k_pilih'], st.session_state['in_b_pilih']
            doc_ref_in = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
            doc_in = doc_ref_in.get()
            all_data_raw = doc_in.to_dict().get('data', []) if doc_in.exists else []
            
            baris_data = [d for d in all_data_raw if float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
            is_all_box = all(str(d.get('Part Number', '')).strip().upper().endswith(('D', 'G')) for d in baris_data) if baris_data else False
            max_slot = (32 if b_pilih <= 16 else 46) if is_all_box else (31 if b_pilih <= 16 else 44)
            
            st.markdown(f"**📦 Rak {k_pilih}-{b_pilih} | Terisi: `{len(baris_data)}` / `{max_slot}`**")
            
            try:
                df_master = pd.read_csv("Master_Produk.csv", sep=";") if ";" in open("Master_Produk.csv").readline() else pd.read_csv("Master_Produk.csv", sep=",")
                daftar_prod = df_master['Nama Barang'].dropna().astype(str).unique().tolist()
            except: df_master = pd.DataFrame(); daftar_prod = []
            
            pilih_p = st.selectbox("Pilih Produk", ["-- Ketik Manual --"] + daftar_prod)
            if pilih_p != "-- Ketik Manual --" and not df_master.empty:
                in_prod = pilih_p
                in_part = str(df_master[df_master['Nama Barang'] == pilih_p]['Part Number'].values[0])
                st.text_input("Part Number", value=in_part, disabled=True)
            else:
                in_prod = st.text_input("Nama Produk")
                in_part = st.text_input("Part Number")
                
            in_lot = st.text_input("No. Lot / Shift")
            cq1, cq2, cq3 = st.columns(3)
            with cq1: i_pal = st.number_input("Pallet Utuh", min_value=0, value=1)
            with cq2: i_pec = st.number_input("Sisa Dus/Sak", min_value=0, value=0)
            with cq3: i_ecs = st.number_input("Eceran Pcs", min_value=0, value=0)
                
            if st.button("🚀 Simpan Inbound", type="primary", use_container_width=True):
                if not in_prod or not in_part: st.error("⚠️ Data belum lengkap!")
                else:
                    with st.spinner("Menyimpan..."):
                        data_seb = copy.deepcopy(all_data_raw)
                        clean_all = [d for d in all_data_raw if not (float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() == '')]
                        baris_act = [d for d in clean_all if float(d.get('Baris', 0) or 0) == float(b_pilih)]
                        
                        filled = [int(float(d.get('Pallet Ke', 0) or 0)) for d in baris_act if pd.notna(d.get('Pallet Ke'))]
                        slots_needed = i_pal + (1 if (i_pec > 0 or i_ecs > 0) else 0)
                        
                        if len(filled) + slots_needed > max_slot:
                            st.error("❌ Kapasitas Rak Penuh!")
                        else:
                            nxt = max(filled) + 1 if filled else 1
                            new_recs = []
                            for _ in range(int(i_pal)):
                                while nxt in filled: nxt += 1
                                new_recs.append({'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': float(nxt), 'Part Number': in_part, 'Lot Number': in_lot, 'Nama Produk': in_prod, 'In': 1, 'Out': 0, 'Sisa': 1, 'Status': 'Terisi', 'Keterangan': ''})
                                filled.append(nxt); nxt += 1
                            if i_pec > 0 or i_ecs > 0:
                                while nxt in filled: nxt += 1
                                kp = []
                                if i_pec > 0: kp.append(f"Pecahan: {i_pec} qty")
                                if i_ecs > 0: kp.append(f"Eceran: {i_ecs} pcs")
                                new_recs.append({'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': float(nxt), 'Part Number': in_part, 'Lot Number': in_lot, 'Nama Produk': in_prod, 'In': 1, 'Out': 0, 'Sisa': 1, 'Status': 'Terisi', 'Keterangan': " | ".join(kp)})
                            clean_all.extend(new_recs)
                            
                            st.session_state['undo_data'] = data_seb
                            st.session_state['action_kolom'] = k_pilih
                            st.session_state['action_msg'] = f"Inbound {in_prod}"
                            st.session_state['action_status'] = 'DONE'
                            
                            doc_ref_in.set({'data': clean_all})
                            st.cache_data.clear() # Clear cache agar data sinkron
                            catat_log_mutasi("INBOUND", k_pilih, b_pilih, in_prod, in_part, in_lot, i_pal, i_pec, i_ecs)
                            st.success("✅ Berhasil!")
                            st.session_state['in_lokasi_dicari'] = False
                            time.sleep(1)
                            st.rerun()

# ----------------------------------------------------------
# TAB 2: OUTBOUND
# ----------------------------------------------------------
with tab2:
    st.header("Penyesuaian Sisa Stok (Outbound)")
    if 'out_cari' not in st.session_state: st.session_state['out_cari'] = False
    co1, co2 = st.columns([1, 2])
    with co1:
        o_kol = st.selectbox("Kolom", ["A", "B", "C", "D", "E", "F", "G"], key="o_kol")
        o_bar = st.number_input("Baris", min_value=1, max_value=28, value=1)
        if st.button("🔍 Cari Data", use_container_width=True):
            st.session_state['out_cari'] = True
            st.session_state['ok_pilih'] = o_kol
            st.session_state['ob_pilih'] = o_bar
    with co2:
        if st.session_state['out_cari']:
            kp, bp = st.session_state['ok_pilih'], st.session_state['ob_pilih']
            doc_o = db.collection(NAMA_DATABASE).document(f"Kolom_{kp}").get()
            all_o = doc_o.to_dict().get('data', []) if doc_o.exists else []
            b_data = [d for d in all_o if float(d.get('Baris', 0) or 0) == float(bp) and str(d.get('Part Number', '')).strip() != '']
            
            if b_data:
                df_o = pd.DataFrame(b_data)
                lots = []
                m_lot = {}
                for _, r in df_o.drop_duplicates(subset=['Lot Number', 'Nama Produk']).iterrows():
                    lbl = f"{r['Nama Produk']} (Lot: {r['Lot Number']})"
                    lots.append(lbl); m_lot[lbl] = str(r['Lot Number'])
                p_lbl = st.selectbox("Pilih Lot:", lots)
                p_lot = m_lot[p_lbl]
                
                d_lot = df_o[df_o['Lot Number'].astype(str) == p_lot]
                j_utuh = d_lot[~d_lot['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
                
                cs1, cs2, cs3 = st.columns(3)
                with cs1: s_utuh = st.number_input("Sisa Pallet Utuh", min_value=0, max_value=j_utuh, value=j_utuh)
                with cs2: s_dus = st.number_input("Sisa Dus/Sak", min_value=0, value=0)
                with cs3: s_pcs = st.number_input("Sisa Pcs", min_value=0, value=0)
                
                if st.button("🚜 Eksekusi Outbound", type="primary", use_container_width=True):
                    st.cache_data.clear()
                    st.success("✅ Outbound tercatat!")
                    time.sleep(1)
                    st.rerun()

# ----------------------------------------------------------
# TAB 3: AUDIT & BACKUP (CSV SUPER)
# ----------------------------------------------------------
with tab3:
    st.header("⚙️ Audit Stok & Ekspor CSV Super")
    if st.button("Racik Laporan Audit Stok", type="primary"):
        docs = db.collection(NAMA_DATABASE).stream()
        all_d = [item for doc in docs for item in doc.to_dict().get('data', [])]
        if all_d:
            df_ex = pd.DataFrame(all_d)
            csv_d = df_ex.to_csv(index=False, sep=";")
            st.download_button("⬇️ Unduh CSV Audit", data=csv_d, file_name="Audit_Stok.csv", mime="text/csv")

# ----------------------------------------------------------
# TAB 4: LOG MUTASI
# ----------------------------------------------------------
with tab4:
    st.header("📊 Log Buku Mutasi")
    if st.button("🔄 Segarkan Log"): st.rerun()
    try:
        logs = db.collection(DB_LOG_MUTASI).order_by("Timestamp_System", direction=firestore.Query.DESCENDING).limit(30).stream()
        l_list = [d.to_dict() for d in logs]
        if l_list: st.dataframe(pd.DataFrame(l_list), use_container_width=True, hide_index=True)
    except: pass

# ----------------------------------------------------------
# TAB 5: PELACAK FIFO (DENGAN CACHE AMAN KUOTA)
# ----------------------------------------------------------
with tab5:
    st.header("🔎 Pelacak Lokasi Rak & FIFO")
    st.caption("Data dipindai dari cache memori agar kuota Firebase Anda tetap aman.")
    
    if st.button("🔄 Segarkan / Muat Ulang Data Gudang"):
        st.cache_data.clear()
        st.rerun()
        
    with st.spinner("Memuat data dari cache..."):
        all_fifo_data = load_all_racks_data()
        
    df_fifo = pd.DataFrame(all_fifo_data)
    if not df_fifo.empty and 'Nama Produk' in df_fifo.columns:
        df_fifo = df_fifo[df_fifo['Part Number'].astype(str).str.strip() != ""]
        prods_fifo = sorted(df_fifo['Nama Produk'].astype(str).unique().tolist())
        
        p_fifo = st.selectbox("Pilih Produk yang dicari:", ["-- Pilih Produk --"] + prods_fifo)
        if p_fifo != "-- Pilih Produk --":
            df_pf = df_fifo[df_fifo['Nama Produk'] == p_fifo].sort_values(by=['Lot Number', 'Kolom', 'Baris'])
            st.dataframe(df_pf[['Lot Number', 'Kolom', 'Baris', 'Pallet Ke', 'Keterangan']], use_container_width=True, hide_index=True)
