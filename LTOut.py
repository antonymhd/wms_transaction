import streamlit as st
import pandas as pd
import wms_core as core

st.set_page_config(page_title="Outbound Racking HDI", layout="centered", initial_sidebar_state="collapsed")
core.inject_custom_css(accent_color="#2563EB", accent_border="#3B82F6", accent_bg_rgba="rgba(37, 99, 235, 0.1)")

# Styling input baris besar agar nyaman di layar HP operator
st.markdown("""
    <style>
        div[data-testid="stNumberInput"] input {
            font-size: 26px !important;
            font-weight: 700 !important;
            text-align: center !important;
            min-height: 56px !important;
        }
    </style>
""", unsafe_allow_html=True)

OPERATOR_OUTBOUND = ["Maheswara", "Amir", "Bakir", "Danny", "Joko", "Abdul"]
KOLOM_RAK = ["A", "B", "C", "D", "E", "F", "G"]
df_master = core.load_master_data()

keys_state = [
    'slide', 'input_baris_temp', 'pilih_kolom', 'pilih_baris', 'item', 'pilih_part',
    'pilih_lot', 'pilih_catatan_rak', 'max_pallet', 'max_dus',
    'operator', 'undo_record'
]
for key in keys_state:
    if key not in st.session_state:
        if key == 'slide':
            st.session_state[key] = 0
        elif key == 'input_baris_temp':
            st.session_state[key] = 1
        else:
            st.session_state[key] = None

def pindah_slide(tujuan):
    st.session_state.slide = tujuan

# ==========================================================
# SLIDE 0: PILIH OPERATOR BERTUGAS
# ==========================================================
if st.session_state.slide == 0:
    st.markdown("""
        <div class="db-info-box" style="text-align: center;">
            <div style="font-size: 18px; font-weight: bold; color: #2563EB;">
                🧭 Outbound Racking HDI
            </div>
            <div style="font-size: 13px; opacity: 0.8; margin-top: 4px;">
                Pilih Operator Forklift yang Bertugas
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="btn-center">', unsafe_allow_html=True)
    for i in range(0, len(OPERATOR_OUTBOUND), 2):
        cols = st.columns(2)
        with cols[0]:
            op1 = OPERATOR_OUTBOUND[i]
            if st.button(f"👤 {op1}", key=f"op_{op1}", use_container_width=True):
                st.session_state.operator = op1
                core.muat_undo_firestore("OUT", op1)
                pindah_slide(1); st.rerun()
        if i + 1 < len(OPERATOR_OUTBOUND):
            with cols[1]:
                op2 = OPERATOR_OUTBOUND[i+1]
                if st.button(f"👤 {op2}", key=f"op_{op2}", use_container_width=True):
                    st.session_state.operator = op2
                    core.muat_undo_firestore("OUT", op2)
                    pindah_slide(1); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.write("")
    with st.expander("➕ Operator Pengganti / Lainnya"):
        op_manual = st.text_input("Ketik Nama Operator", placeholder="Masukkan nama...")
        if st.button("MASUK DENGAN NAMA INI", type="primary", use_container_width=True):
            if op_manual.strip():
                nama_fix = op_manual.strip().title()
                st.session_state.operator = nama_fix
                core.muat_undo_firestore("OUT", nama_fix)
                pindah_slide(1); st.rerun()
            else:
                st.warning("Nama operator tidak boleh kosong.")
    st.stop()

# ==========================================================
# HEADER STATUS, NOTIFIKASI UNDO & PROGRESS BAR
# ==========================================================
core.render_header_dan_undo(
    app_title="Outbound Racking HDI",
    accent_color="#2563EB",
    prefix_app="OUT",
    mode_badge="  |  <span style='color:#2563EB;font-weight:bold;'>📤 OUTBOUND</span>",
    reset_keys=['pilih_kolom', 'pilih_baris', 'item', 'pilih_part', 'pilih_lot', 'pilih_catatan_rak', 'max_pallet', 'max_dus']
)
core.render_progress_bar(st.session_state.slide, "STAGING")

# ==========================================================
# SLIDE 1: INPUT NOMOR BARIS (1-28) & PILIH KOLOM (A-G) DALAM 1 LAYAR
# ==========================================================
if st.session_state.slide == 1:
    st.markdown("### 1. Pilih Lokasi Rak")
    
    with st.container(border=True):
        st.markdown("**Langkah A — Masukkan Nomor Baris (1 – 28):**")
        baris_input = st.number_input(
            "Nomor Baris (1 - 28)",
            min_value=1,
            max_value=28,
            step=1,
            value=int(st.session_state.input_baris_temp or 1),
            label_visibility="collapsed",
            key="entry_nomor_baris_out"
        )
        st.session_state.input_baris_temp = int(baris_input)
        
    semua_data = core.get_all_racking_data()
    df_all = pd.DataFrame(semua_data) if semua_data else pd.DataFrame()
    
    kolom_berisi = set()
    if not df_all.empty and 'Kolom' in df_all.columns and 'Baris' in df_all.columns:
        df_all['Baris_Int'] = pd.to_numeric(df_all['Baris'], errors='coerce').fillna(0).astype(int)
        df_bar = df_all[df_all['Baris_Int'] == int(baris_input)]
        if not df_bar.empty:
            kolom_berisi = set(df_bar['Kolom'].astype(str).str.strip().str.upper().unique())
                    
    st.markdown(f"**Langkah B — Pilih Kolom pada Baris `{int(baris_input)}`:**")
    for i in range(0, len(KOLOM_RAK), 2):
        cols = st.columns(2)
        for j in range(2):
            if i + j < len(KOLOM_RAK):
                kol = KOLOM_RAK[i + j]
                ada_isi = kol in kolom_berisi
                ikon = "📦" if ada_isi else "⚪"
                ket_isi = f"Rak {kol}-{int(baris_input)}" if ada_isi else "Kosong"
                with cols[j]:
                    if st.button(f"{ikon} KOLOM {kol} ({ket_isi})", key=f"kol_btn_{kol}", use_container_width=True):
                        st.session_state.pilih_baris = int(baris_input)
                        st.session_state.pilih_kolom = kol
                        pindah_slide(2); st.rerun()

# ==========================================================
# SLIDE 2: PILIH NAMA PRODUK & NOMOR LOT DI RAK TERPILIH
# ==========================================================
elif st.session_state.slide == 2:
    kol_aktif = st.session_state.pilih_kolom
    bar_aktif = int(st.session_state.pilih_baris)
    
    if st.button("🔙 GANTI BARIS / KOLOM"):
        st.session_state.pilih_kolom = None
        st.session_state.pilih_baris = None
        pindah_slide(1); st.rerun()
        
    st.markdown(f"### 2. Pilih Produk & Lot di Rak `{kol_aktif}-{bar_aktif}`")
    
    semua_data = core.get_all_racking_data()
    df_all = pd.DataFrame(semua_data) if semua_data else pd.DataFrame()
    
    daftar_lot_rak = []
    if not df_all.empty:
        df_all['Baris_Int'] = pd.to_numeric(df_all['Baris'], errors='coerce').fillna(0).astype(int)
        df_all['Pallet_Ke_Num'] = pd.to_numeric(df_all['Pallet Ke'], errors='coerce').fillna(0)
        df_all['Catatan'] = df_all['Catatan'].fillna("").astype(str).replace('nan', '')
        
        df_rak = df_all[
            (df_all['Kolom'].astype(str).str.strip().str.upper() == kol_aktif) &
            (df_all['Baris_Int'] == bar_aktif)
        ].copy()
        
        if not df_rak.empty:
            df_rak['Group_Key'] = df_rak['Nama Produk'].astype(str) + "||" + df_rak['Lot Number'].astype(str)
            for _, grp in df_rak.groupby('Group_Key'):
                nama_prod = str(grp.iloc[0]['Nama Produk']).strip()
                lot_num = str(grp.iloc[0]['Lot Number']).strip()
                part_num = str(grp.iloc[0].get('Part Number', '')).strip()
                catatan_rak = str(grp.iloc[0]['Catatan']).strip()
                satuan_str = core.get_satuan_kemasan(part_num)
                
                jml_utuh = grp[~grp['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
                pecahan_rows = grp[grp['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)]
                jml_pec = sum(core.parse_keterangan(r['Keterangan'])[0] for _, r in pecahan_rows.iterrows())
                min_pos = grp['Pallet_Ke_Num'].min()
                
                daftar_lot_rak.append({
                    'Nama Produk': nama_prod,
                    'Lot Number': lot_num,
                    'Part Number': part_num,
                    'Catatan': catatan_rak,
                    'Pallet': jml_utuh,
                    'Dus': jml_pec,
                    'Satuan': satuan_str,
                    'MinPos': min_pos
                })
                
    if not daftar_lot_rak:
        st.error(f"❌ Rak {kol_aktif}-{bar_aktif} kosong! Tidak ada barang yang dapat dikeluarkan.")
    else:
        daftar_lot_rak = sorted(daftar_lot_rak, key=lambda x: x['MinPos'])
        for idx, item_rak in enumerate(daftar_lot_rak):
            teks_pec = f", {item_rak['Dus']} {item_rak['Satuan']}" if item_rak['Dus'] > 0 else ""
            catatan_txt = f"\n🔖 Catatan Rak: {item_rak['Catatan']}" if item_rak['Catatan'] else ""
            btn_label = (
                f"☑️ {item_rak['Nama Produk']}\n"
                f"🏷️ Lot: {item_rak['Lot Number']}  |  Stok: {item_rak['Pallet']} Pallet{teks_pec}{catatan_txt}"
            )
            if st.button(btn_label, key=f"sel_prod_lot_{idx}", use_container_width=True):
                st.session_state.item = item_rak['Nama Produk']
                st.session_state.pilih_part = item_rak['Part Number']
                st.session_state.pilih_lot = item_rak['Lot Number']
                st.session_state.pilih_catatan_rak = item_rak['Catatan']
                st.session_state.max_pallet = item_rak['Pallet']
                st.session_state.max_dus = item_rak['Dus']
                pindah_slide(3); st.rerun()

# ==========================================================
# SLIDE 3: KONFIRMASI OUTBOUND + CEK FISIK SISA
# ==========================================================
elif st.session_state.slide == 3:
    if st.button("🔙 GANTI PRODUK / LOT"):
        pindah_slide(2); st.rerun()
        
    k_pilih = st.session_state.pilih_kolom
    b_pilih = int(st.session_state.pilih_baris)
    lot_pilih = st.session_state.pilih_lot
    init_p = int(st.session_state.max_pallet or 0)
    init_d = int(st.session_state.max_dus or 0)
    catatan_rak_lama = st.session_state.pilih_catatan_rak or ""
    
    row_m = df_master[df_master['Nama Barang'] == st.session_state.item]
    total_pcs_pallet = int(row_m.iloc[0]['Total_Pcs_Pallet']) if not row_m.empty and int(row_m.iloc[0]['Total_Pcs_Pallet']) > 0 else 1
    pcs_per_box = int(row_m.iloc[0]['Pcs_Per_Box']) if not row_m.empty and int(row_m.iloc[0]['Pcs_Per_Box']) > 0 else 1
    pcs_per_sac = int(row_m.iloc[0]['Pcs_Per_Sac']) if not row_m.empty and int(row_m.iloc[0]['Pcs_Per_Sac']) > 0 else 1
    
    satuan = core.get_satuan_kemasan(st.session_state.pilih_part)
    pcs_per_pecahan = pcs_per_sac if satuan == "Sak" else pcs_per_box
    pecahan_per_pallet = max(1, total_pcs_pallet // pcs_per_pecahan) if pcs_per_pecahan > 0 else 1
    total_pcs_init = (init_p * total_pcs_pallet) + (init_d * pcs_per_pecahan)
    
    st.markdown("### 3. Jumlah Outbound Barang")
    with st.container(border=True):
        st.markdown(core.format_nama_warna_bold(st.session_state.item))
        st.markdown(f"**Lokasi Rak:** `{k_pilih}-{b_pilih}`  |  **Lot:** `{lot_pilih}`")
        st.info(f"📦 **Stok Tercatat di Rak {k_pilih}-{b_pilih}:** **{init_p} Pallet**, **{init_d} {satuan}** ({total_pcs_init:,} Pcs)")
        
    st.markdown("**Berapa yang Dikeluarkan dari Rak?**")
    
    teks_semua_pec = f", {init_d} {satuan}" if init_d > 0 else ""
    turun_semua = st.checkbox(
        f"☑️ Keluarkan Semua ({init_p} Pallet{teks_semua_pec})",
        value=False,
        key=f"chk_turun_all_{k_pilih}_{b_pilih}_{lot_pilih}"
    )
    
    if turun_semua:
        turun_p = init_p
        turun_d = init_d
        c_t1, c_t2 = st.columns(2)
        with c_t1:
            st.number_input(
                "Pallet Utuh Keluar", min_value=0, max_value=init_p, step=1,
                value=init_p, disabled=True, key=f"tr_p_all_{k_pilih}_{b_pilih}_{lot_pilih}"
            )
        with c_t2:
            st.number_input(
                f"Pecahan Keluar ({satuan})", min_value=0, max_value=max(init_d, 1), step=1,
                value=init_d, disabled=True, key=f"tr_d_all_{k_pilih}_{b_pilih}_{lot_pilih}"
            )
    else:
        default_turun_p = 1 if init_p > 0 else 0
        default_turun_d = init_d if init_p == 0 else 0
        
        c_t1, c_t2 = st.columns(2)
        with c_t1:
            turun_p = st.number_input(
                "Pallet Utuh Keluar", min_value=0, max_value=init_p, step=1,
                value=default_turun_p, key=f"tr_p_{k_pilih}_{b_pilih}_{lot_pilih}"
            )
        with c_t2:
            max_turun_d = init_d if init_p == 0 else max(init_d, pecahan_per_pallet - 1)
            turun_d = st.number_input(
                f"Pecahan Keluar ({satuan})", min_value=0, max_value=max_turun_d, step=1,
                value=default_turun_d, key=f"tr_d_{k_pilih}_{b_pilih}_{lot_pilih}"
            )
        
    pcs_turun_input = (turun_p * total_pcs_pallet) + (turun_d * pcs_per_pecahan)
    pcs_sisa_hitung = max(0, total_pcs_init - pcs_turun_input)
    sisa_p_hitung = pcs_sisa_hitung // total_pcs_pallet if total_pcs_pallet > 0 else max(0, init_p - turun_p)
    sisa_pcs_rem = pcs_sisa_hitung % total_pcs_pallet if total_pcs_pallet > 0 else 0
    sisa_d_hitung = sisa_pcs_rem // pcs_per_pecahan if pcs_per_pecahan > 0 else max(0, init_d - turun_d)
    
    st.markdown(f"""
        <div class="cek-fisik-box">
            <div style="font-size: 13px; opacity: 0.85;">👀 <strong>CEK FISIK LORONG RAK {k_pilih}-{b_pilih}:</strong></div>
            <div style="font-size: 20px; font-weight: bold; margin-top: 4px;">
                Sisa di rak harus tinggal: <u>{sisa_p_hitung} Pallet, {sisa_d_hitung} {satuan}</u>
            </div>
            <div style="font-size: 12px; opacity: 0.8; margin-top: 2px;">
                ({pcs_sisa_hitung:,} Pcs) — Pastikan sisa palet di dalam lorong sesuai!
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    with st.expander("🛠️ Tidak aktual? Koreksi disini"):
        st.caption("Ubah angka di bawah jika sisa fisik di lorong rak tidak sama dengan hitungan di atas:")
        max_sisa_d_kor = max(init_d, pecahan_per_pallet - 1) if pecahan_per_pallet > 1 else max(init_d, 100)
        c_kor1, c_kor2 = st.columns(2)
        with c_kor1:
            sisa_pallet_final = st.number_input(
                "Sisa Pallet Utuh Aktual", min_value=0, max_value=init_p, step=1,
                value=int(sisa_p_hitung), key=f"kor_st_p_{k_pilih}_{b_pilih}_{lot_pilih}_{turun_p}_{turun_d}"
            )
        with c_kor2:
            sisa_dus_final = st.number_input(
                f"Sisa Pecahan Aktual ({satuan})", min_value=0, max_value=max_sisa_d_kor, step=1,
                value=int(sisa_d_hitung), key=f"kor_st_d_{k_pilih}_{b_pilih}_{lot_pilih}_{turun_p}_{turun_d}"
            )
            
    total_pcs_final = (sisa_pallet_final * total_pcs_pallet) + (sisa_dus_final * pcs_per_pecahan)
    selisih_pcs_total = total_pcs_init - total_pcs_final
    turun_pallet_eq = selisih_pcs_total // total_pcs_pallet if total_pcs_pallet > 0 else 0
    sisa_pcs_temp = selisih_pcs_total % total_pcs_pallet if total_pcs_pallet > 0 else selisih_pcs_total
    turun_dus_eq = sisa_pcs_temp // pcs_per_pecahan if pcs_per_pecahan > 0 else 0
    
    ada_koreksi_fisik = (sisa_pallet_final != sisa_p_hitung) or (sisa_dus_final != sisa_d_hitung)
    if ada_koreksi_fisik and selisih_pcs_total > 0:
        st.warning(
            f"⚠️ **Koreksi Sisa Aktif:** Tercatat keluar **{turun_pallet_eq} Pallet, {turun_dus_eq} {satuan}** "
            f"({selisih_pcs_total:,} Pcs), sisa akhir Rak {k_pilih}-{b_pilih} menjadi **{sisa_pallet_final} Pallet, {sisa_dus_final} {satuan}**."
        )
    elif pcs_turun_input > total_pcs_init:
        st.error("❌ Jumlah dikeluarkan melebihi stok yang tersedia di rak!")
    elif selisih_pcs_total <= 0:
        st.warning("⚠️ Masukkan minimal 1 Pallet atau Pecahan yang dikeluarkan.")
        
    catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Muat DO / Kirim Customer")
    st.write("")
    
    if selisih_pcs_total > 0 and pcs_turun_input <= total_pcs_init:
        if st.button("📤 KONFIRMASI OUTBOUND", type="primary", use_container_width=True):
            with st.spinner("Menyimpan transaksi outbound..."):
                doc_ref_asal = core.db.collection(core.NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                doc_ref_staging = core.db.collection(core.NAMA_DATABASE).document("Kolom_S")
                transaction = core.db.transaction()
                
                # Backend tetap mencatat perpindahan ke Staging S-1 beserta asal raknya
                catatan_staging = f"Dari Rak {k_pilih}-{b_pilih} | Op: {st.session_state.operator}"
                if catatan_operator.strip():
                    catatan_staging += f" | {catatan_operator.strip()}"
                
                try:
                    backup_dict = core.proses_turun_ke_staging(
                        transaction, doc_ref_asal, doc_ref_staging,
                        k_pilih, b_pilih, lot_pilih,
                        st.session_state.pilih_part, st.session_state.item,
                        init_p, init_d, sisa_pallet_final, sisa_dus_final,
                        turun_pallet_eq, turun_dus_eq, satuan,
                        catatan_rak_lama, catatan_staging
                    )
                    
                    # Log WMS & Telegram tetap mencatat turun ke Staging S-1 untuk PPIC
                    teks_keterangan = (
                        f"Op: {st.session_state.operator} | Rak {k_pilih}-{b_pilih} -> Staging S-1 | "
                        f"Turun: {turun_pallet_eq}P {turun_dus_eq}{satuan[0]} | "
                        f"Sisa Rak: {sisa_pallet_final}P {sisa_dus_final}{satuan[0]}"
                    )
                    if ada_koreksi_fisik:
                        teks_keterangan += f" [Koreksi Fisik dari {sisa_p_hitung}P {sisa_d_hitung}{satuan[0]}]"
                    if catatan_operator.strip():
                        teks_keterangan += f" | Catatan: {catatan_operator.strip()}"
                        
                    core.catat_log_mutasi(
                        "TURUN KE STAGING (S-1)", f"{k_pilih}-{b_pilih} -> S-1", b_pilih,
                        st.session_state.item, st.session_state.pilih_part, lot_pilih,
                        turun_pallet_eq, turun_dus_eq, selisih_pcs_total, teks_keterangan
                    )
                    
                    pesan_tele = (
                        f"⬇️ <b>TURUN KE STAGING (S-1)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📦 <b>Item:</b> {core.escape_html(st.session_state.item)}\n"
                        f"📍 <b>Asal:</b> Rak {k_pilih}-{b_pilih} ➔ <b>Staging S-1</b>\n"
                        f"🏷️ <b>Lot:</b> <code>{core.escape_html(lot_pilih)}</code>\n"
                        f"📊 <b>Turun:</b> <b>{selisih_pcs_total:,} Pcs</b> ({turun_pallet_eq} Pallet, {turun_dus_eq} {satuan})\n"
                        f"📉 <b>Sisa Rak {k_pilih}-{b_pilih}:</b> <b>{sisa_pallet_final}P {sisa_dus_final}{satuan}</b>\n"
                        f"👤 <b>Operator:</b> {core.escape_html(st.session_state.operator)}\n"
                    )
                    if ada_koreksi_fisik:
                        pesan_tele += f"⚠️ <i>Koreksi sisa fisik (hitungan awal: {sisa_p_hitung}P {sisa_d_hitung}{satuan})</i>\n"
                    if catatan_operator.strip():
                        pesan_tele += f"📝 <b>Catatan:</b> <i>{core.escape_html(catatan_operator.strip())}</i>\n"
                    core.kirim_notif_telegram(pesan_tele)
                    
                    core.get_all_racking_data.clear()
                    # Pesan sukses di layar operator tetap tampil sebagai Outbound tanpa menyebut Staging
                    msg_sukses = f"Outbound {turun_pallet_eq}P {turun_dus_eq}{satuan} ({selisih_pcs_total:,} Pcs) dari Rak {k_pilih}-{b_pilih}."
                    core.simpan_undo_firestore("OUT", st.session_state.operator, backup_dict, msg_sukses, mode_label="OUTBOUND")
                    
                    st.toast(msg_sukses, icon="✅")
                    for k in ['pilih_kolom', 'pilih_baris', 'item', 'pilih_part', 'pilih_lot', 'pilih_catatan_rak', 'max_pallet', 'max_dus']:
                        st.session_state[k] = None
                    pindah_slide(1); st.rerun()
                    
                except ValueError as err:
                    st.error(f"❌ Transaksi Dibatalkan: {err}")
                except Exception as err:
                    st.error(f"❌ Kesalahan Sistem: {err}")
