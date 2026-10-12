import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import time
import io
import copy
import re
from datetime import datetime, timedelta

st.set_page_config(page_title="WMS Data Entry - Hokkan Deltapack", page_icon="📝", layout="wide")

NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

st.title("📝 WMS Entry Form (Inbound & Outbound)")
st.caption(f"Alat khusus PPIC. Terhubung ke database: {NAMA_DATABASE}")

# ==========================================================
# 🛡️ FUNGSI PELINDUNG KUOTA UTAMA (Semua Tab Numpang Kesini)
# ==========================================================
@st.cache_data(ttl=3600)
def load_all_racks_data():
    try:
        docs = db.collection(NAMA_DATABASE).stream()
        all_data = []
        for doc in docs:
            all_data.extend(doc.to_dict().get('data', []))
        return all_data
    except Exception as e:
        st.error(f"Gagal memuat data utama: {e}")
        return []

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
            db.collection(NAMA_DATABASE).document(f"Kolom_{st.session_state['action_kolom']}").set({'data': st.session_state['undo_data']})
            st.session_state['action_status'] = 'UNDONE'
            st.cache_data.clear() # Wajib clear agar update terbaca
            st.success("✅ Aksi dibatalkan!")
            time.sleep(1)
            st.rerun()

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📥 1. Inbound", "📤 2. Outbound", "⚙️ 3. Audit & Backup", "📊 4. Log Mutasi", "🔎 5. Pelacak FIFO"])

# ----------------------------------------------------------
# TAB 1: INBOUND
# ----------------------------------------------------------
with tab1:
    st.header("Input Data Kertas (Borongan)")
    if 'in_lokasi_dicari' not in st.session_state: st.session_state['in_lokasi_dicari'] = False

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
            kp, bp = st.session_state['in_k_pilih'], st.session_state['in_b_pilih']
            
            # AMAN KUOTA: Baca dari Cache
            all_data_raw = [d for d in load_all_racks_data() if d.get('Kolom') == kp]
            
            baris_data = [d for d in all_data_raw if float(d.get('Baris', 0) or 0) == float(bp) and str(d.get('Part Number', '')).strip() != '']
            is_all_box = all(str(d.get('Part Number', '')).strip().upper().endswith(('D', 'G')) for d in baris_data) if baris_data else False
            max_slot = (32 if bp <= 16 else 46) if is_all_box else (31 if bp <= 16 else 44)
            
            st.markdown(f"**📦 Rak {kp}-{bp} | Terisi: `{len(baris_data)}` / `{max_slot}`**")
            
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
                        doc_live = db.collection(NAMA_DATABASE).document(f"Kolom_{kp}").get()
                        live_data = doc_live.to_dict().get('data', []) if doc_live.exists else []
                        data_seb = copy.deepcopy(live_data)
                        
                        clean_all = [d for d in live_data if not (float(d.get('Baris', 0) or 0) == float(bp) and str(d.get('Part Number', '')).strip() == '')]
                        baris_act = [d for d in clean_all if float(d.get('Baris', 0) or 0) == float(bp)]
                        
                        filled = [int(float(d.get('Pallet Ke', 0) or 0)) for d in baris_act if pd.notna(d.get('Pallet Ke'))]
                        slots_needed = i_pal + (1 if (i_pec > 0 or i_ecs > 0) else 0)
                        
                        if len(filled) + slots_needed > max_slot:
                            st.error("❌ Kapasitas Rak Penuh!")
                        else:
                            nxt = max(filled) + 1 if filled else 1
                            new_recs = []
                            for _ in range(int(i_pal)):
                                while nxt in filled: nxt += 1
                                new_recs.append({'Kolom': kp, 'Baris': float(bp), 'Pallet Ke': float(nxt), 'Part Number': in_part, 'Lot Number': in_lot, 'Nama Produk': in_prod, 'In': 1, 'Out': 0, 'Sisa': 1, 'Status': 'Terisi', 'Keterangan': ''})
                                filled.append(nxt); nxt += 1
                            if i_pec > 0 or i_ecs > 0:
                                while nxt in filled: nxt += 1
                                kp_ket = []
                                if i_pec > 0: kp_ket.append(f"Pecahan: {i_pec} qty")
                                if i_ecs > 0: kp_ket.append(f"Eceran: {i_ecs} pcs")
                                new_recs.append({'Kolom': kp, 'Baris': float(bp), 'Pallet Ke': float(nxt), 'Part Number': in_part, 'Lot Number': in_lot, 'Nama Produk': in_prod, 'In': 1, 'Out': 0, 'Sisa': 1, 'Status': 'Terisi', 'Keterangan': " | ".join(kp_ket)})
                            clean_all.extend(new_recs)
                            
                            st.session_state['undo_data'] = data_seb
                            st.session_state['action_kolom'] = kp
                            st.session_state['action_msg'] = f"Inbound {in_prod}"
                            st.session_state['action_status'] = 'DONE'
                            
                            db.collection(NAMA_DATABASE).document(f"Kolom_{kp}").set({'data': clean_all})
                            st.cache_data.clear() # Wajib clear cache
                            catat_log_mutasi("INBOUND", kp, bp, in_prod, in_part, in_lot, i_pal, i_pec, i_ecs)
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
                
                pecahan_rows = d_lot[d_lot['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)]
                pecahan_awal = eceran_awal = 0
                if not pecahan_rows.empty:
                    ket_str = str(pecahan_rows['Keterangan'].iloc[0]).lower()
                    mp = re.search(r'pecahan:\s*(\d+)', ket_str)
                    if mp: pecahan_awal = int(mp.group(1))
                    me = re.search(r'eceran:\s*(\d+)', ket_str)
                    if me: eceran_awal = int(me.group(1))

                cs1, cs2, cs3 = st.columns(3)
                with cs1: s_utuh = st.number_input("Sisa Pallet Utuh", min_value=0, max_value=j_utuh, value=j_utuh)
                with cs2: s_dus = st.number_input("Sisa Dus/Sak", min_value=0, value=pecahan_awal)
                with cs3: s_pcs = st.number_input("Sisa Pcs", min_value=0, value=eceran_awal)
                
                if st.button("🚜 Eksekusi Outbound", type="primary", use_container_width=True):
                    keluar_utuh = j_utuh - s_utuh
                    keluar_pecahan = pecahan_awal - s_dus
                    keluar_eceran = eceran_awal - s_pcs
                    
                    data_seb_o = copy.deepcopy(all_o)
                    out_qty = len(d_lot) - (s_utuh + (1 if s_dus > 0 or s_pcs > 0 else 0))
                    pallets_remove = d_lot['Pallet Ke'].head(out_qty).tolist() if out_qty > 0 else []
                    
                    new_all_o = []
                    for d in all_o:
                        b_val = float(d.get('Baris', 0) or 0)
                        p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                        l_str = str(d.get('Lot Number', ''))
                        if b_val == float(bp) and p_val in pallets_remove: continue 
                        if b_val == float(bp) and l_str == p_lot:
                            sisa_pallets = d_lot[~d_lot['Pallet Ke'].isin(pallets_remove)]
                            if not sisa_pallets.empty and p_val == sisa_pallets['Pallet Ke'].max():
                                if s_dus > 0 or s_pcs > 0:
                                    kp_str = []
                                    if s_dus > 0: kp_str.append(f"Pecahan: {s_dus} qty")
                                    if s_pcs > 0: kp_str.append(f"Eceran: {s_pcs} pcs")
                                    d['Keterangan'] = " | ".join(kp_str)
                                else: d['Keterangan'] = ""
                            else: d['Keterangan'] = ""
                        new_all_o.append(d)
                    
                    db.collection(NAMA_DATABASE).document(f"Kolom_{kp}").set({'data': new_all_o})
                    st.session_state['undo_data'] = data_seb_o
                    st.session_state['action_kolom'] = kp
                    st.session_state['action_msg'] = f"Outbound Lot {p_lot}"
                    st.session_state['action_status'] = 'DONE'
                    
                    st.cache_data.clear() # Wajib clear cache
                    catat_log_mutasi("OUTBOUND", kp, bp, str(d_lot['Nama Produk'].iloc[0]), str(d_lot['Part Number'].iloc[0]), p_lot, keluar_utuh, keluar_pecahan, keluar_eceran)
                    st.success("✅ Outbound berhasil!")
                    st.session_state['out_cari'] = False
                    time.sleep(1)
                    st.rerun()

# ----------------------------------------------------------
# TAB 3: AUDIT & BACKUP (DENGAN CACHE AMAN)
# ----------------------------------------------------------
with tab3:
    st.header("⚙️ Audit Stok & Ekspor CSV Super")
    if st.button("Racik Laporan Audit Stok", type="primary"):
        with st.spinner("Memproses Laporan..."):
            # AMAN KUOTA: Baca dari Cache Pusat
            all_d = load_all_racks_data()
            if all_d:
                df_ex = pd.DataFrame(all_d)
                
                # Modul Hitung Pcs (sama seperti sebelumnya, disingkat)
                try: df_m = pd.read_csv("Master_Produk.csv", sep=";") if ";" in open("Master_Produk.csv").readline() else pd.read_csv("Master_Produk.csv", sep=",")
                except: df_m = pd.DataFrame()
                if not df_m.empty: df_m['Part Number Clean'] = df_m['Part Number'].astype(str).str.strip().str.upper()

                qu, qd, qp, gt = [], [], [], []
                for _, r in df_ex.iterrows():
                    if pd.isna(r.get('Part Number')) or str(r.get('Part Number')).strip() == '':
                        qu.append(0); qd.append(0); qp.append(0); gt.append(0); continue
                    ket = str(r.get('Keterangan', '')).lower()
                    is_p = 'pecahan' in ket or 'eceran' in ket
                    u = 0 if is_p else 1
                    d, p = 0, 0
                    if is_p:
                        mp = re.search(r'pecahan:\s*(\d+)', ket)
                        if mp: d = int(mp.group(1))
                        me = re.search(r'eceran:\s*(\d+)', ket)
                        if me: p = int(me.group(1))
                    qu.append(u); qd.append(d); qp.append(p)
                    
                    pnc = str(r.get('Part Number', '')).strip().upper()
                    ip = pps = 0
                    if not df_m.empty and pnc in df_m['Part Number Clean'].values:
                        mr = df_m[df_m['Part Number Clean'] == pnc].iloc[0]
                        ip = float(mr.get('Isi 1 Pallet (pcs)', 0) or 0)
                        pps = float(mr.get('Box / Pallet' if pnc.endswith('D') else 'Sac / Pallet', 0) or 0)
                    gt.append((u * ip) + (d * pps) + p)
                    
                df_ex['Qty Utuh'], df_ex['Qty Dus/Sak'], df_ex['Qty Pcs Eceran'], df_ex['Grand Total Pcs'] = qu, qd, qp, gt
                for col in ['Baris', 'Pallet Ke', 'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs']:
                    if col in df_ex.columns:
                        df_ex[col] = pd.to_numeric(df_ex[col], errors='coerce').fillna(0).astype(int)
                        if col in ['Baris', 'Pallet Ke']: df_ex[col] = df_ex[col].replace(0, "")
                
                kolom_harapan = ['Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk', 'Keterangan', 'Status', 'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs']
                df_ex = df_ex[[c for c in kolom_harapan if c in df_ex.columns]]
                        
                csv_d = df_ex.to_csv(index=False, sep=";")
                st.download_button("⬇️ Unduh CSV Audit", data=csv_d, file_name=f"Audit_Stok_WMS_{(datetime.utcnow() + timedelta(hours=7)).strftime('%Y%m%d')}.csv", mime="text/csv")
            else:
                st.warning("Database kosong.")

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
# TAB 5: PELACAK FIFO (DENGAN CACHE AMAN)
# ----------------------------------------------------------
with tab5:
    st.header("🔎 Pelacak Lokasi Rak & FIFO")
    if st.button("🔄 Segarkan / Muat Ulang Data"):
        st.cache_data.clear()
        st.rerun()
        
    with st.spinner("Memuat data..."):
        all_fifo_data = load_all_racks_data() # AMAN KUOTA: Baca dari Cache Pusat
        
    df_fifo = pd.DataFrame(all_fifo_data)
    if not df_fifo.empty and 'Nama Produk' in df_fifo.columns:
        df_fifo = df_fifo[df_fifo['Part Number'].astype(str).str.strip() != ""]
        prods_fifo = sorted(df_fifo['Nama Produk'].astype(str).unique().tolist())
        
        p_fifo = st.selectbox("Pilih Produk yang dicari:", ["-- Pilih Produk --"] + prods_fifo)
        if p_fifo != "-- Pilih Produk --":
            df_pf = df_fifo[df_fifo['Nama Produk'] == p_fifo].sort_values(by=['Lot Number', 'Kolom', 'Baris'])
            st.dataframe(df_pf[['Lot Number', 'Kolom', 'Baris', 'Pallet Ke', 'Keterangan']], use_container_width=True, hide_index=True)
