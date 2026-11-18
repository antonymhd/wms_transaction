import streamlit as st
import pandas as pd
import wms_core as core

st.set_page_config(page_title="Turun ke Staging (S-1) - Rack Navigator", layout="centered", initial_sidebar_state="collapsed")
core.inject_custom_css(accent_color="#2563EB", accent_border="#3B82F6", accent_bg_rgba="rgba(37, 99, 235, 0.1)")

OPERATOR_OUTBOUND = ["Maheswara", "Amir", "Bakir", "Danny", "Joko", "Abdul"]
KOLOM_RAK = ["A", "B", "C", "D", "E", "F", "G"]
df_master = core.load_master_data()

keys_state = [
    'slide', 'pilih_kolom', 'pilih_baris', 'item', 'pilih_part',
    'pilih_lot', 'pilih_catatan_rak', 'max_pallet', 'max_dus',
    'operator', 'undo_record'
]
for key in keys_state:
    if key not in st.session_state:
        st.session_state[key] = 0 if key == 'slide' else None

def pindah_slide(tujuan):
    st.session_state.slide = tujuan

# ==========================================================
# SLIDE 0: PILIH OPERATOR BERTUGAS
# ==========================================================
if st.session_state.slide == 0:
    st.markdown("""
        <div class="db-info-box" style="text-align: center;">
            <div style="font-size: 18px; font-weight: bold; color: #2563EB;">
                🧭 Turun ke Staging (S-1) - Rack Navigator
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
    app_title="Turun ke Staging (S-1)",
    accent_color="#2563EB",
    prefix_app="OUT",
    mode_badge="  |  <span style='color:#2563EB;font-weight:bold;'>⬇️ KE STAGING S-1</span>",
    reset_keys=['pilih_kolom', 'pilih_baris', 'item', 'pilih_part', 'pilih_lot', 'pilih_catatan_rak', 'max_pallet', 'max_dus']
)
core.render_progress_bar(st.session_state.slide, "STAGING")

# ==========================================================
# SLIDE 1: PILIH KOLOM (A-G) & BARIS (1-28)
# ==========================================================
if st.session_state.slide == 1:
    st.markdown("### 1. Pilih Lokasi Rak Asal")
    
    # Langkah 1A: Pilih Kolom A - G
    if not st.session_state.pilih_kolom:
        st.caption("Pilih Kolom Rak (A – G):")
        st.markdown('<div class="btn-center">', unsafe_allow_html=True)
        for i in range(0, len(KOLOM_RAK), 4):
            cols = st.columns(4)
            for j in range(4):
                if i + j < len(KOLOM_RAK):
                    kol = KOLOM_RAK[i + j]
                    with cols[j]:
                        if st.button(f"KOLOM\n{kol}", key=f"kol_{kol}", use_container_width=True):
                            st.session_state.pilih_kolom = kol
                            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        
    # Langkah 1B: Pilih Baris 1 - 28 pada Kolom terpilih
    else:
        kol_aktif = st.session_state.pilih_kolom
        if st.button("🔙 GANTI KOLOM"):
            st.session_state.pilih_kolom = None
            st.rerun()
            
        st.markdown(f"**Kolom Dipilih: `KOLOM {kol_aktif}`** — Pilih Nomor Baris:")
        
        semua_data = core.get_all_racking_data()
        df_all = pd.DataFrame(semua_data) if semua_data else pd.DataFrame()
        
        isi_per_baris = {}
        if not df_all.empty and 'Kolom' in df_all.columns:
            df_kol = df_all[df_all['Kolom'].astype(str).str.strip().str.upper() == kol_aktif].copy()
            if not df_kol.empty:
                df_kol['Baris_Int'] = pd.to_numeric(df_kol['Baris'], errors='coerce').fillna(0).astype(int)
                for b_num, grp in df_kol.groupby('Baris_Int'):
                    if 1 <= b_num <= 28:
                        items_unik = grp['Nama Produk'].dropna().unique().tolist()
                        ringkas = items_unik[0].split(',')[-1].strip() if (items_unik and ',' in items_unik[0]) else (items_unik[0][:14] if items_unik else "")
                        if len(items_unik) > 1:
                            ringkas += f" +{len(items_unik)-1}"
                        isi_per_baris[b_num] = f"{len(grp)}P ({ringkas})"
                        
        tampilkan_semua = st.checkbox("Tampilkan semua baris 1–28 (termasuk baris kosong)", value=False)
        baris_tampil = list(range(1, 29)) if tampilkan_semua else sorted(list(isi_per_baris.keys()))
        
        if not baris_tampil:
            st.warning(f"Tidak ada stok tercatat di Kolom {kol_aktif}. Centang kotak di atas jika ingin melihat semua baris 1–28.")
        else:
            for i in range(0, len(baris_tampil), 2):
                cols = st.columns(2)
                for j in range(2):
                    if i + j < len(baris_tampil):
                        b_no = baris_tampil[i + j]
                        info_b = isi_per_baris.get(b_no, "Kosong")
                        ikon = "📦" if b_no in isi_per_baris else "⚪"
                        with cols[j]:
                            if st.button(f"{ikon} RAK {kol_aktif}-{b_no}\n{info_b}", key=f"bar_{kol_aktif}_{b_no}", use_container_width=True):
                                st.session_state.pilih_baris = b_no
                                pindah_slide(2); st.rerun()

# ==========================================================
# SLIDE 2: PILIH NAMA PRODUK & NOMOR LOT DI RAK TERPILIH
# ==========================================================
elif st.session_state.slide == 2:
    kol_aktif = st.session_state.pilih_kolom
    bar_aktif = int(st.session_state.pilih_baris)
    
    if st.button("🔙 GANTI BARIS RAK"):
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
        st.error(f"❌ Rak {kol_aktif}-{bar_aktif} kosong! Tidak ada barang yang dapat diturunkan.")
    else:
        daftar_lot_rak = sorted(daftar_lot_rak, key=lambda x: x['MinPos'])
        for idx, item_rak in enumerate(daftar_lot_rak):
            teks_pec = f", {item_rak['Dus']} {item_rak['Satuan']}" if item_rak['Dus'] > 0 else ""
            catatan_txt = f"\n🔖 Catatan Rak: {item_rak['Catatan']}" if item_rak['Catatan'] else ""
            btn_label = (
                f"📦 {item_rak['Nama Produk']}\n"
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
# SLIDE 3: TURUNKAN KE AREA STAGING (S-1) + CEK FISIK SISA
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
    
    st.markdown("### 3. Turunkan ke Area Staging (`S-1`)")
    with st.container(border=True):
        st.markdown(core.format_nama_warna_bold(st.session_state.item))
        st.markdown(f"**Rak Asal:** `{k_pilih}-{b_pilih}`  |  **Lot:** `{lot_pilih}`  ➔  **Tujuan:** `Staging S-1`")
        st.info(f"📦 **Stok Tercatat di Rak {k_pilih}-{b_pilih}:** **{init_p} Pallet**, **{init_d} {satuan}** ({total_pcs_init:,} Pcs)")
        
    st.markdown("**Berapa yang Diturunkan ke Staging (`S-1`)?**")
    default_turun_p = 1 if init_p > 0 else 0
    default_turun_d = init_d if init_p == 0 else 0
    
    c_t1, c_t2 = st.columns(2)
    with c_t1:
        turun_p = st.number_input(
            "Pallet Utuh Turun", min_value=0, max_value=init_p, step=1,
            value=default_turun_p, key=f"tr_p_{k_pilih}_{b_pilih}_{lot_pilih}"
        )
    with c_t2:
        max_turun_d = init_d if init_p == 0 else max(init_d, pecahan_per_pallet - 1)
        turun_d = st.number_input(
            f"Pecahan Turun ({satuan})", min_value=0, max_value=max_turun_d, step=1,
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
    
    with st.expander("🛠️ Sisa fisik di rak berbeda? Koreksi sisa akhir di sini"):
        st.caption("Ubah angka di bawah jika sisa fisik di lorong rak asal tidak sama dengan hitungan di atas:")
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
            f"⚠️ **Koreksi Sisa Aktif:** Masuk ke Staging S-1 **{turun_pallet_eq} Pallet, {turun_dus_eq} {satuan}** "
            f"({selisih_pcs_total:,} Pcs), sisa akhir Rak {k_pilih}-{b_pilih} menjadi **{sisa_pallet_final} Pallet, {sisa_dus_final} {satuan}**."
        )
    elif pcs_turun_input > total_pcs_init:
        st.error("❌ Jumlah diturunkan melebihi stok yang tersedia di rak!")
    elif selisih_pcs_total <= 0:
        st.warning("⚠️ Masukkan minimal 1 Pallet atau Pecahan yang diturunkan.")
        
    catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Persiapan muat DO / Pindah Staging")
    st.write("")
    
    if selisih_pcs_total > 0 and pcs_turun_input <= total_pcs_init:
        if st.button("⬇️ TURUNKAN KE STAGING (S-1)", type="primary", use_container_width=True):
            with st.spinner("Memindahkan stok ke Area Staging S-1..."):
                doc_ref_asal = core.db.collection(core.NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                doc_ref_staging = core.db.collection(core.NAMA_DATABASE).document("Kolom_S")
                transaction = core.db.transaction()
                
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
                    msg_sukses = f"Turun {turun_pallet_eq}P {turun_dus_eq}{satuan} ({selisih_pcs_total:,} Pcs) dari Rak {k_pilih}-{b_pilih} ke Staging S-1."
                    core.simpan_undo_firestore("OUT", st.session_state.operator, backup_dict, msg_sukses, mode_label="TURUN STAGING")
                    
                    st.toast(msg_sukses, icon="✅")
                    for k in ['pilih_kolom', 'pilih_baris', 'item', 'pilih_part', 'pilih_lot', 'pilih_catatan_rak', 'max_pallet', 'max_dus']:
                        st.session_state[k] = None
                    pindah_slide(1); st.rerun()
                    
                except ValueError as err:
                    st.error(f"❌ Transaksi Dibatalkan: {err}")
                except Exception as err:
                    st.error(f"❌ Kesalahan Sistem: {err}")
