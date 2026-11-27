import io
import re
from datetime import datetime, timedelta, timezone
import pandas as pd
import streamlit as st
import wms_core as core

st.set_page_config(
    page_title="Release & Approval QC - Rack Navigator",
    layout="centered",
    initial_sidebar_state="collapsed"
)
core.inject_custom_css(
    accent_color="#2563EB",
    accent_border="#1D4ED8",
    accent_bg_rgba="rgba(37, 99, 235, 0.12)"
)

OPERATOR_QC = ["Admin QC"]
TABEL_RAK = getattr(core, "DB_RAK", getattr(core, "NAMA_DATABASE", "uji_coba_rak"))


def get_password_qc():
    try:
        if "qc" in st.secrets and "password" in st.secrets["qc"]:
            return str(st.secrets["qc"]["password"]).strip()
    except Exception:
        pass
    return "rqc2026"


def clean_str(val):
    if hasattr(core, "clean_str"):
        return core.clean_str(val)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    return "" if s.lower() in ["nan", "none", "<na>"] else s


def get_waktu_jkt_str():
    if hasattr(core, "get_waktu_jkt"):
        return core.get_waktu_jkt().strftime("%d-%m-%Y %H:%M WIB")
    return (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%d-%m-%Y %H:%M WIB")


def parse_tgl_kirim_sort(tgl_str):
    if hasattr(core, "parse_tgl_kirim_sort"):
        return core.parse_tgl_kirim_sort(tgl_str)
    try:
        return datetime.strptime(clean_str(tgl_str), "%d-%m-%Y")
    except Exception:
        return datetime(2099, 12, 31)


def parse_fifo_lot_key(lot_str):
    s = clean_str(lot_str)
    m_sh = re.search(r'([123])(\d{2})(\d{2})(\d{2})$', s)
    if m_sh:
        sh, dd, mm, yy = m_sh.groups()
        return f"{yy}{mm}{dd}{sh}"
    m_dt = re.search(r'(\d{2})(\d{2})(\d{2})$', s)
    if m_dt:
        dd, mm, yy = m_dt.groups()
        return f"{yy}{mm}{dd}0"
    return "9999999"


def _simpan_doc_kolom_supabase(doc_id, arr_data):
    """Menyimpan array slot racking per doc_id ('Kolom_A', dst.) ke Supabase."""
    if hasattr(core, "simpan_kolom_racking"):
        core.simpan_kolom_racking(doc_id, arr_data)
        return
    core.supabase.table(TABEL_RAK).upsert({
        "doc_id": doc_id,
        "data": arr_data,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }, on_conflict="doc_id").execute()


def update_qc_slot_terplot(jadwal_kirim, target_penerima, part_num, lot_num, status_qc, operator_qc, catatan_qc=""):
    """
    Memperbarui atribut Approval QC khusus pada slot-slot yang ter-plot di Supabase
    untuk (Jadwal Kirim, Target Penerima, Part Number, Lot Number) sekaligus menyinkronkan
    status QC ke master qc_approval via core.simpan_approval_qc_lot bila tersedia.
    """
    jk_target = clean_str(jadwal_kirim)
    tp_target = clean_str(target_penerima)
    pn_target = clean_str(part_num).upper()
    lot_target = clean_str(lot_num)
    cat_clean = clean_str(catatan_qc)

    if status_qc == "APPROVED":
        label_qc = f"APPROVED ({operator_qc})" + (f" - {cat_clean}" if cat_clean else "")
    elif status_qc == "HOLD":
        label_qc = f"HOLD ({operator_qc})" + (f" - {cat_clean}" if cat_clean else "")
    else:
        label_qc = ""

    res = core.supabase.table(TABEL_RAK).select("doc_id, data").execute()
    rows = res.data or []
    kolom_terdampak = []
    nama_item_found = ""

    for rec in rows:
        doc_id = clean_str(rec.get("doc_id"))
        if not doc_id or doc_id == "Kolom_S":
            continue

        arr = rec.get("data")
        if isinstance(arr, str):
            import json
            arr = json.loads(arr)
        if not isinstance(arr, list) or not arr:
            continue

        changed = False
        for item in arr:
            if not isinstance(item, dict):
                continue
            if (
                clean_str(item.get("Jadwal Kirim")) == jk_target
                and clean_str(item.get("Target Penerima")) == tp_target
                and clean_str(item.get("Part Number")).upper() == pn_target
                and clean_str(item.get("Lot Number")) == lot_target
            ):
                item["Approval QC"] = label_qc
                if not nama_item_found:
                    nama_item_found = clean_str(item.get("Nama Produk"))
                changed = True

        if changed:
            _simpan_doc_kolom_supabase(doc_id, arr)
            kolom_terdampak.append(doc_id.replace("Kolom_", ""))

    if kolom_terdampak:
        if hasattr(core, "get_all_racking_data"):
            core.get_all_racking_data.clear()
        if hasattr(core, "get_qc_approval_map"):
            core.get_qc_approval_map.clear()

    try:
        if hasattr(core, "simpan_approval_qc_lot"):
            core.simpan_approval_qc_lot(
                pn_target, nama_item_found, lot_target, status_qc, operator_qc, cat_clean
            )
    except Exception:
        pass

    return kolom_terdampak


# ==========================================================
# INISIALISASI SESSION STATE
# ==========================================================
DEFAULT_STATE = {
    "slide": 0,
    "mode_transaksi": "QC",
    "operator": None,
    "qc_jadwal_pilih": None,
    "qc_customer_pilih": None,
    "qc_cache_data": None,
}
for k, v in DEFAULT_STATE.items():
    if k not in st.session_state:
        st.session_state[k] = v


def reset_qc_cache():
    st.session_state.qc_cache_data = None
    if hasattr(core, "get_all_racking_data"):
        core.get_all_racking_data.clear()
    if hasattr(core, "get_qc_approval_map"):
        core.get_qc_approval_map.clear()


# ==========================================================
# SLIDE 0: LOGIN PETUGAS QC & PASSWORD
# ==========================================================
if st.session_state.slide == 0:
    st.markdown("""
        <div class="db-info-box" style="text-align: center;">
            <div style="font-size: 18px; font-weight: bold; color: #2563EB;">
                🔬 Release & Approval QC - Rack Navigator
            </div>
            <div style="font-size: 13px; opacity: 0.8; margin-top: 4px;">
                Otorisasi Kelayakan Outbound Berdasarkan Plotting PPIC
            </div>
        </div>
    """, unsafe_allow_html=True)

    with st.container(border=True):
        pilihan_user = st.selectbox(
            "👤 Pilih User QC:",
            options=OPERATOR_QC + ["Input Nama Petugas Lain..."]
        )
        if pilihan_user == "Input Nama Petugas Lain...":
            nama_qc = st.text_input("Nama Petugas QC:", placeholder="Cth: Budi (QC)").strip().title()
        else:
            nama_qc = pilihan_user

        pwd_input = st.text_input("🔑 Password QC:", type="password", placeholder="Masukkan password QC...")
        st.write("")
        if st.button("🔓 MASUK PORTAL QC", type="primary", use_container_width=True):
            if not nama_qc:
                st.error("⚠️ Nama petugas QC wajib diisi.")
            elif pwd_input.strip() != get_password_qc():
                st.error("❌ Password QC salah!")
            else:
                st.session_state.operator = nama_qc
                st.session_state.mode_transaksi = "QC"
                reset_qc_cache()
                st.session_state.slide = 1
                st.rerun()
    st.stop()

# ==========================================================
# HEADER STATUS PORTAL QC
# ==========================================================
st.session_state.mode_transaksi = "QC"
with st.container(border=True):
    h_c1, h_c2 = st.columns([3, 1])
    with h_c1:
        st.markdown(
            f"**🔬 Release & Approval QC**  |  "
            f"Petugas: <span style='color:#2563EB;font-weight:bold;'>{core.escape_html(st.session_state.operator or '')}</span>",
            unsafe_allow_html=True
        )
    with h_c2:
        if st.button("🚪 Logout", use_container_width=True, key="btn_logout_qc"):
            for k, v in DEFAULT_STATE.items():
                st.session_state[k] = v
            st.rerun()

if st.session_state.qc_cache_data is None:
    with st.spinner("🔍 Memuat jadwal plotting outbound dari database..."):
        st.session_state.qc_cache_data = core.get_all_racking_data() or []

semua_data = st.session_state.qc_cache_data
df_all = pd.DataFrame(semua_data) if semua_data else pd.DataFrame()

if not df_all.empty:
    for col_opt in ["Jadwal Kirim", "Target Penerima", "Approval QC", "Keterangan", "Catatan"]:
        df_all[col_opt] = df_all[col_opt].apply(clean_str) if col_opt in df_all.columns else ""
    df_all["Kolom_Clean"] = df_all["Kolom"].apply(clean_str).str.upper() if "Kolom" in df_all.columns else ""
    df_all["Baris_Int"] = pd.to_numeric(df_all["Baris"], errors="coerce").fillna(0).astype(int) if "Baris" in df_all.columns else 0
    df_plotted = df_all[
        (df_all["Kolom_Clean"] != "S") &
        (df_all["Jadwal Kirim"] != "") &
        (df_all["Target Penerima"] != "")
    ].copy()
else:
    df_plotted = pd.DataFrame()

# ==========================================================
# SLIDE 1: PILIH JADWAL KIRIM & TARGET PENERIMA (CUSTOMER)
# ==========================================================
if st.session_state.slide == 1:
    c_top1, c_top2 = st.columns([2.5, 1])
    with c_top1:
        st.markdown("### 📅 1. Pilih Jadwal Kirim & Customer")
    with c_top2:
        if st.button("🔄 Refresh Data", use_container_width=True):
            reset_qc_cache()
            st.rerun()

    if df_plotted.empty:
        st.info("📭 Saat ini belum ada jadwal plotting outbound dari PPIC yang menunggu pemeriksaan QC.")
    else:
        daftar_jadwal = sorted(
            df_plotted["Jadwal Kirim"].unique().tolist(),
            key=parse_tgl_kirim_sort
        )

        idx_default_jk = 0
        if st.session_state.qc_jadwal_pilih in daftar_jadwal:
            idx_default_jk = daftar_jadwal.index(st.session_state.qc_jadwal_pilih)

        pilih_jadwal = st.selectbox(
            "📆 Tanggal Jadwal Kirim:",
            options=daftar_jadwal,
            index=idx_default_jk
        )

        df_by_jadwal = df_plotted[df_plotted["Jadwal Kirim"] == pilih_jadwal].copy()

        cust_cards = []
        for cust_name, grp_c in df_by_jadwal.groupby("Target Penerima"):
            tot_slot = len(grp_c)
            app_cnt = int(grp_c["Approval QC"].str.upper().str.startswith("APPROVED").sum())
            hold_cnt = int(grp_c["Approval QC"].str.upper().str.startswith("HOLD").sum())
            pend_cnt = max(0, tot_slot - app_cnt - hold_cnt)
            jml_item = grp_c["Part Number"].nunique() if "Part Number" in grp_c.columns else grp_c["Nama Produk"].nunique()
            jml_lot = grp_c["Lot Number"].nunique() if "Lot Number" in grp_c.columns else 0
            cust_cards.append({
                "Customer": cust_name,
                "Item": jml_item,
                "Lot": jml_lot,
                "Total_Slot": tot_slot,
                "Approved": app_cnt,
                "Hold": hold_cnt,
                "Pending": pend_cnt
            })

        st.markdown(f"**Daftar Pengiriman Tanggal `{pilih_jadwal}` ({len(cust_cards)} Customer):**")

        for idx_c, cc in enumerate(cust_cards):
            with st.container(border=True):
                if cc["Pending"] == 0 and cc["Hold"] == 0:
                    st_badge = "<span style='background:#10B981;color:white;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:bold;'>✅ SIAP KIRIM (ALL APPROVED)</span>"
                elif cc["Hold"] > 0:
                    st_badge = f"<span style='background:#DC2626;color:white;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:bold;'>⛔ ADA HOLD ({cc['Hold']} Slot)</span>"
                else:
                    st_badge = f"<span style='background:#F59E0B;color:black;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:bold;'>⏳ MENUNGGU QC ({cc['Pending']} Slot)</span>"

                st.markdown(
                    f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                    f"<span style='font-size:17px;font-weight:bold;'>🏢 {core.escape_html(cc['Customer'])}</span>"
                    f"{st_badge}</div>",
                    unsafe_allow_html=True
                )
                st.caption(
                    f"📦 **{cc['Item']} Jenis Item** ({cc['Lot']} Lot)  |  "
                    f"Total Plot: **{cc['Total_Slot']} Palet/Slot** "
                    f"(✅ {cc['Approved']} Approved • ⏳ {cc['Pending']} Pending • ⛔ {cc['Hold']} Hold)"
                )
                if st.button("🔬 Periksa & Approve QC ➔", type="primary", use_container_width=True, key=f"btn_pilih_cust_{idx_c}"):
                    st.session_state.qc_jadwal_pilih = pilih_jadwal
                    st.session_state.qc_customer_pilih = cc["Customer"]
                    st.session_state.slide = 2
                    st.rerun()

# ==========================================================
# SLIDE 2: APPROVAL QC PER LOT UNTUK CUSTOMER TERPILIH
# ==========================================================
elif st.session_state.slide == 2:
    jk_aktif = st.session_state.qc_jadwal_pilih
    cust_aktif = st.session_state.qc_customer_pilih

    c_nav1, c_nav2 = st.columns([1.2, 1])
    with c_nav1:
        if st.button("🔙 GANTI CUSTOMER / JADWAL", use_container_width=True):
            st.session_state.slide = 1
            st.rerun()
    with c_nav2:
        if st.button("🔄 Refresh Status", use_container_width=True):
            reset_qc_cache()
            st.rerun()

    st.markdown("### 🔬 Verifikasi & Release QC")
    st.info(f"📅 **Jadwal Kirim:** `{jk_aktif}`  \n🏢 **Target Penerima:** **{cust_aktif}**")

    df_target = df_plotted[
        (df_plotted["Jadwal Kirim"] == jk_aktif) &
        (df_plotted["Target Penerima"] == cust_aktif)
    ].copy() if not df_plotted.empty else pd.DataFrame()

    if df_target.empty:
        st.warning("⚠️ Data plotting untuk customer ini sudah tidak ditemukan (kemungkinan sudah diturunkan ke Staging atau dibatalkan PPIC).")
    else:
        daftar_grup = []
        for (pn_v, nama_v, lot_v), grp in df_target.groupby(["Part Number", "Nama Produk", "Lot Number"]):
            pn_str = clean_str(pn_v).upper()
            nama_str = clean_str(nama_v)
            lot_str = clean_str(lot_v)

            is_pec_mask = grp["Keterangan"].str.contains("Pecahan|Eceran", case=False, na=False)
            jml_p = int((~is_pec_mask).sum())
            jml_d = sum(core.parse_keterangan(k)[0] for k in grp.loc[is_pec_mask, "Keterangan"])

            lokasi_list = sorted(set(f"{r['Kolom_Clean']}-{int(r['Baris_Int'])}" for _, r in grp.iterrows()))
            raw_qc_vals = [q for q in grp["Approval QC"].unique() if q]
            raw_qc_str = raw_qc_vals[0] if raw_qc_vals else ""

            if raw_qc_str.upper().startswith("APPROVED"):
                st_qc = "APPROVED"
            elif raw_qc_str.upper().startswith("HOLD"):
                st_qc = "HOLD"
            else:
                st_qc = "PENDING"

            cat_extract = ""
            if " - " in raw_qc_str:
                cat_extract = raw_qc_str.split(" - ", 1)[1].strip()

            daftar_grup.append({
                "Part Number": pn_str,
                "Nama Produk": nama_str,
                "Lot Number": lot_str,
                "FIFO_Key": parse_fifo_lot_key(lot_str),
                "Pallet": jml_p,
                "Pecahan": jml_d,
                "Satuan": core.get_satuan_kemasan(pn_str),
                "Lokasi": ", ".join(lokasi_list),
                "Status_QC": st_qc,
                "Raw_QC": raw_qc_str,
                "Catatan_QC": cat_extract
            })

        df_grup_qc = pd.DataFrame(daftar_grup).sort_values(
            by=["FIFO_Key", "Part Number", "Lot Number"],
            ascending=[True, True, True]
        ).reset_index(drop=True)

        # --- TOMBOL APPROVE MASSAL 1 KLIK (UNTUK LOT YANG MASIH PENDING) ---
        df_pending_only = df_grup_qc[df_grup_qc["Status_QC"] == "PENDING"]
        if not df_pending_only.empty:
            with st.container(border=True):
                st.markdown(f"⚡ **Approval Cepat ({len(df_pending_only)} Lot Menunggu QC)**")
                cat_massal = st.text_input(
                    "Catatan Approval Massal (Opsional):",
                    placeholder="Cth: CoA & Dimensi OK untuk pengiriman",
                    key="cat_qc_massal"
                )
                if st.button(f"✅ APPROVE SEMUA LOT PENDING ({len(df_pending_only)} LOT)", type="primary", use_container_width=True):
                    with st.spinner("Menyimpan Approval QC untuk seluruh lot pending..."):
                        ringkas_tele = []
                        for _, rp in df_pending_only.iterrows():
                            update_qc_slot_terplot(
                                jk_aktif, cust_aktif,
                                rp["Part Number"], rp["Lot Number"],
                                "APPROVED", st.session_state.operator, cat_massal
                            )
                            pec_t = f" + {rp['Pecahan']} {rp['Satuan']}" if rp["Pecahan"] > 0 else ""
                            ket_lg = f"Customer: {cust_aktif} ({jk_aktif}) | QC: {st.session_state.operator} | Rak: {rp['Lokasi']}"
                            if cat_massal.strip():
                                ket_lg += f" | Catatan: {cat_massal.strip()}"
                            core.catat_log_mutasi(
                                "APPROVAL QC (APPROVED)", rp["Lokasi"], "LOT",
                                rp["Nama Produk"], rp["Part Number"], rp["Lot Number"],
                                rp["Pallet"], rp["Pecahan"], 0, ket_lg
                            )
                            ringkas_tele.append(
                                f"• <code>{core.escape_html(rp['Lot Number'])}</code> — {core.escape_html(rp['Nama Produk'])} "
                                f"({rp['Pallet']}P{pec_t} | Rak {core.escape_html(rp['Lokasi'])})"
                            )

                        pesan_tele = (
                            f"✅ <b>BULK APPROVAL QC (RELEASE) - RACK NAVIGATOR</b>\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"📅 <b>Jadwal Kirim:</b> {core.escape_html(jk_aktif)}\n"
                            f"🏢 <b>Customer:</b> {core.escape_html(cust_aktif)}\n"
                            f"🔬 <b>Petugas QC:</b> {core.escape_html(st.session_state.operator)}\n"
                            f"🟢 <b>Status:</b> <b>APPROVED ({len(df_pending_only)} LOT SIAP TURUN)</b>\n"
                            f"📋 <b>Rincian:</b>\n" + "\n".join(ringkas_tele) + "\n"
                        )
                        if cat_massal.strip():
                            pesan_tele += f"📝 <b>Catatan:</b> <i>{core.escape_html(cat_massal.strip())}</i>\n"
                        core.kirim_notif_telegram(pesan_tele)

                        reset_qc_cache()
                        st.toast(f"Semua {len(df_pending_only)} Lot APPROVED untuk {cust_aktif}!", icon="✅")
                        st.rerun()

        st.divider()
        st.markdown("**Daftar Lot Ter-plot (Urutan Strict FIFO):**")

        for idx, r in df_grup_qc.iterrows():
            pn_now = r["Part Number"]
            nama_now = r["Nama Produk"]
            lot_now = r["Lot Number"]
            st_qc = r["Status_QC"]
            pec_txt = f" + {r['Pecahan']} {r['Satuan']}" if r["Pecahan"] > 0 else ""

            with st.container(border=True):
                if st_qc == "APPROVED":
                    badge_html = "<span style='background:#10B981;color:white;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:bold;'>✅ APPROVED QC</span>"
                elif st_qc == "HOLD":
                    badge_html = "<span style='background:#DC2626;color:white;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:bold;'>⛔ HOLD / KARANTINA</span>"
                else:
                    badge_html = "<span style='background:#F59E0B;color:black;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:bold;'>⏳ MENUNGGU QC</span>"

                st.markdown(
                    f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
                    f"<span style='font-size:18px;font-weight:bold;'>#{idx+1}  🏷️ Lot: <code>{lot_now}</code></span>"
                    f"{badge_html}</div>",
                    unsafe_allow_html=True
                )
                st.markdown(f"📦 **{core.format_nama_warna_bold(nama_now)}** (`{pn_now}`)", unsafe_allow_html=True)
                st.markdown(f"📊 **Qty Plot:** {r['Pallet']} Palet{pec_txt}  |  📍 **Lokasi Rak:** `{r['Lokasi']}`")

                if r["Raw_QC"]:
                    st.caption(f"Status Tercatat: **{r['Raw_QC']}**")

                cat_input = st.text_input(
                    "Catatan / Alasan QC:",
                    value=r["Catatan_QC"],
                    placeholder="Wajib diisi jika HOLD (Cth: Penyok / Dimensi out-spec)",
                    key=f"note_qc_{jk_aktif}_{cust_aktif}_{pn_now}_{lot_now}_{idx}"
                )

                c_b1, c_b2, c_b3 = st.columns([1.6, 1.4, 1.1])
                with c_b1:
                    if st.button("✅ APPROVE", type="primary", use_container_width=True, key=f"app_btn_{pn_now}_{lot_now}_{idx}"):
                        with st.spinner("Menyimpan approval QC..."):
                            update_qc_slot_terplot(
                                jk_aktif, cust_aktif, pn_now, lot_now,
                                "APPROVED", st.session_state.operator, cat_input
                            )
                            ket_log = f"Customer: {cust_aktif} ({jk_aktif}) | QC: {st.session_state.operator} | Rak: {r['Lokasi']}"
                            if cat_input.strip():
                                ket_log += f" | Catatan: {cat_input.strip()}"
                            core.catat_log_mutasi(
                                "APPROVAL QC (APPROVED)", r["Lokasi"], "LOT",
                                nama_now, pn_now, lot_now, r["Pallet"], r["Pecahan"], 0, ket_log
                            )
                            pesan_tele = (
                                f"✅ <b>APPROVAL QC (RELEASE) - RACK NAVIGATOR</b>\n"
                                f"━━━━━━━━━━━━━━━━━━\n"
                                f"📅 <b>Jadwal Kirim:</b> {core.escape_html(jk_aktif)}\n"
                                f"🏢 <b>Customer:</b> {core.escape_html(cust_aktif)}\n"
                                f"📦 <b>Item:</b> {core.escape_html(nama_now)}\n"
                                f"🏷️ <b>Lot:</b> <code>{core.escape_html(lot_now)}</code>\n"
                                f"📊 <b>Qty Plot:</b> {r['Pallet']} Palet{pec_txt} (Rak: {core.escape_html(r['Lokasi'])})\n"
                                f"🟢 <b>Status:</b> <b>APPROVED (BOLEH OUTBOUND)</b>\n"
                                f"🔬 <b>Petugas QC:</b> {core.escape_html(st.session_state.operator)}\n"
                            )
                            if cat_input.strip():
                                pesan_tele += f"📝 <b>Keterangan:</b> <i>{core.escape_html(cat_input.strip())}</i>\n"
                            core.kirim_notif_telegram(pesan_tele)

                            reset_qc_cache()
                            st.toast(f"Lot {lot_now} APPROVED!", icon="✅")
                            st.rerun()

                with c_b2:
                    if st.button("⛔ HOLD", use_container_width=True, key=f"hold_btn_{pn_now}_{lot_now}_{idx}"):
                        if not cat_input.strip():
                            st.error("⚠️ Isi alasan penahanan pada kolom Catatan sebelum klik HOLD!")
                        else:
                            with st.spinner("Menyimpan status HOLD QC..."):
                                update_qc_slot_terplot(
                                    jk_aktif, cust_aktif, pn_now, lot_now,
                                    "HOLD", st.session_state.operator, cat_input
                                )
                                ket_log = f"Customer: {cust_aktif} ({jk_aktif}) | QC: {st.session_state.operator} | Rak: {r['Lokasi']} | Alasan: {cat_input.strip()}"
                                core.catat_log_mutasi(
                                    "APPROVAL QC (HOLD)", r["Lokasi"], "LOT",
                                    nama_now, pn_now, lot_now, r["Pallet"], r["Pecahan"], 0, ket_log
                                )
                                pesan_tele = (
                                    f"⛔ <b>HOLD QC (KARANTINA) - RACK NAVIGATOR</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━\n"
                                    f"📅 <b>Jadwal Kirim:</b> {core.escape_html(jk_aktif)}\n"
                                    f"🏢 <b>Customer:</b> {core.escape_html(cust_aktif)}\n"
                                    f"📦 <b>Item:</b> {core.escape_html(nama_now)}\n"
                                    f"🏷️ <b>Lot:</b> <code>{core.escape_html(lot_now)}</code>\n"
                                    f"📊 <b>Qty Plot:</b> {r['Pallet']} Palet{pec_txt} (Rak: {core.escape_html(r['Lokasi'])})\n"
                                    f"🔴 <b>Status:</b> <b>HOLD / DITAHAN QC</b>\n"
                                    f"🔬 <b>Petugas QC:</b> {core.escape_html(st.session_state.operator)}\n"
                                    f"📝 <b>Alasan:</b> <i>{core.escape_html(cat_input.strip())}</i>\n"
                                )
                                core.kirim_notif_telegram(pesan_tele)

                                reset_qc_cache()
                                st.toast(f"Lot {lot_now} ditandai HOLD!", icon="⛔")
                                st.rerun()

                with c_b3:
                    if st_qc != "PENDING":
                        if st.button("🔄 Reset", use_container_width=True, key=f"rst_btn_{pn_now}_{lot_now}_{idx}"):
                            update_qc_slot_terplot(
                                jk_aktif, cust_aktif, pn_now, lot_now,
                                "PENDING", st.session_state.operator, ""
                            )
                            reset_qc_cache()
                            st.toast(f"Status QC Lot {lot_now} direset.", icon="🔄")
                            st.rerun()

        # --- TABEL REKAP RESMI QC (SIAP SCREENSHOT & UNDUH CSV) ---
        st.divider()
        st.subheader("📋 Rekap Status Release QC (Siap Screenshot / Arsip)")
        st.caption(f"Jadwal Kirim: **{jk_aktif}** | Customer: **{cust_aktif}** | Dicetak: {get_waktu_jkt_str()}")

        df_rekap_view = df_grup_qc[[
            "Lokasi", "Part Number", "Nama Produk", "Lot Number", "Pallet", "Pecahan", "Status_QC", "Catatan_QC"
        ]].rename(columns={
            "Pallet": "Palet Utuh",
            "Pecahan": "Pecahan (Dus/Sak)",
            "Status_QC": "Status QC",
            "Catatan_QC": "Catatan QC"
        })
        st.dataframe(df_rekap_view, hide_index=True, use_container_width=True)

        csv_buf = io.StringIO()
        df_rekap_view.to_csv(csv_buf, index=False, sep=";")
        st.download_button(
            label="⬇️ Unduh Laporan Release QC (CSV)",
            data=csv_buf.getvalue(),
            file_name=f"Release_QC_{jk_aktif.replace('-', '')}_{re.sub(r'[^A-Za-z0-9]+', '_', cust_aktif)}.csv",
            mime="text/csv",
            use_container_width=True
        )
