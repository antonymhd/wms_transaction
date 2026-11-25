import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import io
import copy
import re
import html
from datetime import datetime, timedelta, timezone

# ==========================================================
# 1. KONFIGURASI WEB, STATE & SISTEM LOGIN
# ==========================================================
st.set_page_config(page_title="WMS Data Entry - Hokkan Deltapack", page_icon="📝", layout="wide")

DEFAULT_SESSION_STATE = {
    'logged_in': False,
    'fb_usage': {'read': 0, 'write': 0, 'delete': 0},
    'doc_sizes': {},
    'undo_dict': {},
    'action_msg': "",
    'action_status': None,
    'last_reindex': "Belum pernah",
    'form_reset_counter': 0,
    'in_lokasi_dicari': False, 'in_k_pilih': "A", 'in_b_pilih': 1,
    'out_lokasi_dicari': False, 'out_k_pilih': "A", 'out_b_pilih': 1,
    'relok_dicari': False, 'relok_ak': "A", 'relok_ab': 1,
    'kor_dicari': False, 'kor_k': "A", 'kor_b': 1,
    'print_rak_cache': {},
    'print_ready': False,
}
for k, v in DEFAULT_SESSION_STATE.items():
    if k not in st.session_state:
        st.session_state[k] = copy.deepcopy(v)

def log_fb_usage(op_type, count=1):
    if op_type in st.session_state['fb_usage']:
        st.session_state['fb_usage'][op_type] += count

if not st.session_state['logged_in']:
    st.title("🔒 WMS Security Login")
    st.caption("Silakan masukkan kata sandi untuk mengakses form mutasi gudang.")
    
    correct_password = st.secrets.get("wms_password", "Admin123")
    with st.form("login_form"):
        pwd_input = st.text_input("Kata Sandi", type="password")
        if st.form_submit_button("Login", type="primary"):
            if pwd_input == correct_password:
                st.session_state['logged_in'] = True
                st.rerun()
            else:
                st.error("Kata sandi salah!")
    st.stop()

# ==========================================================
# 2. KONSTANTA & INISIALISASI DATABASE (FIREBASE)
# ==========================================================
NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'
KOLOM_OPTIONS = ["A", "B", "C", "D", "E", "F", "G", "S (Staging)"]
KOLOM_IDS = ["A", "B", "C", "D", "E", "F", "G", "S"]
KOLOM_RACKING_AG = ["A", "B", "C", "D", "E", "F", "G"]
LIMIT_DOC_BYTES = 1_048_576         # 1 MiB per dokumen Firestore
LIMIT_SPARK_BYTES = 1_073_741_824   # 1 GiB Spark Plan

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

# ==========================================================
# 3. FUNGSI BANTUAN TERPUSAT (CORE HELPERS)
# ==========================================================
def get_waktu_jkt():
    """Mendapatkan waktu saat ini di zona WIB (UTC+7)."""
    return datetime.now(timezone.utc) + timedelta(hours=7)

def clean_str(val):
    """Membersihkan nilai None/NaN maupun tempelan '| nan' secara konsisten."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    if s.lower() in ["nan", "none", "<na>"]:
        return ""
    return re.sub(r'(\s*\|\s*nan\b|\bnan\s*\|\s*)', '', s, flags=re.IGNORECASE).strip()

def make_g_key(lot, prod, cat):
    """Membuat kunci pengelompokan yang 100% identik antara UI dan Transaksi."""
    return f"{clean_str(lot)}_{clean_str(prod)}_{clean_str(cat)}"

def match_g_key(d, target_key):
    """Mencocokkan dictionary record dengan target Group_Key."""
    return make_g_key(d.get('Lot Number'), d.get('Nama Produk'), d.get('Catatan')) == target_key

def is_valid_item(d):
    """Mengecek apakah slot memiliki Part Number yang valid."""
    return clean_str(d.get('Part Number')) != ""

def is_same_baris(d, baris_num):
    """Mengecek kesesuaian nomor baris secara aman."""
    return float(d.get('Baris', 0) or 0) == float(baris_num)

def get_baris_records(data_list, baris_num, only_valid=True):
    """Menyaring salinan record pada satu nomor baris tertentu secara aman."""
    return [
        copy.deepcopy(d) for d in data_list
        if is_same_baris(d, baris_num) and (not only_valid or is_valid_item(d))
    ]

def parse_keterangan(ket_str):
    """Membaca jumlah Pecahan (Dus/Sak) dan Eceran (Pcs) dari string Keterangan."""
    s = clean_str(ket_str).lower()
    m_pec = re.search(r'pecahan:\s*(\d+)', s)
    m_ec = re.search(r'eceran:\s*(\d+)', s)
    return (int(m_pec.group(1)) if m_pec else 0, int(m_ec.group(1)) if m_ec else 0)

def format_keterangan(pecahan, eceran):
    """Membentuk string Keterangan standar dari nilai Pecahan dan Eceran."""
    parts = []
    if pecahan > 0:
        parts.append(f"Pecahan: {int(pecahan)} qty")
    if eceran > 0:
        parts.append(f"Eceran: {int(eceran)} pcs")
    return " | ".join(parts)

def get_satuan_kemasan(part_number):
    """Menentukan label satuan pecahan berdasarkan akhiran Part Number."""
    pn = clean_str(part_number).upper()
    if pn.endswith(('D', 'G')):
        return "dus"
    if pn.endswith('C'):
        return "sak"
    if pn.endswith('A'):
        return "nictainer"
    return "qty"

def get_master_konversi(df_master, part_num):
    """Mengambil (Isi 1 Pallet pcs, Pcs per Dus/Sak) dari Master_Produk.csv."""
    pn = clean_str(part_num).upper()
    if df_master.empty or 'Part Number Clean' not in df_master.columns or pn not in df_master['Part Number Clean'].values:
        return 0, 0
    m_row = df_master[df_master['Part Number Clean'] == pn].iloc[0]
    isi_pallet = int(pd.to_numeric(m_row.get('Isi 1 Pallet (pcs)', 0), errors='coerce') or 0)
    col_satuan = 'Sac / Pallet' if pn.endswith('C') else 'Box / Pallet'
    pcs_per_satuan = int(pd.to_numeric(m_row.get(col_satuan, 0), errors='coerce') or 0)
    return isi_pallet, pcs_per_satuan

def hitung_konversi_mutasi_pcs(df_master, part_num, awal_p, awal_d, awal_pcs, sisa_p, sisa_d, sisa_pcs):
    """
    Menghitung selisih keluar berbasis total Pcs lalu memecahnya ke (Pallet, Dus/Sak, Eceran Pcs)
    sehingga mencegah angka minus saat palet utuh dipecah.
    """
    isi_pallet, pcs_per_satuan = get_master_konversi(df_master, part_num)
    if isi_pallet > 0 and pcs_per_satuan > 0:
        tot_awal = (awal_p * isi_pallet) + (awal_d * pcs_per_satuan) + awal_pcs
        tot_sisa = (sisa_p * isi_pallet) + (sisa_d * pcs_per_satuan) + sisa_pcs
        diff_pcs = tot_awal - tot_sisa
        if diff_pcs < 0:
            return None, None, None, -1
        kel_p = diff_pcs // isi_pallet
        rem = diff_pcs % isi_pallet
        kel_d = rem // pcs_per_satuan
        kel_pcs = rem % pcs_per_satuan
        return int(kel_p), int(kel_d), int(kel_pcs), int(diff_pcs)

    # Fallback jika item belum terdaftar lengkap di Master_Produk.csv
    diff_p = int(awal_p - sisa_p)
    diff_d = int(awal_d - sisa_d)
    diff_ec = int(awal_pcs - sisa_pcs)
    if diff_p < 0 or (diff_p == 0 and diff_d < 0) or (diff_p == 0 and diff_d == 0 and diff_ec < 0):
        return None, None, None, -1
    return diff_p, diff_d, diff_ec, 0

def hitung_ulang_pcs_log(df_log, df_master):
    """Menghitung ulang Total Pcs mutasi secara dinamis berdasarkan Master_Produk.csv terbaru."""
    if df_log.empty:
        return df_log

    pcs_recalc_list = []
    selisih_info_list = []

    for _, row in df_log.iterrows():
        pn = clean_str(row.get('Part Number')).upper()
        m_pallet = float(pd.to_numeric(row.get('Mutasi Pallet', 0), errors='coerce') or 0)
        m_pecahan = float(pd.to_numeric(row.get('Mutasi Pecahan (Dus/Sak)', 0), errors='coerce') or 0)
        m_eceran_lama = float(pd.to_numeric(row.get('Mutasi Eceran (Pcs)', 0), errors='coerce') or 0)

        isi_pallet, pcs_per_satuan = get_master_konversi(df_master, pn)

        if isi_pallet > 0 and (m_pallet != 0 or m_pecahan != 0):
            pcs_baru = int(round((m_pallet * isi_pallet) + (m_pecahan * pcs_per_satuan)))
            pcs_recalc_list.append(pcs_baru)

            if m_eceran_lama > 0 and int(round(m_eceran_lama)) != pcs_baru:
                selisih_info_list.append(f"Master Baru (Lama: {int(round(m_eceran_lama)):,})")
            else:
                selisih_info_list.append("Sesuai Master")
        else:
            pcs_recalc_list.append(int(round(m_eceran_lama)))
            selisih_info_list.append("Eceran / Non-Master")

    df_log['Total Pcs (Master Terbaru)'] = pcs_recalc_list
    df_log['Status Konversi'] = selisih_info_list
    return df_log

def tentukan_arus_log(jenis_transaksi):
    """Menentukan kategori arus stok standar (MASUK / KELUAR / PINDAH / KOREKSI)."""
    jt = clean_str(jenis_transaksi).upper()
    if "INBOUND" in jt or "RESTORE" in jt:
        return "MASUK"
    if "OUTBOUND" in jt or "WIP" in jt or "STAGING" in jt:
        return "KELUAR"
    if "RELOKASI" in jt:
        return "PINDAH"
    return "KOREKSI"

# ==========================================================
# 4. CETAK KARTU RACKING / PICKING LIST
# ==========================================================
def build_picking_cards(baris_num, kolom_id, baris_records):
    """Membentuk daftar blok kartu picking list (run-length grouping) untuk 1 lokasi rak."""
    lokasi_label = f"Baris {int(baris_num)}{kolom_id}"
    valid_items = sorted(
        [d for d in baris_records if is_valid_item(d)],
        key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))
    )

    if not valid_items:
        return [{
            'Lokasi': lokasi_label,
            'Nama Item': 'RAK KOSONG',
            'Part Number': '-',
            'Lot Produksi': '-',
            'Palet ke': '-',
            'Jumlah': '-',
            'Status QC': '-',
            'Is_Empty': True
        }]

    runs = []
    for item in valid_items:
        pn = clean_str(item.get('Part Number')).upper()
        lot = clean_str(item.get('Lot Number'))
        nama = clean_str(item.get('Nama Produk'))
        p_idx = int(pd.to_numeric(item.get('Pallet Ke', 0)) or 0)
        ket = clean_str(item.get('Keterangan'))
        qc_val = clean_str(item.get('Approval QC'))

        if runs and runs[-1]['pn'] == pn and runs[-1]['lot'] == lot:
            runs[-1]['items'].append((p_idx, ket))
            if qc_val and not runs[-1]['qc']:
                runs[-1]['qc'] = qc_val
        else:
            runs.append({'pn': pn, 'lot': lot, 'nama': nama, 'qc': qc_val, 'items': [(p_idx, ket)]})

    cards = []
    for r in runs:
        start_p = r['items'][0][0]
        end_p = r['items'][-1][0]
        palet_range = f"{start_p}" if start_p == end_p else f"{start_p}-{end_p}"

        utuh_cnt, dus_sak_cnt, pcs_cnt = 0, 0, 0
        for _, ket in r['items']:
            if 'pecahan' in ket.lower() or 'eceran' in ket.lower():
                p_val, e_val = parse_keterangan(ket)
                dus_sak_cnt += p_val
                pcs_cnt += e_val
            else:
                utuh_cnt += 1

        satuan = get_satuan_kemasan(r['pn'])
        qty_parts = []
        if utuh_cnt > 0:
            qty_parts.append(f"{utuh_cnt}P")
        if dus_sak_cnt > 0:
            qty_parts.append(f"{dus_sak_cnt} {satuan}")
        if pcs_cnt > 0:
            qty_parts.append(f"{pcs_cnt} pcs")
        jumlah_str = " + ".join(qty_parts) if qty_parts else "0P"

        cards.append({
            'Lokasi': lokasi_label,
            'Nama Item': r['nama'],
            'Part Number': r['pn'],
            'Lot Produksi': r['lot'] or '-',
            'Palet ke': palet_range,
            'Jumlah': jumlah_str,
            'Status QC': r['qc'] or 'Belum QC',
            'Is_Empty': False
        })
    return cards

def render_print_html(cards_list, judul_cetak, waktu_cetak):
    """Merender HTML siap cetak A4 dengan tombol Print langsung di browser."""
    cards_html = []
    for c in cards_list:
        lok = html.escape(str(c['Lokasi']))
        nama = html.escape(str(c['Nama Item']))
        pn = html.escape(str(c['Part Number']))
        lot = html.escape(str(c['Lot Produksi']))
        palet = html.escape(str(c['Palet ke']))
        jml = html.escape(str(c['Jumlah']))
        qc_st = html.escape(str(c.get('Status QC', '-')))

        row_class = "empty-row" if c['Is_Empty'] else "data-row"
        cards_html.append(f"""
        <div class="card-block">
            <table>
                <thead>
                    <tr class="loc-header">
                        <th colspan="6">{lok}</th>
                    </tr>
                    <tr class="col-header">
                        <th style="width: 34%;">Nama Item</th>
                        <th style="width: 15%;">Part Number</th>
                        <th style="width: 13%;">Lot Produksi</th>
                        <th style="width: 10%;">Palet ke</th>
                        <th style="width: 14%;">Jumlah</th>
                        <th style="width: 14%;">Status QC</th>
                    </tr>
                </thead>
                <tbody>
                    <tr class="{row_class}">
                        <td>{nama}</td>
                        <td>{pn}</td>
                        <td>{lot}</td>
                        <td class="center">{palet}</td>
                        <td class="bold">{jml}</td>
                        <td>{qc_st}</td>
                    </tr>
                </tbody>
            </table>
        </div>
        """)

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, Helvetica, sans-serif; margin: 12px; color: #000; background: #fff; }}
        .toolbar {{
            display: flex; justify-content: space-between; align-items: center;
            padding: 10px 14px; margin-bottom: 14px;
            background: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 6px;
        }}
        .btn-print {{
            background: #0f172a; color: #fff; border: none; padding: 8px 16px;
            font-size: 13px; font-weight: bold; border-radius: 4px; cursor: pointer;
        }}
        .btn-print:hover {{ background: #334155; }}
        .meta {{ font-size: 12px; color: #334155; }}
        .card-block {{ margin-bottom: 14px; page-break-inside: avoid; break-inside: avoid; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
        th, td {{ border: 1px solid #000; padding: 5px 8px; text-align: left; }}
        tr.loc-header th {{ background-color: #e2e8f0; font-size: 13px; font-weight: bold; }}
        tr.col-header th {{ background-color: #f8fafc; font-weight: bold; }}
        tr.empty-row td {{ color: #64748b; font-style: italic; }}
        td.center {{ text-align: center; }}
        td.bold {{ font-weight: bold; }}
        @media print {{
            .toolbar {{ display: none !important; }}
            body {{ margin: 0; }}
            .card-block {{ margin-bottom: 12px; }}
        }}
    </style>
    </head>
    <body>
        <div class="toolbar">
            <div class="meta">
                <strong>{html.escape(judul_cetak)}</strong> • Dicetak: {html.escape(waktu_cetak)} • Total Kartu: {len(cards_list)}
            </div>
            <button class="btn-print" onclick="window.print()">🖨️ Cetak / Simpan PDF</button>
        </div>
        {"".join(cards_html)}
    </body>
    </html>
    """

# ==========================================================
# 5. OPERASI SLOT RAK, CACHE & AUDIT STORAGE
# ==========================================================
def summarize_lot_groups(baris_records):
    """Meringkas list record satu baris menjadi grup per (Lot, Produk, Catatan)."""
    df = pd.DataFrame(baris_records)
    df['Pallet Ke'] = pd.to_numeric(df['Pallet Ke'], errors='coerce')
    df['Catatan_Clean'] = df['Catatan'].apply(clean_str) if 'Catatan' in df.columns else ""
    df['Keterangan_Clean'] = df['Keterangan'].apply(clean_str) if 'Keterangan' in df.columns else ""
    df['Approval_QC_Clean'] = df['Approval QC'].apply(clean_str) if 'Approval QC' in df.columns else ""
    df['Group_Key'] = df.apply(
        lambda r: make_g_key(r.get('Lot Number'), r.get('Nama Produk'), r.get('Catatan')), axis=1
    )

    groups = []
    for _, row in df.drop_duplicates(subset=['Group_Key']).iterrows():
        g_key = str(row['Group_Key'])
        df_lot = df[df['Group_Key'] == g_key]
        is_frac_mask = df_lot['Keterangan_Clean'].str.contains('Pecahan|Eceran', case=False, na=False)
        jml_utuh = int((~is_frac_mask).sum())
        pecahan, eceran = 0, 0
        for ket_val in df_lot.loc[is_frac_mask, 'Keterangan_Clean']:
            p, e = parse_keterangan(ket_val)
            pecahan += p
            eceran += e

        qc_vals = [q for q in df_lot['Approval_QC_Clean'].unique() if q]
        groups.append({
            'Group_Key': g_key,
            'Produk': clean_str(row.get('Nama Produk')),
            'Part Number': clean_str(row.get('Part Number')).upper(),
            'Lot Number': clean_str(row.get('Lot Number')),
            'Catatan': str(row['Catatan_Clean']),
            'Approval QC': qc_vals[0] if qc_vals else "",
            'Utuh': jml_utuh,
            'Dus': int(pecahan),
            'Pcs': int(eceran),
        })
    return groups

def apply_slot_reduction(records_list, baris_num, g_key, sisa_utuh, sisa_dus, sisa_pcs, catatan_tambahan=""):
    """
    Mengurangi slot dari baris rak secara immutable (tanpa mutasi in-place)
    dan memperbarui pecahan serta catatan hanya pada palet yang tersisa.
    """
    df_lot = pd.DataFrame([
        d for d in records_list
        if match_g_key(d, g_key) and is_same_baris(d, baris_num) and is_valid_item(d)
    ])
    if df_lot.empty:
        return None, "empty"

    df_lot['Pallet Ke'] = pd.to_numeric(df_lot['Pallet Ke'])
    df_lot = df_lot.sort_values('Pallet Ke', ascending=False)

    slot_tambahan = 1 if (sisa_dus > 0 or sisa_pcs > 0) else 0
    total_slot_sisa = sisa_utuh + slot_tambahan
    if total_slot_sisa > len(df_lot):
        return None, f"over:{len(df_lot)}"

    out_qty = len(df_lot) - total_slot_sisa
    pallets_to_remove = df_lot['Pallet Ke'].head(out_qty).tolist() if out_qty > 0 else []
    sisa_pallets = df_lot[~df_lot['Pallet Ke'].isin(pallets_to_remove)]
    max_sisa_pallet = sisa_pallets['Pallet Ke'].max() if not sisa_pallets.empty else None

    updated_records = []
    for orig_d in records_list:
        d = copy.deepcopy(orig_d)
        if match_g_key(d, g_key) and is_same_baris(d, baris_num):
            p_val = pd.to_numeric(d.get('Pallet Ke', 0))
            if p_val in pallets_to_remove:
                continue

            c_lama = clean_str(d.get('Catatan'))
            if catatan_tambahan and catatan_tambahan not in c_lama:
                d['Catatan'] = f"{c_lama} | {catatan_tambahan}".strip(' |')
            else:
                d['Catatan'] = c_lama

            if max_sisa_pallet is not None and p_val == max_sisa_pallet:
                d['Keterangan'] = format_keterangan(sisa_dus, sisa_pcs)
            else:
                ket_lama = clean_str(d.get('Keterangan'))
                if 'Pecahan' in ket_lama or 'Eceran' in ket_lama:
                    d['Keterangan'] = ""
        updated_records.append(d)

    return updated_records, ""

def get_max_slot(kolom_id, baris_num, records_baris, extra_part=""):
    """Menghitung kapasitas maksimal slot pada satu baris rak."""
    if kolom_id == 'S':
        return 999
    valid_parts = [clean_str(d.get('Part Number')).upper() for d in records_baris if is_valid_item(d)]
    if clean_str(extra_part):
        valid_parts.append(clean_str(extra_part).upper())
    is_all_box = bool(valid_parts) and all(pn.endswith(('D', 'G')) for pn in valid_parts)
    if is_all_box:
        return 32 if baris_num <= 16 else 46
    return 31 if baris_num <= 16 else 44

def update_bayangan_baris(records_baris, kolom_id, baris_num):
    """Mengevaluasi ulang indeks Pallet Ke (1..N), sanitasi string, dan tag [BAYANGAN - OVERCAPACITY]."""
    max_slot = get_max_slot(kolom_id, baris_num, records_baris)
    result = []
    for idx, orig_item in enumerate(records_baris, start=1):
        item = copy.deepcopy(orig_item)
        item['Pallet Ke'] = float(idx)
        item['Keterangan'] = clean_str(item.get('Keterangan'))
        item['Approval QC'] = clean_str(item.get('Approval QC'))
        cat_lama = clean_str(item.get('Catatan'))
        if idx > max_slot and 'BAYANGAN' not in cat_lama.upper():
            item['Catatan'] = f"[BAYANGAN - OVERCAPACITY] {cat_lama}".strip()
        elif idx <= max_slot and 'BAYANGAN' in cat_lama.upper():
            item['Catatan'] = cat_lama.replace("[BAYANGAN - OVERCAPACITY]", "").strip()
        else:
            item['Catatan'] = cat_lama
        result.append(item)
    return result

def hitung_ukuran_firestore(val):
    """Menghitung estimasi ukuran byte sesuai aturan resmi penyimpanan Cloud Firestore."""
    if val is None or isinstance(val, bool):
        return 1
    if isinstance(val, (int, float, datetime)):
        return 8
    if isinstance(val, str):
        return len(val.encode('utf-8')) + 1
    if isinstance(val, bytes):
        return len(val) + 1
    if isinstance(val, list):
        return sum(hitung_ukuran_firestore(item) for item in val)
    if isinstance(val, dict):
        return sum((len(str(k).encode('utf-8')) + 1) + hitung_ukuran_firestore(v) for k, v in val.items())
    return 0

def hitung_ukuran_dokumen(col_name, doc_id, data_dict):
    """Menghitung total byte 1 dokumen lengkap dengan overhead path + 32 bytes."""
    overhead_path = len(col_name.encode('utf-8')) + len(doc_id.encode('utf-8')) + 32
    return overhead_path + hitung_ukuran_firestore(data_dict)

def record_doc_size(doc_name, data_list):
    """Memperbarui catatan ukuran byte dokumen di session_state tanpa Read tambahan."""
    st.session_state['doc_sizes'][doc_name] = hitung_ukuran_dokumen(NAMA_DATABASE, doc_name, {'data': data_list})

def fetch_kolom_cached(kolom_id, state_key, force_refresh=False):
    """Membaca dokumen Kolom sekali saja saat diminta, sekaligus mencatat ukuran byte-nya."""
    if force_refresh or state_key not in st.session_state or st.session_state[state_key] is None:
        doc_name = f"Kolom_{kolom_id}"
        doc = db.collection(NAMA_DATABASE).document(doc_name).get()
        log_fb_usage('read', 1)
        data_list = copy.deepcopy(doc.to_dict().get('data', [])) if doc.exists else []
        record_doc_size(doc_name, data_list)
        st.session_state[state_key] = data_list
    return st.session_state[state_key]

def clear_all_rak_caches():
    """Membersihkan seluruh cache lokal rak setiap kali ada mutasi tersimpan."""
    for k in ['cache_in_data', 'cache_out_data', 'cache_relok_asal', 'cache_kor_data']:
        st.session_state[k] = None
    st.session_state['print_rak_cache'] = {}
    st.session_state['print_ready'] = False

@st.cache_data(ttl=300)
def load_master_produk():
    """Memuat Master Data Produk untuk Auto-fill Part Number & Konversi Satuan."""
    try:
        try:
            df = pd.read_csv("Master_Produk.csv", sep=";", encoding='utf-8-sig')
        except Exception:
            df = pd.read_csv("Master_Produk.csv", sep=",", encoding='utf-8-sig')
        df.columns = df.columns.str.strip()
        if 'Part Number' in df.columns:
            df['Part Number Clean'] = df['Part Number'].apply(clean_str).str.upper()
        return df
    except Exception as e:
        st.toast(f"Gagal memuat master produk: {e}", icon="🚨")
        return pd.DataFrame()

def catat_log_mutasi(
    jenis, kolom, baris, produk, part_num, no_lot,
    pallet_utuh, pecahan, eceran, keterangan="",
    tujuan_kategori="", detail_sistem=""
):
    """Mencatat setiap pergerakan stok dengan pemisahan Tujuan/Kategori vs Detail Sistem."""
    b_str = str(baris).strip()
    lok_str = f"{kolom}-{b_str}" if b_str and b_str.upper() != "NONE" else str(kolom)
    tujuan_clean = clean_str(tujuan_kategori) or clean_str(keterangan)
    detail_clean = clean_str(detail_sistem)
    ket_gabungan = f"{tujuan_clean} | {detail_clean}".strip(" |") if detail_clean else tujuan_clean

    log_data = {
        "Waktu Transaksi": get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S"),
        "Arus": tentukan_arus_log(jenis),
        "Jenis Transaksi": jenis,
        "Lokasi Rak": lok_str,
        "Nama Produk": clean_str(produk),
        "Part Number": clean_str(part_num),
        "No Lot": clean_str(no_lot) or "-",
        "Mutasi Pallet": float(pallet_utuh),
        "Mutasi Pecahan (Dus/Sak)": float(pecahan),
        "Mutasi Eceran (Pcs)": float(eceran),
        "Tujuan / Kategori": tujuan_clean,
        "Detail Sistem": detail_clean,
        "Keterangan": ket_gabungan,
        "Timestamp_System": firestore.SERVER_TIMESTAMP
    }
    try:
        db.collection(DB_LOG_MUTASI).add(log_data)
        log_fb_usage('write', 1)
        if 'total_log_bytes' in st.session_state:
            st.session_state['total_log_bytes'] += hitung_ukuran_dokumen(DB_LOG_MUTASI, "X" * 20, log_data)
            st.session_state['total_log_docs'] += 1
    except Exception as e:
        st.toast(f"⚠️ Peringatan: Gagal mencatat log mutasi ({e})", icon="⚠️")

# ==========================================================
# 6. SIDEBAR NAVIGASI, KUOTA & MONITOR STORAGE
# ==========================================================
st.sidebar.markdown("### ⚙️ Kontrol Sistem")
if st.sidebar.button("🔄 Reload Master Data CSV", use_container_width=True):
    st.cache_data.clear()
    st.toast("Master Data berhasil diperbarui dari GitHub!", icon="✅")
    st.rerun()

st.sidebar.divider()
st.sidebar.markdown("### 📊 Monitor Kuota Sesi Ini")
u_col1, u_col2, u_col3 = st.sidebar.columns(3)
u_col1.metric("Read", st.session_state['fb_usage']['read'])
u_col2.metric("Write", st.session_state['fb_usage']['write'])
u_col3.metric("Delete", st.session_state['fb_usage']['delete'])
st.sidebar.caption("Menghitung operasi Firebase pada sesi browser aktif ini.")

if st.sidebar.button("Reset Counter Sesi", use_container_width=True):
    st.session_state['fb_usage'] = {'read': 0, 'write': 0, 'delete': 0}
    st.rerun()

st.sidebar.divider()
st.sidebar.markdown("### 💾 Monitor Storage (Spark Limit)")

if st.session_state['doc_sizes']:
    for d_name, b_size in sorted(st.session_state['doc_sizes'].items()):
        kb_size = b_size / 1024
        persen_doc = (b_size / LIMIT_DOC_BYTES) * 100
        ikon_doc = "🔴" if persen_doc >= 80 else ("🟡" if persen_doc >= 50 else "🟢")
        st.sidebar.caption(f"{ikon_doc} **{d_name}**: `{b_size:,}` B ({kb_size:.1f} KB | {persen_doc:.1f}% dari 1 MiB)")
else:
    st.sidebar.caption("Belum ada dokumen rak yang diakses pada sesi ini.")

if st.sidebar.button("📏 Cek Total Storage (Rak + Log)", use_container_width=True):
    docs_all = list(db.collection(NAMA_DATABASE).stream())
    log_fb_usage('read', len(docs_all))
    total_rak_b = 0
    for d in docs_all:
        d_list = (d.to_dict() or {}).get('data', [])
        record_doc_size(d.id, d_list)
        total_rak_b += st.session_state['doc_sizes'][d.id]
    st.session_state['total_rak_bytes'] = total_rak_b

    try:
        count_res = db.collection(DB_LOG_MUTASI).count().get()
        jml_log = count_res[0][0].value
        est_reads = max(1, (jml_log + 999) // 1000)
        log_fb_usage('read', est_reads)

        sample_docs = list(db.collection(DB_LOG_MUTASI).limit(1).stream())
        if sample_docs:
            log_fb_usage('read', 1)
            avg_log_b = hitung_ukuran_dokumen(DB_LOG_MUTASI, sample_docs[0].id, sample_docs[0].to_dict() or {})
        else:
            avg_log_b = 420

        st.session_state['total_log_docs'] = jml_log
        st.session_state['total_log_bytes'] = jml_log * avg_log_b
    except Exception:
        st.session_state['total_log_docs'] = 0
        st.session_state['total_log_bytes'] = 0

    st.rerun()

if 'total_rak_bytes' in st.session_state:
    tot_rak = st.session_state['total_rak_bytes']
    tot_log = st.session_state.get('total_log_bytes', 0)
    jml_log_docs = st.session_state.get('total_log_docs', 0)
    grand_tot_b = tot_rak + tot_log
    persen_spark = (grand_tot_b / LIMIT_SPARK_BYTES) * 100

    st.sidebar.info(
        f"**1. Rak Utama (8 Dok):** `{tot_rak:,}` B ({tot_rak / (1024*1024):.2f} MB)\n\n"
        f"**2. Log Mutasi ({jml_log_docs:,} Dok):** ~`{tot_log:,}` B ({tot_log / (1024*1024):.2f} MB)\n\n"
        f"**Total Gabungan:** `{grand_tot_b / (1024*1024):.2f} MB` (**{persen_spark:.4f}%** dari 1 GiB Spark Plan)"
    )

st.sidebar.divider()
st.sidebar.button("🚪 Logout", on_click=lambda: st.session_state.update({'logged_in': False}), use_container_width=True)

# ==========================================================
# 7. HEADER & NOTIFIKASI UNDO
# ==========================================================
st.title("📝 WMS Entry Form (Inbound, Outbound & Relokasi)")
st.caption(f"Alat khusus PPIC. Terhubung ke database: {NAMA_DATABASE}")

if st.session_state['action_status'] in ['DONE', 'DONE_NO_UNDO']:
    st.toast(f"Berhasil: {st.session_state['action_msg']}", icon="✅")

if st.session_state['action_status'] == 'DONE' and st.session_state.get('undo_dict'):
    with st.container(border=True):
        st.success(f"✅ **Sukses:** {st.session_state['action_msg']}")
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            st.caption("Jika terjadi kesalahan input, Anda bisa mengembalikan stok dan atribut rak ke kondisi persis sebelum aksi ini.")
        with c2:
            if st.button("🔙 UNDO AKSI INI", type="primary", use_container_width=True):
                with st.spinner("Memulihkan data ke kondisi semula..."):
                    snapshot_restore = copy.deepcopy(st.session_state['undo_dict'])
                    batch_undo = db.batch()
                    kolom_terdampak = []
                    for kol, dat in snapshot_restore.items():
                        doc_name = f"Kolom_{kol}"
                        batch_undo.set(db.collection(NAMA_DATABASE).document(doc_name), {'data': dat})
                        record_doc_size(doc_name, dat)
                        kolom_terdampak.append(kol)
                    batch_undo.commit()
                    log_fb_usage('write', len(kolom_terdampak))
                    clear_all_rak_caches()
                    st.session_state['undo_dict'] = {}
                    st.session_state['action_status'] = 'UNDONE'
                    catat_log_mutasi(
                        "PEMBATALAN (UNDO)", ",".join(kolom_terdampak), "ALL",
                        "Koreksi Sistem", "-", "-", 0, 0, 0,
                        keterangan=f"Membatalkan: {st.session_state['action_msg']}",
                        tujuan_kategori="UNDO TRANSAKSI",
                        detail_sistem=f"Membatalkan: {st.session_state['action_msg']}"
                    )
                    st.rerun()
        with c3:
            if st.button("Tutup & Lanjut Input", use_container_width=True):
                st.session_state['undo_dict'] = {}
                st.session_state['action_status'] = None
                st.rerun()

elif st.session_state['action_status'] == 'DONE_NO_UNDO':
    with st.container(border=True):
        st.success(f"✅ **Sukses:** {st.session_state['action_msg']}")
        if st.button("Tutup Pesan Ini"):
            st.session_state['action_status'] = None
            st.rerun()

elif st.session_state['action_status'] == 'UNDONE':
    with st.container(border=True):
        st.warning("⏪ **Berhasil dibatalkan.** Stok, pecahan dus/sak, dan catatan telah kembali persis seperti semula.")
        if st.button("Tutup Pesan", use_container_width=True):
            st.session_state['action_status'] = None
            st.rerun()

# ==========================================================
# 8. TABS MENU UTAMA (8 TAB OPERASIONAL)
# ==========================================================
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "📥 1. Inbound", "📤 2. Outbound", "⚙️ 3. Audit & Backup",
    "📊 4. Log Mutasi", "🚀 5. Sync CSV", "🔄 6. Relokasi",
    "✏️ 7. Koreksi", "🖨️ 8. Cetak Racking"
])

# ----------------------------------------------------------
# TAB 1: INBOUND
# ----------------------------------------------------------
with tab1:
    st.header("Input Barang Masuk")
    reset_key = st.session_state['form_reset_counter']

    col_in1, col_in2 = st.columns([1, 2])
    with col_in1:
        st.markdown("**1. Pilih Lokasi Rak Tujuan**")
        in_kolom = st.selectbox("Lokasi Kolom", KOLOM_OPTIONS, key="in_kolom").split(" ")[0]
        in_baris = st.number_input("Nomor Baris (Inbound)", min_value=1, max_value=29, value=1)
        if st.button("🔍 Cek Status Rak", use_container_width=True):
            st.session_state.update({'in_lokasi_dicari': True, 'in_k_pilih': in_kolom, 'in_b_pilih': in_baris})
            fetch_kolom_cached(in_kolom, 'cache_in_data', force_refresh=True)

    with col_in2:
        if st.session_state['in_lokasi_dicari']:
            k_pilih, b_pilih = st.session_state['in_k_pilih'], st.session_state['in_b_pilih']
            all_data_raw = fetch_kolom_cached(k_pilih, 'cache_in_data', force_refresh=False)
            baris_data_preview = get_baris_records(all_data_raw, b_pilih, only_valid=True)

            max_slot_display = get_max_slot(k_pilih, b_pilih, baris_data_preview)
            current_qty = len(baris_data_preview)
            sisa_ruang = max_slot_display - current_qty

            st.markdown(f"**📦 Status Rak {k_pilih}-{b_pilih} | Terisi: `{current_qty}` / `{max_slot_display}` (Sisa Ruang: `{sisa_ruang}` Slot)**")
            if baris_data_preview:
                df_baris = pd.DataFrame(baris_data_preview)
                df_baris['Pallet Ke'] = pd.to_numeric(df_baris['Pallet Ke'], errors='coerce')
                df_baris = df_baris.sort_values('Pallet Ke', ascending=True)
                kolom_tersedia = [c for c in ['Pallet Ke', 'Part Number', 'Nama Produk', 'Lot Number', 'Keterangan', 'Approval QC'] if c in df_baris.columns]
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
                in_part = st.text_input("Part Number (Auto-fill)", value=clean_str(match_part[0]) if len(match_part) > 0 else "", disabled=True)
            else:
                in_produk = st.text_input("Nama Produk (Ketik Manual)")
                in_part = st.text_input("Part Number (Ketik Manual)")

            in_lot = st.text_input("No. Lot Produksi / Mesin Shift", placeholder="WAJIB DIISI", key=f"in_lot_input_{reset_key}")

            col_qty1, col_qty2, col_qty3 = st.columns(3)
            with col_qty1:
                in_qty = st.number_input("Pallet Masuk UTUH", min_value=0, step=1, value=1, key=f"in_qty_input_{reset_key}")
            with col_qty2:
                in_pecahan = st.number_input("Sisa Pecahan (Dus/Sak)", min_value=0, step=1, value=0, key=f"in_pecahan_input_{reset_key}")
            with col_qty3:
                in_eceran = st.number_input("Eceran (Pcs)", min_value=0, step=1, value=0, key=f"in_eceran_input_{reset_key}")

            in_catatan_ops = st.text_input("Catatan Rak (Opsional)", placeholder="Cth: Titipan Produksi / Retur", key=f"in_cat_input_{reset_key}")
            in_dari_depan = st.checkbox("📥 Masukkan dari DEPAN Rak (Push-back)")

            if st.button("🚀 Simpan Inbound", type="primary", use_container_width=True):
                in_part_clean = clean_str(in_part).upper()
                in_produk_clean = clean_str(in_produk)
                in_lot_final = clean_str(in_lot)
                in_cat_final = clean_str(in_catatan_ops)

                if not in_lot_final or in_lot_final == "-":
                    st.error("⚠️ No. Lot bersifat WAJIB DIISI!")
                elif not in_produk_clean or not in_part_clean:
                    st.error("⚠️ Nama Produk dan Part Number wajib diisi!")
                elif in_qty == 0 and in_pecahan == 0 and in_eceran == 0:
                    st.error("⚠️ Masukkan minimal 1 Qty!")
                else:
                    with st.spinner("Menyusun data ke dalam rak (Transactional Lock)..."):
                        doc_name_in = f"Kolom_{k_pilih}"
                        doc_ref_in = db.collection(NAMA_DATABASE).document(doc_name_in)

                        @firestore.transactional
                        def tx_inbound(transaction, doc_ref):
                            snap = doc_ref.get(transaction=transaction)
                            log_fb_usage('read', 1)
                            fresh_raw = copy.deepcopy(snap.to_dict().get('data', [])) if snap.exists else []
                            working_raw = copy.deepcopy(fresh_raw)

                            all_data = [
                                d for d in working_raw
                                if not (is_same_baris(d, b_pilih) and not is_valid_item(d))
                            ]
                            baris_data_actual = get_baris_records(all_data, b_pilih, only_valid=False)
                            lain_data_actual = [d for d in all_data if not is_same_baris(d, b_pilih)]

                            existing_fraction_row = None
                            if not in_dari_depan:
                                for d in baris_data_actual:
                                    if (clean_str(d.get('Part Number')).upper() == in_part_clean
                                            and clean_str(d.get('Lot Number')) == in_lot_final):
                                        ket = clean_str(d.get('Keterangan'))
                                        if 'Pecahan' in ket or 'Eceran' in ket:
                                            existing_fraction_row = d

                            slot_tambahan_in = 1 if ((in_pecahan > 0 or in_eceran > 0) and not existing_fraction_row) else 0
                            total_inbound = int(in_qty) + slot_tambahan_in

                            new_records = [
                                {
                                    'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': 0.0,
                                    'Part Number': in_part_clean, 'Lot Number': in_lot_final, 'Nama Produk': in_produk_clean,
                                    'Status': 'Terisi', 'Keterangan': '', 'Catatan': in_cat_final, 'Approval QC': ''
                                }
                                for _ in range(int(in_qty))
                            ]

                            if in_pecahan > 0 or in_eceran > 0:
                                if existing_fraction_row and not in_dari_depan:
                                    ex_pec, ex_ec = parse_keterangan(existing_fraction_row.get('Keterangan'))
                                    existing_fraction_row['Keterangan'] = format_keterangan(ex_pec + in_pecahan, ex_ec + in_eceran)
                                else:
                                    new_records.append({
                                        'Kolom': k_pilih, 'Baris': float(b_pilih), 'Pallet Ke': 0.0,
                                        'Part Number': in_part_clean, 'Lot Number': in_lot_final, 'Nama Produk': in_produk_clean,
                                        'Status': 'Terisi', 'Keterangan': format_keterangan(in_pecahan, in_eceran),
                                        'Catatan': in_cat_final, 'Approval QC': ''
                                    })

                            baris_sorted = sorted(baris_data_actual, key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                            combined_baris = (new_records + baris_sorted) if in_dari_depan else (baris_sorted + new_records)
                            combined_baris = update_bayangan_baris(combined_baris, k_pilih, b_pilih)

                            final_kolom_data = lain_data_actual + combined_baris
                            transaction.set(doc_ref, {'data': final_kolom_data})
                            log_fb_usage('write', 1)
                            return fresh_raw, total_inbound, final_kolom_data

                        fresh_snapshot, total_inbound_slots, final_data_in = tx_inbound(db.transaction(), doc_ref_in)
                        record_doc_size(doc_name_in, final_data_in)

                        isi_p, pcs_s = get_master_konversi(df_master, in_part_clean)
                        tot_in_pcs = ((in_qty * isi_p) + (in_pecahan * pcs_s) + in_eceran) if isi_p > 0 else in_eceran

                        sisi_lbl = "Push-Back (Depan)" if in_dari_depan else "Normal (Belakang)"
                        det_in = f"Masuk Rak {k_pilih}-{b_pilih} | Sisi: {sisi_lbl}" + (f" | Catatan: {in_cat_final}" if in_cat_final else "")
                        catat_log_mutasi(
                            "INBOUND (Admin)", k_pilih, b_pilih, in_produk_clean, in_part_clean, in_lot_final,
                            in_qty, in_pecahan, tot_in_pcs,
                            keterangan=det_in,
                            tujuan_kategori="INBOUND PRODUKSI",
                            detail_sistem=det_in
                        )
                        clear_all_rak_caches()

                        st.session_state.update({
                            'undo_dict': {k_pilih: copy.deepcopy(fresh_snapshot)},
                            'action_msg': f"Inbound {total_inbound_slots} Slot '{in_produk_clean}' ke Rak {k_pilih}-{b_pilih} {'(Push-back)' if in_dari_depan else ''}",
                            'action_status': 'DONE',
                            'kor_dicari': False,
                            'relok_dicari': False
                        })
                        st.session_state['form_reset_counter'] += 1
                        st.rerun()

# ----------------------------------------------------------
# TAB 2: OUTBOUND MULTI-LOT (ANTI-MINUS + UNDO SNAPSHOT AMAN)
# ----------------------------------------------------------
with tab2:
    st.header("Penyesuaian Sisa Stok (Outbound Massal)")
    st.caption("Ubah angka sisa fisik pada tabel, pilih Kategori & Tujuan keluar, lalu klik Simpan.")

    st.markdown("**1. Pilih Lokasi Rak**")
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        out_kolom = st.selectbox("Lokasi Kolom", KOLOM_OPTIONS, key="out_kolom_2").split(" ")[0]
    with c2:
        out_baris = st.number_input("Nomor Baris", min_value=1, max_value=29, value=1, key="out_baris_2")
    with c3:
        st.write("")
        st.write("")
        if st.button("🔍 Cari Data Rak", use_container_width=True):
            st.session_state.update({'out_lokasi_dicari': True, 'out_k_pilih': out_kolom, 'out_b_pilih': out_baris})
            fetch_kolom_cached(out_kolom, 'cache_out_data', force_refresh=True)

    st.divider()

    if st.session_state['out_lokasi_dicari']:
        k_pilih, b_pilih = st.session_state['out_k_pilih'], st.session_state['out_b_pilih']
        all_out_data = fetch_kolom_cached(k_pilih, 'cache_out_data', force_refresh=False)
        baris_out_data = get_baris_records(all_out_data, b_pilih, only_valid=True)

        if baris_out_data:
            df_master_out = load_master_produk()
            rekap_lot = [
                {
                    'Group_Key': g['Group_Key'],
                    'Produk': g['Produk'],
                    'Part Number': g['Part Number'],
                    'Lot Number': g['Lot Number'],
                    'Status / Asal': g['Catatan'],
                    'Awal Utuh': g['Utuh'],
                    'Awal Dus': g['Dus'],
                    'Awal Pcs': g['Pcs'],
                    'Sisa Utuh Baru': g['Utuh'],
                    'Sisa Dus Baru': g['Dus'],
                    'Sisa Pcs Baru': g['Pcs'],
                    'Kategori': 'CUSTOMER',
                    'Tujuan / Alasan': ""
                }
                for g in summarize_lot_groups(baris_out_data)
            ]

            df_rekap = pd.DataFrame(rekap_lot)
            st.markdown(f"**2. Edit Sisa Fisik Rak {k_pilih}-{b_pilih} (Konversi Otomatis Anti-Minus)**")
            st.caption("💡 *Kategori: **CUSTOMER** (isi nama tujuan), **OUT** (tujuan menyusul), **WIP** (suplai produksi), atau **EC** (Error Correction).*")

            config_kolom = {
                'Group_Key': None,
                'Produk': st.column_config.TextColumn("Produk", disabled=True),
                'Part Number': st.column_config.TextColumn("Part", disabled=True),
                'Lot Number': st.column_config.TextColumn("Lot", disabled=True),
                'Status / Asal': st.column_config.TextColumn("Status / Asal", disabled=True),
                'Awal Utuh': st.column_config.NumberColumn("Sistem Utuh", disabled=True),
                'Awal Dus': st.column_config.NumberColumn("Sistem Dus/Sak", disabled=True),
                'Awal Pcs': st.column_config.NumberColumn("Sistem Pcs", disabled=True),
                'Sisa Utuh Baru': st.column_config.NumberColumn("SISA UTUH BARU", min_value=0, required=True),
                'Sisa Dus Baru': st.column_config.NumberColumn("SISA DUS/SAK BARU", min_value=0, required=True),
                'Sisa Pcs Baru': st.column_config.NumberColumn("SISA PCS BARU", min_value=0, required=True),
                'Kategori': st.column_config.SelectboxColumn(
                    "Kategori Keluar",
                    options=["CUSTOMER", "OUT", "WIP", "EC"],
                    required=True
                ),
                'Tujuan / Alasan': st.column_config.TextColumn("Nama Customer / Alasan EC", max_chars=80)
            }

            edited_df = st.data_editor(
                df_rekap, column_config=config_kolom, hide_index=True,
                use_container_width=True, num_rows="fixed", key=f"editor_out_{k_pilih}_{b_pilih}"
            )

            if st.button("🚜 Simpan Penyesuaian Massal (OUTBOUND)", type="primary", use_container_width=True):
                doc_name_out = f"Kolom_{k_pilih}"
                doc_ref_out = db.collection(NAMA_DATABASE).document(doc_name_out)

                @firestore.transactional
                def tx_outbound(transaction, doc_ref):
                    snap = doc_ref.get(transaction=transaction)
                    log_fb_usage('read', 1)
                    fresh_out_data = copy.deepcopy(snap.to_dict().get('data', [])) if snap.exists else []
                    working_out_data = copy.deepcopy(fresh_out_data)

                    has_changes = False
                    error_msg = ""
                    logs_to_write = []

                    data_rak_ini = get_baris_records(working_out_data, b_pilih, only_valid=True)
                    data_rak_lain = [d for d in working_out_data if not is_same_baris(d, b_pilih)]

                    for _, row in edited_df.iterrows():
                        g_key = str(row['Group_Key'])
                        l = clean_str(row['Lot Number'])
                        pn = clean_str(row['Part Number']).upper()
                        sisa_utuh, sisa_dus, sisa_pcs = int(row['Sisa Utuh Baru']), int(row['Sisa Dus Baru']), int(row['Sisa Pcs Baru'])
                        awal_utuh, awal_dus, awal_pcs = int(row['Awal Utuh']), int(row['Awal Dus']), int(row['Awal Pcs'])
                        kat_keluar = clean_str(row['Kategori']) or "OUT"
                        teks_tujuan = clean_str(row['Tujuan / Alasan'])

                        angka_berubah = (sisa_utuh != awal_utuh or sisa_dus != awal_dus or sisa_pcs != awal_pcs)
                        if not angka_berubah and teks_tujuan != "":
                            error_msg = f"⚠️ Lot {l}: Anda mengisi tujuan '{teks_tujuan}' tetapi angka sisa stok belum diubah."
                            break

                        if angka_berubah:
                            kel_p, kel_d, kel_pcs, diff_pcs = hitung_konversi_mutasi_pcs(
                                df_master_out, pn, awal_utuh, awal_dus, awal_pcs, sisa_utuh, sisa_dus, sisa_pcs
                            )
                            if diff_pcs == -1:
                                error_msg = f"❌ Total stok sisa Lot {l} lebih besar dari stok awal! Gunakan Tab Inbound untuk menambah stok."
                                break
                            if kel_p == 0 and kel_d == 0 and kel_pcs == 0:
                                error_msg = f"⚠️ Lot {l}: Nilai akhir setara dengan stok awal (selisih 0 Pcs)."
                                break

                            has_changes = True
                            cat_palet = f"{kat_keluar}: {teks_tujuan}" if teks_tujuan else (kat_keluar if kat_keluar in ["WIP", "EC"] else "")
                            updated_rak, err_code = apply_slot_reduction(
                                data_rak_ini, b_pilih, g_key, sisa_utuh, sisa_dus, sisa_pcs, cat_palet
                            )
                            if err_code == "empty":
                                error_msg = f"❌ Data Lot {l} tidak ditemukan di rak terkini. Silakan klik 'Cari Data Rak' ulang."
                                break
                            if err_code.startswith("over:"):
                                error_msg = f"❌ Sisa akhir Lot {l} melebihi kapasitas stok terkini ({err_code.split(':')[1]} slot)."
                                break

                            data_rak_ini = updated_rak

                            if kat_keluar == "WIP":
                                jenis_tx = "SUPLAI WIP"
                                tujuan_final = f"WIP{f' ({teks_tujuan})' if teks_tujuan else ''}"
                            elif kat_keluar == "EC":
                                jenis_tx = "ERROR CORRECTION (EC)"
                                tujuan_final = f"EC: {teks_tujuan or 'Penyesuaian Stok Fisik'}"
                            elif kat_keluar == "CUSTOMER" and teks_tujuan:
                                jenis_tx = "OUTBOUND CUSTOMER"
                                tujuan_final = teks_tujuan
                            else:
                                jenis_tx = "OUTBOUND (Belum Terjadwal)"
                                tujuan_final = teks_tujuan if teks_tujuan else "OUT (Tujuan Menyusul)"

                            satuan_lbl = get_satuan_kemasan(pn)
                            detail_sys = (
                                f"Awal: {awal_utuh}P {awal_dus}{satuan_lbl} -> "
                                f"Sisa: {sisa_utuh}P {sisa_dus}{satuan_lbl} | "
                                f"Keluar: {kel_p}P {kel_d}{satuan_lbl}"
                                + (f" ({diff_pcs:,} pcs)" if diff_pcs > 0 else "")
                            )

                            logs_to_write.append((
                                jenis_tx, k_pilih, b_pilih,
                                clean_str(row['Produk']), pn, l,
                                kel_p, kel_d, diff_pcs if diff_pcs > 0 else kel_pcs,
                                tujuan_final, tujuan_final, detail_sys
                            ))

                    if error_msg or not has_changes:
                        return False, error_msg, fresh_out_data, [], []

                    data_rak_ini = sorted(data_rak_ini, key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                    data_rak_ini = update_bayangan_baris(data_rak_ini, k_pilih, b_pilih)

                    final_kolom_data = data_rak_lain + data_rak_ini
                    transaction.set(doc_ref, {'data': final_kolom_data})
                    log_fb_usage('write', 1)
                    return True, "", fresh_out_data, logs_to_write, final_kolom_data

                sukses, error_msg, fresh_snapshot, logs_list, final_data_out = tx_outbound(db.transaction(), doc_ref_out)
                if error_msg:
                    st.error(error_msg)
                elif sukses:
                    record_doc_size(doc_name_out, final_data_out)
                    for lg in logs_list:
                        catat_log_mutasi(*lg)
                    clear_all_rak_caches()
                    st.session_state.update({
                        'undo_dict': {k_pilih: copy.deepcopy(fresh_snapshot)},
                        'action_msg': f"Penyesuaian Outbound Massal di Rak {k_pilih}-{b_pilih}",
                        'action_status': 'DONE',
                        'kor_dicari': False,
                        'relok_dicari': False
                    })
                    st.rerun()
                else:
                    st.info("Tidak ada perubahan angka pada tabel. Outbound dibatalkan.")
        else:
            st.info("Rak Kosong.")

# ----------------------------------------------------------
# TAB 3: AUDIT STOK & BACKUP
# ----------------------------------------------------------
with tab3:
    st.header("⚙️ Audit Stok & Ekspor Data")
    st.subheader("🔧 Maintenance: Re-Indexing Global")
    st.write("Mengurutkan ulang urutan palet dari nomor 1 serta membersihkan data/tag bayangan di seluruh gudang.")
    st.caption(f"🕒 Terakhir Re-Index: **{st.session_state['last_reindex']}**")

    if st.button("⚙️ Jalankan Re-Indexing Semua Rak Sekarang", type="secondary"):
        with st.spinner("Memperbaiki urutan index pallet..."):
            try:
                docs = list(db.collection(NAMA_DATABASE).stream())
                log_fb_usage('read', len(docs))
                batch = db.batch()
                total_koreksi = 0

                for doc in docs:
                    kolom_doc_id = doc.id
                    kol_huruf = kolom_doc_id.replace("Kolom_", "")
                    doc_data = doc.to_dict().get('data', [])
                    if not doc_data:
                        record_doc_size(kolom_doc_id, [])
                        continue

                    valid_records = [d for d in doc_data if is_valid_item(d)]
                    if not valid_records:
                        batch.set(db.collection(NAMA_DATABASE).document(kolom_doc_id), {'data': []})
                        record_doc_size(kolom_doc_id, [])
                        total_koreksi += 1
                        continue

                    df_col = pd.DataFrame(valid_records)
                    if 'Baris' not in df_col.columns or 'Pallet Ke' not in df_col.columns:
                        continue

                    df_col['Baris'] = pd.to_numeric(df_col['Baris'], errors='coerce')
                    df_col['Pallet Ke'] = pd.to_numeric(df_col['Pallet Ke'], errors='coerce')
                    new_doc_data = []

                    for baris, group in df_col.groupby('Baris'):
                        group = group.sort_values('Pallet Ke', ascending=True)
                        baris_records = group.to_dict('records')
                        for r in baris_records:
                            r['Kolom'] = kol_huruf
                            r['Baris'] = float(baris)
                            r['Part Number'] = clean_str(r.get('Part Number')).upper()
                            r['Lot Number'] = clean_str(r.get('Lot Number'))
                            r['Nama Produk'] = clean_str(r.get('Nama Produk'))
                            r['Approval QC'] = clean_str(r.get('Approval QC'))
                            r['Status'] = 'Terisi'
                        baris_records = update_bayangan_baris(baris_records, kol_huruf, int(baris))
                        new_doc_data.extend(baris_records)

                    batch.set(db.collection(NAMA_DATABASE).document(kolom_doc_id), {'data': new_doc_data})
                    record_doc_size(kolom_doc_id, new_doc_data)
                    total_koreksi += 1

                batch.commit()
                log_fb_usage('write', total_koreksi)
                clear_all_rak_caches()
                st.session_state['last_reindex'] = get_waktu_jkt().strftime("%d-%m-%Y %H:%M:%S")
                st.balloons()
                st.session_state.update({
                    'action_msg': f"Re-Indexing & Pembersihan Selesai! {total_koreksi} Kolom diperbarui.",
                    'action_status': 'DONE_NO_UNDO'
                })
                st.rerun()
            except Exception as e:
                st.error(f"Gagal Re-Indexing: {e}")

    st.divider()
    st.subheader("📥 Tarik Laporan Audit (CSV Super)")
    if st.button("Racik Laporan Audit Stok Sekarang", type="primary"):
        with st.spinner("Membaca database..."):
            docs = list(db.collection(NAMA_DATABASE).stream())
            log_fb_usage('read', len(docs))
            semua_data = []
            total_bytes_audit = 0
            for doc in docs:
                d_list = (doc.to_dict() or {}).get('data', [])
                record_doc_size(doc.id, d_list)
                total_bytes_audit += st.session_state['doc_sizes'][doc.id]
                semua_data.extend(d_list)
            st.session_state['total_rak_bytes'] = total_bytes_audit

            if semua_data:
                df_export = pd.DataFrame(semua_data)
                df_master = load_master_produk()

                qty_utuh_list, qty_dus_list, qty_pcs_list, grand_total_list = [], [], [], []
                for _, row in df_export.iterrows():
                    part_num_clean = clean_str(row.get('Part Number')).upper()
                    if not part_num_clean:
                        qty_utuh_list.append(0)
                        qty_dus_list.append(0)
                        qty_pcs_list.append(0)
                        grand_total_list.append(0)
                        continue

                    ket = clean_str(row.get('Keterangan')).lower()
                    is_pecahan = 'pecahan' in ket or 'eceran' in ket
                    q_utuh = 0 if is_pecahan else 1
                    q_dus, q_pcs = parse_keterangan(ket) if is_pecahan else (0, 0)

                    qty_utuh_list.append(q_utuh)
                    qty_dus_list.append(q_dus)
                    qty_pcs_list.append(q_pcs)

                    isi_pallet, pcs_per_satuan = get_master_konversi(df_master, part_num_clean)
                    grand_total_list.append((q_utuh * isi_pallet) + (q_dus * pcs_per_satuan) + q_pcs)

                df_export['Keterangan'] = df_export['Keterangan'].apply(clean_str) if 'Keterangan' in df_export.columns else ""
                df_export['Catatan'] = df_export['Catatan'].apply(clean_str) if 'Catatan' in df_export.columns else ""
                df_export['Approval QC'] = df_export['Approval QC'].apply(clean_str) if 'Approval QC' in df_export.columns else ""
                df_export['Qty Utuh'] = qty_utuh_list
                df_export['Qty Dus/Sak'] = qty_dus_list
                df_export['Qty Pcs Eceran'] = qty_pcs_list
                df_export['Grand Total Pcs'] = grand_total_list

                kolom_harapan = [
                    'Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk',
                    'Keterangan', 'Catatan', 'Approval QC', 'Status',
                    'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs'
                ]
                for c in kolom_harapan:
                    if c not in df_export.columns:
                        df_export[c] = ""

                for col in ['Baris', 'Pallet Ke', 'Qty Utuh', 'Qty Dus/Sak', 'Qty Pcs Eceran', 'Grand Total Pcs']:
                    df_export[col] = pd.to_numeric(df_export[col], errors='coerce').fillna(0).astype(int)
                    if col in ['Baris', 'Pallet Ke']:
                        df_export[col] = df_export[col].replace(0, "")

                df_export = df_export[kolom_harapan]
                csv_buffer = io.StringIO()
                df_export.to_csv(csv_buffer, index=False, sep=";")
                st.success("✅ CSV Super berhasil diracik!")
                st.download_button(
                    label="⬇️ Unduh CSV Super",
                    data=csv_buffer.getvalue(),
                    file_name=f"Audit_Stok_WMS_{get_waktu_jkt().strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )
            else:
                st.warning("Database kosong.")

# ----------------------------------------------------------
# TAB 4: LAPORAN MUTASI (DINAMIS MASTER TERBARU) & BERSIH LOG
# ----------------------------------------------------------
with tab4:
    st.header("📊 Laporan Riwayat Mutasi")
    st.caption("Total Pcs otomatis dihitung ulang mengikuti Master_Produk.csv terbaru tanpa membebani kuota Write Firebase.")

    hari_ini_jkt = get_waktu_jkt().date()
    c_tgl1, c_tgl2 = st.columns(2)
    with c_tgl1:
        tgl_mulai = st.date_input("Dari Tanggal", value=hari_ini_jkt, max_value=hari_ini_jkt)
    with c_tgl2:
        tgl_selesai = st.date_input("Sampai Tanggal", value=hari_ini_jkt, min_value=tgl_mulai, max_value=hari_ini_jkt)

    c_opt1, c_opt2 = st.columns(2)
    with c_opt1:
        tampilkan_ket_gabungan = st.checkbox(
            "Tampilkan kolom 'Keterangan' gabungan (untuk arsip lama)",
            value=False,
            key="chk_show_ket_gabungan"
        )
    with c_opt2:
        filter_arus = st.multiselect(
            "Filter Arus Transaksi (Opsional):",
            options=["MASUK", "KELUAR", "PINDAH", "KOREKSI"],
            default=[],
            key="filter_arus_log"
        )

    if st.button("Racik Laporan Mutasi Sekarang", type="primary"):
        with st.spinner("Menarik data log mutasi & menyesuaikan konversi Master..."):
            try:
                start_utc = datetime.combine(tgl_mulai, datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=7)
                end_utc = datetime.combine(tgl_selesai, datetime.max.time(), tzinfo=timezone.utc) - timedelta(hours=7)

                log_docs = list(
                    db.collection(DB_LOG_MUTASI)
                    .where("Timestamp_System", ">=", start_utc)
                    .where("Timestamp_System", "<=", end_utc)
                    .order_by("Timestamp_System", direction=firestore.Query.DESCENDING)
                    .stream()
                )
                log_fb_usage('read', len(log_docs))
                log_list = [d.to_dict() for d in log_docs]

                if log_list:
                    df_log = pd.DataFrame(log_list)
                    df_master_log = load_master_produk()
                    df_log = hitung_ulang_pcs_log(df_log, df_master_log)

                    if 'Arus' not in df_log.columns and 'Jenis Transaksi' in df_log.columns:
                        df_log['Arus'] = df_log['Jenis Transaksi'].apply(tentukan_arus_log)
                    else:
                        df_log['Arus'] = df_log['Arus'].fillna('').apply(clean_str)
                        mask_empty_arus = df_log['Arus'] == ""
                        if mask_empty_arus.any() and 'Jenis Transaksi' in df_log.columns:
                            df_log.loc[mask_empty_arus, 'Arus'] = df_log.loc[mask_empty_arus, 'Jenis Transaksi'].apply(tentukan_arus_log)

                    if filter_arus:
                        df_log = df_log[df_log['Arus'].isin(filter_arus)]

                    if 'Tujuan / Kategori' not in df_log.columns:
                        df_log['Tujuan / Kategori'] = df_log.get('Keterangan', '').apply(clean_str)
                    else:
                        df_log['Tujuan / Kategori'] = df_log['Tujuan / Kategori'].apply(clean_str)

                    if 'Detail Sistem' not in df_log.columns:
                        df_log['Detail Sistem'] = ""
                    else:
                        df_log['Detail Sistem'] = df_log['Detail Sistem'].apply(clean_str)

                    target_cols = [
                        "Waktu Transaksi", "Arus", "Jenis Transaksi", "Lokasi Rak",
                        "Part Number", "Nama Produk", "No Lot",
                        "Mutasi Pallet", "Mutasi Pecahan (Dus/Sak)",
                        "Total Pcs (Master Terbaru)", "Status Konversi",
                        "Tujuan / Kategori", "Detail Sistem"
                    ]
                    if tampilkan_ket_gabungan:
                        target_cols.append("Keterangan")

                    avail_cols = [c for c in target_cols if c in df_log.columns]
                    df_log = df_log[avail_cols]

                    jml_recalc = int((df_log['Status Konversi'].str.startswith("Master Baru")).sum()) if 'Status Konversi' in df_log.columns else 0
                    st.dataframe(df_log, hide_index=True, use_container_width=True, height=300)

                    csv_log_buffer = io.StringIO()
                    df_log.to_csv(csv_log_buffer, index=False, sep=";")
                    info_recalc = f" | **{jml_recalc} baris** disesuaikan dengan Master terbaru" if jml_recalc > 0 else ""
                    st.success(f"✅ Ditemukan **{len(df_log)}** catatan mutasi ({len(log_docs)} Reads){info_recalc}.")
                    st.download_button(
                        label="⬇️ Unduh Laporan Mutasi",
                        data=csv_log_buffer.getvalue(),
                        file_name=f"Mutasi_WMS_{tgl_mulai.strftime('%Y%m%d')}_{tgl_selesai.strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.info("Tidak ada catatan mutasi pada rentang tanggal tersebut.")
            except Exception as e:
                st.error(f"Gagal memuat log mutasi: {e}. Pastikan Index di Firestore sudah dibuat!")

    st.divider()
    with st.expander("🗑️ Bersihkan Log Mutasi Lama (Hemat Storage Spark Plan)"):
        st.warning("⚠️ Pastikan Anda sudah mengunduh laporan CSV di atas sebagai arsip sebelum menghapus data dari database!")
        batas_default = hari_ini_jkt - timedelta(days=30)
        tgl_batas_hapus = st.date_input(
            "Hapus seluruh log SEBELUM tanggal:",
            value=batas_default,
            max_value=hari_ini_jkt,
            key="tgl_batas_hapus"
        )
        konfirmasi_hapus = st.checkbox(f"Saya sudah backup CSV dan yakin menghapus log sebelum {tgl_batas_hapus.strftime('%d-%m-%Y')}.")

        if st.button("🗑️ Hapus Log Lama (Maks. 500 Dokumen/Klik)", type="secondary", disabled=not konfirmasi_hapus):
            with st.spinner("Menghapus log mutasi lama..."):
                try:
                    cutoff_utc = datetime.combine(tgl_batas_hapus, datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=7)
                    old_docs = list(
                        db.collection(DB_LOG_MUTASI)
                        .where("Timestamp_System", "<", cutoff_utc)
                        .limit(500)
                        .stream()
                    )
                    log_fb_usage('read', len(old_docs))

                    if old_docs:
                        batch_del = db.batch()
                        for d in old_docs:
                            batch_del.delete(d.reference)
                        batch_del.commit()
                        log_fb_usage('delete', len(old_docs))

                        st.session_state.pop('total_log_bytes', None)
                        st.session_state.pop('total_log_docs', None)
                        st.success(f"✅ Berhasil menghapus **{len(old_docs)}** log mutasi lama ({len(old_docs)} Read, {len(old_docs)} Delete).")
                    else:
                        st.info("Tidak ada log mutasi yang lebih lama dari tanggal tersebut.")
                except Exception as e:
                    st.error(f"Gagal menghapus log: {e}")

# ----------------------------------------------------------
# TAB 5: SYNC CSV (RESTORE)
# ----------------------------------------------------------
with tab5:
    st.header("🚀 Restore Data Database dari CSV")
    st.warning("⚠️ **PERINGATAN:** Fitur ini berfungsi sebagai *Disaster Recovery*. Jika dieksekusi, sistem akan **MENGHAPUS SELURUH ISI RAK** dan menimpanya dengan isi dari file CSV yang diunggah.")

    uploaded_file = st.file_uploader("Upload File Backup CSV (.csv)", type=['csv'])
    if uploaded_file is not None:
        if st.button("🚨 SAYA YAKIN, HAPUS DATABASE & TIMPA DENGAN CSV INI", type="primary", use_container_width=True):
            with st.spinner("Membaca CSV dan menimpa database..."):
                try:
                    try:
                        df_sync = pd.read_csv(uploaded_file, sep=";")
                    except Exception:
                        df_sync = pd.read_csv(uploaded_file, sep=",")

                    req_cols = ['Kolom', 'Baris', 'Pallet Ke', 'Part Number', 'Lot Number', 'Nama Produk']
                    if not all(col in df_sync.columns for col in req_cols):
                        st.error("❌ Format CSV tidak valid! Kolom wajib tidak ditemukan.")
                    else:
                        batch = db.batch()
                        for kol in KOLOM_IDS:
                            df_kol = df_sync[df_sync['Kolom'].apply(clean_str) == kol]
                            new_data = []
                            if not df_kol.empty:
                                df_kol = df_kol.sort_values(by=['Baris', 'Pallet Ke'])
                                for _, row in df_kol.iterrows():
                                    pn = clean_str(row['Part Number']).upper()
                                    if not pn:
                                        continue
                                    new_data.append({
                                        'Kolom': kol,
                                        'Baris': float(row['Baris']),
                                        'Pallet Ke': float(row['Pallet Ke']),
                                        'Part Number': pn,
                                        'Lot Number': clean_str(row['Lot Number']),
                                        'Nama Produk': clean_str(row['Nama Produk']),
                                        'Keterangan': clean_str(row.get('Keterangan')),
                                        'Catatan': clean_str(row.get('Catatan')),
                                        'Approval QC': clean_str(row.get('Approval QC')),
                                        'Status': 'Terisi'
                                    })

                            doc_name = f"Kolom_{kol}"
                            batch.set(db.collection(NAMA_DATABASE).document(doc_name), {'data': new_data})
                            record_doc_size(doc_name, new_data)

                        batch.commit()
                        log_fb_usage('write', len(KOLOM_IDS))
                        clear_all_rak_caches()
                        catat_log_mutasi(
                            "RESTORE CSV", "ALL", "ALL", "Sistem Database", "-", "-", 0, 0, 0,
                            keterangan="Hard Reset via CSV Upload",
                            tujuan_kategori="RESTORE DATABASE",
                            detail_sistem="Hard Reset via CSV Upload"
                        )

                        st.session_state.update({
                            'action_msg': "Database berhasil di-restore dari file CSV!",
                            'action_status': 'DONE_NO_UNDO'
                        })
                        st.rerun()
                except Exception as e:
                    st.error(f"Terjadi kesalahan saat memproses CSV: {e}")

# ----------------------------------------------------------
# TAB 6: RELOKASI RAK (PUSH-BACK & ROTASI LORONG SAMA)
# ----------------------------------------------------------
with tab6:
    st.header("🔄 Relokasi Pallet (Pindah Rak / Rotasi Push-Back)")
    st.caption("Pindahkan item antar rak secara presisi (Pallet, Dus, Pcs), atau tarik Lot ke posisi terdepan (Pallet Ke-1) dengan mode Push-Back.")

    col_r1, col_r2 = st.columns(2)
    with col_r1:
        st.markdown("**Rak ASAL (Sumber)**")
        r_asal_k = st.selectbox("Kolom Asal", KOLOM_OPTIONS, key="r_ak").split(" ")[0]
        r_asal_b = st.number_input("Baris Asal", min_value=1, max_value=29, value=1, key="r_ab")
    with col_r2:
        st.markdown("**Rak TUJUAN (Destinasi)**")
        r_tuj_k = st.selectbox("Kolom Tujuan", KOLOM_OPTIONS, key="r_tk").split(" ")[0]
        r_tuj_b = st.number_input("Baris Tujuan", min_value=1, max_value=29, value=1, key="r_tb")

    if st.button("🔍 Cari Data Rak Asal", type="primary", use_container_width=True):
        st.session_state.update({'relok_dicari': True, 'relok_ak': r_asal_k, 'relok_ab': r_asal_b})
        fetch_kolom_cached(r_asal_k, 'cache_relok_asal', force_refresh=True)

    if st.session_state.get('relok_dicari'):
        ak, ab = st.session_state['relok_ak'], st.session_state['relok_ab']
        tk, tb = r_tuj_k, r_tuj_b
        is_same_rack = (ak == tk and ab == tb)

        all_asal_data = fetch_kolom_cached(ak, 'cache_relok_asal', force_refresh=False)
        baris_asal_data = get_baris_records(all_asal_data, ab, only_valid=True)

        if baris_asal_data:
            df_master_relok = load_master_produk()
            rekap_relok = [
                {
                    'Group_Key': g['Group_Key'],
                    'Produk': g['Produk'],
                    'Lot Number': g['Lot Number'],
                    'Catatan': g['Catatan'],
                    'Approval QC': g['Approval QC'],
                    'Part Number': g['Part Number'],
                    'Tersedia Utuh': g['Utuh'],
                    'Tersedia Dus': g['Dus'],
                    'Tersedia Pcs': g['Pcs'],
                    'PINDAH UTUH': 0,
                    'PINDAH DUS': 0,
                    'PINDAH PCS': 0
                }
                for g in summarize_lot_groups(baris_asal_data)
            ]

            df_rekap_relok = pd.DataFrame(rekap_relok)

            st.divider()
            st.markdown(f"**2. Tentukan Jumlah yang Dipindah dari `{ak}-{ab}` ke `{tk}-{tb}`**")

            config_kolom = {
                'Group_Key': None, 'Part Number': None, 'Approval QC': None,
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

            edited_relok = st.data_editor(
                df_rekap_relok, column_config=config_kolom, hide_index=True,
                use_container_width=True, key=f"editor_relok_{ak}_{ab}_{tk}_{tb}"
            )

            relok_dari_depan = st.checkbox(
                "📥 Masukkan ke Rak Tujuan dari DEPAN (Push-back)",
                value=is_same_rack,
                key="relok_dari_depan"
            )

            if is_same_rack:
                if relok_dari_depan:
                    st.info(f"🔄 **Mode Rotasi Lorong ({ak}-{ab}):** Item terpilih akan ditarik keluar dan dimasukkan kembali dari pintu depan (**Pallet Ke-1**), mendorong slot lainnya ke belakang.")
                else:
                    st.warning("⚠️ Rak Asal dan Tujuan sama. Centang opsi **Push-back** di atas jika ingin memindahkan item ke posisi terdepan (Pallet Ke-1).")

            if st.button("🚀 Eksekusi Pindah Rak", type="primary", use_container_width=True):
                if is_same_rack and not relok_dari_depan:
                    st.error("❌ Rak asal dan tujuan sama! Aktifkan checkbox Push-back jika ingin memutar posisi Lot ke depan.")
                else:
                    doc_ref_asal = db.collection(NAMA_DATABASE).document(f"Kolom_{ak}")
                    doc_ref_tuj = db.collection(NAMA_DATABASE).document(f"Kolom_{tk}") if ak != tk else None

                    @firestore.transactional
                    def tx_relokasi(transaction):
                        snap_asal = doc_ref_asal.get(transaction=transaction)
                        log_fb_usage('read', 1)
                        fresh_asal = copy.deepcopy(snap_asal.to_dict().get('data', [])) if snap_asal.exists else []

                        if ak != tk:
                            snap_tuj = doc_ref_tuj.get(transaction=transaction)
                            log_fb_usage('read', 1)
                            fresh_tuj = copy.deepcopy(snap_tuj.to_dict().get('data', [])) if snap_tuj.exists else []
                        else:
                            fresh_tuj = copy.deepcopy(fresh_asal)

                        has_changes = False
                        error_msg = ""
                        logs_to_write = []

                        data_rak_asal = copy.deepcopy(fresh_asal)
                        data_rak_tujuan = data_rak_asal if ak == tk else copy.deepcopy(fresh_tuj)

                        undo_dict_setup = {ak: copy.deepcopy(fresh_asal)}
                        if ak != tk:
                            undo_dict_setup[tk] = copy.deepcopy(fresh_tuj)

                        all_moved_records = []

                        for _, row in edited_relok.iterrows():
                            pindah_utuh, pindah_dus, pindah_pcs = int(row['PINDAH UTUH']), int(row['PINDAH DUS']), int(row['PINDAH PCS'])
                            if pindah_utuh == 0 and pindah_dus == 0 and pindah_pcs == 0:
                                continue

                            g_key = str(row['Group_Key'])
                            l = clean_str(row['Lot Number'])
                            nama_prod = clean_str(row['Produk'])
                            part_num = clean_str(row['Part Number']).upper()
                            catatan_awal = clean_str(row['Catatan'])
                            qc_awal = clean_str(row.get('Approval QC'))

                            sisa_utuh = int(row['Tersedia Utuh']) - pindah_utuh
                            sisa_dus = int(row['Tersedia Dus']) - pindah_dus
                            sisa_pcs = int(row['Tersedia Pcs']) - pindah_pcs

                            if sisa_utuh < 0 or sisa_dus < 0 or sisa_pcs < 0:
                                error_msg = f"❌ Jumlah pindah Lot {l} melebihi stok tersedia!"
                                break

                            has_changes = True
                            updated_asal, err_code = apply_slot_reduction(
                                data_rak_asal, ab, g_key, sisa_utuh, sisa_dus, sisa_pcs
                            )
                            if err_code == "empty":
                                error_msg = f"❌ Data Lot {l} di rak asal tidak ditemukan. Silakan klik 'Cari Data Rak Asal' ulang."
                                break
                            if err_code.startswith("over:"):
                                error_msg = f"❌ Stok terkini Lot {l} tidak mencukupi untuk dipindahkan."
                                break

                            data_rak_asal = updated_asal
                            if ak == tk:
                                data_rak_tujuan = data_rak_asal

                            catatan_baru = catatan_awal.replace("[BAYANGAN - OVERCAPACITY]", "").strip()
                            if tk == 'S' and ak != 'S':
                                cat_bersih = re.sub(r'\[Asal:.*?\]\s*', '', catatan_baru)
                                catatan_baru = f"[Asal: {ak}-{ab}] {cat_bersih}".strip()
                            elif tk != 'S':
                                catatan_baru = re.sub(r'\[Asal:.*?\]\s*', '', catatan_baru).strip()

                            for _ in range(pindah_utuh):
                                all_moved_records.append({
                                    'Kolom': tk, 'Baris': float(tb), 'Pallet Ke': 0.0,
                                    'Part Number': part_num, 'Lot Number': l, 'Nama Produk': nama_prod,
                                    'Status': 'Terisi', 'Keterangan': '', 'Catatan': catatan_baru, 'Approval QC': qc_awal
                                })

                            if pindah_dus > 0 or pindah_pcs > 0:
                                all_moved_records.append({
                                    'Kolom': tk, 'Baris': float(tb), 'Pallet Ke': 0.0,
                                    'Part Number': part_num, 'Lot Number': l, 'Nama Produk': nama_prod,
                                    'Status': 'Terisi', 'Keterangan': format_keterangan(pindah_dus, pindah_pcs),
                                    'Catatan': catatan_baru, 'Approval QC': qc_awal
                                })

                            isi_p, pcs_s = get_master_konversi(df_master_relok, part_num)
                            tot_pindah_pcs = ((pindah_utuh * isi_p) + (pindah_dus * pcs_s) + pindah_pcs) if isi_p > 0 else pindah_pcs

                            jenis_log = f"RELOKASI{' (Push-Back)' if relok_dari_depan else ''}"
                            rute_relok = f"{ak}-{ab} -> {tk}-{tb}"
                            logs_to_write.append((
                                jenis_log, rute_relok, "NONE", nama_prod, part_num, l,
                                pindah_utuh, pindah_dus, tot_pindah_pcs,
                                f"Pindah ke {tk}-{tb}", f"Pindah ke {tk}-{tb}",
                                f"Asal: {ak}-{ab} | Tujuan: {tk}-{tb}"
                            ))

                        if error_msg or not has_changes:
                            return False, error_msg, {}, [], {}

                        updated_docs = {}
                        if ak == tk and ab == tb:
                            existing_tb = sorted(get_baris_records(data_rak_tujuan, tb), key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                            lain_tb = [d for d in data_rak_tujuan if not is_same_baris(d, tb)]
                            combined_tb = update_bayangan_baris((all_moved_records + existing_tb) if relok_dari_depan else (existing_tb + all_moved_records), tk, tb)

                            final_asal = lain_tb + combined_tb
                            transaction.set(doc_ref_asal, {'data': final_asal})
                            updated_docs[f"Kolom_{ak}"] = final_asal
                            log_fb_usage('write', 1)

                        elif ak == tk and ab != tb:
                            baris_asal_sisa = update_bayangan_baris(sorted(get_baris_records(data_rak_asal, ab), key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))), ak, ab)
                            existing_tb = sorted(get_baris_records(data_rak_asal, tb), key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                            combined_tb = update_bayangan_baris((all_moved_records + existing_tb) if relok_dari_depan else (existing_tb + all_moved_records), tk, tb)
                            lain_bars = [d for d in data_rak_asal if not (is_same_baris(d, ab) or is_same_baris(d, tb))]

                            final_asal = lain_bars + baris_asal_sisa + combined_tb
                            transaction.set(doc_ref_asal, {'data': final_asal})
                            updated_docs[f"Kolom_{ak}"] = final_asal
                            log_fb_usage('write', 1)

                        else:
                            baris_asal_sisa = update_bayangan_baris(sorted(get_baris_records(data_rak_asal, ab), key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))), ak, ab)
                            lain_asal = [d for d in data_rak_asal if not is_same_baris(d, ab)]
                            final_asal = lain_asal + baris_asal_sisa
                            transaction.set(doc_ref_asal, {'data': final_asal})

                            existing_tb = sorted(get_baris_records(data_rak_tujuan, tb), key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0)))
                            combined_tb = update_bayangan_baris((all_moved_records + existing_tb) if relok_dari_depan else (existing_tb + all_moved_records), tk, tb)
                            lain_tuj = [d for d in data_rak_tujuan if not is_same_baris(d, tb)]
                            final_tuj = lain_tuj + combined_tb
                            transaction.set(doc_ref_tuj, {'data': final_tuj})

                            updated_docs[f"Kolom_{ak}"] = final_asal
                            updated_docs[f"Kolom_{tk}"] = final_tuj
                            log_fb_usage('write', 2)

                        return True, "", undo_dict_setup, logs_to_write, updated_docs

                    sukses, error_msg, undo_dict_setup, logs_list, updated_docs = tx_relokasi(db.transaction())
                    if error_msg:
                        st.error(error_msg)
                    elif sukses:
                        for doc_k, doc_v in updated_docs.items():
                            record_doc_size(doc_k, doc_v)
                        for lg in logs_list:
                            catat_log_mutasi(*lg)
                        clear_all_rak_caches()
                        mode_txt = " (Push-Back ke Depan)" if relok_dari_depan else ""
                        st.session_state.update({
                            'undo_dict': copy.deepcopy(undo_dict_setup),
                            'action_msg': f"Berhasil memindahkan item dari Rak {ak}-{ab} ke {tk}-{tb}{mode_txt}",
                            'action_status': 'DONE',
                            'relok_dicari': False,
                            'kor_dicari': False
                        })
                        st.rerun()
                    else:
                        st.info("Tidak ada jumlah yang dipindah.")
        else:
            st.warning("Rak Asal Kosong!")
            st.session_state['relok_dicari'] = False

# ----------------------------------------------------------
# TAB 7: KOREKSI DATA (EDIT LANGSUNG + AUDIT DETAIL PER SLOT)
# ----------------------------------------------------------
with tab7:
    st.header("✏️ Koreksi Data Rak (Manual Edit)")
    st.caption("Gunakan tab ini untuk memperbaiki kesalahan input seperti salah Tipe Produk, salah No Lot, salah Part Number, atau tag Catatan secara instan.")

    c_kor1, c_kor2, c_kor3 = st.columns([1, 1, 2])
    with c_kor1:
        kor_kolom = st.selectbox("Lokasi Kolom", KOLOM_OPTIONS, key="kor_kolom").split(" ")[0]
    with c_kor2:
        kor_baris = st.number_input("Nomor Baris", min_value=1, max_value=29, value=1, key="kor_baris")
    with c_kor3:
        st.write("")
        st.write("")
        if st.button("🔍 Tarik Data untuk Koreksi", use_container_width=True):
            st.session_state.update({'kor_dicari': True, 'kor_k': kor_kolom, 'kor_b': kor_baris})
            fetch_kolom_cached(kor_kolom, 'cache_kor_data', force_refresh=True)

    st.divider()

    if st.session_state.get('kor_dicari'):
        kk, kb = st.session_state['kor_k'], st.session_state['kor_b']
        all_kor_data = fetch_kolom_cached(kk, 'cache_kor_data', force_refresh=False)
        baris_kor_data = get_baris_records(all_kor_data, kb, only_valid=True)

        if baris_kor_data:
            df_kor = pd.DataFrame(baris_kor_data)
            df_kor['Pallet Ke'] = pd.to_numeric(df_kor['Pallet Ke'], errors='coerce')

            for col in ['Part Number', 'Lot Number', 'Nama Produk', 'Keterangan', 'Catatan', 'Approval QC']:
                df_kor[col] = df_kor[col].apply(clean_str) if col in df_kor.columns else ""

            df_kor = df_kor.sort_values('Pallet Ke', ascending=True).reset_index(drop=True)
            st.markdown(f"**Data Baris {kk}-{kb}**")

            config_kor = {
                'Kolom': None, 'Baris': None, 'Status': None,
                'Pallet Ke': st.column_config.NumberColumn("Idx Pallet", disabled=True),
                'Part Number': st.column_config.TextColumn("Part Number", required=True),
                'Lot Number': st.column_config.TextColumn("Lot Number", required=True),
                'Nama Produk': st.column_config.TextColumn("Nama Produk", required=True),
                'Keterangan': st.column_config.TextColumn("Keterangan (Dus/Pcs)"),
                'Catatan': st.column_config.TextColumn("Catatan / Tag"),
                'Approval QC': st.column_config.TextColumn("Approval QC")
            }

            edited_kor_df = st.data_editor(
                df_kor, column_config=config_kor, hide_index=True,
                use_container_width=True, num_rows="fixed", key=f"editor_kor_{kk}_{kb}"
            )

            if st.button("💾 Simpan Perubahan Koreksi", type="primary"):
                if not edited_kor_df.equals(df_kor):
                    with st.spinner("Menyimpan koreksi (Transactional Lock)..."):
                        doc_name_kor = f"Kolom_{kk}"
                        doc_ref_kor = db.collection(NAMA_DATABASE).document(doc_name_kor)
                        new_baris_data = edited_kor_df.to_dict('records')

                        for d in new_baris_data:
                            d['Kolom'] = kk
                            d['Baris'] = float(kb)
                            d['Pallet Ke'] = float(d.get('Pallet Ke', 0))
                            d['Status'] = 'Terisi'
                            d['Part Number'] = clean_str(d.get('Part Number')).upper()
                            d['Lot Number'] = clean_str(d.get('Lot Number'))
                            d['Nama Produk'] = clean_str(d.get('Nama Produk'))
                            d['Keterangan'] = clean_str(d.get('Keterangan'))
                            d['Catatan'] = clean_str(d.get('Catatan'))
                            d['Approval QC'] = clean_str(d.get('Approval QC'))

                        new_baris_data = update_bayangan_baris(new_baris_data, kk, kb)

                        @firestore.transactional
                        def tx_koreksi(transaction, doc_ref):
                            snap = doc_ref.get(transaction=transaction)
                            log_fb_usage('read', 1)
                            fresh_kor = copy.deepcopy(snap.to_dict().get('data', [])) if snap.exists else []
                            lain_kor_fresh = [d for d in fresh_kor if not is_same_baris(d, kb)]
                            final_kolom_data = lain_kor_fresh + new_baris_data
                            transaction.set(doc_ref, {'data': final_kolom_data})
                            log_fb_usage('write', 1)
                            return fresh_kor, final_kolom_data

                        fresh_snapshot, final_data_kor = tx_koreksi(db.transaction(), doc_ref_kor)
                        record_doc_size(doc_name_kor, final_data_kor)

                        perubahan_tercatat = 0
                        for idx_k in range(len(df_kor)):
                            r_lama = df_kor.iloc[idx_k]
                            r_baru = edited_kor_df.iloc[idx_k]
                            diff_fields = []
                            for col_cek in ['Part Number', 'Lot Number', 'Nama Produk', 'Keterangan', 'Catatan', 'Approval QC']:
                                v_lama = clean_str(r_lama.get(col_cek))
                                v_baru = clean_str(r_baru.get(col_cek))
                                if v_lama != v_baru:
                                    diff_fields.append(f"{col_cek}: '{v_lama}' -> '{v_baru}'")
                            if diff_fields:
                                perubahan_tercatat += 1
                                det_kor = f"Pallet #{int(r_baru.get('Pallet Ke', 0))} | " + "; ".join(diff_fields)
                                catat_log_mutasi(
                                    "ERROR CORRECTION (Edit Slot)", kk, kb,
                                    clean_str(r_baru.get('Nama Produk')),
                                    clean_str(r_baru.get('Part Number')),
                                    clean_str(r_baru.get('Lot Number')),
                                    0, 0, 0,
                                    keterangan=det_kor,
                                    tujuan_kategori="EC: Koreksi Atribut Slot",
                                    detail_sistem=det_kor
                                )

                        if perubahan_tercatat == 0:
                            catat_log_mutasi(
                                "KOREKSI DATA", kk, kb, "Koreksi Rak", "-", "-", 0, 0, 0,
                                keterangan="Koreksi Manual via Tab Koreksi",
                                tujuan_kategori="EC: Koreksi Manual",
                                detail_sistem="Koreksi Manual via Tab Koreksi"
                            )

                        clear_all_rak_caches()
                        st.session_state.update({
                            'undo_dict': {kk: copy.deepcopy(fresh_snapshot)},
                            'action_msg': f"Koreksi data di Rak {kk}-{kb} berhasil disimpan.",
                            'action_status': 'DONE'
                        })
                        st.rerun()
                else:
                    st.info("Tidak ada perubahan yang dilakukan pada tabel.")
        else:
            st.info("Rak Kosong, tidak ada yang bisa dikoreksi.")

# ----------------------------------------------------------
# TAB 8: CETAK RACKING / PICKING LIST (1A - 29G)
# ----------------------------------------------------------
with tab8:
    st.header("🖨️ Cetak Kartu Racking / Petunjuk Pengambilan (1A – 29G)")
    st.caption("Hemat kuota: sistem hanya membaca dokumen kolom dari lokasi yang dipilih dan menyimpannya di cache sesi.")

    mode_cetak = st.radio(
        "Pilih Mode Cetak:",
        ["🎯 Lokasi Spesifik (Pilih / Ketik Bebas)", "📑 Rentang Massal (Per Kolom & Baris)"],
        horizontal=True,
        key="print_mode_radio"
    )

    daftar_semua_lokasi = [f"{b}{k}" for b in range(1, 30) for k in KOLOM_RACKING_AG]
    target_lokasi_list = []

    if mode_cetak.startswith("🎯"):
        sp_c1, sp_c2 = st.columns([2, 1])
        with sp_c1:
            pilih_lokasi_multi = st.multiselect(
                "Pilih Satu atau Beberapa Lokasi Rak (1A – 29G):",
                options=daftar_semua_lokasi,
                placeholder="Contoh: 1A, 2B, 17C...",
                key="print_multi_loc"
            )
        with sp_c2:
            ketik_lokasi_manual = st.text_input(
                "Atau Ketik Cepat (pisah koma/spasi):",
                placeholder="Cth: 1A, 5C, 12G",
                key="print_manual_loc"
            )

        seen_loc = set()
        for loc in pilih_lokasi_multi:
            if loc not in seen_loc:
                target_lokasi_list.append(loc)
                seen_loc.add(loc)

        if clean_str(ketik_lokasi_manual):
            tokens = re.split(r'[\s,;]+', clean_str(ketik_lokasi_manual).upper())
            for tok in tokens:
                if not tok:
                    continue
                m = re.fullmatch(r'(\d{1,2})([A-G])', tok)
                if m and 1 <= int(m.group(1)) <= 29:
                    norm_loc = f"{int(m.group(1))}{m.group(2)}"
                    if norm_loc not in seen_loc:
                        target_lokasi_list.append(norm_loc)
                        seen_loc.add(norm_loc)
                else:
                    st.warning(f"⚠️ Format lokasi '{tok}' diabaikan (gunakan format 1A s/d 29G).")

    else:
        p_col1, p_col2, p_col3, p_col4 = st.columns([2, 1, 1, 2])
        with p_col1:
            pilih_kolom_cetak = st.multiselect(
                "Pilih Kolom Racking",
                options=KOLOM_RACKING_AG,
                default=KOLOM_RACKING_AG,
                key="print_kolom_sel"
            )
        with p_col2:
            baris_awal_cetak = st.number_input("Dari Baris", min_value=1, max_value=29, value=1, key="print_b_start")
        with p_col3:
            baris_akhir_cetak = st.number_input("Sampai Baris", min_value=1, max_value=29, value=29, key="print_b_end")
        with p_col4:
            mode_urutan = st.selectbox(
                "Urutan Cetak",
                ["Per Baris (1A, 1B ... 29G)", "Per Kolom (1A, 2A ... 29G)"],
                key="print_sort_mode"
            )

        if pilih_kolom_cetak and baris_awal_cetak <= baris_akhir_cetak:
            kolom_terurut = [k for k in KOLOM_RACKING_AG if k in pilih_kolom_cetak]
            rentang_baris = range(int(baris_awal_cetak), int(baris_akhir_cetak) + 1)
            if mode_urutan.startswith("Per Baris"):
                target_lokasi_list = [f"{b}{k}" for b in rentang_baris for k in kolom_terurut]
            else:
                target_lokasi_list = [f"{b}{k}" for k in kolom_terurut for b in rentang_baris]
        elif baris_awal_cetak > baris_akhir_cetak:
            st.error("❌ 'Dari Baris' tidak boleh lebih besar dari 'Sampai Baris'.")

    btn_c1, btn_c2 = st.columns([2, 1])
    with btn_c1:
        if st.button("📋 Susun Kartu Racking Sekarang", type="primary", use_container_width=True):
            if not target_lokasi_list:
                st.warning("Pilih atau ketik minimal 1 lokasi rak (contoh: 1A) terlebih dahulu.")
            else:
                kolom_dibutuhkan = sorted({re.sub(r'\d+', '', loc) for loc in target_lokasi_list})
                with st.spinner(f"Mengambil data untuk {len(kolom_dibutuhkan)} kolom terkait..."):
                    for kol in kolom_dibutuhkan:
                        if kol not in st.session_state['print_rak_cache']:
                            doc_name = f"Kolom_{kol}"
                            doc = db.collection(NAMA_DATABASE).document(doc_name).get()
                            log_fb_usage('read', 1)
                            d_list = doc.to_dict().get('data', []) if doc.exists else []
                            record_doc_size(doc_name, d_list)
                            st.session_state['print_rak_cache'][kol] = d_list
                    st.session_state['print_ready'] = True
    with btn_c2:
        if st.button("🔄 Tarik Ulang Data Terbaru", use_container_width=True):
            st.session_state['print_rak_cache'] = {}
            st.session_state['print_ready'] = False
            st.toast("Cache cetak dibersihkan. Klik 'Susun Kartu Racking Sekarang' untuk menarik data terbaru.", icon="🔄")

    if st.session_state.get('print_ready') and target_lokasi_list:
        kolom_dibutuhkan = {re.sub(r'\d+', '', loc) for loc in target_lokasi_list}
        if not kolom_dibutuhkan.issubset(st.session_state['print_rak_cache'].keys()):
            st.info("ℹ️ Anda menambahkan lokasi dari kolom baru. Klik **'Susun Kartu Racking Sekarang'** untuk memuat data kolom tersebut.")
        else:
            semua_kartu = []
            for loc in target_lokasi_list:
                m = re.fullmatch(r'(\d{1,2})([A-G])', loc)
                b_num, k_id = int(m.group(1)), m.group(2)
                raw_kol = st.session_state['print_rak_cache'].get(k_id, [])
                baris_recs = get_baris_records(raw_kol, b_num, only_valid=True)
                semua_kartu.extend(build_picking_cards(b_num, k_id, baris_recs))

            st.divider()
            info_c1, info_c2 = st.columns([2, 1])
            with info_c1:
                jml_isi = sum(1 for c in semua_kartu if not c['Is_Empty'])
                jml_kosong = sum(1 for c in semua_kartu if c['Is_Empty'])
                st.success(f"✅ Siap Cetak: **{len(semua_kartu)} Kartu** dari **{len(target_lokasi_list)} Lokasi** ({jml_isi} Terisi, {jml_kosong} Kosong).")
            with info_c2:
                df_print_csv = pd.DataFrame(semua_kartu).drop(columns=['Is_Empty'])
                csv_print_buf = io.StringIO()
                df_print_csv.to_csv(csv_print_buf, index=False, sep=";")
                st.download_button(
                    label="⬇️ Unduh Data Picking List (CSV)",
                    data=csv_print_buf.getvalue(),
                    file_name=f"Picking_List_Racking_{get_waktu_jkt().strftime('%Y%m%d_%H%M')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )

            ringkasan_judul = ", ".join(target_lokasi_list[:6]) + (f" (+{len(target_lokasi_list)-6} lainnya)" if len(target_lokasi_list) > 6 else "")
            judul_dok = f"Petunjuk Pengambilan Racking: {ringkasan_judul}"
            waktu_skrg = get_waktu_jkt().strftime("%d-%m-%Y %H:%M WIB")
            html_cetak = render_print_html(semua_kartu, judul_dok, waktu_skrg)
            components.html(html_cetak, height=650, scrolling=True)
