import streamlit as st
import wms_core as core

st.set_page_config(page_title="Inbound & WIP - Rack Navigator", layout="centered", initial_sidebar_state="collapsed")
core.inject_custom_css(accent_color="#10B981", accent_border="#059669", accent_bg_rgba="rgba(16, 185, 129, 0.1)")

OPERATOR_INBOUND = ["Fahmi", "Adip", "Didik", "Agung"]
df_master = core.load_master_data()

keys_state = [
    'slide', 'kategori', 'gramasi', 'item', 'pilih_part', 'mode_transaksi',
    'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'in_sisi_masuk', 'alokasi_terpilih',
    'wip_kolom', 'wip_baris', 'wip_lot', 'wip_catatan_rak', 'wip_max_pallet', 'wip_max_dus',
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
            <div style="font-size: 18px; font-weight: bold; color: #10B981;">
                🧭 Inbound & WIP - Rack Navigator
            </div>
            <div style="font-size: 13px; opacity: 0.8; margin-top: 4px;">
                Pilih Operator Forklift yang Bertugas
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="btn-center">', unsafe_allow_html=True)
    for i in range(0, len(OPERATOR_INBOUND), 2):
        cols = st.columns(2)
        with cols[0]:
            op1 = OPERATOR_INBOUND[i]
            if st.button(f"👤 {op1}", key=f"op_{op1}", use_container_width=True):
                st.session_state.operator = op1
                core.muat_undo_firestore("IN", op1)
                pindah_slide(1); st.rerun()
        if i + 1 < len(OPERATOR_INBOUND):
            with cols[1]:
                op2 = OPERATOR_INBOUND[i+1]
                if st.button(f"👤 {op2}", key=f"op_{op2}", use_container_width=True):
                    st.session_state.operator = op2
                    core.muat_undo_firestore("IN", op2)
                    pindah_slide(1); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.write("")
    with st.expander("➕ Operator Pengganti / Lainnya"):
        op_manual = st.text_input("Ketik Nama Operator", placeholder="Masukkan nama...")
        if st.button("MASUK DENGAN NAMA INI", type="primary", use_container_width=True):
            if op_manual.strip():
                nama_fix = op_manual.strip().title()
                st.session_state.operator = nama_fix
                core.muat_undo_firestore("IN", nama_fix)
                pindah_slide(1); st.rerun()
            else:
                st.warning("Nama operator tidak boleh kosong.")
    st.stop()

# ==========================================================
# HEADER STATUS & NOTIFIKASI UNDO
# ==========================================================
mode_badge = ""
if st.session_state.mode_transaksi == "INBOUND":
    mode_badge = "  |  <span style='color:#10B981;font-weight:bold;'>📥 INBOUND</span>"
elif st.session_state.mode_transaksi == "WIP":
    mode_badge = "  |  <span style='color:#F59E0B;font-weight:bold;'>🏭 AMBIL WIP</span>"

core.render_header_dan_undo(
    app_title="Inbound & WIP - Rack Navigator",
    accent_color="#10B981",
    prefix_app="IN",
    mode_badge=mode_badge,
    reset_keys=['mode_transaksi']
)

def render_progress():
    current = st.session_state.slide
    mode = st.session_state.mode_transaksi
    if mode == "WIP":
        steps = ["Kategori", "Item", "Aksi", "Lokasi WIP", "Selesai"]
        idx_map = {1:1, 2:1, 3:2, 4:3, 5:4, 6:5}
    elif mode == "INBOUND":
        steps = ["Kategori", "Item", "Aksi", "Input", "Lokasi", "Selesai"]
        idx_map = {1:1, 2:1, 3:2, 4:3, 5:4, 6:5, 7:6}
    else:
        steps = ["Kategori", "Item", "Aksi", "Proses", "Selesai"]
        idx_map = {1:1, 2:1, 3:2, 4:3, 5:4, 6:5, 7:5}
        
    curr_step = idx_map.get(current, 1)
    html = "<div class='progress-container'>"
    for i, s in enumerate(steps, 1):
        if i == curr_step: html += f"<span class='step-active'>{s}</span>"
        else: html += f"<span class='step-inactive'>{s}</span>"
        if i < len(steps): html += " <span style='opacity: 0.5;'>➔</span> "
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

render_progress()

# ==========================================================
# SLIDE 1 - 4: KATEGORI -> GRAMASI -> ITEM -> AKSI
# ==========================================================
if st.session_state.slide == 1:
    st.session_state.mode_transaksi = None
    st.markdown("### Pilih Kategori Produk")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("PREFORM", use_container_width=True): st.session_state.kategori = "PREFORM"; pindah_slide(2); st.rerun()
        if st.button("CUP COLOR", use_container_width=True): st.session_state.kategori = "CUP COLOR"; pindah_slide(2); st.rerun()
        if st.button("CAP (TUTUP)", use_container_width=True): st.session_state.kategori = "CAP"; pindah_slide(2); st.rerun()
    with col2:
        if st.button("CUP NATURAL", use_container_width=True): st.session_state.kategori = "CUP NATURAL"; pindah_slide(2); st.rerun()
        if st.button("CUP PRINTING", use_container_width=True): st.session_state.kategori = "CUP PRINTING"; pindah_slide(2); st.rerun()

elif st.session_state.slide == 2:
    if st.button("🔙 KEMBALI"): pindah_slide(1); st.rerun()
    st.markdown(f"### Gramasi: **{st.session_state.kategori}**")
    df_kat = df_master[df_master['Kategori'] == st.session_state.kategori]
    list_gramasi = sorted(df_kat['Gramasi'].unique())
    
    st.markdown('<div class="btn-gramasi">', unsafe_allow_html=True)
    for i in range(0, len(list_gramasi), 2):
        cols = st.columns(2)
        with cols[0]:
            if st.button(f"{list_gramasi[i]}", key=f"g_{i}", use_container_width=True): st.session_state.gramasi = list_gramasi[i]; pindah_slide(3); st.rerun()
        if i + 1 < len(list_gramasi):
            with cols[1]:
                if st.button(f"{list_gramasi[i+1]}", key=f"g_{i+1}", use_container_width=True): st.session_state.gramasi = list_gramasi[i+1]; pindah_slide(3); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

elif st.session_state.slide == 3:
    if st.button("🔙 KEMBALI"): pindah_slide(2); st.rerun()
    st.markdown("### Pilih Item Produk")
    st.caption(f"{st.session_state.kategori} | {st.session_state.gramasi}")
    
    df_item = df_master[(df_master['Kategori'] == st.session_state.kategori) & (df_master['Gramasi'] == st.session_state.gramasi)]
    for idx, row in df_item.iterrows():
        nama_brg = row['Nama Barang']
        part_num = str(row['Part Number']).strip().upper()
        
        tipe_kemasan = ""
        if part_num:
            if part_num[-1] in ['D', 'G']: tipe_kemasan = "[📦 DUS] "
            elif part_num[-1] == 'C': tipe_kemasan = "[👝 SAK] "
            elif part_num[-1] == 'A': tipe_kemasan = "[🗃️ NICTAINER] "
                
        btn_label = f"{tipe_kemasan}{core.format_nama_warna_bold(nama_brg)}"
        if st.button(btn_label, use_container_width=True, key=f"btn_item_{idx}"): 
            st.session_state.item = nama_brg
            st.session_state.pilih_part = part_num
            pindah_slide(4); st.rerun()

elif st.session_state.slide == 4:
    if st.button("🔙 KEMBALI KE ITEM"):
        st.session_state.mode_transaksi = None
        pindah_slide(3); st.rerun()
        
    st.markdown("### Pilih Jenis Aktivitas")
    st.info(core.format_nama_warna_bold(st.session_state.item))
    
    st.write("")
    if st.button("📥 INBOUND (Simpan Barang Masuk ke Rak)\nMemasukkan palet baru hasil produksi / datang ke dalam rak", type="primary", use_container_width=True):
        st.session_state.mode_transaksi = "INBOUND"
        pindah_slide(5); st.rerun()
        
    st.write("")
    if st.button("🏭 AMBIL WIP (Suplai Bahan ke Produksi)\nMengeluarkan stok dari rak berdasarkan akses 2 pintu (Depan FIFO / Belakang LIFO)", use_container_width=True):
        st.session_state.mode_transaksi = "WIP"
        pindah_slide(5); st.rerun()

# ==========================================================
# CABANG A: PROSES INBOUND (SLIDE 5 -> 6 -> 7)
# ==========================================================
elif st.session_state.mode_transaksi == "INBOUND":
    core.render_alur_inbound_terpadu(
        prefix_app="IN",
        judul_inbound="Detail Barang Masuk (Inbound)",
        default_sisi_masuk="BELAKANG",
        jenis_log="INBOUND (Remote)",
        judul_tele="INBOUND - RACK NAVIGATOR",
        mode_undo_label="INBOUND"
    )

# ==========================================================
# CABANG B: PROSES AMBIL WIP (SLIDE 5 -> 6, 2 PINTU SHUTTLE)
# ==========================================================
elif st.session_state.mode_transaksi == "WIP":
    if st.session_state.slide == 5:
        if st.button("🔙 GANTI AKTIVITAS"): pindah_slide(4); st.rerun()
        st.markdown("### Lokasi Target WIP (Akses 2 Pintu)")
        st.info(core.format_nama_warna_bold(st.session_state.item))
        
        with st.spinner("🔍 Memindai posisi lorong depan & belakang..."):
            semua_data = core.get_all_racking_data()
            df_rekap = core.cari_lokasi_stok_hibrida(semua_data, st.session_state.item)
            
            if df_rekap.empty:
                st.error("❌ Stok WIP untuk item ini tidak ditemukan di seluruh rak!")
            else:
                for idx, row in df_rekap.iterrows():
                    if row['SisiAkses'] == "DEPAN":
                        akses_badge = "✅ AKSES DEPAN (FIFO)"
                        prioritas_label = "🌟 REKOMENDASI UTAMA" if idx == 0 else f"⭐ URUTAN {idx + 1}"
                    elif row['SisiAkses'] == "BELAKANG":
                        akses_badge = "✅ AKSES BELAKANG (LIFO)"
                        prioritas_label = "🌟 REKOMENDASI UTAMA" if idx == 0 else f"⭐ URUTAN {idx + 1}"
                    else:
                        sisi_str = "Depan" if row['SisiAkses'] == "TERHALANG_DEPAN" else "Belakang"
                        akses_badge = f"⚠️ TERHALANG {row['MinPenghalang']} PALET ({sisi_str})"
                        prioritas_label = f"🚧 PERLU LANGSIR ({row['InfoPenghalang']})"
                        
                    catatan_label = f"\n🔖 Catatan: {row['Catatan']}" if row['Catatan'] else ""
                    btn_label = f"📍 RAK {row['Kolom']}-{row['Baris']}  |  🏷️ Lot: {row['Lot Number']}\n"
                    btn_label += f"Stok: {row['Pallet']} Pallet, {row['Dus']} {row['Satuan']}  |  {akses_badge}\n"
                    btn_label += f"{prioritas_label}{catatan_label}"
                    
                    if st.button(btn_label, use_container_width=True, key=f"btn_wip_loc_{idx}"):
                        st.session_state.wip_kolom = row['Kolom']
                        st.session_state.wip_baris = row['Baris']
                        st.session_state.wip_lot = row['Lot Number']
                        st.session_state.pilih_part = row['Part']
                        st.session_state.wip_catatan_rak = row['Catatan']
                        st.session_state.wip_max_pallet = row['Pallet']
                        st.session_state.wip_max_dus = row['Dus']
                        pindah_slide(6); st.rerun()

    elif st.session_state.slide == 6:
        if st.button("🔙 BATAL"): pindah_slide(5); st.rerun()
        
        k_pilih = st.session_state.wip_kolom
        b_pilih = st.session_state.wip_baris
        lot_pilih = st.session_state.wip_lot
        init_p = int(st.session_state.wip_max_pallet or 0)
        init_d = int(st.session_state.wip_max_dus or 0)
        
        row_m = df_master[df_master['Nama Barang'] == st.session_state.item]
        total_pcs_pallet = int(row_m.iloc[0]['Total_Pcs_Pallet']) if not row_m.empty and int(row_m.iloc[0]['Total_Pcs_Pallet']) > 0 else 1
        pcs_per_box = int(row_m.iloc[0]['Pcs_Per_Box']) if not row_m.empty and int(row_m.iloc[0]['Pcs_Per_Box']) > 0 else 1
        pcs_per_sac = int(row_m.iloc[0]['Pcs_Per_Sac']) if not row_m.empty and int(row_m.iloc[0]['Pcs_Per_Sac']) > 0 else 1
        
        satuan = core.get_satuan_kemasan(st.session_state.pilih_part)
        pcs_per_pecahan = pcs_per_sac if satuan == "Sak" else pcs_per_box
        pecahan_per_pallet = max(1, total_pcs_pallet // pcs_per_pecahan) if pcs_per_pecahan > 0 else 1
        total_pcs_init = (init_p * total_pcs_pallet) + (init_d * pcs_per_pecahan)
        
        st.markdown("### Konfirmasi Ambil WIP (Produksi)")
        with st.container(border=True):
            st.markdown(core.format_nama_warna_bold(st.session_state.item))
            st.markdown(f"**Lokasi:** Rak {k_pilih}-{b_pilih}  |  **Lot:** `{lot_pilih}`")
            st.info(f"📦 **Stok Awal (Initial):** {init_p} Pallet, {init_d} {satuan} (**{total_pcs_init:,} Pcs**)")
            st.caption(f"Standar Packing: 1 Pallet = {pecahan_per_pallet} {satuan} | 1 {satuan} = {pcs_per_pecahan:,} Pcs")
            
        st.write("")
        st.markdown("**INPUT SISA STOK AKHIR DI RAK (FINAL):**")
        default_sisa_p = max(0, init_p - 1) if init_p > 0 else 0
        max_sisa_d = max(init_d, pecahan_per_pallet - 1) if pecahan_per_pallet > 1 else max(init_d, 100)
        
        col_q1, col_q2 = st.columns(2)
        with col_q1:
            sisa_pallet = st.number_input(
                "SISA PALLET UTUH", min_value=0, max_value=init_p, step=1,
                value=default_sisa_p, key=f"wip_sisa_p_{k_pilih}_{b_pilih}_{lot_pilih}"
            )
        with col_q2:
            sisa_dus = st.number_input(
                f"SISA PECAHAN ({satuan.upper()})", min_value=0, max_value=max_sisa_d, step=1,
                value=init_d, key=f"wip_sisa_d_{k_pilih}_{b_pilih}_{lot_pilih}"
            )
            
        total_pcs_final = (sisa_pallet * total_pcs_pallet) + (sisa_dus * pcs_per_pecahan)
        selisih_pcs = total_pcs_init - total_pcs_final
        keluar_pallet_eq = selisih_pcs // total_pcs_pallet if total_pcs_pallet > 0 else 0
        sisa_pcs_temp = selisih_pcs % total_pcs_pallet if total_pcs_pallet > 0 else selisih_pcs
        keluar_dus_eq = sisa_pcs_temp // pcs_per_pecahan if pcs_per_pecahan > 0 else 0
        
        st.write("")
        if selisih_pcs > 0:
            st.success(f"📊 **Diambil untuk WIP:** **{selisih_pcs:,} Pcs** ({keluar_pallet_eq} Pallet, {keluar_dus_eq} {satuan})\n\nSisa Akhir di Rak: {sisa_pallet} Pallet, {sisa_dus} {satuan} ({total_pcs_final:,} Pcs)")
        elif selisih_pcs == 0:
            st.warning("⚠️ Sisa akhir sama dengan stok awal (0 Pcs diambil). Ubah angka sisa di atas.")
        else:
            st.error("❌ Sisa stok akhir tidak boleh lebih besar dari stok awal!")
            
        catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Suplai Mesin 3 / Line Printing")
        st.write("")
        if selisih_pcs > 0:
            if st.button("🏭 KONFIRMASI AMBIL WIP", type="primary", use_container_width=True):
                with st.spinner("Memproses pengambilan WIP..."):
                    doc_ref = core.db.collection(core.NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                    transaction = core.db.transaction() 
                    
                    try:
                        data_sebelum_update = core.proses_pengambilan_stok(
                            transaction, doc_ref, k_pilih, b_pilih, lot_pilih,
                            st.session_state.pilih_part, st.session_state.item,
                            init_p, init_d, sisa_pallet, sisa_dus, satuan,
                            st.session_state.wip_catatan_rak or ""
                        )
                        
                        teks_keterangan = f"WIP Op: {st.session_state.operator} | Awal: {init_p}P {init_d}{satuan[0]} -> Sisa: {sisa_pallet}P {sisa_dus}{satuan[0]}"
                        if catatan_operator.strip(): teks_keterangan += f" | Catatan: {catatan_operator.strip()}"
                        
                        core.catat_log_mutasi(
                            "AMBIL WIP (Remote)", k_pilih, b_pilih, st.session_state.item,
                            st.session_state.pilih_part, lot_pilih,
                            keluar_pallet_eq, keluar_dus_eq, selisih_pcs, teks_keterangan
                        )
                        
                        pesan_tele = (
                            f"🏭 <b>AMBIL WIP - RACK NAVIGATOR</b>\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"📦 <b>Item:</b> {core.escape_html(st.session_state.item)}\n"
                            f"📍 <b>Rak:</b> {k_pilih}-{b_pilih} | <b>Lot:</b> <code>{core.escape_html(lot_pilih)}</code>\n"
                            f"📊 <b>Diambil WIP:</b> <b>{selisih_pcs:,} Pcs</b> ({keluar_pallet_eq} Pallet, {keluar_dus_eq} {satuan})\n"
                            f"📉 <b>Stok Rak:</b> {init_p}P {init_d}{satuan} ➔ <b>{sisa_pallet}P {sisa_dus}{satuan}</b>\n"
                            f"👤 <b>Operator:</b> {core.escape_html(st.session_state.operator)}\n"
                        )
                        if catatan_operator.strip(): pesan_tele += f"📝 <b>Catatan:</b> <i>{core.escape_html(catatan_operator.strip())}</i>\n"
                        core.kirim_notif_telegram(pesan_tele)
                        
                        core.get_all_racking_data.clear() 
                        msg_sukses = f"Ambil WIP {selisih_pcs:,} Pcs ({keluar_pallet_eq}P {keluar_dus_eq}{satuan}) dari Rak {k_pilih}-{b_pilih}. Sisa: {sisa_pallet}P {sisa_dus}{satuan}."
                        core.simpan_undo_firestore("IN", st.session_state.operator, {k_pilih: data_sebelum_update}, msg_sukses, mode_label="AMBIL WIP")
                        
                        st.toast(msg_sukses, icon="✅")
                        for k in ['kategori', 'gramasi', 'item', 'pilih_part', 'mode_transaksi', 'wip_kolom', 'wip_baris', 'wip_lot', 'wip_catatan_rak', 'wip_max_pallet', 'wip_max_dus']:
                            st.session_state[k] = None
                        pindah_slide(1); st.rerun()
                        
                    except ValueError as err: st.error(f"❌ Transaksi Dibatalkan: {err}")
                    except Exception as err: st.error(f"❌ Kesalahan Sistem: {err}")
