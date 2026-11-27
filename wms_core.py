import streamlit as st
import pandas as pd
import plotly.express as px
from supabase import create_client, Client
import copy
import re
import math
from datetime import datetime, timedelta, timezone

# ==========================================================
# 1. KONFIGURASI HALAMAN & SESSION STATE STREAMLIT
# ==========================================================
st.set_page_config(
    page_title="RackNav WMS Core - Hokkan Deltapack",
    page_icon="🏭",
    layout="wide"
)

DEFAULT_STATE = {
    'wms_core_logged_in': False,
    'rak_data_cache': None,
    'last_sync_wib': "Belum dimuat",
    'ltin_k': "A",
    'ltin_b': 1,
    'ltout_selected_part': None,
}
for k, v in DEFAULT_STATE.items():
    if k not in st.session_state:
        st.session_state[k] = copy.deepcopy(v)

# Login ringan agar aman jika diakses publik
if not st.session_state['wms_core_logged_in']:
    st.title("🏭 RackNav WMS Core — Login")
    pwd = st.secrets.get("wms_password", "Admin123")
    with st.form("login_wms_core"):
        in_pwd = st.text_input("Kata Sandi Gudang", type="password")
        if st.form_submit_button("Masuk ke Dashboard", type="primary"):
            if in_pwd == pwd:
                st.session_state['wms_core_logged_in'] = True
                st.rerun()
            else:
                st.error("Kata sandi salah!")
    st.stop()

# ==========================================================
# 2. INISIALISASI KONEKSI SUPABASE & KONSTANTA
# ==========================================================
TBL_STOK_RAK = 'uji_coba_rak'
TBL_LOG_MUTASI = 'log_mutasi_rak'
KOLOM_RACKING = ["A", "B", "C", "D", "E", "F", "G"]
KOLOM_ALL = ["A", "B", "C", "D", "E", "F", "G", "S"]

@st.cache_resource
def init_supabase() -> Client:
    url = str(st.secrets["supabase"]["url"]).strip().rstrip("/")
    key = str(st.secrets["supabase"]["key"]).strip()
    return create_client(url, key)

supabase = init_supabase()

# ==========================================================
# 3. FUNGSI HELPER & ADAPTER SUPABASE
# ==========================================================
def get_waktu_jkt():
    return datetime.now(timezone.utc) + timedelta(hours=7)

def clean_str(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    if s.lower() in ["nan", "none", "<na>"]:
        return ""
    return re.sub(r'(\s*\|\s*nan\b|\bnan\s*\|\s*)', '', s, flags=re.IGNORECASE).strip()

def parse_keterangan(ket_str):
    s = clean_str(ket_str).lower()
    m_pec = re.search(r'pecahan:\s*(\d+)', s)
    m_ec = re.search(r'eceran:\s*(\d+)', s)
    return (int(m_pec.group(1)) if m_pec else 0, int(m_ec.group(1)) if m_ec else 0)

def format_keterangan(pecahan, eceran):
    parts = []
    if pecahan > 0:
        parts.append(f"Pecahan: {int(pecahan)} qty")
    if eceran > 0:
        parts.append(f"Eceran: {int(eceran)} pcs")
    return " | ".join(parts)

def get_satuan_kemasan(part_number):
    pn = clean_str(part_number).upper()
    if pn.endswith(('D', 'G')):
        return "dus"
    if pn.endswith('C'):
        return "sak"
    if pn.endswith('A'):
        return "nictainer"
    return "qty"

def set_kategori_produk(nama):
    """Cup Color masuk kategori WIP sesuai aturan operasional gudang."""
    s = clean_str(nama).upper()
    if s.startswith('CUP COLOR'):
        return 'WIP (CUP COLOR)'
    if s.startswith('PREFORM'):
        return 'PREFORM'
    if s.startswith('CUP NATURAL'):
        return 'CUP NATURAL'
    if s.startswith('CUP PRINTING'):
        return 'CUP PRINTING'
    if s.startswith('CAP'):
        return 'CAP'
    return 'LAINNYA'

def parse_fifo_lot_key(lot_str):
    """Kunci urut FIFO murni dari nomor lot (YYMMDD + Shift)."""
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

@st.cache_data(ttl=300)
def load_master_produk():
    try:
        try:
            df = pd.read_csv("Master_Produk.csv", sep=";", encoding='utf-8-sig')
        except Exception:
            df = pd.read_csv("Master_Produk.csv", sep=",", encoding='utf-8-sig')
        df.columns = df.columns.str.strip()
        if 'Part Number' in df.columns:
            df['Part Number Clean'] = df['Part Number'].apply(clean_str).str.upper()
        return df
    except Exception:
        return pd.DataFrame()

def get_master_konversi(df_master, part_num):
    pn = clean_str(part_num).upper()
    if df_master.empty or 'Part Number Clean' not in df_master.columns or pn not in df_master['Part Number Clean'].values:
        return 0, 0
    m_row = df_master[df_master['Part Number Clean'] == pn].iloc[0]
    isi_pallet = int(pd.to_numeric(m_row.get('Isi 1 Pallet (pcs)', 0), errors='coerce') or 0)
    col_satuan = 'Sac / Pallet' if pn.endswith('C') else 'Box / Pallet'
    pcs_per_satuan = int(pd.to_numeric(m_row.get(col_satuan, 0), errors='coerce') or 0)
    return isi_pallet, pcs_per_satuan

def get_max_slot(kolom_id, baris_num, records_baris):
    if kolom_id == 'S':
        return 999
    valid_parts = [clean_str(d.get('Part Number')).upper() for d in records_baris if clean_str(d.get('Part Number'))]
    is_all_box = bool(valid_parts) and all(pn.endswith(('D', 'G')) for pn in valid_parts)
    if is_all_box:
        return 32 if baris_num <= 16 else 46
    return 31 if baris_num <= 16 else 44

def update_bayangan_baris(records_baris, kolom_id, baris_num):
    max_slot = get_max_slot(kolom_id, baris_num, records_baris)
    res = []
    for idx, orig in enumerate(records_baris, start=1):
        item = copy.deepcopy(orig)
        item['Pallet Ke'] = idx
        cat_lama = clean_str(item.get('Catatan'))
        if idx > max_slot and 'BAYANGAN' not in cat_lama.upper():
            item['Catatan'] = f"[BAYANGAN - OVERCAPACITY] {cat_lama}".strip()
        elif idx <= max_slot and 'BAYANGAN' in cat_lama.upper():
            item['Catatan'] = cat_lama.replace("[BAYANGAN - OVERCAPACITY]", "").strip()
        else:
            item['Catatan'] = cat_lama
        res.append(item)
    return res

def fetch_all_rak_supabase(force_refresh=False):
    """Membaca seluruh slot dari tabel uji_coba_rak dengan pagination 1000 baris."""
    if not force_refresh and st.session_state['rak_data_cache'] is not None:
        return st.session_state['rak_data_cache']

    semua = []
    step = 1000
    start = 0
    while True:
        res = (
            supabase.table(TBL_STOK_RAK)
            .select("*")
            .order("kolom", desc=False)
            .order("baris", desc=False)
            .order("pallet_ke", desc=False)
            .range(start, start + step - 1)
            .execute()
        )
        batch = res.data or []
        for r in batch:
            semua.append({
                'id': r.get('id'),
                'Kolom': clean_str(r.get('kolom')).upper(),
                'Baris': int(pd.to_numeric(r.get('baris', 0), errors='coerce') or 0),
                'Pallet Ke': int(pd.to_numeric(r.get('pallet_ke', 0), errors='coerce') or 0),
                'Part Number': clean_str(r.get('part_number')).upper(),
                'Nama Produk': clean_str(r.get('nama_produk')),
                'Lot Number': clean_str(r.get('lot_number')),
                'Keterangan': clean_str(r.get('keterangan')),
                'Catatan': clean_str(r.get('catatan')),
                'Approval QC': clean_str(r.get('approval_qc')),
                'Jadwal Kirim': clean_str(r.get('jadwal_kirim')),
                'Target Penerima': clean_str(r.get('target_penerima')),
                'Status': clean_str(r.get('status')) or 'Terisi',
            })
        if len(batch) < step:
            break
        start += step

    st.session_state['rak_data_cache'] = semua
    st.session_state['last_sync_wib'] = get_waktu_jkt().strftime("%H:%M:%S WIB")
    return semua

def save_baris_to_supabase(kolom_id: str, baris_num: int, updated_baris_records: list):
    """
    Menyimpan perubahan pada 1 lorong rak (Kolom-Baris) secara atomik ke Supabase:
    1. Upsert slot aktif terbaru.
    2. Hapus slot sisa pada lorong tersebut yang sudah kosong/keluar.
    """
    kol = clean_str(kolom_id).upper()
    bar = int(baris_num)
    payload_map = {}

    for item in updated_baris_records:
        pn = clean_str(item.get('Part Number')).upper()
        plt_ke = int(item.get('Pallet Ke', 0))
        if not pn or plt_ke <= 0:
            continue
        payload_map[plt_ke] = {
            'kolom': kol,
            'baris': bar,
            'pallet_ke': plt_ke,
            'part_number': pn,
            'nama_produk': clean_str(item.get('Nama Produk')),
            'lot_number': clean_str(item.get('Lot Number')),
            'keterangan': clean_str(item.get('Keterangan')),
            'catatan': clean_str(item.get('Catatan')),
            'approval_qc': clean_str(item.get('Approval QC')),
            'jadwal_kirim': clean_str(item.get('Jadwal Kirim')),
            'target_penerima': clean_str(item.get('Target Penerima')),
            'status': clean_str(item.get('Status')) or 'Terisi',
            'updated_at': datetime.now(timezone.utc).isoformat(),
        }

    payload = list(payload_map.values())
    if payload:
        supabase.table(TBL_STOK_RAK).upsert(payload, on_conflict="kolom,baris,pallet_ke").execute()

    existing_res = (
        supabase.table(TBL_STOK_RAK)
        .select("id,pallet_ke")
        .eq("kolom", kol)
        .eq("baris", bar)
        .execute()
    )
    ids_to_delete = [
        r['id'] for r in (existing_res.data or [])
        if int(r['pallet_ke']) not in payload_map
    ]
    if ids_to_delete:
        supabase.table(TBL_STOK_RAK).delete().in_("id", ids_to_delete).execute()

    st.session_state['rak_data_cache'] = None

def catat_log_mutasi_core(
    arus, jenis, kolom, baris, produk, part_num, no_lot,
    m_pallet, m_pecahan, total_pcs, tujuan_kategori, detail_sistem
):
    """Mencatat transaksi LTIn / LTOut ke tabel log_mutasi_rak Supabase."""
    bar_int = int(baris)
    lok_str = f"{kolom}-{bar_int}" if bar_int > 0 else str(kolom)
    row_log = {
        "waktu_transaksi": datetime.now(timezone.utc).isoformat(),
        "arus": arus,
        "jenis_transaksi": jenis,
        "lokasi_rak": lok_str,
        "kolom": clean_str(kolom),
        "baris": bar_int,
        "part_number": clean_str(part_num),
        "nama_produk": clean_str(produk),
        "lot_number": clean_str(no_lot) or "-",
        "mutasi_pallet": int(m_pallet),
        "mutasi_pecahan": int(m_pecahan),
        "mutasi_eceran": int(total_pcs),
        "total_pcs": int(total_pcs),
        "tujuan_kategori": clean_str(tujuan_kategori),
        "detail_sistem": clean_str(detail_sistem),
        "operator": "Operator Lapangan (RackNav)",
        "keterangan": f"{tujuan_kategori} | {detail_sistem}".strip(" |"),
    }
    try:
        supabase.table(TBL_LOG_MUTASI).insert(row_log).execute()
    except Exception as e:
        st.toast(f"⚠️ Log mutasi gagal tercatat: {e}", icon="⚠️")

# ==========================================================
# 4. HEADER & SIDEBAR REFRESH
# ==========================================================
with st.sidebar:
    st.markdown("### 🏭 RackNav WMS Core")
    st.caption(f"Database: **Supabase (`{TBL_STOK_RAK}`)**")
    st.caption(f"Terakhir sinkron: **{st.session_state['last_sync_wib']}**")
    if st.button("🔄 Refresh Data Gudang", use_container_width=True):
        fetch_all_rak_supabase(force_refresh=True)
        st.rerun()
    st.divider()
    if st.button("🚪 Logout", use_container_width=True):
        st.session_state['wms_core_logged_in'] = False
        st.rerun()

all_records = fetch_all_rak_supabase(force_refresh=False)
df_master = load_master_produk()

st.title("🏭 RackNav — Visualisasi Gudang, LTIn & LTOut")
tab_dash, tab_ltin, tab_ltout = st.tabs([
    "🗺️ Dashboard & Peta Rak 2D",
    "📥 LTIn (Barang Masuk Lapangan)",
    "📤 LTOut (Pengambilan / Outbound)"
])

# ==========================================================
# TAB 1: DASHBOARD & VISUALISASI PETA RAK 2D (1A - 29G)
# ==========================================================
with tab_dash:
    if not all_records:
        st.warning("Data rak masih kosong di Supabase.")
    else:
        df_rak = pd.DataFrame(all_records)
        df_rak = df_rak[df_rak['Part Number'] != ""].copy()
        df_rak['Kategori'] = df_rak['Nama Produk'].apply(set_kategori_produk)
        df_rak['Is_Pecahan'] = df_rak['Keterangan'].str.contains('Pecahan|Eceran', case=False, na=False)
        df_rak['Pallet_Utuh'] = (~df_rak['Is_Pecahan']).astype(int)

        # KPI Ringkas
        k1, k2, k3, k4 = st.columns(4)
        total_slot_aktif = len(df_rak)
        total_utuh = int(df_rak['Pallet_Utuh'].sum())
        total_slot_pecahan = int(df_rak['Is_Pecahan'].sum())
        total_staging = int((df_rak['Kolom'] == 'S').sum())

        k1.metric("Total Slot Terisi", f"{total_slot_aktif:,} Slot")
        k2.metric("Palet Utuh", f"{total_utuh:,} Palet")
        k3.metric("Slot Pecahan / Eceran", f"{total_slot_pecahan:,} Slot")
        k4.metric("Area Staging (S)", f"{total_staging:,} Slot")

        st.divider()
        c_chart, c_grid = st.columns([1.1, 1.9])

        with c_chart:
            st.subheader("📊 Komposisi Kategori Stok")
            st.caption("*Seluruh item Cup Color dikelompokkan sebagai **WIP (CUP COLOR)**.*")
            df_cat = df_rak.groupby('Kategori', as_index=False).size().rename(columns={'size': 'Jumlah Slot'})
            fig_pie = px.pie(
                df_cat,
                names='Kategori',
                values='Jumlah Slot',
                hole=0.4,
                color_discrete_sequence=px.colors.qualitative.Set2
            )
            fig_pie.update_traces(textposition='inside', textinfo='percent+label')
            fig_pie.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=360)
            st.plotly_chart(fig_pie, use_container_width=True)

        with c_grid:
            st.subheader("🗺️ Peta Okupansi Shuttle Racking (1A – 29G)")
            st.caption("Warna menunjukkan tingkat kepadatan lorong rak berdasarkan kapasitas maksimal masing-masing baris.")

            grid_rows = []
            for b in range(1, 30):
                row_dict = {'Baris': f"Baris {b:02d}"}
                for k in KOLOM_RACKING:
                    sub = df_rak[(df_rak['Kolom'] == k) & (df_rak['Baris'] == b)]
                    cnt = len(sub)
                    max_cap = get_max_slot(k, b, sub.to_dict('records'))
                    if cnt == 0:
                        row_dict[k] = "⬜ 0"
                    elif cnt > max_cap:
                        row_dict[k] = f"🔴 {cnt}/{max_cap}"
                    elif cnt >= max_cap * 0.85:
                        row_dict[k] = f"🟠 {cnt}/{max_cap}"
                    else:
                        row_dict[k] = f"🟢 {cnt}/{max_cap}"
                grid_rows.append(row_dict)

            df_grid = pd.DataFrame(grid_rows)
            st.dataframe(df_grid, hide_index=True, use_container_width=True, height=360)

        st.divider()
        st.subheader("🔍 Inspeksi Detail Lorong Rak")
        ci1, ci2 = st.columns([1, 3])
        with ci1:
            insp_k = st.selectbox("Pilih Kolom", KOLOM_ALL, key="dash_insp_k")
            insp_b = st.number_input("Pilih Baris", min_value=1, max_value=29, value=1, key="dash_insp_b")
        with ci2:
            df_lorong = df_rak[(df_rak['Kolom'] == insp_k) & (df_rak['Baris'] == insp_b)].sort_values('Pallet Ke')
            if df_lorong.empty:
                st.info(f"Rak **{insp_k}-{insp_b}** saat ini kosong.")
            else:
                cols_show = ['Pallet Ke', 'Part Number', 'Nama Produk', 'Lot Number', 'Keterangan', 'Approval QC', 'Jadwal Kirim', 'Target Penerima', 'Catatan']
                st.dataframe(df_lorong[cols_show], hide_index=True, use_container_width=True, height=220)

# ==========================================================
# TAB 2: LTIn (BARANG MASUK LAPANGAN — TANPA SCAN FOTO)
# ==========================================================
with tab_ltin:
    st.header("📥 LTIn — Input Barang Masuk ke Rak")
    st.caption("Pencatatan cepat barang masuk dari lini produksi ke lokasi shuttle racking.")

    c_in1, c_in2 = st.columns([1, 2])
    with c_in1:
        ltin_kol = st.selectbox("Kolom Tujuan", KOLOM_ALL, key="ltin_kol_sel")
        ltin_bar = st.number_input("Baris Tujuan", min_value=1, max_value=29, value=1, key="ltin_bar_sel")

        baris_existing = [
            d for d in all_records
            if d['Kolom'] == ltin_kol and d['Baris'] == ltin_bar and d['Part Number'] != ""
        ]
        max_cap_in = get_max_slot(ltin_kol, ltin_bar, baris_existing)
        st.metric(
            f"Kapasitas Rak {ltin_kol}-{ltin_bar}",
            f"{len(baris_existing)} / {max_cap_in} Slot",
            delta=f"Sisa {max_cap_in - len(baris_existing)} slot"
        )

    with c_in2:
        daftar_barang_in = sorted(df_master['Nama Barang'].dropna().astype(str).unique().tolist()) if not df_master.empty else []
        pilih_prod_in = st.selectbox("Pilih Nama Produk", ["-- Ketik Manual --"] + daftar_barang_in, key="ltin_prod_sel")

        if pilih_prod_in != "-- Ketik Manual --" and not df_master.empty:
            in_nama_final = pilih_prod_in
            m_pn = df_master[df_master['Nama Barang'] == pilih_prod_in]['Part Number'].values
            in_pn_final = st.text_input("Part Number", value=clean_str(m_pn[0]) if len(m_pn) > 0 else "", disabled=True)
        else:
            in_nama_final = st.text_input("Nama Produk (Manual)", key="ltin_nama_man")
            in_pn_final = st.text_input("Part Number (Manual)", key="ltin_pn_man")

        in_lot_val = st.text_input("Nomor Lot Produksi (Wajib)", placeholder="Contoh: 1A271126", key="ltin_lot_val")

        q1, q2, q3 = st.columns(3)
        with q1:
            in_pal_utuh = st.number_input("Palet Utuh Masuk", min_value=0, value=1, step=1, key="ltin_pal_utuh")
        with q2:
            in_pec_qty = st.number_input("Sisa Pecahan (Dus/Sak)", min_value=0, value=0, step=1, key="ltin_pec_qty")
        with q3:
            in_ec_pcs = st.number_input("Eceran (Pcs)", min_value=0, value=0, step=1, key="ltin_ec_pcs")

        in_push_front = st.checkbox("📥 Masukkan dari DEPAN (Push-Back)", key="ltin_push_front")
        in_catatan = st.text_input("Catatan Tambahan (Opsional)", key="ltin_catatan")

        if st.button("✅ Simpan LTIn ke Supabase", type="primary", use_container_width=True):
            pn_clean = clean_str(in_pn_final).upper()
            nama_clean = clean_str(in_nama_final)
            lot_clean = clean_str(in_lot_val)
            cat_clean = clean_str(in_catatan)

            if not pn_clean or not nama_clean or not lot_clean:
                st.error("⚠️ Part Number, Nama Produk, dan Nomor Lot wajib diisi!")
            elif in_pal_utuh == 0 and in_pec_qty == 0 and in_ec_pcs == 0:
                st.error("⚠️ Minimal masukkan 1 palet utuh, pecahan, atau eceran!")
            else:
                with st.spinner("Menyimpan transaksi LTIn ke Supabase..."):
                    fresh_all = fetch_all_rak_supabase(force_refresh=True)
                    baris_curr = sorted(
                        [copy.deepcopy(d) for d in fresh_all if d['Kolom'] == ltin_kol and d['Baris'] == ltin_bar and d['Part Number'] != ""],
                        key=lambda x: x['Pallet Ke']
                    )

                    new_slots = [
                        {
                            'Kolom': ltin_kol, 'Baris': ltin_bar, 'Pallet Ke': 0,
                            'Part Number': pn_clean, 'Nama Produk': nama_clean, 'Lot Number': lot_clean,
                            'Keterangan': '', 'Catatan': cat_clean,
                            'Approval QC': '', 'Jadwal Kirim': '', 'Target Penerima': '', 'Status': 'Terisi'
                        }
                        for _ in range(int(in_pal_utuh))
                    ]

                    if in_pec_qty > 0 or in_ec_pcs > 0:
                        merged_pec = False
                        if not in_push_front:
                            for ex in baris_curr:
                                if (ex['Part Number'] == pn_clean and ex['Lot Number'] == lot_clean
                                        and ex['Jadwal Kirim'] == ""
                                        and ('Pecahan' in ex['Keterangan'] or 'Eceran' in ex['Keterangan'])):
                                    p_old, e_old = parse_keterangan(ex['Keterangan'])
                                    ex['Keterangan'] = format_keterangan(p_old + in_pec_qty, e_old + in_ec_pcs)
                                    merged_pec = True
                                    break
                        if not merged_pec:
                            new_slots.append({
                                'Kolom': ltin_kol, 'Baris': ltin_bar, 'Pallet Ke': 0,
                                'Part Number': pn_clean, 'Nama Produk': nama_clean, 'Lot Number': lot_clean,
                                'Keterangan': format_keterangan(in_pec_qty, in_ec_pcs), 'Catatan': cat_clean,
                                'Approval QC': '', 'Jadwal Kirim': '', 'Target Penerima': '', 'Status': 'Terisi'
                            })

                    combined = (new_slots + baris_curr) if in_push_front else (baris_curr + new_slots)
                    combined = update_bayangan_baris(combined, ltin_kol, ltin_bar)

                    save_baris_to_supabase(ltin_kol, ltin_bar, combined)

                    isi_p, pcs_s = get_master_konversi(df_master, pn_clean)
                    tot_pcs_in = ((in_pal_utuh * isi_p) + (in_pec_qty * pcs_s) + in_ec_pcs) if isi_p > 0 else in_ec_pcs
                    sisi_lbl = "Push-Back (Depan)" if in_push_front else "Normal (Belakang)"
                    det_str = f"Masuk Rak {ltin_kol}-{ltin_bar} | Sisi: {sisi_lbl}" + (f" | {cat_clean}" if cat_clean else "")

                    catat_log_mutasi_core(
                        "MASUK", "LTIn (Inbound Lapangan)", ltin_kol, ltin_bar,
                        nama_clean, pn_clean, lot_clean, in_pal_utuh, in_pec_qty, tot_pcs_in,
                        "INBOUND PRODUKSI", det_str
                    )
                    st.success(f"✅ LTIn berhasil disimpan ke Rak **{ltin_kol}-{ltin_bar}** ({nama_clean} — Lot {lot_clean})!")
                    st.rerun()

# ==========================================================
# TAB 3: LTOut (PENGAMBILAN STOK: FIFO + HABISKAN STOK TERKECIL DULU)
# ==========================================================
with tab_ltout:
    st.header("📤 LTOut — Rekomendasi Pengambilan & Outbound")
    st.caption(
        "Urutan rekomendasi otomatis memprioritaskan: **1) Lot & Shift Tertua (FIFO)**, "
        "**2) Stok Palet Paling Sedikit (Habiskan sisa kecil dulu)**, dan **3) Akses Pintu Tanpa Penghalang**."
    )

    if not all_records:
        st.warning("Stok gudang kosong.")
    else:
        df_avail = pd.DataFrame([d for d in all_records if d['Part Number'] != ""])
        produk_list = sorted(
            df_avail[['Part Number', 'Nama Produk']]
            .drop_duplicates()
            .apply(lambda r: f"{r['Nama Produk']} ({r['Part Number']})", axis=1)
            .tolist()
        )

        pilih_label_out = st.selectbox("1. Pilih Item Produk yang Akan Diambil", produk_list, key="ltout_item_sel")
        target_pn_out = re.search(r'\(([^()]+)\)$', pilih_label_out).group(1).strip().upper()
        target_nama_out = pilih_label_out.rsplit(" (", 1)[0].strip()
        satuan_out = get_satuan_kemasan(target_pn_out)

        # Hitung rekomendasi per (Kolom, Baris, Lot Number, Jadwal Kirim, Target Penerima)
        df_sub_out = df_avail[df_avail['Part Number'] == target_pn_out].copy()
        rekom_list = []

        for (kol, bar, lot, jk, tp), grp in df_sub_out.groupby(
            ['Kolom', 'Baris', 'Lot Number', 'Jadwal Kirim', 'Target Penerima']
        ):
            is_pec = grp['Keterangan'].str.contains('Pecahan|Eceran', case=False, na=False)
            jml_utuh = int((~is_pec).sum())
            jml_dus = sum(parse_keterangan(k)[0] for k in grp.loc[is_pec, 'Keterangan'])
            jml_pcs = sum(parse_keterangan(k)[1] for k in grp.loc[is_pec, 'Keterangan'])

            min_pos = grp['Pallet Ke'].min()
            max_pos = grp['Pallet Ke'].max()
            df_lorong_all = df_avail[(df_avail['Kolom'] == kol) & (df_avail['Baris'] == bar)]
            other_mask = ~(
                (df_lorong_all['Part Number'] == target_pn_out) &
                (df_lorong_all['Lot Number'] == lot) &
                (df_lorong_all['Jadwal Kirim'] == jk)
            )
            blk_depan = int((other_mask & (df_lorong_all['Pallet Ke'] < min_pos)).sum())
            blk_belakang = int((other_mask & (df_lorong_all['Pallet Ke'] > max_pos)).sum())

            if blk_depan == 0:
                akses_str, akses_rank, min_blk = "DEPAN (FIFO)", 0, 0
            elif blk_belakang == 0:
                akses_str, akses_rank, min_blk = "BELAKANG (LIFO)", 1, 0
            else:
                akses_rank = 2
                if blk_depan <= blk_belakang:
                    akses_str, min_blk = f"TERHALANG DEPAN ({blk_depan}P)", blk_depan
                else:
                    akses_str, min_blk = f"TERHALANG BELAKANG ({blk_belakang}P)", blk_belakang

            plot_tag = f"{tp} ({jk})" if (tp or jk) else "Stok Bebas"
            rekom_list.append({
                'Prioritas': 0,
                'Lokasi': f"{kol}-{int(bar)}",
                'Kolom': kol,
                'Baris': int(bar),
                'Lot Number': lot,
                'FIFO_Key': parse_fifo_lot_key(lot),
                'Plot / Status': plot_tag,
                'Jadwal Kirim': jk,
                'Target Penerima': tp,
                'Akses Pintu': akses_str,
                'AksesRank': akses_rank,
                'MinBlk': min_blk,
                'Stok Utuh': jml_utuh,
                f'Stok {satuan_out.title()}': jml_dus,
                'Stok Pcs': jml_pcs,
            })

        df_rekom = pd.DataFrame(rekom_list).sort_values(
            by=['FIFO_Key', 'Stok Utuh', 'MinBlk', 'AksesRank', 'Lot Number', 'Kolom', 'Baris'],
            ascending=[True, True, True, True, True, True, True]
        ).reset_index(drop=True)
        df_rekom['Prioritas'] = df_rekom.index + 1

        st.markdown("**2. Daftar Rekomendasi Lokasi Pengambilan (Terurut FIFO & Stok Terkecil)**")
        cols_rekom_view = [
            'Prioritas', 'Lokasi', 'Lot Number', 'Plot / Status', 'Akses Pintu',
            'Stok Utuh', f'Stok {satuan_out.title()}', 'Stok Pcs'
        ]
        st.dataframe(df_rekom[cols_rekom_view], hide_index=True, use_container_width=True)

        st.divider()
        st.markdown("**3. Eksekusi Pengambilan Barang (LTOut)**")
        opsi_lokasi = [
            f"#{r['Prioritas']} | Rak {r['Lokasi']} | Lot {r['Lot Number']} ({r['Stok Utuh']}P, {r[f'Stok {satuan_out.title()}']} {satuan_out}) — {r['Plot / Status']}"
            for _, r in df_rekom.iterrows()
        ]
        pilih_eksekusi = st.selectbox("Pilih Rak & Lot yang Diambil", opsi_lokasi, key="ltout_pick_row")
        idx_pilih = int(re.search(r'^#(\d+)', pilih_eksekusi).group(1)) - 1
        row_target = df_rekom.iloc[idx_pilih]

        e1, e2, e3, e4 = st.columns([1, 1, 1, 2])
        with e1:
            ambil_p = st.number_input("Ambil Palet Utuh", min_value=0, max_value=int(row_target['Stok Utuh']), value=min(1, int(row_target['Stok Utuh'])), step=1)
        with e2:
            ambil_d = st.number_input(f"Ambil {satuan_out.title()}", min_value=0, max_value=int(row_target[f'Stok {satuan_out.title()}']), value=0, step=1)
        with e3:
            ambil_ec = st.number_input("Ambil Pcs Eceran", min_value=0, max_value=int(row_target['Stok Pcs']), value=0, step=1)
        with e4:
            tujuan_out = st.text_input(
                "Tujuan Delivery / Keterangan",
                value=clean_str(row_target['Target Penerima']),
                placeholder="Nama Customer atau WIP"
            )

        if st.button("🚜 Konfirmasi Pengambilan (LTOut)", type="primary", use_container_width=True):
            if ambil_p == 0 and ambil_d == 0 and ambil_ec == 0:
                st.error("⚠️ Masukkan minimal 1 kuantitas yang diambil!")
            else:
                with st.spinner("Memperbarui stok rak di Supabase..."):
                    k_t = row_target['Kolom']
                    b_t = int(row_target['Baris'])
                    lot_t = row_target['Lot Number']
                    jk_t = row_target['Jadwal Kirim']
                    tp_t = row_target['Target Penerima']

                    fresh_all = fetch_all_rak_supabase(force_refresh=True)
                    baris_curr = sorted(
                        [copy.deepcopy(d) for d in fresh_all if d['Kolom'] == k_t and d['Baris'] == b_t and d['Part Number'] != ""],
                        key=lambda x: x['Pallet Ke']
                    )

                    match_slots = [
                        d for d in baris_curr
                        if d['Part Number'] == target_pn_out
                        and d['Lot Number'] == lot_t
                        and d['Jadwal Kirim'] == jk_t
                        and d['Target Penerima'] == tp_t
                    ]

                    # Ambil dari posisi paling luar (index terbesar jika dari belakang, atau terkecil jika dari depan)
                    dari_belakang = "BELAKANG" in row_target['Akses Pintu']
                    match_slots.sort(key=lambda x: x['Pallet Ke'], reverse=dari_belakang)

                    sisa_p_ambil = int(ambil_p)
                    sisa_d_ambil = int(ambil_d)
                    sisa_e_ambil = int(ambil_ec)
                    ids_hapus = set()

                    for slot in match_slots:
                        is_pec_slot = ('Pecahan' in slot['Keterangan'] or 'Eceran' in slot['Keterangan'])
                        if not is_pec_slot and sisa_p_ambil > 0:
                            ids_hapus.add(slot['Pallet Ke'])
                            sisa_p_ambil -= 1
                        elif is_pec_slot and (sisa_d_ambil > 0 or sisa_e_ambil > 0):
                            cur_d, cur_e = parse_keterangan(slot['Keterangan'])
                            new_d = max(0, cur_d - sisa_d_ambil)
                            new_e = max(0, cur_e - sisa_e_ambil)
                            sisa_d_ambil = 0
                            sisa_e_ambil = 0
                            if new_d == 0 and new_e == 0:
                                ids_hapus.add(slot['Pallet Ke'])
                            else:
                                slot['Keterangan'] = format_keterangan(new_d, new_e)

                    baris_after = [d for d in baris_curr if d['Pallet Ke'] not in ids_hapus]
                    baris_after = update_bayangan_baris(baris_after, k_t, b_t)
                    save_baris_to_supabase(k_t, b_t, baris_after)

                    isi_p, pcs_s = get_master_konversi(df_master, target_pn_out)
                    tot_pcs_out = ((ambil_p * isi_p) + (ambil_d * pcs_s) + ambil_ec) if isi_p > 0 else ambil_ec
                    tuj_final = clean_str(tujuan_out) or "OUTBOUND LAPANGAN"
                    det_out = f"Ambil dari {k_t}-{b_t} | Keluar: {ambil_p}P {ambil_d}{satuan_out} ({tot_pcs_out:,} pcs)"

                    catat_log_mutasi_core(
                        "KELUAR", "LTOut (Outbound Lapangan)", k_t, b_t,
                        target_nama_out, target_pn_out, lot_t,
                        ambil_p, ambil_d, tot_pcs_out,
                        tuj_final, det_out
                    )
                    st.success(f"✅ LTOut berhasil! {ambil_p}P {ambil_d} {satuan_out} ({target_nama_out} Lot {lot_t}) dikeluarkan dari Rak **{k_t}-{b_t}**.")
                    st.rerun()
