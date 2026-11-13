import streamlit as st
import pandas as pd
import math
import wms_core as core

st.set_page_config(page_title="Inbound & WIP - Rack Navigator", layout="centered", initial_sidebar_state="collapsed")
core.inject_custom_css(accent_color="#10B981", accent_border="#059669", accent_bg_rgba="rgba(16, 185, 129, 0.1)")

OPERATOR_INBOUND = ["Fahmi", "Adip", "Didik", "Agung"]
df_master = core.load_master_data()

keys_state = [
    'slide', 'kategori', 'gramasi', 'item', 'pilih_part', 'mode_transaksi',
    'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'alokasi_terpilih',
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
    if st.button("🏭 AMBIL WIP (Suplai Bahan ke Produksi)\nMengeluarkan stok dari rak berdasarkan akses langsung & FIFO", use_container_width=True):
        st.session_state.mode_transaksi = "WIP"
        pindah_slide(5); st.rerun()

# ==========================================================
# CABANG A: PROSES INBOUND (SLIDE 5 -> 6 -> 7)
# ==========================================================
elif st.session_state.mode_transaksi == "INBOUND":
    if st.session_state.slide == 5:
        if st.button("🔙 GANTI AKTIVITAS"): pindah_slide(4); st.rerun()
        st.markdown("### Detail Barang Masuk (Inbound)")
        st.info(core.format_nama_warna_bold(st.session_state.item))
        
        satuan_otomatis = core.get_satuan_kemasan(st.session_state.pilih_part)
        with st.container(border=True):
            in_lot = st.text_input("Nomor Lot", value=st.session_state.in_lot or "", placeholder="Cth: 3D3121126")
            c_q1, c_q2 = st.columns(2)
            with c_q1:
                in_qty_p = st.number_input("Pallet Utuh", min_value=0, value=st.session_state.in_qty_p if st.session_state.in_qty_p is not None else 1)
            with c_q2:
                in_qty_c = st.number_input(f"Qty Pecahan ({satuan_otomatis})", min_value=0, value=st.session_state.in_qty_c if st.session_state.in_qty_c is not None else 0)
                
        st.write("")
        if st.button("🔍 CARI LOKASI RAK (KONSOLIDASI)", type="primary", use_container_width=True):
            if not in_lot.strip(): st.error("⚠️ Nomor Lot wajib diisi!")
            elif in_qty_p == 0 and in_qty_c == 0: st.error("⚠️ Masukkan minimal 1 kuantitas!")
            else:
                st.session_state.in_lot = in_lot.strip()
                st.session_state.in_qty_p = in_qty_p
                st.session_state.in_qty_c = in_qty_c
                st.session_state.in_tipe_c = satuan_otomatis 
                pindah_slide(6); st.rerun()

    elif st.session_state.slide == 6:
        if st.button("🔙 UBAH INPUT"): pindah_slide(5); st.rerun()
        st.markdown("### Rekomendasi Lokasi Rak")
        
        qty_p_in = int(st.session_state.in_qty_p or 0)
        qty_c_in = int(st.session_state.in_qty_c or 0)
        total_slot_butuh = qty_p_in + (1 if qty_c_in > 0 else 0)
        
        with st.spinner("📐 Menghitung kapasitas & prioritas konsolidasi..."):
            semua_data = core.get_all_racking_data()
            KOLOM_LIST = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
            BARIS_LIST = list(range(1, 29))
            slot_map = {f"{k}-{b}": [] for k in KOLOM_LIST for b in BARIS_LIST}
            
            for d in semua_data:
                k = str(d.get('Kolom', '')).strip().upper()
                try: b = int(float(d.get('Baris', 0)))
                except Exception: continue
                if f"{k}-{b}" in slot_map: slot_map[f"{k}-{b}"].append(d)
                    
            in_is_sac = str(st.session_state.pilih_part).strip().upper().endswith('C')
            target_item = str(st.session_state.item).strip()
            
            kandidat_rak = []
            for loc, pallets in slot_map.items():
                k, b = loc.split('-')
                b = int(b)
                max_sac = 31 if b <= 16 else 44
                max_dus = 32 if b <= 16 else 46
                max_target = max_sac if in_is_sac else max_dus
                
                used_cap = 0.0
                distinct_items = set()
                distinct_lots = set()
                has_sac = False
                
                for p in pallets:
                    p_name = str(p.get('Nama Produk', '')).strip()
                    p_lot = str(p.get('Lot Number', '')).strip()
                    if p_name: distinct_items.add(p_name)
                    if p_lot: distinct_lots.add(p_lot)
                    p_part = str(p.get('Part Number', '')).strip().upper()
                    if p_part.endswith('C'):
                        has_sac = True
                        used_cap += 1.0 / max_sac
                    else:
                        used_cap += 1.0 / max_dus
                    
                sisa_cap = max(0.0, 1.0001 - used_cap)
                sisa_slot_pallet = int(math.floor(sisa_cap * max_target))
                
                if (in_is_sac or has_sac) and sisa_slot_pallet <= 1:
                    continue
                if sisa_slot_pallet <= 0:
                    continue
                    
                # Sub-sort score:
                # Prio 1 (Sejenis) -> rapatkan baris yang sudah paling terisi (-used_cap)
                # Prio 2 (Kosong)  -> urutkan kolom & baris terdepan (0)
                # Prio 3 (Campur)  -> utamakan baris yang isinya paling sedikit (+used_cap) agar tidak mengunci banyak stok lama
                if len(distinct_items) == 1 and target_item in distinct_items:
                    prio, status = 1, "🟢 KONSOLIDASI (SEJENIS)"
                    isi_eksisting = f"{len(pallets)} Pallet sejenis"
                    sub_sort = -used_cap
                elif len(distinct_items) == 0:
                    prio, status = 2, "⚪ BARIS KOSONG"
                    isi_eksisting = "Kosong"
                    sub_sort = 0.0
                elif len(distinct_items) == 1 and target_item not in distinct_items:
                    prio, status = 3, "🟡 CAMPUR (MAX 2 ITEM)"
                    item_lain = list(distinct_items)[0]
                    lot_str = ", ".join(sorted(list(distinct_lots))[:2])
                    nama_ringkas = f"{item_lain[:18]}..." if len(item_lain) > 18 else item_lain
                    isi_eksisting = f"{len(pallets)}P {nama_ringkas} (Lot: {lot_str})"
                    sub_sort = used_cap
                else:
                    continue
                    
                kandidat_rak.append({
                    'Loc': loc, 'Kolom': k, 'Baris': b, 'Priority': prio, 'Status': status,
                    'IsiEksisting': isi_eksisting, 'UsedCap': used_cap, 'SubSort': sub_sort,
                    'MaxTarget': max_target, 'SisaSlot': sisa_slot_pallet
                })
                
            if not kandidat_rak:
                st.error("❌ Seluruh rak penuh atau sudah berisi 2 jenis item berbeda!")
            else:
                df_kandidat = pd.DataFrame(kandidat_rak).sort_values(
                    by=['Priority', 'SubSort', 'Kolom', 'Baris'], ascending=[True, True, True, True]
                ).reset_index(drop=True)
                
                if in_is_sac:
                    st.caption("🛡️ *Filter aktif: Rak Sak dengan sisa ≤ 1 slot otomatis dilewati karena toleransi panjang fisik.*")
                    
                rak_skip = st.multiselect(
                    "🚫 Lewati Rak (Jika di lapangan sudah penuh / terhalang):",
                    options=df_kandidat['Loc'].tolist(),
                    placeholder="Pilih rak untuk dilewati (opsional)..."
                )
                if rak_skip:
                    df_kandidat = df_kandidat[~df_kandidat['Loc'].isin(rak_skip)].reset_index(drop=True)
                    
                df_single_muat = df_kandidat[df_kandidat['SisaSlot'] >= total_slot_butuh].reset_index(drop=True)
                
                if not df_single_muat.empty:
                    st.caption(f"Kebutuhan ruang: **{total_slot_butuh} slot pallet** (Muat dalam 1 baris rak)")
                    for idx, row in df_single_muat.head(8).iterrows():
                        prio_label = "🌟 REKOMENDASI TERBAIK" if idx == 0 else ""
                        persentase_terisi = int(row['UsedCap'] * 100)
                        est_akhir = int(min(100, (row['UsedCap'] + (total_slot_butuh / row['MaxTarget'])) * 100))
                        
                        btn_label = f"📍 RAK {row['Kolom']}-{row['Baris']}  |  {row['Status']}\n"
                        btn_label += f"📊 Sisa Muat: {row['SisaSlot']} Pallet  |  Terisi: {persentase_terisi}% ➔ {est_akhir}%\n"
                        btn_label += f"📦 Kondisi: {row['IsiEksisting']} {(' | ' + prio_label) if prio_label else ''}"
                        
                        if st.button(btn_label, use_container_width=True, key=f"btn_single_{idx}"):
                            st.session_state.alokasi_terpilih = [{
                                'Kolom': row['Kolom'], 'Baris': int(row['Baris']),
                                'qty_p': qty_p_in, 'qty_c': qty_c_in, 'Status': row['Status']
                            }]
                            pindah_slide(7); st.rerun()
                else:
                    total_kapasitas_gudang = int(df_kandidat['SisaSlot'].sum()) if not df_kandidat.empty else 0
                    if total_kapasitas_gudang < total_slot_butuh:
                        st.error(f"❌ Total sisa kapasitas gudang ({total_kapasitas_gudang} palet) tidak mencukupi untuk menampung {total_slot_butuh} palet!")
                    else:
                        st.info(f"🔀 **Alokasi Multi-Rak Otomatis:** Kebutuhan **{total_slot_butuh} slot pallet** dibagi berurutan sesuai prioritas:")
                        rencana_alokasi = []
                        sisa_utuh, sisa_pecahan = qty_p_in, qty_c_in
                        
                        for _, row in df_kandidat.iterrows():
                            if sisa_utuh == 0 and sisa_pecahan == 0: break
                            kap = int(row['SisaSlot'])
                            ambil_p = min(sisa_utuh, kap)
                            sisa_utuh -= ambil_p
                            kap_sisa = kap - ambil_p
                            
                            ambil_c = 0
                            if sisa_utuh == 0 and sisa_pecahan > 0 and kap_sisa >= 1:
                                ambil_c = sisa_pecahan
                                sisa_pecahan = 0
                                
                            if ambil_p > 0 or ambil_c > 0:
                                rencana_alokasi.append({
                                    'Kolom': row['Kolom'], 'Baris': int(row['Baris']),
                                    'qty_p': ambil_p, 'qty_c': ambil_c,
                                    'Status': row['Status'], 'SisaSlot': int(row['SisaSlot'])
                                })
                                
                        with st.container(border=True):
                            for i_alok, al in enumerate(rencana_alokasi, 1):
                                teks_pec = f" + {al['qty_c']} {st.session_state.in_tipe_c}" if al['qty_c'] > 0 else ""
                                st.markdown(f"**{i_alok}. Rak {al['Kolom']}-{al['Baris']}** ({al['Status']}) ➔ Masuk **{al['qty_p']} Pallet{teks_pec}** *(dari sisa {al['SisaSlot']} slot)*")
                                
                        st.write("")
                        if st.button("✅ GUNAKAN RENCANA PENEMPATAN INI", type="primary", use_container_width=True):
                            st.session_state.alokasi_terpilih = rencana_alokasi
                            pindah_slide(7); st.rerun()

    elif st.session_state.slide == 7:
        if st.button("🔙 BATAL (GANTI LOKASI)"): pindah_slide(6); st.rerun()
        alokasi = st.session_state.alokasi_terpilih or []
        st.markdown("### Konfirmasi Inbound")
        with st.container(border=True):
            st.markdown(core.format_nama_warna_bold(st.session_state.item))
            st.markdown(f"**Lot Masuk:** `{st.session_state.in_lot}`")
            teks_pecahan = f", {st.session_state.in_qty_c} {st.session_state.in_tipe_c}" if st.session_state.in_qty_c > 0 else ""
            st.caption(f"Total Masuk: {st.session_state.in_qty_p} Pallet Utuh{teks_pecahan}")
            st.divider()
            st.markdown("**Rincian Rak Tujuan:**")
            for al in alokasi:
                p_str = f" + {al['qty_c']} {st.session_state.in_tipe_c}" if al['qty_c'] > 0 else ""
                st.markdown(f"- 📍 **Rak {al['Kolom']}-{al['Baris']}**: {al['qty_p']} Pallet{p_str} *({al['Status']})*")
            
        st.write("")
        catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Titipan sementara / DO-9821")
        st.write("")
        if st.button("📥 MASUKKAN KE DATABASE SEKARANG", type="primary", use_container_width=True):
            with st.spinner("Memasukkan data ke rak secara aman..."):
                kolom_unik = sorted(list(set(al['Kolom'] for al in alokasi)))
                doc_refs_dict = {kol: core.db.collection(core.NAMA_DATABASE).document(f"Kolom_{kol}") for kol in kolom_unik}
                transaction = core.db.transaction()
                
                try:
                    backup_kolom = core.proses_inbound_multi_transaction(
                        transaction, doc_refs_dict, alokasi,
                        st.session_state.in_lot, st.session_state.pilih_part, st.session_state.item,
                        st.session_state.in_tipe_c, catatan_operator
                    )
                    
                    teks_keterangan = f"Dieksekusi oleh: {st.session_state.operator}"
                    if catatan_operator.strip(): teks_keterangan += f" | Catatan: {catatan_operator.strip()}"
                    
                    ringkasan_rak = []
                    for al in alokasi:
                        core.catat_log_mutasi(
                            "INBOUND (Remote)", al['Kolom'], al['Baris'],
                            st.session_state.item, st.session_state.pilih_part, st.session_state.in_lot,
                            al['qty_p'], al['qty_c'], 0, teks_keterangan
                        )
                        p_str = f"+{al['qty_c']}{st.session_state.in_tipe_c}" if al['qty_c'] > 0 else ""
                        ringkasan_rak.append(f"{al['Kolom']}-{al['Baris']} ({al['qty_p']}P{p_str})")
                    
                    lokasi_gabungan = ", ".join(ringkasan_rak)
                    pecahan_tele = f", {st.session_state.in_qty_c} {st.session_state.in_tipe_c}" if st.session_state.in_qty_c > 0 else ""
                    
                    pesan_tele = (
                        f"📥 <b>INBOUND - RACK NAVIGATOR</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📦 <b>Item:</b> {core.escape_html(st.session_state.item)}\n"
                        f"🏷️ <b>Lot:</b> <code>{core.escape_html(st.session_state.in_lot)}</code>\n"
                        f"📊 <b>Total:</b> {st.session_state.in_qty_p} Pallet{pecahan_tele}\n"
                        f"📍 <b>Penempatan:</b> {core.escape_html(lokasi_gabungan)}\n"
                        f"👤 <b>Operator:</b> {core.escape_html(st.session_state.operator)}\n"
                    )
                    if catatan_operator.strip():
                        pesan_tele += f"📝 <b>Catatan:</b> <i>{core.escape_html(catatan_operator.strip())}</i>\n"
                    core.kirim_notif_telegram(pesan_tele)
                    
                    core.get_all_racking_data.clear()
                    msg_sukses = f"{st.session_state.in_qty_p} Pallet{pecahan_tele} masuk ke {lokasi_gabungan}."
                    core.simpan_undo_firestore("IN", st.session_state.operator, backup_kolom, msg_sukses, mode_label="INBOUND")
                    
                    st.toast(msg_sukses, icon="✅")
                    for k in ['kategori', 'gramasi', 'item', 'pilih_part', 'mode_transaksi', 'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'alokasi_terpilih']:
                        st.session_state[k] = None
                    pindah_slide(1); st.rerun()
                    
                except ValueError as err:
                    st.error(f"❌ Validasi Kapasitas: {err}")
                except Exception as err:
                    st.error(f"❌ Kesalahan Sistem: {err}")

# ==========================================================
# CABANG B: PROSES AMBIL WIP (SLIDE 5 -> 6, HIBRIDA LIFO + FIFO)
# ==========================================================
elif st.session_state.mode_transaksi == "WIP":
    if st.session_state.slide == 5:
        if st.button("🔙 GANTI AKTIVITAS"): pindah_slide(4); st.rerun()
        st.markdown("### Lokasi Target WIP (Akses Langsung & FIFO)")
        st.info(core.format_nama_warna_bold(st.session_state.item))
        
        with st.spinner("🔍 Memindai posisi lorong & stok gudang..."):
            semua_data = core.get_all_racking_data()
            df_rekap = core.cari_lokasi_stok_hibrida(semua_data, st.session_state.item)
            
            if df_rekap.empty:
                st.error("❌ Stok WIP untuk item ini tidak ditemukan di seluruh rak!")
            else:
                for idx, row in df_rekap.iterrows():
                    if row['JmlPenghalang'] == 0:
                        akses_badge = "✅ AKSES LANGSUNG (FIFO)"
                        prioritas_label = "🌟 REKOMENDASI UTAMA" if idx == 0 else f"⭐ URUTAN {idx + 1}"
                    else:
                        akses_badge = f"⚠️ TERHALANG {row['JmlPenghalang']} PALET"
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
