import streamlit as st
from firebase_admin import firestore
import wms_core as core

st.set_page_config(page_title="Outbound FIFO & QC - Rack Navigator", layout="centered", initial_sidebar_state="collapsed")
core.inject_custom_css(accent_color="#F59E0B", accent_border="#D97706", accent_bg_rgba="rgba(245, 158, 11, 0.12)")

OPERATOR_OUTBOUND = ["Maheswara", "Amir", "Bakir", "Danny", "Joko", "Abdul"]
df_master = core.load_master_data()

keys_state = [
    'slide', 'mode_transaksi', 'kategori', 'gramasi', 'item', 'pilih_part',
    'out_kolom', 'out_baris', 'out_lot', 'out_catatan_rak', 'out_qc_info',
    'out_max_pallet', 'out_max_dus',
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
            <div style="font-size: 18px; font-weight: bold; color: #F59E0B;">
                🚚 Outbound FIFO & QC - Rack Navigator
            </div>
            <div style="font-size: 13px; opacity: 0.8; margin-top: 4px;">
                Pengeluaran Barang Berbasis FIFO & Approval QC ke Staging (S-1)
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="btn-center">', unsafe_allow_html=True)
    for i in range(0, len(OPERATOR_OUTBOUND), 2):
        cols = st.columns(2)
        with cols[0]:
            op1 = OPERATOR_OUTBOUND[i]
            if st.button(f"👤 {op1}", key=f"op_out2_{op1}", use_container_width=True):
                st.session_state.operator = op1
                st.session_state.mode_transaksi = "OUTBOUND_QC"
                core.muat_undo_firestore("OUT2", op1)
                pindah_slide(2); st.rerun()
        if i + 1 < len(OPERATOR_OUTBOUND):
            with cols[1]:
                op2 = OPERATOR_OUTBOUND[i+1]
                if st.button(f"👤 {op2}", key=f"op_out2_{op2}", use_container_width=True):
                    st.session_state.operator = op2
                    st.session_state.mode_transaksi = "OUTBOUND_QC"
                    core.muat_undo_firestore("OUT2", op2)
                    pindah_slide(2); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.write("")
    with st.expander("➕ Operator Pengganti / Lainnya"):
        op_manual = st.text_input("Ketik Nama Operator", placeholder="Masukkan nama...")
        if st.button("MASUK DENGAN NAMA INI", type="primary", use_container_width=True):
            if op_manual.strip():
                nama_fix = op_manual.strip().title()
                st.session_state.operator = nama_fix
                st.session_state.mode_transaksi = "OUTBOUND_QC"
                core.muat_undo_firestore("OUT2", nama_fix)
                pindah_slide(2); st.rerun()
            else:
                st.warning("Nama operator tidak boleh kosong.")
    st.stop()

# ==========================================================
# HEADER STATUS, NOTIFIKASI UNDO & PROGRESS BAR
# ==========================================================
st.session_state.mode_transaksi = "OUTBOUND_QC"
core.render_header_dan_undo(
    app_title="Outbound FIFO & QC - Rack Navigator",
    accent_color="#F59E0B",
    prefix_app="OUT2",
    mode_badge="  |  <span style='color:#F59E0B;font-weight:bold;'>🚚 OUTBOUND QC</span>",
    reset_keys=['kategori', 'gramasi', 'item', 'pilih_part', 'out_kolom', 'out_baris', 'out_lot']
)
core.render_progress_bar(st.session_state.slide, "OUTBOUND_QC")

# ==========================================================
# SLIDE 2 - 4: PEMILIHAN PRODUK (KATEGORI -> GRAMASI -> ITEM)
# ==========================================================
if st.session_state.slide in [2, 3, 4]:
    core.render_pemilihan_produk(df_master, allow_back_to_slide1=False)

# ==========================================================
# SLIDE 5: REKOMENDASI RAK APPROVED QC (URUTAN FIFO)
# ==========================================================
elif st.session_state.slide == 5:
    if st.button("🔙 GANTI ITEM PRODUK"):
        pindah_slide(4)
        st.rerun()

    st.markdown("### Pilih Lokasi Outbound (Approved QC)")
    st.info(core.format_nama_warna_bold(st.session_state.item))

    with st.spinner("🔍 Memeriksa status Approval QC & urutan FIFO lorong..."):
        semua_data = core.get_all_racking_data()
        qc_map = core.get_qc_approval_map()
        df_approved, df_locked = core.cari_lokasi_stok_outbound_qc(semua_data, st.session_state.item, qc_map)

        if df_approved.empty:
            st.error("🚫 **TIDAK ADA STOK APPROVED QC!**\n\nSeluruh lot untuk item ini di rak belum mendapatkan Approval QC (atau berstatus HOLD). Hubungi tim QC untuk release di aplikasi RQC.")
        else:
            st.caption("🌟 *Menampilkan khusus lot yang sudah APPROVED QC, diurutkan dari akses bebas & FIFO terlama.*")
            for idx, row in df_approved.iterrows():
                sisi_akses = row.get('SisiAkses', 'DEPAN')
                if sisi_akses == "DEPAN":
                    akses_badge = "✅ AKSES DEPAN (FIFO)"
                    prioritas_label = "🌟 REKOMENDASI UTAMA" if idx == 0 else f"⭐ URUTAN {idx + 1}"
                elif sisi_akses == "BELAKANG":
                    akses_badge = "✅ AKSES BELAKANG (LIFO)"
                    prioritas_label = "🌟 REKOMENDASI UTAMA" if idx == 0 else f"⭐ URUTAN {idx + 1}"
                else:
                    sisi_str = "Depan" if sisi_akses == "TERHALANG_DEPAN" else "Belakang"
                    akses_badge = f"⚠️ TERHALANG {row.get('MinPenghalang', 0)} PALET ({sisi_str})"
                    prioritas_label = f"🚧 PERLU LANGSIR ({row.get('InfoPenghalang', '-')})"

                qc_note_str = f" ({row['QC_Catatan']})" if row.get('QC_Catatan') else ""
                catatan_label = f"\n🔖 Catatan Rak: {row['Catatan']}" if row['Catatan'] else ""
                btn_label = (
                    f"📍 RAK {row['Kolom']}-{row['Baris']}  |  🏷️ Lot: {row['Lot Number']}\n"
                    f"Stok: {row['Pallet']} Pallet, {row['Dus']} {row['Satuan']}  |  {akses_badge}\n"
                    f"✅ QC APPROVED ({row['QC_By']}{qc_note_str})  |  {prioritas_label}{catatan_label}"
                )

                if st.button(btn_label, use_container_width=True, key=f"btn_out2_loc_{idx}"):
                    st.session_state.out_kolom = row['Kolom']
                    st.session_state.out_baris = row['Baris']
                    st.session_state.out_lot = row['Lot Number']
                    st.session_state.pilih_part = row['Part']
                    st.session_state.out_catatan_rak = row['Catatan']
                    st.session_state.out_qc_info = f"APPROVED ({row['QC_By']})"
                    st.session_state.out_max_pallet = row['Pallet']
                    st.session_state.out_max_dus = row['Dus']
                    pindah_slide(6)
                    st.rerun()

        # Tampilkan daftar lot yang masih terkunci (BELUM QC / HOLD) hanya sebagai info transparan
        if not df_locked.empty:
            st.write("")
            with st.expander(f"🔒 Lihat {len(df_locked)} Lokasi Lot Belum Approved QC / HOLD (Terkunci)", expanded=False):
                st.caption("Lot di bawah ini tidak dapat dikeluarkan sebelum di-approve oleh QC di aplikasi RQC:")
                for _, rl in df_locked.iterrows():
                    st_lbl = "⛔ HOLD QC" if rl['QC_Status'] == "HOLD" else "⏳ Belum QC"
                    st.markdown(
                        f"- **Rak {rl['Kolom']}-{rl['Baris']}** | Lot `{rl['Lot Number']}` "
                        f"({rl['Pallet']} Pallet, {rl['Dus']} {rl['Satuan']}) — **{st_lbl}**"
                    )

# ==========================================================
# SLIDE 6: KONFIRMASI PENGELUARAN KE AREA STAGING (S-1)
# ==========================================================
elif st.session_state.slide == 6:
    if st.button("🔙 GANTI LOKASI RAK"):
        pindah_slide(5)
        st.rerun()

    k_pilih = st.session_state.out_kolom
    b_pilih = st.session_state.out_baris
    lot_pilih = st.session_state.out_lot
    item_name = st.session_state.item
    part_num = st.session_state.pilih_part
    init_p = int(st.session_state.out_max_pallet or 0)
    init_d = int(st.session_state.out_max_dus or 0)
    catatan_rak_lama = st.session_state.out_catatan_rak or ""
    qc_info_str = st.session_state.out_qc_info or "APPROVED"

    row_m = df_master[df_master['Nama Barang'] == item_name]
    total_pcs_pallet = int(row_m.iloc[0]['Total_Pcs_Pallet']) if not row_m.empty and int(row_m.iloc[0]['Total_Pcs_Pallet']) > 0 else 1
    pcs_per_box = int(row_m.iloc[0]['Pcs_Per_Box']) if not row_m.empty and int(row_m.iloc[0]['Pcs_Per_Box']) > 0 else 1
    pcs_per_sac = int(row_m.iloc[0]['Pcs_Per_Sac']) if not row_m.empty and int(row_m.iloc[0]['Pcs_Per_Sac']) > 0 else 1

    satuan = core.get_satuan_kemasan(part_num)
    pcs_per_pecahan = pcs_per_sac if satuan == "Sak" else pcs_per_box
    pecahan_per_pallet = max(1, total_pcs_pallet // pcs_per_pecahan) if pcs_per_pecahan > 0 else 1
    total_pcs_init = (init_p * total_pcs_pallet) + (init_d * pcs_per_pecahan)

    st.markdown("### Turunkan Barang ke Staging (S-1)")
    with st.container(border=True):
        st.markdown(core.format_nama_warna_bold(item_name))
        st.markdown(f"**Asal:** Rak {k_pilih}-{b_pilih}  |  **Lot:** `{lot_pilih}`  |  **QC:** `{qc_info_str}`")
        st.info(f"📦 **Stok Tercatat di Rak:** **{init_p} Pallet**, **{init_d} {satuan}** ({total_pcs_init:,} Pcs)")

    st.markdown("**1. Berapa yang Diturunkan ke Staging (S-1)?**")
    c_amb1, c_amb2 = st.columns(2)
    with c_amb1:
        turun_p = st.number_input(
            "Pallet Utuh Diturunkan", min_value=0, max_value=init_p, step=1,
            value=init_p, key=f"out2_p_{k_pilih}_{b_pilih}_{lot_pilih}"
        )
    with c_amb2:
        max_turun_d = init_d if init_p == 0 else max(init_d, pecahan_per_pallet - 1)
        turun_d = st.number_input(
            f"Pecahan Diturunkan ({satuan})", min_value=0, max_value=max_turun_d, step=1,
            value=init_d, key=f"out2_d_{k_pilih}_{b_pilih}_{lot_pilih}"
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
                ({pcs_sisa_hitung:,} Pcs) — Pastikan jumlah palet tersisa di lorong sesuai!
            </div>
        </div>
    """, unsafe_allow_html=True)

    with st.expander("🛠️ Tidak aktual? Koreksi sisa fisik di sini"):
        max_sisa_d_kor = max(init_d, pecahan_per_pallet - 1) if pecahan_per_pallet > 1 else max(init_d, 100)
        c_kor1, c_kor2 = st.columns(2)
        with c_kor1:
            sisa_pallet_final = st.number_input(
                "Sisa Pallet Utuh Aktual", min_value=0, max_value=init_p, step=1,
                value=int(sisa_p_hitung), key=f"out2_kor_p_{k_pilih}_{b_pilih}_{lot_pilih}_{turun_p}_{turun_d}"
            )
        with c_kor2:
            sisa_dus_final = st.number_input(
                f"Sisa Pecahan Aktual ({satuan})", min_value=0, max_value=max_sisa_d_kor, step=1,
                value=int(sisa_d_hitung), key=f"out2_kor_d_{k_pilih}_{b_pilih}_{lot_pilih}_{turun_p}_{turun_d}"
            )

    total_pcs_final = (sisa_pallet_final * total_pcs_pallet) + (sisa_dus_final * pcs_per_pecahan)
    selisih_pcs_total = total_pcs_init - total_pcs_final
    keluar_pallet_eq = selisih_pcs_total // total_pcs_pallet if total_pcs_pallet > 0 else 0
    sisa_pcs_temp = selisih_pcs_total % total_pcs_pallet if total_pcs_pallet > 0 else selisih_pcs_total
    keluar_dus_eq = sisa_pcs_temp // pcs_per_pecahan if pcs_per_pecahan > 0 else 0

    ada_koreksi_fisik = (sisa_pallet_final != sisa_p_hitung) or (sisa_dus_final != sisa_d_hitung)
    if pcs_turun_input > total_pcs_init:
        st.error("❌ Jumlah diturunkan melebihi stok yang tersedia di rak!")
    elif selisih_pcs_total <= 0:
        st.warning("⚠️ Masukkan minimal 1 Pallet atau Pecahan yang diturunkan ke Staging.")

    catatan_operator = st.text_input("📝 CATATAN PENGIRIMAN / SJ (Opsional)", placeholder="Cth: Muat Truk B 9123 KLA")
    st.write("")

    if selisih_pcs_total > 0 and pcs_turun_input <= total_pcs_init:
        if st.button("🚚 TURUNKAN KE STAGING (S-1)", type="primary", use_container_width=True):
            with st.spinner("Memindahkan stok ke area Staging (S-1)..."):
                doc_ref_asal = core.db.collection(core.NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                doc_ref_staging = core.db.collection(core.NAMA_DATABASE).document("Kolom_S")
                transaction = core.db.transaction()

                catatan_stg = f"Ex {k_pilih}-{b_pilih} | {qc_info_str}"
                if catatan_operator.strip():
                    catatan_stg += f" | {catatan_operator.strip()}"

                try:
                    backup_dict = core.proses_turun_ke_staging(
                        transaction, doc_ref_asal, doc_ref_staging,
                        k_pilih, b_pilih, lot_pilih, part_num, item_name,
                        init_p, init_d, sisa_pallet_final, sisa_dus_final,
                        keluar_pallet_eq, keluar_dus_eq, satuan,
                        catatan_rak_lama, catatan_stg
                    )

                    teks_keterangan = (
                        f"Op: {st.session_state.operator} | {qc_info_str} | "
                        f"Turun ke S-1: {keluar_pallet_eq}P {keluar_dus_eq}{satuan[0]} | "
                        f"Sisa Rak {k_pilih}-{b_pilih}: {sisa_pallet_final}P {sisa_dus_final}{satuan[0]}"
                    )
                    if ada_koreksi_fisik:
                        teks_keterangan += f" [Koreksi Fisik dari {sisa_p_hitung}P {sisa_d_hitung}{satuan[0]}]"
                    if catatan_operator.strip():
                        teks_keterangan += f" | Catatan: {catatan_operator.strip()}"

                    core.catat_log_mutasi(
                        "STAGING OUTBOUND QC", k_pilih, b_pilih, item_name,
                        part_num, lot_pilih,
                        keluar_pallet_eq, keluar_dus_eq, selisih_pcs_total, teks_keterangan
                    )

                    pesan_tele = (
                        f"🚚 <b>STAGING OUTBOUND (QC APPROVED) - RACK NAVIGATOR</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📦 <b>Item:</b> {core.escape_html(item_name)}\n"
                        f"📍 <b>Asal Rak:</b> {k_pilih}-{b_pilih} ➔ <b>Staging S-1</b>\n"
                        f"🏷️ <b>Lot:</b> <code>{core.escape_html(lot_pilih)}</code> ({core.escape_html(qc_info_str)})\n"
                        f"📊 <b>Turun Muat:</b> <b>{selisih_pcs_total:,} Pcs</b> ({keluar_pallet_eq} Pallet, {keluar_dus_eq} {satuan})\n"
                        f"📉 <b>Sisa Rak:</b> {sisa_pallet_final}P {sisa_dus_final}{satuan}\n"
                        f"👤 <b>Operator:</b> {core.escape_html(st.session_state.operator)}\n"
                    )
                    if catatan_operator.strip():
                        pesan_tele += f"📝 <b>Catatan:</b> <i>{core.escape_html(catatan_operator.strip())}</i>\n"
                    core.kirim_notif_telegram(pesan_tele)

                    core.get_all_racking_data.clear()
                    msg_sukses = f"Turun {keluar_pallet_eq}P {keluar_dus_eq}{satuan} dari Rak {k_pilih}-{b_pilih} ke Staging S-1."
                    core.simpan_undo_firestore("OUT2", st.session_state.operator, backup_dict, msg_sukses, mode_label="OUTBOUND QC")

                    st.toast(msg_sukses, icon="✅")
                    for k in ['kategori', 'gramasi', 'item', 'pilih_part', 'out_kolom', 'out_baris', 'out_lot', 'out_catatan_rak', 'out_qc_info', 'out_max_pallet', 'out_max_dus']:
                        st.session_state[k] = None
                    st.session_state.slide = 2
                    st.rerun()

                except ValueError as err:
                    st.error(f"❌ Transaksi Dibatalkan: {err}")
                except Exception as err:
                    st.error(f"❌ Kesalahan Sistem: {err}")
