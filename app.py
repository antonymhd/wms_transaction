import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import time
import io
import copy
import re
from datetime import datetime, timedelta

# ==========================================================
# 1. KONFIGURASI WEB & SISTEM LOGIN
# ==========================================================
st.set_page_config(page_title="WMS Data Entry - Hokkan Deltapack", page_icon="📝", layout="wide")

if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False

if not st.session_state['logged_in']:
    st.title("🔒 WMS Security Login")
    st.caption("Silakan masukkan kata sandi untuk mengakses form mutasi gudang.")
    
    correct_password = st.secrets.get("wms_password", "Admin123")
    
    with st.form("login_form"):
        pwd_input = st.text_input("Kata Sandi", type="password")
        submit_btn = st.form_submit_button("Login", type="primary")
        
        if submit_btn:
            if pwd_input == correct_password:
                st.session_state['logged_in'] = True
                st.rerun()
            else:
                st.error("Kata sandi salah!")
    st.stop()

st.sidebar.button("🚪 Logout", on_click=lambda: st.session_state.update({'logged_in': False}))

# ==========================================================
# 2. INIT DATABASE (FIREBASE)
# ==========================================================
NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

st.title("📝 WMS Entry Form (Inbound, Outbound & Relokasi)")
st.caption(f"Alat khusus PPIC. Terhubung ke database: {NAMA_DATABASE}")

# ==========================================================
# 3. HELPER FUNCTIONS
# ==========================================================
@st.cache_data(ttl=3600)
def load_master_produk():
    try:
        try: df = pd.read_csv("Master_Produk.csv", sep=";", encoding='utf-8-sig')
        except: df = pd.read_csv("Master_Produk.csv", sep=",", encoding='utf-8-sig')
        df.columns = df.columns.str.strip()
        if 'Part Number' in df.columns:
            df['Part Number Clean'] = df['Part Number'].astype(str).str.strip().str.upper()
        return df
    except Exception as e:
        st.toast(f"Gagal memuat master produk: {e}", icon="🚨")
        return pd.DataFrame()

def catat_log_mutasi(jenis, kolom, baris, produk, part_num, no_lot, pallet_utuh, pecahan, eceran, keterangan=""):
    waktu_sekarang = (datetime.utcnow() + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
    no_lot_final = no_lot if str(no_lot).strip() != "" else "-"
    log_data = {
        "Waktu Transaksi": waktu_sekarang, "Jenis Transaksi": jenis, "Lokasi Rak": f"{kolom}-{baris}",
        "Nama Produk": produk, "Part Number": part_num, "No Lot": no_lot_final,
        "Mutasi Pallet": float(pallet_utuh), "Mutasi Pecahan (Dus/Sak)": float(pecahan),
        "Mutasi Eceran (Pcs)": float(eceran), "Keterangan": keterangan,
        "Timestamp_System": firestore.SERVER_TIMESTAMP
    }
    try: db.collection(DB_LOG_MUTASI).add(log_data)
    except Exception as e: st.toast(f"⚠️ Peringatan: Gagal mencatat log mutasi ({e})", icon="⚠️")

# ==========================================================
# 4. NOTIFIKASI MELAYANG (FLOATING BOTTOM BOX) & UNDO
# ==========================================================
if 'undo_dict' not in st.session_state:
    st.session_state.update({'undo_dict': {}, 'action_msg': "", 'action_status': None})

css_floating_box = """
<style>
div[data-testid="stVerticalBlock"]:has(#floating-notif-anchor) {
    position: fixed;
    bottom: 25px;
    left: 50%;
    transform: translateX(-50%);
    background-color: #ffffff;
    padding: 1rem 1.5rem;
    border-radius: 12px;
    box-shadow: 0px 8px 30px rgba(0,0,0,0.3);
    z-index: 99999;
    width: 90%;
    max-width: 800px;
    border-left: 6px solid {border_color};
}
</style>
<div id="floating-notif-anchor"></div>
"""

if st.session_state['action_status'] == 'DONE':
    with st.container():
        st.markdown(css_floating_box.replace("{border_color}", "#10b981"), unsafe_allow_html=True)
        notif_c1, notif_c2 = st.columns([4, 1])
        notif_c1.success(f"✅ **Sukses:** {st.session_state['action_msg']}")
        
        if notif_c2.button("🔙 UNDO AKSI INI", type="primary", use_container_width=True):
            with st.spinner("Memulihkan data..."):
                batch_undo = db.batch()
                kolom_terdampak = []
                for kol, dat in st.session_state['undo_dict'].items():
                    doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{kol}")
                    batch_undo.set(doc_ref, {'data': dat})
                    kolom_terdampak.append(kol)
                batch_undo.commit()
                
                st.session_state['action_status'] = 'UNDONE'
                catat_log_mutasi("PEMBATALAN (UNDO)", ",".join(kolom_terdampak), "ALL", "Koreksi Sistem", "-", "-", 0, 0, 0, f"Membatalkan: {st.session_state['action_msg']}")
                st.rerun()

elif st.session_state['action_status'] == 'DONE_NO_UNDO':
    with st.container():
        st.markdown(css_floating_box.replace("{border_color}", "#3b82f6"), unsafe_allow_html=True)
        st.success(f"✅ **Sukses:** {st.session_state['action_msg']}")
        if st.button("Tutup Pesan Ini"):
            st.session_state['action_status'] = None
            st.rerun()

elif st.session_state['action_status'] == 'UNDONE':
    with st.container():
        st.markdown(css_floating_box.replace("{border_color}", "#f59e0b"), unsafe_allow_html=True)
        notif_c1, notif_c2 = st.columns([4, 1])
        notif_c1.warning("⏪ **Berhasil dibatalkan.** Data telah kembali seperti semula.")
        if notif_c2.button("Tutup Pesan", use_container_width=True):
            st.session_state['action_status'] = None
            st.rerun()

# ==========================================================
# 5. TABS MENU UTAMA
# ==========================================================
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📥 1. Inbound", "📤 2. Outbound", "⚙️ 3. Audit & Backup", 
    "📊 4. Log Mutasi", "🚀 5. Sync CSV", "📅 6. Jadwal", "🔄 7. Relokasi Rak"
])

# ----------------------------------------------------------
# TAB 1: INBOUND
# ----------------------------------------------------------
with tab1:
    st.header("Input Barang Masuk")
    
    # Inisialisasi state untuk form reset
    if 'in_lokasi_dicari' not in st.session_state:
        st.session_state.update({'in_lokasi_dicari': False, 'in_k_pilih': "A", 'in_b_pilih': 1})
    if 'in_lot_input' not in st.session_state: st.session_state['in_lot_input'] = ""
    if 'in_qty_input' not in st.session_state: st.session_state['in_qty_input'] = 1
    if 'in_pecahan_input' not in st.session_state: st.session_state['in_pecahan_input'] = 0
    if 'in_eceran_input' not in st.session_state: st.session_state['in_eceran_input'] = 0

    col_in1, col_in2 = st.columns([1, 2])
    with col_in1:
        st.markdown("**1. Pilih Lokasi Rak Tujuan**")
        in_kolom_label = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G", "S (Staging)"], key="in_kolom")
        in_kolom = in_kolom_label.split(" ")[0]
        in_baris = st.number_input("Nomor Baris (Inbound)", min_value=1, max_value=28, value=1)
        
        if st.button("🔍 Cek Status Rak", use_container_width=True):
            st.session_state.update({'in_lokasi_dicari': True, 'in_k_pilih': in_kolom, 'in_b_pilih': in_baris})
            
    with col_in2:
        if st.session_state['in_lokasi_dicari']:
            k_pilih, b_pilih = st.session_state['in_k_pilih'], st.session_state['in_b_pilih']
            doc_ref_in = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
            doc_in = doc_ref_in.get()
            all_data_raw = doc_in.to_dict().get('data', []) if doc_in.exists else []
            
            baris_data_preview = [d for d in all_data_raw if float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
            is_all_box_existing = all(str(d.get('Part Number', '')).strip().upper().endswith(('D', 'G')) for d in baris_data_preview) if baris_data_preview else False
            
            max_slot_display = 999 if k_pilih == 'S' else (32 if (is_all_box_existing and b_pilih <= 16) else (46 if is_all_box_existing else (31 if b_pilih <= 16 else 44)))
            
            current_qty = len(baris_data_preview)
            sisa_ruang = max_slot_display - current_qty
            
            st.markdown(f"**📦 Status Rak {k_pilih}-{b_pilih} | Terisi: `{current_qty}` / `{max_slot_display}` (Sisa Ruang: `{sisa_ruang}` Slot)**")
            
            df_baris = pd.DataFrame(baris_data_preview)
            if not df_baris.empty:
                df_baris['Pallet Ke'] = pd.to_numeric(df_baris['Pallet Ke'], errors='coerce')
                df_baris = df_baris.sort_values('Pallet Ke', ascending=True)
                kolom_tersedia = [c for c in ['Pallet Ke', 'Part Number', 'Nama Produk', 'Lot Number', 'Keterangan'] if c in df_baris.columns]
                st.dataframe(df_baris[kolom_tersedia], hide_index=True, height=150)
            else:
                st.info("Rak ini masih kosong. Siap diisi penuh!")
                
            st.divider()
            st.markdown(f"**2. Eksekusi Inbound (Tujuan: {k_pilih}-{b_pilih})**")
            
            df_master = load_master_produk()
            daftar_produk = df_master['Nama Barang'].dropna().astype(str).unique().tolist() if not df_master.empty else []
            pilihan_barang = st.selectbox("Pilih Produk", ["-- Ketik Manual --"] + daftar_produk)
            
            if pilihan_barang != "-- Ketik Manual --" and not df_master.empty:
                in_produk = pilihan_barang
                match_part = df_master[df_master['Nama Barang'] == pilihan_barang]['Part Number'].values
                in_part = st.text_input("Part Number (Auto-fill)", value=str(match_part[0]) if len(match_part) > 0 else "", disabled=True)
            else:
                in_produk = st.text_input("Nama Produk (Ketik Manual)")
                in_part = st.text_input("Part Number (Ketik Manual)")
                
            in_lot = st.text_input("No. Lot Produksi / Mesin Shift", placeholder="WAJIB DIISI", key="in_lot_input")
            
            col_qty1, col_qty2, col_qty3 = st.columns(3)
            with col_qty1: in_qty = st.number_input("Pallet Masuk UTUH", min_value=0, step=1, key="in_qty_input")
            with col_qty2: in_pecahan = st.number_input("Sisa Pecahan (Dus/Sak)", min_value=0, step=1, key="in_pecahan_input")
            with col_qty3: in_eceran = st.number_input("Eceran (Pcs)", min_value=0, step=1, key="in_eceran_input")
            
            in_dari_depan = st.checkbox("📥 Masukkan dari DEPAN Rak (Push-back)")
                
            if st.button("🚀 Simpan Inbound", type="primary", use_container_width=True):
                in_part_clean = in_part.strip().upper() if in_part else ""
                in_produk_clean = in_produk.strip() if in_produk else ""
                in_lot_final = in_lot.strip()

                if not in_lot_final or in_lot_final == "-":
                    st.error("⚠️ No. Lot bersifat WAJIB DIISI!")
                elif not in_produk_clean or not in_part_clean:
                    st.error("⚠️ Nama Produk dan Part Number wajib diisi!")
                elif in_qty == 0 and in_pecahan == 0 and in_eceran == 0:
                    st.error("⚠️ Masukkan minimal 1 Qty!")
                else:
                    with st.spinner("Menyusun data ke dalam rak..."):
                        all_data = [d for d in all_data_raw if not (float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() == '')]
                        baris_data_actual = [d for d in all_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
                        
                        is_all_box_actual = in_part_clean.endswith(('D', 'G'))
                        existing_fraction_row = None
                        
                        for d in baris_data_actual:
                            pn = str(d.get('Part Number', '')).strip().upper()
                            if pn and not pn.endswith(('D', 'G')): is_all_box_actual = False
                            if not in_dari_depan and pn == in_part_clean and str(d.get('Lot Number', '')).strip() == in_lot_final:
                                ket = str(d.get('Keterangan', ''))
                                if 'Pecahan' in ket or 'Eceran' in ket: existing_fraction_row = d
                                    
                        max_slot_actual = 999 if k_pilih == 'S' else (32 if (is_all_box_actual and b_pilih <= 16) else (46 if is_all_box_actual else (31 if b_pilih <= 16 else 44)))
                        
                        slot_tambahan_in = 0
                        if (in_pecahan > 0 or in_eceran > 0) and not existing_fraction_row: slot_tambahan_in = 1
                        total_inbound_slots = in_qty + slot_tambahan_in
                        
                        new_records = []
                        
                        if in_dari_depan and baris_data_actual:
                            for d in all_data:
                                if float(d.get('Baris', 0) or 0) == float(b_pilih):
                                    d['Pallet Ke'] = float(d.get('Pallet Ke', 0)) + total_inbound_slots
                            next_pallet = 1
                            filled_pallets = []
                        else:
                            filled_pallets = [int(float(d.get('Pallet Ke', 0) or 0)) for d in baris_data_actual if pd.notna(d.get('Pallet Ke'))]
                            next_pallet = max(filled_pallets) + 1 if filled_pallets else 1
                            
                        for _ in range(int(in_qty)):
                            while not in_dari_depan and next_pallet in filled_pallets: next_pallet += 1
                            is_bayangan = next_pallet > max_slot_actual
                            new_records.append({
                                'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': float(next_pallet),
                                'Part Number': in_part_clean, 'Lot Number': in_lot_final, 'Nama Produk': in_produk_clean,
                                'Status': 'Terisi', 'Keterangan': '', 'Catatan': "[BAYANGAN - OVERCAPACITY]" if is_bayangan else ""
                            })
                            filled_pallets.append(next_pallet)
                            next_pallet += 1
                            
                        if in_pecahan > 0 or in_eceran > 0:
                            if existing_fraction_row and not in_dari_depan:
                                ket_str = str(existing_fraction_row.get('Keterangan', '')).lower()
                                ex_pecahan, ex_eceran = 0, 0
                                match_pec = re.search(r'pecahan:\s*(\d+)', ket_str)
                                if match_pec: ex_pecahan = int(match_pec.group(1))
                                else:
                                    nums = re.findall(r'\d+', ket_str)
                                    if nums and 'pecahan' in ket_str: ex_pecahan = int(nums[0])
                                match_ec = re.search(r'eceran:\s*(\d+)', ket_str)
                                if match_ec: ex_eceran = int(match_ec.group(1))
                                
                                new_pec, new_ec = ex_pecahan + in_pecahan, ex_eceran + in_eceran
                                ket_parts = []
                                if new_pec > 0: ket_parts.append(f"Pecahan: {new_pec} qty")
                                if new_ec > 0: ket_parts.append(f"Eceran: {new_ec} pcs")
                                existing_fraction_row['Keterangan'] = " | ".join(ket_parts)
                            else:
                                while not in_dari_depan and next_pallet in filled_pallets: next_pallet += 1
                                ket_parts = []
                                if in_pecahan > 0: ket_parts.append(f"Pecahan: {in_pecahan} qty")
                                if in_eceran > 0: ket_parts.append(f"Eceran: {in_eceran} pcs")
                                new_records.append({
                                    'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': float(next_pallet),
                                    'Part Number': in_part_clean, 'Lot Number': in_lot_final, 'Nama Produk': in_produk_clean,
                                    'Status': 'Terisi', 'Keterangan': " | ".join(ket_parts), 'Catatan': "[BAYANGAN - OVERCAPACITY]" if next_pallet > max_slot_actual else ""
                                })
                                
                        all_data.extend(new_records)
                        
                        st.session_state.update({
                            'undo_dict': {k_pilih: all_data_raw}, 
                            'action_msg': f"Inbound {total_inbound_slots} Slot '{in_produk_clean}' ke Rak {k_pilih}-{b_pilih} {'(Push-back)' if in_dari_depan else ''}", 
                            'action_status': 'DONE'
                        })
                        doc_ref_in.set({'data': all_data})
                        catat_log_mutasi("INBOUND", k_pilih, b_pilih, in_produk_clean, in_part_clean, in_lot_final, in_qty, in_pecahan, in_eceran)
                        
                        # Reset nilai form sebelum Rerun
                        st.session_state['in_lot_input'] = ""
                        st.session_state['in_qty_input'] = 1
                        st.session_state['in_pecahan_input'] = 0
                        st.session_state['in_eceran_input'] = 0
                        st.rerun()

# ----------------------------------------------------------
# TAB 2: OUTBOUND MULTI-LOT
# ----------------------------------------------------------
with tab2:
    st.header("Penyesuaian Sisa Stok (Outbound Massal)")
    st.caption("Ubah angka sisa fisik pada tabel, lalu klik Simpan untuk eksekusi Outbound.")
    
    if 'out_lokasi_dicari' not in st.session_state: 
        st.session_state.update({'out_lokasi_dicari': False, 'out_k_pilih': "A", 'out_b_pilih': 1})

    st.markdown("**1. Pilih Lokasi Rak**")
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1: 
        out_kolom_label = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G", "S (Staging)"], key="out_kolom_2")
        out_kolom = out_kolom_label.split(" ")[0]
    with c2: 
        out_baris = st.number_input("Nomor Baris", min_value=1, max_value=28, value=1, key="out_baris_2")
    with c3:
        st.write("") 
        st.write("")
        if st.button("🔍 Cari Data Rak", use_container_width=True): 
            st.session_state.update({'out_lokasi_dicari': True, 'out_k_pilih': out_kolom, 'out_b_pilih': out_baris})
            
    st.divider()
    
    if st.session_state['out_lokasi_dicari']:
        k_pilih, b_pilih = st.session_state['out_k_pilih'], st.session_state['out_b_pilih']
        doc_ref_out = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
        doc_out = doc_ref_out.get()
        all_out_data = doc_out.to_dict().get('data', []) if doc_out.exists else []
        baris_out_data = [d for d in all_out_data if float(d.get('Baris', 0) or 0) == float(b_pilih) and str(d.get('Part Number', '')).strip() != '']
        
        if baris_out_data:
            df_out = pd.DataFrame(baris_out_data)
            df_out['Pallet Ke'] = pd.to_numeric(df_out['Pallet Ke'], errors='coerce')
            
            df_out['Catatan_Clean'] = df_out['Catatan'].fillna("").astype(str).replace('nan', '')
            df_out['Group_Key'] = df_out['Lot Number'].astype(str) + "_" + df_out['Nama Produk'].astype(str) + "_" + df_out['Catatan_Clean'].astype(str)
            
            rekap_lot = []
            for _, row in df_out.drop_duplicates(subset=['Group_Key']).iterrows():
                g_key = str(row['Group_Key'])
                l = str(row['Lot Number'])
                nama_prod = str(row['Nama Produk'])
                part_num = str(row['Part Number'])
                c_catatan = str(row['Catatan_Clean'])
                
                df_lot = df_out[df_out['Group_Key'] == g_key]
                
                jml_utuh_awal = df_lot[~df_lot['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
                pecahan_rows = df_lot[df_lot['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)]
                pecahan_awal, eceran_awal = 0, 0
                
                for _, r in pecahan_rows.iterrows():
                    ks = str(r['Keterangan']).lower()
                    m1 = re.search(r'pecahan:\s*(\d+)', ks)
                    if m1: pecahan_awal += int(m1.group(1))
                    else:
                        ns = re.findall(r'\d+', ks)
                        if ns and 'pecahan' in ks: pecahan_awal += int(ns[0])
                    m2 = re.search(r'eceran:\s*(\d+)', ks)
                    if m2: eceran_awal += int(m2.group(1))
                
                rekap_lot.append({
                    'Group_Key': g_key,
                    'Produk': nama_prod, 'Part Number': part_num, 'Lot Number': l, 'Status / Asal': c_catatan,
                    'Awal Utuh': int(jml_utuh_awal), 'Awal Dus': int(pecahan_awal), 'Awal Pcs': int(eceran_awal),
                    'Sisa Utuh Baru': int(jml_utuh_awal), 'Sisa Dus Baru': int(pecahan_awal), 'Sisa Pcs Baru': int(eceran_awal),
                    'Catatan Tambahan': ""
                })
            
            df_rekap = pd.DataFrame(rekap_lot)
            st.markdown(f"**2. Edit Sisa Fisik Rak {k_pilih}-{b_pilih} (Tampilan Lebar)**")
            
            config_kolom = {
                'Group_Key': None, 
                'Produk': st.column_config.TextColumn("Produk", disabled=True),
                'Part Number': st.column_config.TextColumn("Part", disabled=True),
                'Lot Number': st.column_config.TextColumn("Lot", disabled=True),
                'Status / Asal': st.column_config.TextColumn("Status / Asal", disabled=True),
                'Awal Utuh': st.column_config.NumberColumn("Sistem Utuh", disabled=True),
                'Awal Dus': st.column_config.NumberColumn("Sistem Dus", disabled=True),
                'Awal Pcs': st.column_config.NumberColumn("Sistem Pcs", disabled=True),
                'Sisa Utuh Baru': st.column_config.NumberColumn("SISA UTUH BARU", min_value=0, required=True),
                'Sisa Dus Baru': st.column_config.NumberColumn("SISA DUS BARU", min_value=0, required=True),
                'Sisa Pcs Baru': st.column_config.NumberColumn("SISA PCS BARU", min_value=0, required=True),
                'Catatan Tambahan': st.column_config.TextColumn("Catatan Tambahan", max_chars=100)
            }
            
            edited_df = st.data_editor(df_rekap, column_config=config_kolom, hide_index=True, use_container_width=True, num_rows="fixed")
            
            if st.button("🚜 Simpan Penyesuaian Massal (OUTBOUND)", type="primary", use_container_width=True):
                has_changes = False
                error_msg = ""
                
                data_rak_ini = [d for d in all_out_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
                data_rak_lain = [d for d in all_out_data if float(d.get('Baris', 0) or 0) != float(b_pilih)]
                
                def match_g_key(d, target_key):
                    c = str(d.get('Catatan', '')).replace('nan', '')
                    return (str(d.get('Lot Number','')) + "_" + str(d.get('Nama Produk','')) + "_" + c) == target_key

                for index, row in edited_df.iterrows():
                    g_key = str(row['Group_Key'])
                    l = str(row['Lot Number'])
                    sisa_utuh = int(row['Sisa Utuh Baru'])
                    sisa_dus = int(row['Sisa Dus Baru'])
                    sisa_pcs = int(row['Sisa Pcs Baru'])
                    catatan_out = str(row['Catatan Tambahan'])
                    
                    awal_utuh = int(row['Awal Utuh'])
                    awal_dus = int(row['Awal Dus'])
                    awal_pcs = int(row['Awal Pcs'])
                    
                    if sisa_utuh != awal_utuh or sisa_dus != awal_dus or sisa_pcs != awal_pcs or catatan_out != "":
                        has_changes = True
                        df_lot = pd.DataFrame([d for d in data_rak_ini if match_g_key(d, g_key)])
                        
                        if not df_lot.empty:
                            df_lot['Pallet Ke'] = pd.to_numeric(df_lot['Pallet Ke'])
                            df_lot = df_lot.sort_values('Pallet Ke', ascending=False)
                            
                            slot_tambahan = 1 if (sisa_dus > 0 or sisa_pcs > 0) else 0
                            total_slot_dibutuhkan = sisa_utuh + slot_tambahan
                            
                            if total_slot_dibutuhkan > len(df_lot):
                                error_msg = f"❌ Sisa akhir Lot {l} melebihi kapasitas awal."
                                break
                            
                            out_qty = len(df_lot) - total_slot_dibutuhkan
                            pallets_to_remove = df_lot['Pallet Ke'].head(out_qty).tolist() if out_qty > 0 else []
                            
                            updated_rak_ini = []
                            for d in data_rak_ini:
                                p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                                if match_g_key(d, g_key):
                                    if p_val in pallets_to_remove: continue 
                                    
                                    c_lama = str(d.get('Catatan', '')).replace('nan', '')
                                    if catatan_out != "" and catatan_out not in c_lama:
                                        d['Catatan'] = (c_lama + " | " + catatan_out).strip(' |')
                                        
                                    sisa_pallets = df_lot[~df_lot['Pallet Ke'].isin(pallets_to_remove)]
                                    if not sisa_pallets.empty and p_val == sisa_pallets['Pallet Ke'].max():
                                        if sisa_dus > 0 or sisa_pcs > 0:
                                            kp = []
                                            if sisa_dus > 0: kp.append(f"Pecahan: {sisa_dus} qty")
                                            if sisa_pcs > 0: kp.append(f"Eceran: {sisa_pcs} pcs")
                                            d['Keterangan'] = " | ".join(kp)
                                        else: d['Keterangan'] = ""
                                    else:
                                        if 'Pecahan' in str(d.get('Keterangan', '')) or 'Eceran' in str(d.get('Keterangan', '')): 
                                            d['Keterangan'] = ""
                                updated_rak_ini.append(d)
                            data_rak_ini = updated_rak_ini
                            
                            keluar_utuh = awal_utuh - sisa_utuh
                            keluar_pecahan = awal_dus - sisa_dus
                            keluar_eceran = awal_pcs - sisa_pcs
                            catat_log_mutasi("OUTBOUND (Massal)", k_pilih, b_pilih, str(row['Produk']), str(row['Part Number']), l, keluar_utuh, keluar_pecahan, keluar_eceran, catatan_out)

                if error_msg: st.error(error_msg)
                elif has_changes:
                    data_rak_ini = sorted([d for d in data_rak_ini if float(d.get('Baris', 0) or 0) == float(b_pilih)], key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                    for i, d in enumerate(data_rak_ini, 1): d['Pallet Ke'] = float(i)
                    
                    doc_ref_out.set({'data': data_rak_lain + data_rak_ini})
                    
                    st.session_state.update({
                        'undo_dict': {k_pilih: all_out_data},
                        'action_msg': f"Penyesuaian Outbound Massal di Rak {k_pilih}-{b_pilih}",
                        'action_status': 'DONE'
                    })
                    st.rerun()
                else: st.info("Tidak ada perubahan angka pada tabel. Outbound dibatalkan.")
        else: st.info("Rak Kosong.")

# ----------------------------------------------------------
# TAB 3: AUDIT STOK & BACKUP
# ----------------------------------------------------------
with tab3:
    st.header("⚙️ Audit Stok & Ekspor Data")
    st.subheader("🔧 Maintenance: Re-Indexing Global")
    st.write("Mengurutkan ulang urutan palet dari nomor 1 di seluruh gudang.")
    
    if 'last_reindex' not in st.session_state: st.session_state['last_reindex'] = "Belum pernah"
    st.caption(f"🕒 Terakhir Re-Index: **{st.session_state['last_reindex']}**")
    
    if st.button("⚙️ Jalankan Re-Indexing Semua Rak Sekarang", type="secondary"):
        with st.spinner("Memperbaiki urutan index pallet..."):
            try:
                docs = db.collection(NAMA_DATABASE).stream()
                batch = db.batch()
                total_koreksi = 0
                
                for doc in docs:
                    kolom_id = doc.id
                    doc_data = doc.to_dict().get('data', [])
                    if not doc_data: continue
                    
                    df_col = pd.DataFrame(doc_data)
                    if 'Baris' not in df_col.columns or 'Pallet Ke' not in df_col.columns: continue
                    
                    df_col['Baris'] = pd.to_numeric(df_col['Baris'], errors='coerce')
                    df_col['Pallet Ke'] = pd.to_numeric(df_col['Pallet Ke'], errors='coerce')
                    new_doc_data = []
                    
                    for baris, group in df_col.groupby('Baris'):
                        group = group.sort_values('Pallet Ke', ascending=True)
                        valid_group = group[~group['Part Number'].astype(str).str.strip().str.lower().isin(["", "nan", "none", "<na>"])]
                        is_all_box = False
                        if not valid_group.empty:
                            is_all_box = bool(valid_group['Part Number'].astype(str).str.upper().str.endswith(('D', 'G')).all())
                            
                        max_slot_actual = 999 if kolom_id == "Kolom_S" else (int(32 if baris <= 16 else 46) if is_all_box else int(31 if baris <= 16 else 44))
                        
                        for i, (idx, row) in enumerate(group.iterrows(), start=1):
                            row_dict = row.to_dict()
                            row_dict['Pallet Ke'] = float(i)
                            catatan_lama = str(row_dict.get('Catatan', ''))
                            if i <= max_slot_actual and 'BAYANGAN' in catatan_lama.upper():
                                row_dict['Catatan'] = catatan_lama.replace("[BAYANGAN - OVERCAPACITY]", "").strip()
                            elif i > max_slot_actual and 'BAYANGAN' not in catatan_lama.upper():
                                row_dict['Catatan'] = ("[BAYANGAN - OVERCAPACITY] " + catatan_lama).strip()
                            new_doc_data.append(row_dict)
                    
                    doc_ref = db.collection(NAMA_DATABASE).document(kolom_id)
                    batch.set(doc_ref, {'data': new_doc_data})
                    total_koreksi += 1
                
                batch.commit()
                st.session_state['last_reindex'] = (datetime.utcnow() + timedelta(hours=7)).strftime("%d-%m-%Y %H:%M:%S")
                st.balloons()
                st.session_state.update({'action_msg': f"Re-Indexing Selesai! {total_koreksi} Kolom diperbarui.", 'action_status': 'DONE_NO_UNDO'})
                st.rerun()
            except Exception as e: st.error(f"Gagal Re-Indexing: {e}")

    st.divider()
    st.subheader("📥 Tarik Laporan Audit (CSV Super)")
    if st.button("Racik Laporan Audit Stok Sekarang", type="primary"):
        with st.spinner("Membaca database..."):
            docs = db.collection(NAMA_DATABASE).stream()
            semua_data = []
            for doc in docs:
                doc_data = doc.to_dict()
                if 'data' in doc_data: semua_data.extend(doc_data['data'])
                    
            if semua_data:
                df_export = pd.DataFrame(semua_data)
                df_master = load_master_produk()
                
                qty_utuh_list, qty_dus_list, qty_pcs_list, grand_total_list = [], [], [], []
                for idx, row in df_export.iterrows():
                    if pd.isna(row.get('Part Number')) or str(row.get('Part Number')).strip() == '':
                        qty_utuh_list.append(0); qty_dus_list.append(0); qty_pcs_list.append(0); grand_total_list.append(0)
                        continue
                        
                    ket = str(row.get('Keterangan', '')).lower()
                    is_pecahan = 'pecahan' in ket or 'eceran' in ket
                    q_utuh = 0 if is_pecahan else 1
                    q_dus, q_pcs = 0, 0
                    
                    if is_pecahan:
                        match_pec = re.search(r'pecahan:\s*(\d+)', ket)
                        if match_pec: q_dus = int(match_pec.group(1))
                        else:
                            nums = re.findall(r'\d+', ket)
                            if nums and 'pecahan' in ket: q_dus = int(nums[0])
                        match_ec = re.search(r'eceran:\s*(\d+)', ket)
                        if match_ec: q_pcs = int(match_ec.group(1))
                        
                    qty_utuh_list.append(q_utuh)
                    qty_dus_list.append(q_dus)
                    qty_pcs_list.append(q_pcs)
                    
                    part_num_clean = str(row.get('Part Number', '')).strip().upper()
                    isi_pallet, pcs_per_satuan = 0, 0
                    if not df_master.empty and part_num_clean in df_master['Part Number Clean'].values:
                        m_row = df_master[df_master['Part Number Clean'] == part_num_clean].iloc[0]
                        isi_pallet = pd.to_numeric(m_row.get('Isi 1 Pallet (pcs)', 0), errors='coerce')
                        isi_pallet = 0 if pd.isna(isi_pallet) else isi_pallet
                        val_satuan = pd.to_numeric(m_row.get('Box / Pallet' if part_num_clean.endswith('D') else 'Sac / Pallet', 0), errors='coerce')
                        pcs_per_satuan = 0 if pd.isna(val_satuan) else val_satuan
                        
                    grand_total_list.append((q_utuh * isi_pallet) + (q_dus * pcs_per_satuan) + q_pcs)
                    
                df_export['Qty Utuh'] = qty_utuh_list
                df_export['Qty Dus/Sak'] = qty_dus_list
                df_export['Qty Pcs Eceran'] = qty_pcs_list
                df_export['Grand Total Pcs'] = grand_total_list
                
                kolom_harapan = ['Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk', 'Keterangan', 'Catatan', 'Status', 'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs']
                for c in kolom_harapan:
                    if c not in df_export.columns: df_export[c] = ""
                        
                for col in ['Baris', 'Pallet Ke', 'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs']:
                    df_export[col] = pd.to_numeric(df_export[col], errors='coerce').fillna(0).astype(int)
                    if col in ['Baris', 'Pallet Ke']: df_export[col] = df_export[col].replace(0, "")
                        
                df_export = df_export[kolom_harapan]
                csv_buffer = io.StringIO()
                df_export.to_csv(csv_buffer, index=False, sep=";")
                st.success("✅ CSV Super berhasil diracik!")
                st.download_button(label="⬇️ Unduh CSV Super", data=csv_buffer.getvalue(), file_name=f"Audit_Stok_WMS_{(datetime.utcnow() + timedelta(hours=7)).strftime('%Y%m%d')}.csv", mime="text/csv")
            else: st.warning("Database kosong.")

# ----------------------------------------------------------
# TAB 4: LAPORAN MUTASI HARIAN
# ----------------------------------------------------------
with tab4:
    st.header("📊 Laporan Mutasi Harian (24 Jam Terakhir)")
    if st.button("Racik Laporan Mutasi Sekarang", type="primary"):
        with st.spinner("Menarik data log mutasi..."):
            try:
                waktu_24_jam_lalu_utc = datetime.utcnow() - timedelta(hours=24)
                log_docs = db.collection(DB_LOG_MUTASI).where("Timestamp_System", ">=", waktu_24_jam_lalu_utc).order_by("Timestamp_System", direction=firestore.Query.DESCENDING).stream()
                log_list = [d.to_dict() for d in log_docs]
                    
                if log_list:
                    df_log = pd.DataFrame(log_list)
                    avail_cols = [c for c in ["Waktu Transaksi", "Jenis Transaksi", "Lokasi Rak", "Nama Produk", "No Lot", "Mutasi Pallet", "Mutasi Pecahan (Dus/Sak)", "Mutasi Eceran (Pcs)", "Part Number", "Keterangan"] if c in df_log.columns]
                    df_log = df_log[avail_cols]
                    
                    csv_log_buffer = io.StringIO()
                    df_log.to_csv(csv_log_buffer, index=False, sep=";")
                    st.success("✅ Laporan Mutasi berhasil diracik!")
                    st.download_button(label="⬇️ Unduh Laporan Mutasi", data=csv_log_buffer.getvalue(), file_name=f"Mutasi_WMS_{(datetime.utcnow() + timedelta(hours=7)).strftime('%Y%m%d')}.csv", mime="text/csv")
                else: st.info("Belum ada mutasi dalam 24 jam terakhir.")
            except Exception as e: st.error(f"Gagal memuat log mutasi: {e}")

# ----------------------------------------------------------
# TAB 5: SYNC CSV (RESTORE)
# ----------------------------------------------------------
with tab5:
    st.header("🚀 Restore Data Database dari CSV")
    st.warning("⚠️ **PERINGATAN:** Fitur ini berfungsi sebagai *Disaster Recovery*. Jika dieksekusi, sistem akan **MENGHAPUS SELURUH ISI RAK** dan menimpanya dengan isi dari file CSV yang diunggah. Pastikan format CSV sesuai dengan output dari 'Tarik Laporan Audit'.")
    
    uploaded_file = st.file_uploader("Upload File Backup CSV (.csv)", type=['csv'])
    if uploaded_file is not None:
        if st.button("🚨 SAYA YAKIN, HAPUS DATABASE & TIMPA DENGAN CSV INI", type="primary", use_container_width=True):
            with st.spinner("Membaca CSV dan menimpa database..."):
                try:
                    try: df_sync = pd.read_csv(uploaded_file, sep=";")
                    except: df_sync = pd.read_csv(uploaded_file, sep=",")
                    
                    req_cols = ['Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk']
                    if not all(col in df_sync.columns for col in req_cols):
                        st.error("❌ Format CSV tidak valid! Kolom wajib tidak ditemukan.")
                    else:
                        batch = db.batch()
                        for kol in ["A", "B", "C", "D", "E", "F", "G", "S"]:
                            df_kol = df_sync[df_sync['Kolom'] == kol]
                            new_data = []
                            if not df_kol.empty:
                                df_kol = df_kol.sort_values(by=['Baris', 'Pallet Ke'])
                                for _, row in df_kol.iterrows():
                                    if pd.isna(row['Part Number']) or str(row['Part Number']).strip() == "":
                                        continue
                                    record = {
                                        'Kolom': kol, 'Baris': float(row['Baris']), 'Pallet Ke': float(row['Pallet Ke']),
                                        'Part Number': str(row['Part Number']).strip(), 'Lot Number': str(row['Lot Number']).strip(),
                                        'Nama Produk': str(row['Nama Produk']).strip(),
                                        'Keterangan': str(row.get('Keterangan', '')).strip(),
                                        'Catatan': str(row.get('Catatan', '')).strip(),
                                        'Status': 'Terisi'
                                    }
                                    if record['Keterangan'] == 'nan': record['Keterangan'] = ""
                                    if record['Catatan'] == 'nan': record['Catatan'] = ""
                                    new_data.append(record)
                            
                            doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{kol}")
                            batch.set(doc_ref, {'data': new_data})
                        
                        batch.commit()
                        catat_log_mutasi("RESTORE CSV", "ALL", "ALL", "Sistem Database", "-", "-", 0, 0, 0, "Hard Reset via CSV Upload")
                        
                        st.session_state.update({'action_msg': "Database berhasil di-restore dari file CSV!", 'action_status': 'DONE_NO_UNDO'})
                        st.rerun()
                except Exception as e:
                    st.error(f"Terjadi kesalahan saat memproses CSV: {e}")

# ----------------------------------------------------------
# TAB 6: PENJADWALAN KIRIM (BOOKING)
# ----------------------------------------------------------
with tab6:
    st.header("📅 Penjadwalan Pengiriman (Booking Pallet)")
    if 'book_lokasi_dicari' not in st.session_state: st.session_state.update({'book_lokasi_dicari': False, 'book_k_pilih': "A", 'book_b_pilih': 1})

    col_b1, col_b2 = st.columns([1, 2])
    with col_b1:
        st.markdown("**1. Pilih Lokasi Rak**")
        book_kolom_label = st.selectbox("Lokasi Kolom", ["A", "B", "C", "D", "E", "F", "G", "S (Staging)"], key="book_kolom_3")
        book_kolom = book_kolom_label.split(" ")[0]
        book_baris = st.number_input("Nomor Baris (Booking)", min_value=1, max_value=28, value=1, key="book_baris_3")
        if st.button("🔍 Cari Data Rak (Booking)", use_container_width=True): st.session_state.update({'book_lokasi_dicari': True, 'book_k_pilih': book_kolom, 'book_b_pilih': book_baris})
            
    with col_b2:
        if st.session_state['book_lokasi_dicari']:
            k_pilih, b_pilih = st.session_state['book_k_pilih'], st.session_state['book_b_pilih']
            doc_ref_book = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
            doc_book = doc_ref_book.get()
            all_book_data = doc_book.to_dict().get('data', []) if doc_book.exists else []
            baris_book_data = [d for d in all_book_data if float(d.get('Baris', 0) or 0) == float(b_pilih)]
            
            if baris_book_data:
                df_book = pd.DataFrame(baris_book_data)
                df_book['Pallet Ke'] = pd.to_numeric(df_book['Pallet Ke'], errors='coerce')
                df_book = df_book[df_book['Part Number'].astype(str).str.strip() != ""]
                
                if not df_book.empty and 'Lot Number' in df_book.columns:
                    tersedia_lot = []
                    mapping_lot = {}
                    
                    df_book['Catatan'] = df_book['Catatan'].fillna("").astype(str).replace('nan', '')
                    
                    for _, row in df_book.drop_duplicates(subset=['Lot Number', 'Nama Produk', 'Catatan']).iterrows():
                        l = str(row['Lot Number'])
                        c = str(row['Catatan'])
                        lb = f"{str(row['Nama Produk'])} (Lot: {l})"
                        if c: lb += f" - {c}"
                        tersedia_lot.append(lb)
                        mapping_lot[lb] = (l, c)
                        
                    st.markdown(f"**2. Pilih Produk & Lot untuk di-Booking di {k_pilih}-{b_pilih}**")
                    pilihan_label = st.selectbox("🎯 Pilih Produk:", tersedia_lot, key="book_lot")
                    pilih_lot, pilih_cat = mapping_lot[pilihan_label]
                    
                    df_lot = df_book[(df_book['Lot Number'].astype(str) == pilih_lot) & (df_book['Catatan'].astype(str) == pilih_cat)].sort_values('Pallet Ke', ascending=True)
                    df_belum_book = df_lot[~df_lot['Catatan'].astype(str).str.contains('BOOKED', case=False, na=False)]
                    qty_tersedia = len(df_belum_book)
                    
                    st.markdown(f"**📦 Tersedia untuk di-Booking: `{qty_tersedia}` Slot Fisik**")
                    kolom_tersedia = [c for c in ['Pallet Ke', 'Part Number', 'Lot Number', 'Catatan'] if c in df_lot.columns]
                    st.dataframe(df_lot[kolom_tersedia], hide_index=True, height=150)
                    
                    if qty_tersedia > 0:
                        book_qty = st.number_input("Jumlah Pallet UTUH yang akan di-Booking", min_value=1, max_value=qty_tersedia, step=1, value=qty_tersedia)
                        book_do = st.text_input("📝 Terjadwal Untuk (Cth: DO-123 / Tgl 28)")
                        
                        if st.button("🔒 Kunci / Booking Pallet Sekarang", type="primary", use_container_width=True):
                            if book_do.strip() == "": st.error("❌ Keterangan jadwal wajib diisi!")
                            else:
                                with st.spinner("Menyematkan label booking..."):
                                    pallets_to_book = df_belum_book['Pallet Ke'].head(book_qty).tolist()
                                    new_all_book_data = []
                                    for d in all_book_data:
                                        b_val = float(d.get('Baris', 0) or 0)
                                        p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                                        if b_val == float(b_pilih) and p_val in pallets_to_book:
                                            catatan_lama = str(d.get('Catatan', '')).replace('nan', '')
                                            d['Catatan'] = (f"[BOOKED: {book_do.strip()}] " + catatan_lama).strip()
                                        new_all_book_data.append(d)
                                        
                                    doc_ref_book.set({'data': new_all_book_data})
                                    st.session_state.update({
                                        'undo_dict': {k_pilih: all_book_data}, 
                                        'action_msg': f"Booking {book_qty} Pallet Lot '{pilih_lot}' untuk '{book_do}'", 
                                        'action_status': 'DONE'
                                    })
                                    st.rerun()
                    else: st.warning("⚠️ Semua pallet pada Lot ini sudah ter-booking.")
            else: st.info("Rak Kosong.")

# ----------------------------------------------------------
# TAB 7: RELOKASI RAK
# ----------------------------------------------------------
with tab7:
    st.header("🔄 Relokasi Pallet (Pindah Rak / Subsidi Silang)")
    st.caption("Pindahkan item antar rak secara presisi (Pallet, Dus, Pcs). Sangat cocok untuk tarik barang ke Staging Area.")
    
    col_r1, col_r2 = st.columns(2)
    with col_r1:
        st.markdown("**Rak ASAL (Sumber)**")
        r_asal_k_label = st.selectbox("Kolom Asal", ["A", "B", "C", "D", "E", "F", "G", "S (Staging)"], key="r_ak")
        r_asal_k = r_asal_k_label.split(" ")[0]
        r_asal_b = st.number_input("Baris Asal", min_value=1, max_value=28, value=1, key="r_ab")
    with col_r2:
        st.markdown("**Rak TUJUAN (Destinasi)**")
        r_tuj_k_label = st.selectbox("Kolom Tujuan", ["A", "B", "C", "D", "E", "F", "G", "S (Staging)"], key="r_tk")
        r_tuj_k = r_tuj_k_label.split(" ")[0]
        r_tuj_b = st.number_input("Baris Tujuan", min_value=1, max_value=28, value=1, key="r_tb")

    if st.button("🔍 Cari Data Rak Asal", type="primary", use_container_width=True):
        st.session_state.update({'relok_dicari': True, 'relok_ak': r_asal_k, 'relok_ab': r_asal_b, 'relok_tk': r_tuj_k, 'relok_tb': r_tuj_b})
        
    if st.session_state.get('relok_dicari'):
        ak, ab = st.session_state['relok_ak'], st.session_state['relok_ab']
        tk, tb = st.session_state['relok_tk'], st.session_state['relok_tb']
        
        doc_asal = db.collection(NAMA_DATABASE).document(f"Kolom_{ak}").get()
        all_asal_data = doc_asal.to_dict().get('data', []) if doc_asal.exists else []
        baris_asal_data = [d for d in all_asal_data if float(d.get('Baris', 0) or 0) == float(ab) and str(d.get('Part Number', '')).strip() != '']
        
        if baris_asal_data:
            df_asal = pd.DataFrame(baris_asal_data)
            df_asal['Pallet Ke'] = pd.to_numeric(df_asal['Pallet Ke'], errors='coerce')
            
            df_asal['Catatan'] = df_asal['Catatan'].fillna("").astype(str).replace('nan', '')
            df_asal['Keterangan'] = df_asal['Keterangan'].fillna("").astype(str).replace('nan', '')
            
            df_asal['Group_Key'] = df_asal['Lot Number'].astype(str) + "_" + df_asal['Nama Produk'].astype(str) + "_" + df_asal['Catatan'].astype(str)
            
            rekap_relok = []
            for _, row in df_asal.drop_duplicates(subset=['Group_Key']).iterrows():
                g_key = str(row['Group_Key'])
                l = str(row['Lot Number'])
                nama_prod = str(row['Nama Produk'])
                part_num = str(row['Part Number'])
                catatan = str(row['Catatan'])
                
                df_lot = df_asal[df_asal['Group_Key'] == g_key]
                jml_utuh_awal = df_lot[~df_lot['Keterangan'].str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
                
                pecahan_awal, eceran_awal = 0, 0
                pecahan_rows = df_lot[df_lot['Keterangan'].str.contains('Pecahan|Eceran', case=False, na=False)]
                for _, r in pecahan_rows.iterrows():
                    ks = str(r['Keterangan']).lower()
                    m1 = re.search(r'pecahan:\s*(\d+)', ks)
                    if m1: pecahan_awal += int(m1.group(1))
                    else:
                        ns = re.findall(r'\d+', ks)
                        if ns and 'pecahan' in ks: pecahan_awal += int(ns[0])
                    m2 = re.search(r'eceran:\s*(\d+)', ks)
                    if m2: eceran_awal += int(m2.group(1))
                    
                rekap_relok.append({
                    'Group_Key': g_key, 'Produk': nama_prod, 'Lot Number': l, 'Catatan': catatan, 'Part Number': part_num,
                    'Tersedia Utuh': int(jml_utuh_awal), 'Tersedia Dus': int(pecahan_awal), 'Tersedia Pcs': int(eceran_awal),
                    'PINDAH UTUH': 0, 'PINDAH DUS': 0, 'PINDAH PCS': 0
                })
                
            df_rekap_relok = pd.DataFrame(rekap_relok)
            
            st.divider()
            st.markdown(f"**2. Tentukan Jumlah yang Dipindah dari {ak}-{ab} ke {tk}-{tb}**")
            
            config_kolom = {
                'Group_Key': None, 'Part Number': None,
                'Produk': st.column_config.TextColumn("Produk", disabled=True),
                'Lot Number': st.column_config.TextColumn("Lot", disabled=True),
                'Catatan': st.column_config.TextColumn("Tag / Catatan", disabled=True),
                'Tersedia Utuh': st.column_config.NumberColumn("Stok Utuh", disabled=True),
                'Tersedia Dus': st.column_config.NumberColumn("Stok Dus", disabled=True),
                'Tersedia Pcs': st.column_config.NumberColumn("Stok Pcs", disabled=True),
                'PINDAH UTUH': st.column_config.NumberColumn("🚚 PINDAH UTUH", min_value=0),
                'PINDAH DUS': st.column_config.NumberColumn("📦 PINDAH DUS", min_value=0),
                'PINDAH PCS': st.column_config.NumberColumn("🧩 PINDAH PCS", min_value=0)
            }
            
            edited_relok = st.data_editor(df_rekap_relok, column_config=config_kolom, hide_index=True, use_container_width=True)
            
            if st.button("🚀 Eksekusi Pindah Rak", type="primary", use_container_width=True):
                if ak == tk and ab == tb:
                    st.error("❌ Rak asal dan tujuan tidak boleh sama!")
                else:
                    has_changes = False
                    error_msg = ""
                    
                    doc_tuj = db.collection(NAMA_DATABASE).document(f"Kolom_{tk}").get()
                    all_tuj_data = doc_tuj.to_dict().get('data', []) if doc_tuj.exists else []
                    
                    data_rak_asal = copy.deepcopy(all_asal_data)
                    data_rak_tujuan = copy.deepcopy(all_tuj_data)
                    
                    undo_dict_setup = {ak: all_asal_data}
                    if ak != tk: undo_dict_setup[tk] = all_tuj_data
                        
                    def match_g_key(d, target_key):
                        c = str(d.get('Catatan', '')).replace('nan', '')
                        return (str(d.get('Lot Number','')) + "_" + str(d.get('Nama Produk','')) + "_" + c) == target_key

                    for index, row in edited_relok.iterrows():
                        pindah_utuh = int(row['PINDAH UTUH'])
                        pindah_dus = int(row['PINDAH DUS'])
                        pindah_pcs = int(row['PINDAH PCS'])
                        
                        if pindah_utuh == 0 and pindah_dus == 0 and pindah_pcs == 0: continue
                            
                        g_key = str(row['Group_Key'])
                        l = str(row['Lot Number'])
                        nama_prod = str(row['Produk'])
                        part_num = str(row['Part Number'])
                        catatan_awal = str(row['Catatan'])
                        
                        awal_utuh, awal_dus, awal_pcs = int(row['Tersedia Utuh']), int(row['Tersedia Dus']), int(row['Tersedia Pcs'])
                        sisa_utuh = awal_utuh - pindah_utuh
                        sisa_dus = awal_dus - pindah_dus
                        sisa_pcs = awal_pcs - pindah_pcs
                        
                        if sisa_utuh < 0 or sisa_dus < 0 or sisa_pcs < 0:
                            error_msg = f"❌ Jumlah pindah Lot {l} melebihi stok tersedia!"
                            break
                            
                        has_changes = True
                        df_lot = df_asal[df_asal['Group_Key'] == g_key].sort_values('Pallet Ke', ascending=False)
                        
                        # --- 1. DEDUCT DARI RAK ASAL ---
                        slot_tambahan = 1 if (sisa_dus > 0 or sisa_pcs > 0) else 0
                        total_slot_sisa = sisa_utuh + slot_tambahan
                        out_qty = len(df_lot) - total_slot_sisa
                        pallets_to_remove = df_lot['Pallet Ke'].head(out_qty).tolist() if out_qty > 0 else []
                        
                        updated_rak_asal = []
                        for d in data_rak_asal:
                            if match_g_key(d, g_key) and float(d.get('Baris', 0) or 0) == float(ab):
                                p_val = pd.to_numeric(d.get('Pallet Ke', 0))
                                if p_val in pallets_to_remove: continue 
                                
                                sisa_pallets = df_lot[~df_lot['Pallet Ke'].isin(pallets_to_remove)]
                                if not sisa_pallets.empty and p_val == sisa_pallets['Pallet Ke'].max():
                                    if sisa_dus > 0 or sisa_pcs > 0:
                                        kp = []
                                        if sisa_dus > 0: kp.append(f"Pecahan: {sisa_dus} qty")
                                        if sisa_pcs > 0: kp.append(f"Eceran: {sisa_pcs} pcs")
                                        d['Keterangan'] = " | ".join(kp)
                                    else: d['Keterangan'] = ""
                                else:
                                    if 'Pecahan' in str(d.get('Keterangan', '')) or 'Eceran' in str(d.get('Keterangan', '')): 
                                        d['Keterangan'] = ""
                            updated_rak_asal.append(d)
                        data_rak_asal = updated_rak_asal
                        
                        # --- 2. ADD KE RAK TUJUAN ---
                        catatan_baru = catatan_awal
                        if tk == 'S' and ak != 'S':
                            cat_bersih = re.sub(r'\[Asal:.*?\]\s*', '', catatan_baru)
                            catatan_baru = (f"[Asal: {ak}-{ab}] " + cat_bersih).strip()
                        elif tk != 'S':
                            catatan_baru = re.sub(r'\[Asal:.*?\]\s*', '', catatan_baru).strip()
                            
                        b_tuj = [pd.to_numeric(d.get('Pallet Ke', 0)) for d in data_rak_tujuan if float(d.get('Baris',0) or 0)==float(tb) and pd.notna(d.get('Pallet Ke'))]
                        next_pallet = max(b_tuj) + 1 if b_tuj else 1
                        
                        new_records = []
                        for _ in range(pindah_utuh):
                            new_records.append({
                                'Kolom': tk, 'Baris': float(tb), 'Pallet Ke': float(next_pallet),
                                'Part Number': part_num, 'Lot Number': l, 'Nama Produk': nama_prod,
                                'Status': 'Terisi', 'Keterangan': '', 'Catatan': catatan_baru
                            })
                            next_pallet += 1
                            
                        if pindah_dus > 0 or pindah_pcs > 0:
                            kp = []
                            if pindah_dus > 0: kp.append(f"Pecahan: {pindah_dus} qty")
                            if pindah_pcs > 0: kp.append(f"Eceran: {pindah_pcs} pcs")
                            new_records.append({
                                'Kolom': tk, 'Baris': float(tb), 'Pallet Ke': float(next_pallet),
                                'Part Number': part_num, 'Lot Number': l, 'Nama Produk': nama_prod,
                                'Status': 'Terisi', 'Keterangan': " | ".join(kp), 'Catatan': catatan_baru
                            })
                            next_pallet += 1
                            
                        data_rak_tujuan.extend(new_records)
                        catat_log_mutasi("RELOKASI", f"{ak} ke {tk}", f"{ab} ke {tb}", nama_prod, part_num, l, pindah_utuh, pindah_dus, pindah_pcs)

                    if error_msg: 
                        st.error(error_msg)
                    elif has_changes:
                        baris_asal_sisa = sorted([d for d in data_rak_asal if float(d.get('Baris',0) or 0)==float(ab)], key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                        lain_asal = [d for d in data_rak_asal if float(d.get('Baris',0) or 0)!=float(ab)]
                        for i, d in enumerate(baris_asal_sisa, 1): d['Pallet Ke'] = float(i)
                        final_data_asal = lain_asal + baris_asal_sisa
                        
                        batch = db.batch()
                        batch.set(db.collection(NAMA_DATABASE).document(f"Kolom_{ak}"), {'data': final_data_asal})
                        if ak != tk: batch.set(db.collection(NAMA_DATABASE).document(f"Kolom_{tk}"), {'data': data_rak_tujuan})
                        batch.commit()
                        
                        st.session_state.update({
                            'undo_dict': undo_dict_setup,
                            'action_msg': f"Berhasil memindahkan item dari Rak {ak}-{ab} ke {tk}-{tb}",
                            'action_status': 'DONE',
                            'relok_dicari': False
                        })
                        st.rerun()
                    else: st.info("Tidak ada jumlah yang dipindah.")
        else:
            st.warning("Rak Asal Kosong!")
            st.session_state['relok_dicari'] = False
