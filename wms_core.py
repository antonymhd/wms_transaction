import streamlit as st
import pandas as pd
import math
import re
import requests
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================================
# 1. KONSTANTA, ZONA KHUSUS, FILTER WIP & INISIALISASI FIREBASE
# ==========================================================
NAMA_DATABASE = 'uji_coba_rak'
DB_LOG_MUTASI = 'log_mutasi_rak'
DB_UNDO_STATE = 'wms_undo_state'
DB_DRAFT_INBOUND = 'wms_inbound_drafts'

# Batas maksimal palet di sisi ujung pintu masuk untuk lorong >= 3 item (mencegah delay bongkar outbound)
MAX_TAIL_PALLETS_MULTI_ITEM = 10

# Item khusus JDO Diamond (Utama: F1-16, Cadangan: G17-28)
PART_KHUSUS_TOP_TIER = {'MJSCN3802703D', 'MJSCN3802901D'}

# Item Cup Natural khusus yang boleh diambil untuk Suplai WIP (selain seluruh Preform & Cup Color)
PART_WIP_CUP_NATURAL = {'MJSCN3902902G', 'MJSCN3903101D', 'MJSCN3204303D', 'MJSCN3205004D'}

def is_zona_jdo(kolom, baris_int):
    k = str(kolom).strip().upper()
    return (k == 'F' and 1 <= baris_int <= 16) or (k == 'G' and 17 <= baris_int <= 28)

def is_rak_valid_untuk_produk(kolom, baris_int, part_num):
    p = str(part_num).strip().upper()
    if p in PART_KHUSUS_TOP_TIER:
        return is_zona_jdo(kolom, baris_int)
    return True

def hitung_zone_penalty(kolom, baris_int, target_item, part_num):
    """
    Menentukan urutan prioritas zona beserta pelimpahan (overflow) otomatis:
    - JDO Diamond: F1-16 (0) -> jika penuh ke G17-28 (1)
    - PREFORM: Baris 1-16 non-F (0) -> jika penuh ke Baris 17-28 non-G (1) -> cadangan F1-16/G17-28 (2)
    - CUP: Baris 17-28 non-G (0) -> jika penuh ke Baris 1-16 non-F (1) -> cadangan G17-28/F1-16 (2)
    - Lainnya (CAP, dll): Non-JDO (0) -> cadangan zona JDO (1)
    """
    k = str(kolom).strip().upper()
    p = str(part_num).strip().upper()
    nama_upper = str(target_item).strip().upper()
    
    if p in PART_KHUSUS_TOP_TIER:
        if k == 'F' and 1 <= baris_int <= 16:
            return 0
        if k == 'G' and 17 <= baris_int <= 28:
            return 1
        return 99
        
    in_jdo_zone = is_zona_jdo(k, baris_int)
    
    if nama_upper.startswith('PREFORM'):
        if not in_jdo_zone and 1 <= baris_int <= 16:
            return 0
        elif not in_jdo_zone and 17 <= baris_int <= 28:
            return 1
        else:
            return 2
            
    elif nama_upper.startswith('CUP'):
        if not in_jdo_zone and 17 <= baris_int <= 28:
            return 0
        elif not in_jdo_zone and 1 <= baris_int <= 16:
            return 1
        else:
            return 2
            
    return 1 if in_jdo_zone else 0

if not firebase_admin._apps:
    cred = credentials.Certificate(dict(st.secrets["firebase"]))
    firebase_admin.initialize_app(cred)
db = firestore.client()

def get_waktu_jkt():
    return datetime.utcnow() + timedelta(hours=7)

# ==========================================================
# 2. CSS ADAPTIF & KOMPONEN HEADER + PROGRESS BAR
# ==========================================================
def inject_custom_css(accent_color="#10B981", accent_border="#059669", accent_bg_rgba="rgba(16, 185, 129, 0.1)"):
    st.markdown(f"""
        <style>
            #MainMenu {{visibility: hidden;}}
            header {{visibility: hidden;}}
            footer {{visibility: hidden;}}
            
            .stButton>button {{
                min-height: 70px; 
                height: auto;
                font-size: 16px; 
                font-weight: 600;
                border-radius: 12px;
                white-space: pre-wrap; 
                word-wrap: break-word;
                text-align: left; 
                padding: 14px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
                transition: all 0.15s ease-in-out;
            }}
            
            button[kind="primary"] {{
                background-color: {accent_color} !important;
                border-color: {accent_border} !important;
                color: white !important;
                font-size: 18px !important;
                text-align: center !important;
            }}
            
            .btn-undo > button {{
                background-color: #DC2626 !important;
                border-color: #EF4444 !important;
                color: white !important;
                text-align: center !important;
            }}

            .btn-center > button {{
                text-align: center !important;
                font-size: 19px !important;
            }}

            .btn-header-action > button {{
                min-height: 58px !important;
                padding: 8px 10px !important;
                font-size: 13px !important;
                text-align: center !important;
            }}

            .btn-compact > button {{
                min-height: 44px !important;
                padding: 8px 12px !important;
                font-size: 14px !important;
                text-align: center !important;
            }}
            
            .progress-container {{
                font-size: 13px;
                font-weight: 500;
                margin-bottom: 15px;
                padding-bottom: 10px;
                border-bottom: 1px solid var(--border-color);
                text-align: center;
            }}
            .step-active {{ color: {accent_color}; font-weight: bold; background: {accent_bg_rgba}; padding: 4px 8px; border-radius: 6px; }}
            .step-inactive {{ color: var(--text-color); opacity: 0.6; }}
            
            .db-info-box {{
                background-color: var(--secondary-background-color);
                border: 1px solid var(--border-color);
                padding: 10px 14px;
                border-radius: 10px;
                margin-bottom: 10px;
            }}

            .cek-fisik-box {{
                background-color: var(--secondary-background-color);
                border-left: 5px solid {accent_color};
                padding: 12px 14px;
                border-radius: 8px;
                margin: 10px 0;
            }}
            
            .btn-gramasi > button {{ font-size: 20px !important; text-align: center !important; }}
        </style>
    """, unsafe_allow_html=True)

def render_progress_bar(current_slide, mode_transaksi):
    if mode_transaksi == "STAGING":
        steps = ["Pilih Rak", "Produk & Lot", "Outbound"]
        idx_map = {1: 1, 2: 2, 3: 3}
    elif mode_transaksi in ["INBOUND", "INBOUND_SISA"]:
        steps = ["Aksi", "Kategori", "Item", "Input", "Lokasi", "Selesai"]
        idx_map = {1: 1, 2: 2, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}
    elif mode_transaksi in ["WIP", "OUTBOUND"]:
        steps = ["Aksi", "Kategori", "Item", "Lokasi", "Selesai"]
        idx_map = {1: 1, 2: 2, 3: 2, 4: 3, 5: 4, 6: 5}
    else:
        steps = ["Aksi", "Kategori", "Item", "Proses", "Selesai"]
        idx_map = {1: 1, 2: 2, 3: 2, 4: 3, 5: 4, 6: 5, 7: 5}
        
    curr_step = idx_map.get(current_slide, 1)
    html = "<div class='progress-container'>"
    for i, s in enumerate(steps, 1):
        if i == curr_step:
            html += f"<span class='step-active'>{s}</span>"
        else:
            html += f"<span class='step-inactive'>{s}</span>"
        if i < len(steps):
            html += " <span style='opacity: 0.5;'>➔</span> "
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

# ==========================================================
# 3. TELEGRAM & PARSING UTILITAS
# ==========================================================
def kirim_notif_telegram(pesan_html):
    try:
        token = st.secrets["telegram"]["bot_token"]
        raw_chat_ids = str(st.secrets["telegram"]["chat_id"])
        daftar_id = [cid.strip() for cid in raw_chat_ids.split(",") if cid.strip()]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        for cid in daftar_id:
            payload = {"chat_id": cid, "text": pesan_html, "parse_mode": "HTML"}
            requests.post(url, json=payload, timeout=2)
    except Exception:
        pass

def escape_html(text):
    return str(text).replace("&", "&").replace("<", "<").replace(">", ">")

def parse_keterangan(ket_str):
    ket_str = str(ket_str).lower()
    pecahan, eceran = 0, 0
    m_pec = re.search(r'pecahan:\s*(\d+)', ket_str)
    if m_pec: pecahan = int(m_pec.group(1))
    m_ec = re.search(r'eceran:\s*(\d+)', ket_str)
    if m_ec: eceran = int(m_ec.group(1))
    return pecahan, eceran

def parse_fifo_key(lot_str):
    """Kunci pengurutan kronologis FIFO murni: YYMMDD + Shift (1/2/3) agar lot tanggal & shift sama diadu berdasarkan stok terkecil."""
    s = str(lot_str).strip()
    match_shift = re.search(r'([123])(\d{2})(\d{2})(\d{2})$', s)
    if match_shift:
        sh, dd, mm, yy = match_shift.groups()
        return f"{yy}{mm}{dd}{sh}"
    match_date = re.search(r'(\d{2})(\d{2})(\d{2})$', s)
    if match_date:
        dd, mm, yy = match_date.groups()
        return f"{yy}{mm}{dd}0"
    return "9999999"

def parse_lifo_rank(lot_str):
    """Skor numerik terbaru (Inbound): YYMMDD * 10 + Shift (1/2/3)."""
    s = str(lot_str).strip()
    match_shift = re.search(r'([123])(\d{2})(\d{2})(\d{2})$', s)
    if match_shift:
        sh, dd, mm, yy = match_shift.groups()
        return int(f"{yy}{mm}{dd}{sh}")
    match_date = re.search(r'(\d{2})(\d{2})(\d{2})$', s)
    if match_date:
        dd, mm, yy = match_date.groups()
        return int(f"{yy}{mm}{dd}0")
    return 0

def get_satuan_kemasan(part_num):
    p = str(part_num).strip().upper()
    if p.endswith('C'): return "Sak"
    elif p.endswith('A'): return "Nictainer"
    return "Dus"

def ekstrak_gramasi(nama_produk):
    match = re.search(r'(\d+[.,]\d+)\s*(?:gr|GR)', str(nama_produk), re.IGNORECASE)
    if match: return f"{float(match.group(1).replace(',', '.')):.2f} gr"
    return "Lainnya"

def set_kategori(nama):
    nama_str = str(nama).strip().upper()
    if nama_str.startswith('PREFORM'): return 'PREFORM'
    elif nama_str.startswith('CUP NATURAL'): return 'CUP NATURAL'
    elif nama_str.startswith('CUP PRINTING'): return 'CUP PRINTING'
    elif nama_str.startswith('CUP COLOR'): return 'CUP COLOR'
    elif nama_str.startswith('CAP'): return 'CAP'
    else: return 'LAINNYA'

def format_nama_warna_bold(nama_barang):
    parts = str(nama_barang).rsplit(',', 1)
    if len(parts) == 2: return f"{parts[0]}, **{parts[1].strip()}**"
    return str(nama_barang)

def hitung_blok_ujung(pallets_sorted, target_item_clean, sisi_masuk):
    """Menghitung berapa palet berturut-turut dari sisi pintu masuk yang berisi target_item_clean."""
    seq = pallets_sorted if sisi_masuk == "DEPAN" else list(reversed(pallets_sorted))
    count = 0
    for p in seq:
        if str(p.get('Nama Produk', '')).strip() == target_item_clean:
            count += 1
        else:
            break
    return count

# ==========================================================
# 4. DATA LOADERS & ALGORITMA PENEMPATAN / PENGAMBILAN
# ==========================================================
@st.cache_data(ttl=300)
def get_all_racking_data():
    docs = db.collection(NAMA_DATABASE).stream()
    semua_data = []
    for doc in docs:
        doc_data = doc.to_dict()
        if 'data' in doc_data: semua_data.extend(doc_data['data'])
    return semua_data

@st.cache_data(ttl=3600)
def load_master_data():
    try:
        df = pd.read_csv("Master_Produk.csv", sep=";", dtype=str)
        df = df.dropna(subset=['Nama Barang'])
        df.columns = df.columns.str.strip()
        if 'Part Number' not in df.columns: df['Part Number'] = ""
        
        df['Part Number'] = df['Part Number'].fillna("").astype(str).str.strip()
        df['Nama Barang'] = df['Nama Barang'].fillna("").astype(str).str.strip()
        df = df.drop_duplicates(subset=['Part Number', 'Nama Barang']).reset_index(drop=True)
        
        df['Kategori'] = df['Nama Barang'].apply(set_kategori)
        df['Gramasi'] = df['Nama Barang'].apply(ekstrak_gramasi)
        
        def clean_num(val):
            try:
                c = re.sub(r'[^\d]', '', str(val))
                return int(c) if c else 0
            except Exception:
                return 0
                
        df['Total_Pcs_Pallet'] = df['Isi 1 Pallet (pcs)'].apply(clean_num) if 'Isi 1 Pallet (pcs)' in df.columns else 0
        df['Pcs_Per_Box'] = df['Box / Pallet'].apply(clean_num) if 'Box / Pallet' in df.columns else 0
        df['Pcs_Per_Sac'] = df['Sac / Pallet'].apply(clean_num) if 'Sac / Pallet' in df.columns else 0
        return df
    except Exception:
        return pd.DataFrame(columns=[
            'Nama Barang', 'Kategori', 'Gramasi', 'Part Number',
            'Total_Pcs_Pallet', 'Pcs_Per_Box', 'Pcs_Per_Sac'
        ])

def get_df_master_terfilter(df_master):
    """Memfilter Master_Produk.csv secara dinamis: mode WIP menampilkan Preform, Cup Color, & 4 Cup Natural WIP."""
    if df_master.empty:
        return df_master
    if st.session_state.get("mode_transaksi") == "WIP":
        part_clean = df_master['Part Number'].astype(str).str.strip().str.upper()
        return df_master[
            (df_master['Kategori'].isin(['PREFORM', 'CUP COLOR'])) | (part_clean.isin(PART_WIP_CUP_NATURAL))
        ].copy()
    return df_master

def hitung_kandidat_rak_inbound(semua_data, target_item, part_num, sisi_masuk="BELAKANG", target_lot=""):
    KOLOM_LIST = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
    BARIS_LIST = list(range(1, 29))
    slot_map = {f"{k}-{b}": [] for k in KOLOM_LIST for b in BARIS_LIST}
    
    for d in semua_data:
        k = str(d.get('Kolom', '')).strip().upper()
        try: b = int(float(d.get('Baris', 0)))
        except Exception: continue
        if f"{k}-{b}" in slot_map: slot_map[f"{k}-{b}"].append(d)
            
    part_clean = str(part_num).strip().upper()
    in_is_sac = part_clean.endswith('C')
    target_item_clean = str(target_item).strip()
    target_lot_clean = str(target_lot).strip().upper()
    
    kandidat_rak = []
    for loc, pallets in slot_map.items():
        k, b = loc.split('-')
        b = int(b)
        
        if not is_rak_valid_untuk_produk(k, b, part_clean):
            continue
            
        zone_penalty = hitung_zone_penalty(k, b, target_item_clean, part_clean)
        
        max_sac = 31 if b <= 16 else 44
        max_dus = 32 if b <= 16 else 46
        max_target = max_sac if in_is_sac else max_dus
        
        pallets_sorted = sorted(pallets, key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0), errors='coerce'))
        
        used_cap = 0.0
        distinct_items = set()
        distinct_lots = set()
        sejenis_lots = []
        has_sac = False
        jml_sejenis = 0
        
        for p in pallets_sorted:
            p_name = str(p.get('Nama Produk', '')).strip()
            p_lot = str(p.get('Lot Number', '')).strip()
            if p_name:
                distinct_items.add(p_name)
                if p_name == target_item_clean:
                    jml_sejenis += 1
                    if p_lot:
                        sejenis_lots.append(p_lot)
            if p_lot:
                distinct_lots.add(p_lot)
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
            
        if len(distinct_items) >= 2 and target_item_clean not in distinct_items:
            continue
            
        # Priority 1: Lorong 1 Item Sejenis (Murni)
        if target_item_clean in distinct_items and len(distinct_items) == 1:
            prio = 1
            lot_ujung = (
                str(pallets_sorted[0].get('Lot Number', '')).strip()
                if sisi_masuk == "DEPAN"
                else str(pallets_sorted[-1].get('Lot Number', '')).strip()
            )
            sama_lot = any(l.upper() == target_lot_clean for l in sejenis_lots) if target_lot_clean else False
            newest_rank = max([parse_lifo_rank(l) for l in sejenis_lots], default=0)
            
            status = "🟢 Lanjutkan Lot Sama" if sama_lot else "🟢 Lanjutkan Sejenis (Lot Terbaru)"
            lot_match_pen = 0 if sama_lot else 1
            info_lot = f"Lot {lot_ujung}" if lot_ujung else "sejenis"
            isi_eksisting = f"{jml_sejenis} Pallet ({info_lot})"
            sub_sort = (lot_match_pen, -newest_rank, -used_cap)
            
        # Priority 2: Lorong 2 Item (Ujung Pintu Masuk Sejenis)
        elif target_item_clean in distinct_items and len(distinct_items) == 2:
            item_ujung = (
                str(pallets_sorted[0].get('Nama Produk', '')).strip()
                if sisi_masuk == "DEPAN"
                else str(pallets_sorted[-1].get('Nama Produk', '')).strip()
            )
            if item_ujung != target_item_clean:
                continue
                
            prio = 2
            status = f"🔵 Lorong 2 Item (Sejenis di {sisi_masuk.title()})"
            sama_lot = any(l.upper() == target_lot_clean for l in sejenis_lots) if target_lot_clean else False
            newest_rank = max([parse_lifo_rank(l) for l in sejenis_lots], default=0)
            item_lain = [x for x in distinct_items if x != target_item_clean][0]
            nama_ringkas = f"{item_lain[:16]}..." if len(item_lain) > 16 else item_lain
            isi_eksisting = f"{jml_sejenis}P sejenis + {len(pallets_sorted)-jml_sejenis}P {nama_ringkas}"
            sub_sort = (0 if sama_lot else 1, -newest_rank, -used_cap)
            
        # Priority 3: Lorong Kosong
        elif len(distinct_items) == 0:
            prio = 3
            status = "⚪ Lorong Kosong"
            isi_eksisting = f"Kosong (Kapasitas {max_target} Pallet)"
            sub_sort = (0, 0, 0.0)
            
        # Priority 4: Lorong >= 3 Item (Ujung Sejenis + Batas Maksimal Penumpukan Ujung)
        elif target_item_clean in distinct_items and len(distinct_items) >= 3:
            item_ujung = (
                str(pallets_sorted[0].get('Nama Produk', '')).strip()
                if sisi_masuk == "DEPAN"
                else str(pallets_sorted[-1].get('Nama Produk', '')).strip()
            )
            if item_ujung != target_item_clean:
                continue
                
            blok_ujung = hitung_blok_ujung(pallets_sorted, target_item_clean, sisi_masuk)
            sisa_kuota_ujung = MAX_TAIL_PALLETS_MULTI_ITEM - blok_ujung
            if sisa_kuota_ujung <= 0:
                continue
                
            sisa_slot_pallet = min(sisa_slot_pallet, sisa_kuota_ujung)
            prio = 4
            status = f"🟣 Lorong {len(distinct_items)} Item (Sisa Ekor Maks. {sisa_slot_pallet}P)"
            sama_lot = any(l.upper() == target_lot_clean for l in sejenis_lots) if target_lot_clean else False
            newest_rank = max([parse_lifo_rank(l) for l in sejenis_lots], default=0)
            isi_eksisting = f"{len(distinct_items)} item campur | Ujung {sisi_masuk.lower()}: {blok_ujung}P sejenis"
            sub_sort = (0 if sama_lot else 1, -newest_rank, blok_ujung)
            
        # Priority 5: Campur Baru (Lorong 1 Item Lain -> Menjadi Maksimal 2 Item)
        elif len(distinct_items) == 1 and target_item_clean not in distinct_items:
            prio = 5
            status = "🟡 Campur (Max 2 Item)"
            item_lain = list(distinct_items)[0]
            lot_str = ", ".join(sorted(list(distinct_lots))[:2])
            nama_ringkas = f"{item_lain[:16]}..." if len(item_lain) > 16 else item_lain
            isi_eksisting = f"{len(pallets_sorted)}P {nama_ringkas} (Lot: {lot_str})"
            sub_sort = (0, 0, used_cap)
        else:
            continue
            
        kandidat_rak.append({
            'Loc': loc, 'Kolom': k, 'Baris': b, 'Priority': prio,
            'ZonePenalty': zone_penalty, 'Status': status,
            'IsiEksisting': isi_eksisting, 'UsedCap': used_cap, 'SubSort': sub_sort,
            'MaxTarget': max_target, 'SisaSlot': sisa_slot_pallet
        })
        
    if not kandidat_rak:
        return pd.DataFrame()
        
    return pd.DataFrame(kandidat_rak).sort_values(
        by=['ZonePenalty', 'Priority', 'SubSort', 'Kolom', 'Baris'],
        ascending=[True, True, True, True, True]
    ).reset_index(drop=True)

def cari_lokasi_stok_hibrida(semua_data, target_item):
    if not semua_data:
        return pd.DataFrame()
        
    df_all = pd.DataFrame(semua_data)
    df_all['Baris_Int'] = pd.to_numeric(df_all['Baris'], errors='coerce').fillna(0).astype(int)
    df_all['Pallet_Ke_Num'] = pd.to_numeric(df_all['Pallet Ke'], errors='coerce').fillna(0)
    df_all['Catatan'] = df_all['Catatan'].fillna("").astype(str).replace('nan', '')
    
    df_target = df_all[
        (df_all['Nama Produk'] == target_item) & 
        (df_all['Part Number'].astype(str).str.strip() != "")
    ].copy()
    
    if df_target.empty:
        return pd.DataFrame()
        
    df_target['Group_Key'] = (
        df_target['Kolom'].astype(str) + "_" + 
        df_target['Baris_Int'].astype(str) + "_" + 
        df_target['Lot Number'].astype(str)
    )
    
    rekap_lokasi = []
    for _, df_group in df_target.groupby('Group_Key'):
        kol = str(df_group.iloc[0]['Kolom'])
        bar = int(df_group.iloc[0]['Baris_Int'])
        lot = str(df_group.iloc[0]['Lot Number'])
        catatan = str(df_group.iloc[0]['Catatan'])
        part_num = str(df_group.iloc[0]['Part Number'])
        satuan_str = get_satuan_kemasan(part_num)
        
        jml_utuh = df_group[~df_group['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)].shape[0]
        pecahan_rows = df_group[df_group['Keterangan'].astype(str).str.contains('Pecahan|Eceran', case=False, na=False)]
        jml_dus = sum(parse_keterangan(r['Keterangan'])[0] for _, r in pecahan_rows.iterrows())
        
        min_pos_target = df_group['Pallet_Ke_Num'].min()
        max_pos_target = df_group['Pallet_Ke_Num'].max()
        df_aisle = df_all[(df_all['Kolom'] == kol) & (df_all['Baris_Int'] == bar)]
        
        is_other = ~((df_aisle['Nama Produk'] == target_item) & (df_aisle['Lot Number'].astype(str) == lot))
        df_penghalang_depan = df_aisle[is_other & (df_aisle['Pallet_Ke_Num'] < min_pos_target)]
        df_penghalang_belakang = df_aisle[is_other & (df_aisle['Pallet_Ke_Num'] > max_pos_target)]
        
        jml_depan = len(df_penghalang_depan)
        jml_belakang = len(df_penghalang_belakang)
        
        if jml_depan == 0:
            akses_rank = 0
            min_penghalang = 0
            sisi_akses = "DEPAN"
            info_penghalang = ""
        elif jml_belakang == 0:
            akses_rank = 1
            min_penghalang = 0
            sisi_akses = "BELAKANG"
            info_penghalang = ""
        else:
            akses_rank = 2
            if jml_depan <= jml_belakang:
                min_penghalang = jml_depan
                sisi_akses = "TERHALANG_DEPAN"
                df_blok = df_penghalang_depan
            else:
                min_penghalang = jml_belakang
                sisi_akses = "TERHALANG_BELAKANG"
                df_blok = df_penghalang_belakang
                
            item_blok = df_blok['Nama Produk'].dropna().unique().tolist()
            if len(item_blok) == 1 and item_blok[0] == target_item:
                lot_blok = df_blok['Lot Number'].dropna().unique().tolist()
                info_penghalang = f"Lot {', '.join(lot_blok[:2])}"
            else:
                nama_blok = [x.split(',')[-1].strip() if ',' in x else x[:16] for x in item_blok[:2]]
                info_penghalang = f"{', '.join(nama_blok)}"
                
        rekap_lokasi.append({
            'Kolom': kol,
            'Baris': bar,
            'Lot Number': lot,
            'FIFO_Key': parse_fifo_key(lot),
            'Pallet': jml_utuh,
            'Dus': jml_dus,
            'Satuan': satuan_str,
            'Catatan': catatan,
            'Part': part_num,
            'AksesRank': akses_rank,
            'MinPenghalang': min_penghalang,
            'SisiAkses': sisi_akses,
            'InfoPenghalang': info_penghalang
        })
        
    df_rekap = pd.DataFrame(rekap_lokasi).sort_values(
        by=['MinPenghalang', 'FIFO_Key', 'Pallet', 'Dus', 'AksesRank', 'Lot Number', 'Kolom', 'Baris'],
        ascending=[True, True, True, True, True, True, True, True]
    ).reset_index(drop=True)
    
    return df_rekap

# ==========================================================
# 5. AUDIT LOG, PERSISTENT UNDO & AUTO-SAVE DRAFT INBOUND
# ==========================================================
def catat_log_mutasi(jenis, kolom, baris, produk, part_num, no_lot, pallet_utuh, pecahan, eceran, keterangan=""):
    waktu_sekarang = get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S")
    log_data = {
        "Waktu Transaksi": waktu_sekarang, "Jenis Transaksi": jenis, "Lokasi Rak": f"{kolom}-{baris}",
        "Nama Produk": produk, "Part Number": part_num, "No Lot": str(no_lot).strip() or "-",
        "Mutasi Pallet": float(pallet_utuh), "Mutasi Pecahan (Dus/Sak)": float(pecahan),
        "Mutasi Eceran (Pcs)": float(eceran), "Keterangan": keterangan,
        "Timestamp_System": firestore.SERVER_TIMESTAMP
    }
    try: db.collection(DB_LOG_MUTASI).add(log_data)
    except Exception: pass 

def simpan_draft_inbound(prefix_app, operator_name, draft_payload):
    """Menyimpan draft inbound ke 1 dokumen tunggal per operator agar tahan saat HP mati/terkunci."""
    if not operator_name:
        return
    draft_payload["saved_at"] = get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S")
    st.session_state.inbound_draft = draft_payload
    try:
        db.collection(DB_DRAFT_INBOUND).document(f"{prefix_app}_{operator_name}").set(draft_payload)
    except Exception:
        pass

def muat_draft_inbound(prefix_app, operator_name):
    """Memuat draft inbound terakhir milik operator (berlaku maksimal 12 jam)."""
    if not operator_name:
        st.session_state.inbound_draft = None
        return
    try:
        doc = db.collection(DB_DRAFT_INBOUND).document(f"{prefix_app}_{operator_name}").get()
        if doc.exists:
            data = doc.to_dict()
            if data and data.get("saved_at"):
                saved_dt = datetime.strptime(data["saved_at"], "%Y-%m-%d %H:%M:%S")
                if (get_waktu_jkt() - saved_dt) <= timedelta(hours=12):
                    st.session_state.inbound_draft = data
                    return
    except Exception:
        pass
    st.session_state.inbound_draft = None

def hapus_draft_inbound(prefix_app, operator_name):
    """Menghapus draft inbound setelah transaksi final tersimpan atau dibatalkan operator."""
    st.session_state.inbound_draft = None
    if not operator_name:
        return
    try:
        db.collection(DB_DRAFT_INBOUND).document(f"{prefix_app}_{operator_name}").delete()
    except Exception:
        pass

def simpan_undo_firestore(prefix_app, operator_name, undo_dict, action_msg, mode_label):
    now_str = get_waktu_jkt().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "status": "DONE",
        "mode": mode_label,
        "undo_dict": undo_dict,
        "action_msg": action_msg,
        "created_at": now_str
    }
    st.session_state.undo_record = payload
    try:
        db.collection(DB_UNDO_STATE).document(f"{prefix_app}_{operator_name}").set(payload)
    except Exception:
        pass

def muat_undo_firestore(prefix_app, operator_name):
    try:
        doc = db.collection(DB_UNDO_STATE).document(f"{prefix_app}_{operator_name}").get()
        if doc.exists:
            data = doc.to_dict()
            if data.get("status") == "DONE" and data.get("created_at"):
                created_dt = datetime.strptime(data["created_at"], "%Y-%m-%d %H:%M:%S")
                if (get_waktu_jkt() - created_dt) <= timedelta(minutes=30):
                    st.session_state.undo_record = data
                    return
    except Exception:
        pass
    st.session_state.undo_record = None

def tutup_undo_firestore(prefix_app, operator_name, new_status="CLOSED"):
    st.session_state.undo_record = {"status": new_status} if new_status == "UNDONE" else None
    try:
        db.collection(DB_UNDO_STATE).document(f"{prefix_app}_{operator_name}").update({"status": new_status})
    except Exception:
        pass

def render_header_dan_undo(app_title, accent_color, prefix_app, mode_badge="", reset_keys=None):
    col_hdr, col_sync, col_op = st.columns([2.6, 1.2, 1.2])
    with col_hdr:
        st.markdown(f"""
            <div class="db-info-box">
                <div style="font-size: 14px; font-weight: bold; color: {accent_color};">
                    🧭 {app_title}
                </div>
                <div style="font-size: 12px; opacity: 0.85; margin-top: 2px;">
                    👤 <strong>{st.session_state.operator}</strong>  |  📡 {NAMA_DATABASE}{mode_badge}
                </div>
            </div>
        """, unsafe_allow_html=True)
    with col_sync:
        st.markdown('<div class="btn-header-action">', unsafe_allow_html=True)
        if st.button("🔄 Sinkronkan\nStok", use_container_width=True, help="Muat ulang data rak terbaru dari server"):
            get_all_racking_data.clear()
            st.toast("Data stok rak berhasil disinkronkan!", icon="🔄")
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    with col_op:
        st.markdown('<div class="btn-header-action">', unsafe_allow_html=True)
        if st.button("👤 Ganti\nOperator", use_container_width=True):
            st.session_state.operator = None
            st.session_state.undo_record = None
            st.session_state.inbound_draft = None
            if reset_keys:
                for rk in reset_keys:
                    st.session_state[rk] = None
            st.session_state.slide = 0
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    undo_rec = st.session_state.get("undo_record")
    if undo_rec and undo_rec.get("status") == "DONE":
        masih_berlaku = True
        if undo_rec.get("created_at"):
            created_dt = datetime.strptime(undo_rec["created_at"], "%Y-%m-%d %H:%M:%S")
            if (get_waktu_jkt() - created_dt) > timedelta(minutes=30):
                masih_berlaku = False
                st.session_state.undo_record = None
                
        if masih_berlaku:
            mode_undo = undo_rec.get("mode", "TRANSAKSI")
            with st.container(border=True):
                st.success(f"✅ **BERHASIL ({mode_undo}):** {undo_rec.get('action_msg', '')}")
                st.caption(f"⏱️ Opsi Undo tersedia selama 30 menit (Input pukul {undo_rec.get('created_at', '')[-8:]} WIB)")
                c_u1, c_u2 = st.columns(2)
                with c_u1:
                    st.markdown('<div class="btn-undo">', unsafe_allow_html=True)
                    if st.button("⏪ BATALKAN (UNDO)", use_container_width=True):
                        with st.spinner("Memulihkan posisi stok..."):
                            batch_undo = db.batch()
                            kol_terdampak = []
                            for kol, dat in undo_rec.get("undo_dict", {}).items():
                                doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{kol}")
                                batch_undo.set(doc_ref, {'data': dat})
                                kol_terdampak.append(kol)
                            batch_undo.commit()
                            
                            get_all_racking_data.clear()
                            tutup_undo_firestore(prefix_app, st.session_state.operator, "UNDONE")
                            catat_log_mutasi(f"PEMBATALAN (UNDO {mode_undo})", ",".join(kol_terdampak), "ALL", "Koreksi Sistem", "-", "-", 0, 0, 0, f"Dibatalkan oleh: {st.session_state.operator}")
                            
                            pesan_undo = (
                                f"⏪ <b>UNDO {mode_undo} - RACK NAVIGATOR</b>\n"
                                f"━━━━━━━━━━━━━━━━━━\n"
                                f"📍 <b>Kolom Dipulihkan:</b> {', '.join(kol_terdampak)}\n"
                                f"👤 <b>Operator:</b> {escape_html(st.session_state.operator)}\n"
                                f"🕒 <b>Waktu:</b> {get_waktu_jkt().strftime('%H:%M:%S WIB')}"
                            )
                            kirim_notif_telegram(pesan_undo)
                            st.rerun()
                    st.markdown('</div>', unsafe_allow_html=True)
                with c_u2:
                    if st.button("TUTUP / LANJUT", use_container_width=True):
                        tutup_undo_firestore(prefix_app, st.session_state.operator, "CLOSED")
                        st.rerun()

    elif undo_rec and undo_rec.get("status") == "UNDONE":
        with st.container(border=True):
            st.warning("⏪ **Transaksi dibatalkan.** Stok kembali ke posisi semula.")
            if st.button("TUTUP PESAN", use_container_width=True):
                st.session_state.undo_record = None
                st.rerun()

# ==========================================================
# 6. TRANSAKSI ATOMIK FIREBASE (INBOUND, WIP & OUTBOUND STAGING)
# ==========================================================
@firestore.transactional
def proses_inbound_multi_transaction(transaction, doc_refs_dict, alokasi_list, lot_pilih, part_num, item_name, tipe_pecahan, catatan, sisi_masuk="BELAKANG"):
    snapshots = {kol: ref.get(transaction=transaction) for kol, ref in doc_refs_dict.items()}
    backup_kolom = {}
    data_per_kolom = {}
    
    for kol, snap in snapshots.items():
        existing = snap.to_dict().get('data', []) if snap.exists else []
        backup_kolom[kol] = [d.copy() for d in existing]
        data_per_kolom[kol] = [d.copy() for d in existing]
        
    in_is_sac = str(part_num).strip().upper().endswith('C')
    target_clean = str(item_name).strip()
    
    for item_alok in alokasi_list:
        k_pilih = item_alok['Kolom']
        b_int = int(item_alok['Baris'])
        b_pilih = float(b_int)
        qty_p = int(item_alok['qty_p'])
        qty_c = int(item_alok['qty_c'])
        
        if qty_p == 0 and qty_c == 0:
            continue
            
        if not is_rak_valid_untuk_produk(k_pilih, b_int, part_num):
            raise ValueError(f"Rak {k_pilih}-{b_int} bukan zona khusus untuk {item_name} (wajib Kolom F baris 1-16 atau Kolom G baris 17-28).")
            
        all_col = data_per_kolom[k_pilih]
        rak_ini = sorted(
            [d for d in all_col if float(d.get('Baris', 0) or 0) == b_pilih],
            key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0), errors='coerce')
        )
        rak_lain = [d for d in all_col if float(d.get('Baris', 0) or 0) != b_pilih]
        
        existing_items = {str(p.get('Nama Produk', '')).strip() for p in rak_ini if str(p.get('Nama Produk', '')).strip()}
        if len(existing_items) >= 2:
            if target_clean not in existing_items:
                raise ValueError(f"Rak {k_pilih}-{b_int} sudah terisi {len(existing_items)} jenis item lain. Tidak boleh menambah item baru berbeda!")
            item_ujung = (
                str(rak_ini[0].get('Nama Produk', '')).strip()
                if sisi_masuk == "DEPAN"
                else str(rak_ini[-1].get('Nama Produk', '')).strip()
            )
            if item_ujung != target_clean:
                raise ValueError(f"Rak {k_pilih}-{b_int} memiliki item lain di posisi {sisi_masuk.lower()}, tidak boleh dikunci dari sisi {sisi_masuk.lower()}!")
            
        max_sac = 31 if b_int <= 16 else 44
        max_dus = 32 if b_int <= 16 else 46
        
        used_cap = 0.0
        for p in rak_ini:
            p_part = str(p.get('Part Number', '')).strip().upper()
            used_cap += (1.0 / max_sac) if p_part.endswith('C') else (1.0 / max_dus)
            
        slot_masuk = qty_p + (1 if qty_c > 0 else 0)
        inc_cap = (slot_masuk / max_sac) if in_is_sac else (slot_masuk / max_dus)
        
        if used_cap + inc_cap > 1.0001:
            raise ValueError(f"Rak {k_pilih}-{b_int} melebihi kapasitas maksimal baris ({max_sac if in_is_sac else max_dus} palet).")
            
        pallets_baru = []
        for i in range(qty_p):
            pallets_baru.append({
                'Kolom': k_pilih, 'Baris': b_pilih,
                'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
                'Keterangan': 'Utuh', 'Catatan': catatan,
                'Pallet Ke': float(i + 1)
            })
            
        if qty_c > 0:
            pallets_baru.append({
                'Kolom': k_pilih, 'Baris': b_pilih,
                'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
                'Keterangan': f"Pecahan: {qty_c} {tipe_pecahan}", 'Catatan': catatan,
                'Pallet Ke': float(qty_p + 1)
            })
            
        if sisi_masuk == "DEPAN":
            gabungan_rak = pallets_baru + rak_ini
        else:
            gabungan_rak = rak_ini + pallets_baru
            
        for idx_p, d in enumerate(gabungan_rak, 1):
            d['Pallet Ke'] = float(idx_p)
            
        data_per_kolom[k_pilih] = rak_lain + gabungan_rak
        
    for kol, ref in doc_refs_dict.items():
        if snapshots[kol].exists:
            transaction.update(ref, {'data': data_per_kolom[kol]})
        else:
            transaction.set(ref, {'data': data_per_kolom[kol]})
            
    return backup_kolom

@firestore.transactional
def proses_pengambilan_stok(transaction, doc_ref, k_pilih, b_pilih, lot_pilih, part_num, item_name, init_pallet, init_dus, final_pallet, final_dus, satuan_kemasan, catatan_lama):
    snapshot = doc_ref.get(transaction=transaction)
    if not snapshot.exists:
        raise ValueError("Data Kolom tidak ditemukan di database.")
        
    all_col_data = snapshot.to_dict().get('data', [])
    data_lama_sebelum_update = [d.copy() for d in all_col_data]
    
    rak_ini = sorted(
        [d for d in all_col_data if float(d.get('Baris', 0) or 0) == float(b_pilih)],
        key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))
    )
    rak_lain = [d for d in all_col_data if float(d.get('Baris', 0) or 0) != float(b_pilih)]
    
    lot_records = [d for d in rak_ini if str(d.get('Lot Number', '')) == lot_pilih and str(d.get('Nama Produk', '')) == item_name]
    db_utuh = sum(1 for d in lot_records if 'pecahan' not in str(d.get('Keterangan', '')).lower() and 'eceran' not in str(d.get('Keterangan', '')).lower())
    db_dus = sum(parse_keterangan(d.get('Keterangan', ''))[0] for d in lot_records if 'pecahan' in str(d.get('Keterangan', '')).lower() or 'eceran' in str(d.get('Keterangan', '')).lower())
    
    if db_utuh != init_pallet or db_dus != init_dus:
        raise ValueError(f"Stok di database baru saja berubah ({db_utuh} Pallet, {db_dus} {satuan_kemasan}). Silakan tekan 'Sinkronkan Stok'.")
    
    first_target_idx = next(
        (idx for idx, d in enumerate(rak_ini) if str(d.get('Lot Number', '')) == lot_pilih and str(d.get('Nama Produk', '')) == item_name),
        len(rak_ini)
    )
    
    records_baru = []
    for _ in range(final_pallet):
        records_baru.append({
            'Kolom': k_pilih, 'Baris': float(b_pilih),
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': 'Utuh', 'Catatan': catatan_lama, 'Pallet Ke': 0.0
        })
        
    if final_dus > 0:
        records_baru.append({
            'Kolom': k_pilih, 'Baris': float(b_pilih),
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': f"Pecahan: {final_dus} {satuan_kemasan}", 'Catatan': catatan_lama, 'Pallet Ke': 0.0
        })
        
    updated_rak_ini = []
    inserted = False
    for idx, d in enumerate(rak_ini):
        if idx == first_target_idx and not inserted:
            updated_rak_ini.extend(records_baru)
            inserted = True
        if not (str(d.get('Lot Number', '')) == lot_pilih and str(d.get('Nama Produk', '')) == item_name):
            updated_rak_ini.append(d)
    if not inserted:
        updated_rak_ini.extend(records_baru)
        
    for i, d in enumerate(updated_rak_ini, 1):
        d['Pallet Ke'] = float(i)
        
    final_data = rak_lain + updated_rak_ini
    transaction.update(doc_ref, {'data': final_data}) 
    return data_lama_sebelum_update

@firestore.transactional
def proses_turun_ke_staging(
    transaction, doc_ref_asal, doc_ref_staging,
    k_pilih, b_pilih, lot_pilih, part_num, item_name,
    init_pallet, init_dus, final_pallet, final_dus,
    turun_pallet, turun_dus, satuan_kemasan,
    catatan_rak_lama, catatan_staging
):
    snap_asal = doc_ref_asal.get(transaction=transaction)
    if not snap_asal.exists:
        raise ValueError(f"Data Kolom {k_pilih} tidak ditemukan di database.")
        
    snap_staging = doc_ref_staging.get(transaction=transaction)
    
    all_col_asal = snap_asal.to_dict().get('data', [])
    backup_asal = [d.copy() for d in all_col_asal]
    
    all_col_staging = snap_staging.to_dict().get('data', []) if snap_staging.exists else []
    backup_staging = [d.copy() for d in all_col_staging]
    
    rak_ini = sorted(
        [d for d in all_col_asal if float(d.get('Baris', 0) or 0) == float(b_pilih)],
        key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))
    )
    rak_lain = [d for d in all_col_asal if float(d.get('Baris', 0) or 0) != float(b_pilih)]
    
    lot_records = [
        d for d in rak_ini
        if str(d.get('Lot Number', '')) == lot_pilih and str(d.get('Nama Produk', '')) == item_name
    ]
    db_utuh = sum(
        1 for d in lot_records
        if 'pecahan' not in str(d.get('Keterangan', '')).lower() and 'eceran' not in str(d.get('Keterangan', '')).lower()
    )
    db_dus = sum(
        parse_keterangan(d.get('Keterangan', ''))[0] for d in lot_records
        if 'pecahan' in str(d.get('Keterangan', '')).lower() or 'eceran' in str(d.get('Keterangan', '')).lower()
    )
    
    if db_utuh != init_pallet or db_dus != init_dus:
        raise ValueError(
            f"Stok di Rak {k_pilih}-{b_pilih} baru saja berubah ({db_utuh} Pallet, {db_dus} {satuan_kemasan}). "
            f"Silakan tekan 'Sinkronkan Stok'."
        )
        
    first_target_idx = next(
        (idx for idx, d in enumerate(rak_ini)
         if str(d.get('Lot Number', '')) == lot_pilih and str(d.get('Nama Produk', '')) == item_name),
        len(rak_ini)
    )
    
    sisa_records_asal = []
    for _ in range(final_pallet):
        sisa_records_asal.append({
            'Kolom': k_pilih, 'Baris': float(b_pilih),
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': 'Utuh', 'Catatan': catatan_rak_lama, 'Pallet Ke': 0.0
        })
    if final_dus > 0:
        sisa_records_asal.append({
            'Kolom': k_pilih, 'Baris': float(b_pilih),
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': f"Pecahan: {final_dus} {satuan_kemasan}", 'Catatan': catatan_rak_lama, 'Pallet Ke': 0.0
        })
        
    updated_rak_asal = []
    inserted = False
    for idx, d in enumerate(rak_ini):
        if idx == first_target_idx and not inserted:
            updated_rak_asal.extend(sisa_records_asal)
            inserted = True
        if not (str(d.get('Lot Number', '')) == lot_pilih and str(d.get('Nama Produk', '')) == item_name):
            updated_rak_asal.append(d)
    if not inserted:
        updated_rak_asal.extend(sisa_records_asal)
        
    for i, d in enumerate(updated_rak_asal, 1):
        d['Pallet Ke'] = float(i)
        
    staging_s1 = sorted(
        [d.copy() for d in all_col_staging if float(d.get('Baris', 0) or 0) == 1.0],
        key=lambda x: pd.to_numeric(x.get('Pallet Ke', 0))
    )
    staging_lain = [d.copy() for d in all_col_staging if float(d.get('Baris', 0) or 0) != 1.0]
    
    pallets_ke_staging = []
    for _ in range(turun_pallet):
        pallets_ke_staging.append({
            'Kolom': 'S', 'Baris': 1.0,
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': 'Utuh', 'Catatan': catatan_staging, 'Pallet Ke': 0.0
        })
    if turun_dus > 0:
        pallets_ke_staging.append({
            'Kolom': 'S', 'Baris': 1.0,
            'Nama Produk': item_name, 'Part Number': part_num, 'Lot Number': lot_pilih,
            'Keterangan': f"Pecahan: {turun_dus} {satuan_kemasan}", 'Catatan': catatan_staging, 'Pallet Ke': 0.0
        })
        
    gabungan_s1 = staging_s1 + pallets_ke_staging
    for i, d in enumerate(gabungan_s1, 1):
        d['Pallet Ke'] = float(i)
        
    transaction.update(doc_ref_asal, {'data': rak_lain + updated_rak_asal})
    if snap_staging.exists:
        transaction.update(doc_ref_staging, {'data': staging_lain + gabungan_s1})
    else:
        transaction.set(doc_ref_staging, {'data': staging_lain + gabungan_s1})
        
    return {k_pilih: backup_asal, 'S': backup_staging}

# ==========================================================
# 7. MODUL PEMILIHAN PRODUK TERPADU (SLIDE 2 -> 3 -> 4)
# ==========================================================
def render_pemilihan_produk(df_master):
    df_aktif = get_df_master_terfilter(df_master)
    
    if st.session_state.slide == 2:
        if st.button("🔙 GANTI AKSI"):
            st.session_state.mode_transaksi = None
            st.session_state.slide = 1
            st.rerun()

        st.markdown("### Pilih Kategori Produk")
        if st.session_state.get("mode_transaksi") == "WIP":
            st.caption("🏭 *Menampilkan khusus item bahan WIP (Seluruh Preform, Cup Color, & Cup Mountea/JDB).*")

        urutan_kategori = ["PREFORM", "CUP NATURAL", "CUP COLOR", "CUP PRINTING", "CAP"]
        kategori_ada = [k for k in urutan_kategori if k in set(df_aktif['Kategori'].unique())]
        if not kategori_ada:
            kategori_ada = urutan_kategori

        for i in range(0, len(kategori_ada), 2):
            cols = st.columns(2)
            with cols[0]:
                kat1 = kategori_ada[i]
                lbl1 = "CAP (TUTUP)" if kat1 == "CAP" else kat1
                if st.button(lbl1, key=f"kat_{kat1}", use_container_width=True):
                    st.session_state.kategori = kat1
                    st.session_state.slide = 3
                    st.rerun()
            if i + 1 < len(kategori_ada):
                with cols[1]:
                    kat2 = kategori_ada[i + 1]
                    lbl2 = "CAP (TUTUP)" if kat2 == "CAP" else kat2
                    if st.button(lbl2, key=f"kat_{kat2}", use_container_width=True):
                        st.session_state.kategori = kat2
                        st.session_state.slide = 3
                        st.rerun()

    elif st.session_state.slide == 3:
        if st.button("🔙 KEMBALI"):
            st.session_state.slide = 2
            st.rerun()
        st.markdown(f"### Gramasi: **{st.session_state.kategori}**")
        df_kat = df_aktif[df_aktif['Kategori'] == st.session_state.kategori]
        list_gramasi = sorted(df_kat['Gramasi'].unique())
        
        st.markdown('<div class="btn-gramasi">', unsafe_allow_html=True)
        for i in range(0, len(list_gramasi), 2):
            cols = st.columns(2)
            with cols[0]:
                if st.button(f"{list_gramasi[i]}", key=f"g_{i}", use_container_width=True):
                    st.session_state.gramasi = list_gramasi[i]
                    st.session_state.slide = 4
                    st.rerun()
            if i + 1 < len(list_gramasi):
                with cols[1]:
                    if st.button(f"{list_gramasi[i+1]}", key=f"g_{i+1}", use_container_width=True):
                        st.session_state.gramasi = list_gramasi[i+1]
                        st.session_state.slide = 4
                        st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state.slide == 4:
        if st.button("🔙 KEMBALI"):
            st.session_state.slide = 3
            st.rerun()
        st.markdown("### Pilih Item Spesifik")
        st.caption(f"{st.session_state.kategori} | {st.session_state.gramasi}")
        
        df_item = df_aktif[(df_aktif['Kategori'] == st.session_state.kategori) & (df_aktif['Gramasi'] == st.session_state.gramasi)]
        for idx, row in df_item.iterrows():
            nama_brg = row['Nama Barang']
            part_num = str(row['Part Number']).strip().upper()
            
            tipe_kemasan = ""
            if part_num:
                if part_num[-1] in ['D', 'G']: tipe_kemasan = "[📦 DUS] "
                elif part_num[-1] == 'C': tipe_kemasan = "[👝 SAK] "
                elif part_num[-1] == 'A': tipe_kemasan = "[🗃️ NICTAINER] "
                    
            btn_label = f"{tipe_kemasan}{format_nama_warna_bold(nama_brg)}"
            if st.button(btn_label, use_container_width=True, key=f"btn_item_{idx}"): 
                st.session_state.item = nama_brg
                st.session_state.pilih_part = part_num
                st.session_state.slide = 5
                st.rerun()

# ==========================================================
# 8. MODUL UI INBOUND MULTI-RAK TERPADU + AUTO-SAVE DRAFT
# ==========================================================
def render_alur_inbound_terpadu(prefix_app, judul_inbound, default_sisi_masuk="BELAKANG", jenis_log="INBOUND (Remote)", judul_tele="INBOUND - RACK NAVIGATOR", mode_undo_label="INBOUND", reset_keys=None):
    if st.session_state.slide == 5:
        if st.button("🔙 GANTI ITEM"):
            st.session_state.slide = 4 if st.session_state.get("gramasi") else 2
            st.rerun()
            
        st.markdown(f"### {judul_inbound}")
        st.info(format_nama_warna_bold(st.session_state.item))
        if str(st.session_state.pilih_part).strip().upper() in PART_KHUSUS_TOP_TIER:
            st.caption("🏗️ *Item Zona Khusus: Utama di Rak F1–F16, jika penuh dialihkan ke G17–G28.*")
        
        satuan_otomatis = get_satuan_kemasan(st.session_state.pilih_part)
        opsi_sisi = [
            "BELAKANG (Normal - Lanjut Antrean Belakang)",
            "DEPAN (Push-Back - Dorong Stok Lama ke Dalam)"
        ]
        idx_default_sisi = 1 if st.session_state.get("in_sisi_masuk", default_sisi_masuk) == "DEPAN" else 0
        
        # --- KARTU 1: SUSUN NOMOR LOT TERSTRUKTUR ---
        with st.container(border=True):
            st.markdown("**🏷️ Susun Nomor Lot Produksi:**")
            mode_lot_manual = st.checkbox(
                "✏️ Input Manual (Jika format lot non-standar)",
                value=False,
                key=f"chk_lot_man_{prefix_app}"
            )
            
            if mode_lot_manual:
                in_lot = st.text_input(
                    "Nomor Lot Manual",
                    value=st.session_state.get("in_lot") or "",
                    placeholder="Cth: 1A1231126"
                ).strip().upper()
            else:
                lot_awal = str(st.session_state.get("in_lot") or "").strip().upper()
                opsi_mesin = ["1", "2", "3", "4", "5"]
                opsi_grup = ["A", "B", "C", "D"]
                opsi_shift = ["1", "2", "3"]
                
                idx_m, idx_g, idx_s = 0, 0, 0
                tgl_default = get_waktu_jkt().date()
                
                if len(lot_awal) == 9:
                    if lot_awal[0] in opsi_mesin: idx_m = opsi_mesin.index(lot_awal[0])
                    if lot_awal[1] in opsi_grup: idx_g = opsi_grup.index(lot_awal[1])
                    if lot_awal[2] in opsi_shift: idx_s = opsi_shift.index(lot_awal[2])
                    try:
                        tgl_default = datetime.strptime(lot_awal[3:], "%d%m%y").date()
                    except Exception:
                        pass

                c_l1, c_l2, c_l3, c_l4 = st.columns([1, 1, 1, 1.8])
                with c_l1:
                    sel_mesin = st.selectbox("Mesin", opsi_mesin, index=idx_m, key=f"lot_m_{prefix_app}")
                with c_l2:
                    sel_grup = st.selectbox("Grup", opsi_grup, index=idx_g, key=f"lot_g_{prefix_app}")
                with c_l3:
                    sel_shift = st.selectbox("Shift", opsi_shift, index=idx_s, key=f"lot_s_{prefix_app}")
                with c_l4:
                    sel_tgl = st.date_input(
                        "Tanggal",
                        value=tgl_default,
                        format="DD/MM/YYYY",
                        key=f"lot_d_{prefix_app}"
                    )
                    
                in_lot = f"{sel_mesin}{sel_grup}{sel_shift}{sel_tgl.strftime('%d%m%y')}"
                st.markdown(
                    f"<div style='background:rgba(16,185,129,0.12); border:1px solid #10B981; "
                    f"padding:8px 12px; border-radius:8px; text-align:center; margin-top:4px;'>"
                    f"Hasil Nomor Lot: <span style='font-size:20px; font-weight:bold; letter-spacing:1.5px;'>{in_lot}</span>"
                    f"</div>",
                    unsafe_allow_html=True
                )

        # --- KARTU 2: KUANTITAS & PINTU MASUK ---
        with st.container(border=True):
            c_q1, c_q2 = st.columns(2)
            with c_q1:
                in_qty_p = st.number_input("Berapa Pallet Utuh?", min_value=0, value=st.session_state.get("in_qty_p") if st.session_state.get("in_qty_p") is not None else 1)
            with c_q2:
                in_qty_c = st.number_input(f"Qty Pecahan ({satuan_otomatis})", min_value=0, value=st.session_state.get("in_qty_c") if st.session_state.get("in_qty_c") is not None else 0)
            
            sisi_pilihan = st.radio(
                "🚪 Pintu Masuk Shuttle Racking:",
                options=opsi_sisi,
                index=idx_default_sisi
            )
                
        st.write("")
        if st.button("🔍 HITUNG ALOKASI RAK OTOMATIS", type="primary", use_container_width=True):
            if not in_lot.strip():
                st.error("⚠️ Nomor Lot wajib diisi!")
            elif in_qty_p == 0 and in_qty_c == 0:
                st.error("⚠️ Masukkan minimal 1 kuantitas!")
            else:
                st.session_state.in_lot = in_lot.strip().upper()
                st.session_state.in_qty_p = in_qty_p
                st.session_state.in_qty_c = in_qty_c
                st.session_state.in_tipe_c = satuan_otomatis
                st.session_state.in_sisi_masuk = "DEPAN" if sisi_pilihan.startswith("DEPAN") else "BELAKANG"
                st.session_state.in_rak_skip = []
                st.session_state.slide = 6
                st.rerun()

    elif st.session_state.slide == 6:
        if st.button("🔙 UBAH INPUT"):
            st.session_state.in_rak_skip = []
            st.session_state.slide = 5
            st.rerun()
            
        if st.session_state.get("in_rak_skip") is None:
            st.session_state.in_rak_skip = []
            
        sisi_masuk = st.session_state.get("in_sisi_masuk", default_sisi_masuk)
        sisi_label = "Depan" if sisi_masuk == "DEPAN" else "Belakang"
        sisi_color = "#3B82F6" if sisi_masuk == "DEPAN" else "#10B981"
        part_aktif = str(st.session_state.pilih_part).strip().upper()
        lot_aktif = str(st.session_state.in_lot or "").strip()
        qty_p_in = int(st.session_state.in_qty_p or 0)
        qty_c_in = int(st.session_state.in_qty_c or 0)
        satuan_c = st.session_state.in_tipe_c
        total_slot_butuh = qty_p_in + (1 if qty_c_in > 0 else 0)
        
        st.markdown("### Rekomendasi Lokasi Rak")
        teks_pec_hdr = f" + {qty_c_in} {satuan_c}" if qty_c_in > 0 else ""
        with st.container(border=True):
            st.markdown(f"📦 {format_nama_warna_bold(st.session_state.item)}")
            st.markdown(
                f"<div style='font-size:13px; opacity:0.9;'>Lot: <strong>{lot_aktif}</strong>  |  "
                f"Total: <strong>{qty_p_in} Pallet{teks_pec_hdr}</strong>  |  "
                f"Masuk: <span style='color:{sisi_color}; font-weight:bold;'>{sisi_label.upper()}</span></div>",
                unsafe_allow_html=True
            )
        
        with st.spinner("📐 Mencari lorong rak terbaik..."):
            semua_data = get_all_racking_data()
            df_kandidat = hitung_kandidat_rak_inbound(
                semua_data, st.session_state.item, part_aktif,
                sisi_masuk=sisi_masuk, target_lot=lot_aktif
            )
            in_is_sac = part_aktif.endswith('C')
                
            if df_kandidat.empty:
                if part_aktif in PART_KHUSUS_TOP_TIER:
                    st.error("❌ Seluruh rak zona khusus JDO (F1–F16 & G17–G28) penuh!")
                else:
                    st.error("❌ Seluruh rak penuh atau tidak memenuhi syarat posisi item!")
            else:
                if in_is_sac:
                    st.caption("🛡️ *Rak Sak sisa ≤ 1 slot otomatis dilewati (toleransi panjang fisik).*")
                    
                rak_skip_list = st.session_state.in_rak_skip
                if rak_skip_list:
                    df_kandidat = df_kandidat[~df_kandidat['Loc'].isin(rak_skip_list)].reset_index(drop=True)
                    
                total_kapasitas_gudang = int(df_kandidat['SisaSlot'].sum()) if not df_kandidat.empty else 0
                if total_kapasitas_gudang < total_slot_butuh:
                    zona_teks = "zona khusus JDO (F1–F16 & G17–G28)" if part_aktif in PART_KHUSUS_TOP_TIER else "gudang"
                    st.error(f"❌ Total sisa kapasitas {zona_teks} ({total_kapasitas_gudang} palet) tidak mencukupi untuk menampung {total_slot_butuh} palet!")
                else:
                    rencana_alokasi = []
                    sisa_utuh, sisa_pecahan = qty_p_in, qty_c_in
                    
                    for _, row in df_kandidat.iterrows():
                        if sisa_utuh == 0 and sisa_pecahan == 0:
                            break
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
                                'Kolom': row['Kolom'],
                                'Baris': int(row['Baris']),
                                'qty_p': ambil_p,
                                'qty_c': ambil_c,
                                'Status': row['Status'],
                                'IsiEksisting': row['IsiEksisting'],
                                'SisaSlot': int(row['SisaSlot'])
                            })
                            
                    # --- AUTO-SAVE DRAFT OTOMATIS (HEMAT KUOTA: HANYA TULIS SAAT ISI BERUBAH) ---
                    draft_sig = f"{st.session_state.item}|{lot_aktif}|{qty_p_in}|{qty_c_in}|{sisi_masuk}|" + ",".join(
                        f"{x['Kolom']}-{x['Baris']}:{x['qty_p']}:{x['qty_c']}" for x in rencana_alokasi
                    )
                    if st.session_state.get("_last_draft_sig") != draft_sig:
                        simpan_draft_inbound(prefix_app, st.session_state.operator, {
                            "kategori": st.session_state.get("kategori") or "",
                            "gramasi": st.session_state.get("gramasi") or "",
                            "item": st.session_state.item,
                            "pilih_part": part_aktif,
                            "in_lot": lot_aktif,
                            "in_qty_p": qty_p_in,
                            "in_qty_c": qty_c_in,
                            "in_tipe_c": satuan_c,
                            "in_sisi_masuk": sisi_masuk,
                            "alokasi_terpilih": rencana_alokasi
                        })
                        st.session_state["_last_draft_sig"] = draft_sig

                    st.info("💾 **Draft tersimpan otomatis.** Layar HP/tablet aman dimatikan selama proses muat ke rak.")

                    for i_alok, al in enumerate(rencana_alokasi, 1):
                        loc_str = f"{al['Kolom']}-{al['Baris']}"
                        teks_pec = f" + {al['qty_c']} {satuan_c}" if al['qty_c'] > 0 else ""
                        slot_dipakai = al['qty_p'] + (1 if al['qty_c'] > 0 else 0)
                        sisa_setelah = al['SisaSlot'] - slot_dipakai
                        
                        if sisa_setelah == 0:
                            instruksi_baris = f"Isi dari {sisi_label} sampai penuh ± {al['qty_p']} Pallet{teks_pec}"
                        else:
                            instruksi_baris = f"Isi dari {sisi_label} ± {al['qty_p']} Pallet{teks_pec}"
                            
                        with st.container(border=True):
                            c_info, c_skip = st.columns([3.3, 1.3])
                            with c_info:
                                st.markdown(f"<div style='font-size:20px; font-weight:bold;'>{i_alok}. 📍 RAK {loc_str}</div>", unsafe_allow_html=True)
                                st.markdown(f"➡️ **{instruksi_baris}**")
                                st.caption(f"Kondisi rak: {al['IsiEksisting']}")
                            with c_skip:
                                st.markdown('<div class="btn-compact">', unsafe_allow_html=True)
                                if st.button("🚫 Lewati", key=f"skip_{prefix_app}_{loc_str}_{i_alok}", use_container_width=True, help=f"Lewati Rak {loc_str} jika di lapangan penuh/terhalang"):
                                    st.session_state.in_rak_skip.append(loc_str)
                                    st.rerun()
                                st.markdown('</div>', unsafe_allow_html=True)

                if rak_skip_list:
                    c_sk1, c_sk2 = st.columns([3.2, 1.4])
                    with c_sk1:
                        st.caption(f"🚫 Dilewati: **{', '.join(rak_skip_list)}**")
                    with c_sk2:
                        st.markdown('<div class="btn-compact">', unsafe_allow_html=True)
                        if st.button("↩️ Reset", key=f"reset_skip_{prefix_app}", use_container_width=True):
                            st.session_state.in_rak_skip = []
                            st.rerun()
                        st.markdown('</div>', unsafe_allow_html=True)
                            
                if total_kapasitas_gudang >= total_slot_butuh:
                    st.write("")
                    if st.button("✅ LANJUT KE KONFIRMASI AKTUAL", type="primary", use_container_width=True):
                        st.session_state.alokasi_terpilih = rencana_alokasi
                        st.session_state.slide = 7
                        st.rerun()

        st.write("")
        with st.expander("ℹ️ Aturan Zonasi & Urutan Rekomendasi Rak", expanded=False):
            st.markdown(f"""
**Zonasi Area (Otomatis Melimpah Jika Zona Utama Penuh):**
* **Preform:** Diutamakan **Baris 1–16**, jika penuh otomatis melimpah ke **Baris 17–28**.
* **Cup:** Diutamakan **Baris 17–28**, jika penuh otomatis melimpah ke **Baris 1–16**.
* **JDO Diamond:** Diutamakan **Kolom F (Baris 1–16)**, jika penuh dialihkan ke **Kolom G (Baris 17–28)**.

**Urutan Pemilihan Lorong di Dalam Zona:**
1. **Lorong 1 Item Sejenis:** Melanjutkan lot yang sama atau lot/shift terbaru sampai penuh.
2. **Lorong 2 Item:** Melanjutkan item sejenis yang berada di ujung pintu masuk (`Belakang` / `Depan`).
3. **Lorong Kosong:** Membuka lorong kosong baru agar stok utama tetap murni 1 item.
4. **Lorong ≥3 Item (Penampung Sisa):** Menampung sisa palet di ujung yang sejenis (maksimal **{MAX_TAIL_PALLETS_MULTI_ITEM} palet** di ujung agar bongkar *outbound* tidak terhambat).
5. **Campur Baru (Max 2 Item):** Menggabung ke lorong yang baru berisi 1 item lain sebagai pilihan terakhir.
            """)

    elif st.session_state.slide == 7:
        if st.button("🔙 BATAL (UBAH ALOKASI)"):
            st.session_state.slide = 6
            st.rerun()
            
        alokasi = st.session_state.alokasi_terpilih or []
        sisi_masuk = st.session_state.get("in_sisi_masuk", default_sisi_masuk)
        sisi_str = "DEPAN (Push-Back)" if sisi_masuk == "DEPAN" else "BELAKANG (Normal)"
        target_p_awal = int(st.session_state.in_qty_p or 0)
        target_c_awal = int(st.session_state.in_qty_c or 0)
        satuan_c = st.session_state.in_tipe_c
        in_is_sac = str(st.session_state.pilih_part).strip().upper().endswith('C')
        lot_aktif = str(st.session_state.in_lot or "").strip()
        
        st.markdown(f"### Konfirmasi Aktual {judul_inbound}")
        with st.container(border=True):
            st.markdown(format_nama_warna_bold(st.session_state.item))
            st.markdown(f"**Lot:** `{lot_aktif}`  |  **Pintu:** {sisi_str}")
            teks_pec_target = f", {target_c_awal} {satuan_c}" if target_c_awal > 0 else ""
            st.caption(f"Rencana Awal: {target_p_awal} Pallet Utuh{teks_pec_target} *(Ubah angka di bawah jika aktual lapangan berbeda)*")
            
        st.markdown("**Penempatan Aktual per Lorong Rak:**")
        alokasi_aktual = []
        indeks_hapus = None
        
        for idx_a, al in enumerate(alokasi):
            b_int = int(al['Baris'])
            max_fisik_baris = (31 if b_int <= 16 else 44) if in_is_sac else (32 if b_int <= 16 else 46)
            
            with st.container(border=True):
                c_hdr1, c_hdr2 = st.columns([3.5, 1.2])
                with c_hdr1:
                    st.markdown(f"📍 **Rak {al['Kolom']}-{b_int}**  |  <span style='font-size:12px;'>{al['Status']}</span>", unsafe_allow_html=True)
                with c_hdr2:
                    if len(alokasi) > 1:
                        st.markdown('<div class="btn-compact">', unsafe_allow_html=True)
                        if st.button("🗑️ Hapus", key=f"del_alok_{prefix_app}_{idx_a}", use_container_width=True):
                            indeks_hapus = idx_a
                        st.markdown('</div>', unsafe_allow_html=True)
                        
                c_in1, c_in2 = st.columns(2)
                with c_in1:
                    val_p = st.number_input(
                        "Pallet Utuh Aktual", min_value=0, max_value=max_fisik_baris, step=1,
                        value=int(al['qty_p']), key=f"akt_p_{prefix_app}_{al['Kolom']}_{b_int}_{idx_a}"
                    )
                with c_in2:
                    val_c = st.number_input(
                        f"Pecahan Aktual ({satuan_c})", min_value=0, step=1,
                        value=int(al['qty_c']), key=f"akt_c_{prefix_app}_{al['Kolom']}_{b_int}_{idx_a}"
                    )
                alokasi_aktual.append({
                    'Kolom': al['Kolom'], 'Baris': b_int,
                    'qty_p': val_p, 'qty_c': val_c, 'Status': al['Status']
                })
                
        if indeks_hapus is not None:
            alokasi.pop(indeks_hapus)
            st.session_state.alokasi_terpilih = alokasi
            st.rerun()
            
        st.session_state.alokasi_terpilih = alokasi_aktual
        
        total_p_aktual = sum(x['qty_p'] for x in alokasi_aktual)
        total_c_aktual = sum(x['qty_c'] for x in alokasi_aktual)
        sisa_p_belum = target_p_awal - total_p_aktual
        sisa_c_belum = target_c_awal - total_c_aktual
        
        if sisa_p_belum > 0 or sisa_c_belum > 0:
            st.warning(
                f"⚠️ **Ada sisa belum ditempatkan:** **{max(0, sisa_p_belum)} Pallet**"
                + (f", **{max(0, sisa_c_belum)} {satuan_c}**" if sisa_c_belum > 0 else "")
                + " (karena rak sebelumnya sudah penuh di fisik)."
            )
            semua_data = get_all_racking_data()
            df_kandidat_sisa = hitung_kandidat_rak_inbound(
                semua_data, st.session_state.item, st.session_state.pilih_part,
                sisi_masuk=sisi_masuk, target_lot=lot_aktif
            )
            rak_sudah_dipakai = {f"{x['Kolom']}-{x['Baris']}" for x in alokasi_aktual}
            
            if not df_kandidat_sisa.empty:
                df_opsi_tambah = df_kandidat_sisa[~df_kandidat_sisa['Loc'].isin(rak_sudah_dipakai)].reset_index(drop=True)
                if not df_opsi_tambah.empty:
                    opsi_label = [
                        f"{r['Loc']} | {r['Status']} (Sisa {r['SisaSlot']} slot)"
                        for _, r in df_opsi_tambah.head(15).iterrows()
                    ]
                    c_add1, c_add2 = st.columns([2.6, 1.4])
                    with c_add1:
                        pilih_rak_tambahan = st.selectbox(
                            "Pilih Rak Berikutnya untuk Sisa Palet:",
                            options=opsi_label,
                            label_visibility="collapsed",
                            key=f"sel_add_rak_{prefix_app}_{len(alokasi_aktual)}"
                        )
                    with c_add2:
                        st.markdown('<div class="btn-compact">', unsafe_allow_html=True)
                        if st.button("➕ Tambah Rak", use_container_width=True, key=f"btn_add_rak_{prefix_app}"):
                            idx_pilih = opsi_label.index(pilih_rak_tambahan)
                            row_t = df_opsi_tambah.iloc[idx_pilih]
                            alokasi_aktual.append({
                                'Kolom': row_t['Kolom'],
                                'Baris': int(row_t['Baris']),
                                'qty_p': max(0, sisa_p_belum),
                                'qty_c': max(0, sisa_c_belum),
                                'Status': row_t['Status']
                            })
                            st.session_state.alokasi_terpilih = alokasi_aktual
                            st.rerun()
                        st.markdown('</div>', unsafe_allow_html=True)
        elif sisa_p_belum < 0 or sisa_c_belum < 0:
            st.info(
                f"ℹ️ **Aktual lebih besar dari rencana awal:** Total terinput **{total_p_aktual} Pallet**"
                + (f", **{total_c_aktual} {satuan_c}**" if total_c_aktual > 0 else "")
                + ". Pastikan angka fisik sudah sesuai sebelum menyimpan."
            )
            
        st.write("")
        catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Aktual muat 29 palet, 1 pindah ke rak sebelah")
        st.write("")
        
        alokasi_valid = [x for x in alokasi_aktual if x['qty_p'] > 0 or x['qty_c'] > 0]
        if not alokasi_valid:
            st.error("⚠️ Masukkan minimal 1 kuantitas aktual pada rak tujuan.")
        else:
            if st.button("📥 MASUKKAN KE DATABASE SEKARANG", type="primary", use_container_width=True):
                with st.spinner("Memasukkan data aktual ke rak..."):
                    kolom_unik = sorted(list(set(al['Kolom'] for al in alokasi_valid)))
                    doc_refs_dict = {kol: db.collection(NAMA_DATABASE).document(f"Kolom_{kol}") for kol in kolom_unik}
                    transaction = db.transaction()
                    
                    try:
                        backup_kolom = proses_inbound_multi_transaction(
                            transaction, doc_refs_dict, alokasi_valid,
                            lot_aktif, st.session_state.pilih_part, st.session_state.item,
                            satuan_c, catatan_operator, sisi_masuk=sisi_masuk
                        )
                        
                        teks_keterangan = f"Op: {st.session_state.operator} | Sisi: {sisi_str}"
                        if total_p_aktual != target_p_awal or total_c_aktual != target_c_awal:
                            teks_keterangan += f" | Rencana:{target_p_awal}P -> Aktual:{total_p_aktual}P"
                        if catatan_operator.strip():
                            teks_keterangan += f" | Catatan: {catatan_operator.strip()}"
                        
                        ringkasan_rak = []
                        for al in alokasi_valid:
                            catat_log_mutasi(
                                jenis_log, al['Kolom'], al['Baris'],
                                st.session_state.item, st.session_state.pilih_part, lot_aktif,
                                al['qty_p'], al['qty_c'], 0, teks_keterangan
                            )
                            p_str = f"+{al['qty_c']}{satuan_c}" if al['qty_c'] > 0 else ""
                            ringkasan_rak.append(f"{al['Kolom']}-{al['Baris']} ({al['qty_p']}P{p_str})")
                        
                        lokasi_gabungan = ", ".join(ringkasan_rak)
                        pecahan_tele = f", {total_c_aktual} {satuan_c}" if total_c_aktual > 0 else ""
                        
                        pesan_tele = (
                            f"📥 <b>{judul_tele}</b>\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"📦 <b>Item:</b> {escape_html(st.session_state.item)}\n"
                            f"🏷️ <b>Lot:</b> <code>{escape_html(lot_aktif)}</code>\n"
                            f"📊 <b>Total Aktual:</b> {total_p_aktual} Pallet{pecahan_tele}\n"
                            f"📍 <b>Penempatan:</b> {escape_html(lokasi_gabungan)} ({sisi_str})\n"
                            f"👤 <b>Operator:</b> {escape_html(st.session_state.operator)}\n"
                        )
                        if catatan_operator.strip():
                            pesan_tele += f"📝 <b>Catatan:</b> <i>{escape_html(catatan_operator.strip())}</i>\n"
                        kirim_notif_telegram(pesan_tele)
                        
                        # Hapus draft aktif dari Firestore karena sudah selesai diinput
                        hapus_draft_inbound(prefix_app, st.session_state.operator)
                        st.session_state.pop("_last_draft_sig", None)
                        
                        get_all_racking_data.clear()
                        msg_sukses = f"{total_p_aktual} Pallet{pecahan_tele} masuk ke {lokasi_gabungan} ({sisi_str})."
                        simpan_undo_firestore(prefix_app, st.session_state.operator, backup_kolom, msg_sukses, mode_label=mode_undo_label)
                        
                        st.toast(msg_sukses, icon="✅")
                        keys_clear = ['kategori', 'gramasi', 'item', 'pilih_part', 'mode_transaksi', 'in_lot', 'in_qty_p', 'in_qty_c', 'in_tipe_c', 'in_sisi_masuk', 'in_rak_skip', 'alokasi_terpilih']
                        if reset_keys:
                            keys_clear.extend(reset_keys)
                        for k in set(keys_clear):
                            if k in st.session_state:
                                st.session_state[k] = None
                        st.session_state.slide = 1
                        st.rerun()
                        
                    except ValueError as err:
                        st.error(f"❌ Validasi Kapasitas: {err}")
                    except Exception as err:
                        st.error(f"❌ Kesalahan Sistem: {err}")

# ==========================================================
# 9. MODUL UI PENGAMBILAN TERPADU (UNTUK CABANG WIP DI LTIn.py)
# ==========================================================
def render_alur_pengambilan_terpadu(
    prefix_app, df_master, judul_ambil, label_tombol_eksekusi,
    jenis_log, judul_tele, mode_undo_label, reset_keys
):
    k_pilih = st.session_state.get(reset_keys['kolom'])
    b_pilih = st.session_state.get(reset_keys['baris'])
    lot_pilih = st.session_state.get(reset_keys['lot'])
    init_p = int(st.session_state.get(reset_keys['max_p']) or 0)
    init_d = int(st.session_state.get(reset_keys['max_d']) or 0)
    catatan_rak_lama = st.session_state.get(reset_keys['catatan']) or ""
    
    row_m = df_master[df_master['Nama Barang'] == st.session_state.item]
    total_pcs_pallet = int(row_m.iloc[0]['Total_Pcs_Pallet']) if not row_m.empty and int(row_m.iloc[0]['Total_Pcs_Pallet']) > 0 else 1
    pcs_per_box = int(row_m.iloc[0]['Pcs_Per_Box']) if not row_m.empty and int(row_m.iloc[0]['Pcs_Per_Box']) > 0 else 1
    pcs_per_sac = int(row_m.iloc[0]['Pcs_Per_Sac']) if not row_m.empty and int(row_m.iloc[0]['Pcs_Per_Sac']) > 0 else 1
    
    satuan = get_satuan_kemasan(st.session_state.pilih_part)
    pcs_per_pecahan = pcs_per_sac if satuan == "Sak" else pcs_per_box
    pecahan_per_pallet = max(1, total_pcs_pallet // pcs_per_pecahan) if pcs_per_pecahan > 0 else 1
    total_pcs_init = (init_p * total_pcs_pallet) + (init_d * pcs_per_pecahan)
    
    st.markdown(f"### {judul_ambil}")
    with st.container(border=True):
        st.markdown(format_nama_warna_bold(st.session_state.item))
        st.markdown(f"**Lokasi:** Rak {k_pilih}-{b_pilih}  |  **Lot:** `{lot_pilih}`")
        st.info(f"📦 **Stok Tercatat di Rak:** **{init_p} Pallet**, **{init_d} {satuan}** ({total_pcs_init:,} Pcs)")
        
    st.markdown("**1. Berapa yang Diambil Forklift?**")
    default_ambil_p = 1 if init_p > 0 else 0
    default_ambil_d = init_d if init_p == 0 else 0
    
    c_amb1, c_amb2 = st.columns(2)
    with c_amb1:
        ambil_p = st.number_input(
            "Pallet Utuh Diambil", min_value=0, max_value=init_p, step=1,
            value=default_ambil_p, key=f"amb_p_{prefix_app}_{k_pilih}_{b_pilih}_{lot_pilih}"
        )
    with c_amb2:
        max_ambil_d = init_d if init_p == 0 else max(init_d, pecahan_per_pallet - 1)
        ambil_d = st.number_input(
            f"Pecahan Diambil ({satuan})", min_value=0, max_value=max_ambil_d, step=1,
            value=default_ambil_d, key=f"amb_d_{prefix_app}_{k_pilih}_{b_pilih}_{lot_pilih}"
        )
        
    pcs_diambil_input = (ambil_p * total_pcs_pallet) + (ambil_d * pcs_per_pecahan)
    pcs_sisa_hitung = max(0, total_pcs_init - pcs_diambil_input)
    sisa_p_hitung = pcs_sisa_hitung // total_pcs_pallet if total_pcs_pallet > 0 else max(0, init_p - ambil_p)
    sisa_pcs_rem = pcs_sisa_hitung % total_pcs_pallet if total_pcs_pallet > 0 else 0
    sisa_d_hitung = sisa_pcs_rem // pcs_per_pecahan if pcs_per_pecahan > 0 else max(0, init_d - ambil_d)
    
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
    
    with st.expander("🛠️ Tidak aktual? Koreksi disini"):
        st.caption("Ubah angka di bawah jika setelah diambil ternyata sisa fisik di lorong tidak sama dengan hitungan di atas:")
        max_sisa_d_kor = max(init_d, pecahan_per_pallet - 1) if pecahan_per_pallet > 1 else max(init_d, 100)
        c_kor1, c_kor2 = st.columns(2)
        with c_kor1:
            sisa_pallet_final = st.number_input(
                "Sisa Pallet Utuh Aktual", min_value=0, max_value=init_p, step=1,
                value=int(sisa_p_hitung), key=f"kor_p_{prefix_app}_{k_pilih}_{b_pilih}_{lot_pilih}_{ambil_p}_{ambil_d}"
            )
        with c_kor2:
            sisa_dus_final = st.number_input(
                f"Sisa Pecahan Aktual ({satuan})", min_value=0, max_value=max_sisa_d_kor, step=1,
                value=int(sisa_d_hitung), key=f"kor_d_{prefix_app}_{k_pilih}_{b_pilih}_{lot_pilih}_{ambil_p}_{ambil_d}"
            )
            
    total_pcs_final = (sisa_pallet_final * total_pcs_pallet) + (sisa_dus_final * pcs_per_pecahan)
    selisih_pcs_total = total_pcs_init - total_pcs_final
    keluar_pallet_eq = selisih_pcs_total // total_pcs_pallet if total_pcs_pallet > 0 else 0
    sisa_pcs_temp = selisih_pcs_total % total_pcs_pallet if total_pcs_pallet > 0 else selisih_pcs_total
    keluar_dus_eq = sisa_pcs_temp // pcs_per_pecahan if pcs_per_pecahan > 0 else 0
    
    ada_koreksi_fisik = (sisa_pallet_final != sisa_p_hitung) or (sisa_dus_final != sisa_d_hitung)
    if ada_koreksi_fisik and selisih_pcs_total > 0:
        st.warning(
            f"⚠️ **Koreksi Sisa Aktif:** Tercatat keluar **{selisih_pcs_total:,} Pcs** "
            f"({keluar_pallet_eq} Pallet, {keluar_dus_eq} {satuan}), sisa akhir rak menjadi **{sisa_pallet_final} Pallet, {sisa_dus_final} {satuan}**."
        )
    elif pcs_diambil_input > total_pcs_init:
        st.error("❌ Jumlah diambil melebihi stok yang tersedia di rak!")
    elif selisih_pcs_total <= 0:
        st.warning("⚠️ Masukkan minimal 1 Pallet atau Pecahan yang diambil.")
        
    catatan_operator = st.text_input("📝 CATATAN (Opsional)", placeholder="Cth: Suplai Mesin 3")
    st.write("")
    
    if selisih_pcs_total > 0 and pcs_diambil_input <= total_pcs_init:
        if st.button(label_tombol_eksekusi, type="primary", use_container_width=True):
            with st.spinner("Menyimpan transaksi ke database..."):
                doc_ref = db.collection(NAMA_DATABASE).document(f"Kolom_{k_pilih}")
                transaction = db.transaction()
                
                try:
                    data_sebelum_update = proses_pengambilan_stok(
                        transaction, doc_ref, k_pilih, b_pilih, lot_pilih,
                        st.session_state.pilih_part, st.session_state.item,
                        init_p, init_d, sisa_pallet_final, sisa_dus_final, satuan,
                        catatan_rak_lama
                    )
                    
                    teks_keterangan = (
                        f"Op: {st.session_state.operator} | Ambil: {ambil_p}P {ambil_d}{satuan[0]} | "
                        f"Awal: {init_p}P {init_d}{satuan[0]} -> Sisa: {sisa_pallet_final}P {sisa_dus_final}{satuan[0]}"
                    )
                    if ada_koreksi_fisik:
                        teks_keterangan += f" [Koreksi Fisik dari {sisa_p_hitung}P {sisa_d_hitung}{satuan[0]}]"
                    if catatan_operator.strip():
                        teks_keterangan += f" | Catatan: {catatan_operator.strip()}"
                        
                    catat_log_mutasi(
                        jenis_log, k_pilih, b_pilih, st.session_state.item,
                        st.session_state.pilih_part, lot_pilih,
                        keluar_pallet_eq, keluar_dus_eq, selisih_pcs_total, teks_keterangan
                    )
                    
                    pesan_tele = (
                        f"📤 <b>{judul_tele}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📦 <b>Item:</b> {escape_html(st.session_state.item)}\n"
                        f"📍 <b>Rak:</b> {k_pilih}-{b_pilih} | <b>Lot:</b> <code>{escape_html(lot_pilih)}</code>\n"
                        f"📊 <b>Keluar:</b> <b>{selisih_pcs_total:,} Pcs</b> ({keluar_pallet_eq} Pallet, {keluar_dus_eq} {satuan})\n"
                        f"📉 <b>Stok Rak:</b> {init_p}P {init_d}{satuan} ➔ <b>{sisa_pallet_final}P {sisa_dus_final}{satuan}</b>\n"
                        f"👤 <b>Operator:</b> {escape_html(st.session_state.operator)}\n"
                    )
                    if ada_koreksi_fisik:
                        pesan_tele += f"⚠️ <i>Koreksi sisa fisik (hitungan awal: {sisa_p_hitung}P {sisa_d_hitung}{satuan})</i>\n"
                    if catatan_operator.strip():
                        pesan_tele += f"📝 <b>Catatan:</b> <i>{escape_html(catatan_operator.strip())}</i>\n"
                    kirim_notif_telegram(pesan_tele)
                    
                    get_all_racking_data.clear()
                    msg_sukses = f"Keluar {selisih_pcs_total:,} Pcs ({keluar_pallet_eq}P {keluar_dus_eq}{satuan}) dari Rak {k_pilih}-{b_pilih}. Sisa: {sisa_pallet_final}P {sisa_dus_final}{satuan}."
                    simpan_undo_firestore(prefix_app, st.session_state.operator, {k_pilih: data_sebelum_update}, msg_sukses, mode_label=mode_undo_label)
                    
                    st.toast(msg_sukses, icon="✅")
                    keys_clear = ['kategori', 'gramasi', 'item', 'pilih_part', 'mode_transaksi'] + list(reset_keys.values())
                    for k in set(keys_clear):
                        if k in st.session_state:
                            st.session_state[k] = None
                    st.session_state.slide = 1
                    st.rerun()
                    
                except ValueError as err:
                    st.error(f"❌ Transaksi Dibatalkan: {err}")
                except Exception as err:
                    st.error(f"❌ Kesalahan Sistem: {err}")
