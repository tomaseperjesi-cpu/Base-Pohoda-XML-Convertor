import streamlit as st
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import io
import re
import os

# ==========================================
# 1. NASTAVENIE STRÁNKY (MUSÍ BYŤ PRVÉ)
# ==========================================
st.set_page_config(page_title="Pohoda XML Transform", page_icon="📝", layout="wide")

# ==========================================
# KONFIGURÁCIA A MENNÉ PRIESTORY POHODY
# ==========================================
MY_ICO = "57039607"
NS = {
    'dat': 'http://www.stormware.cz/schema/version_2/data.xsd',
    'inv': 'http://www.stormware.cz/schema/version_2/invoice.xsd',
    'typ': 'http://www.stormware.cz/schema/version_2/type.xsd',
    'rsp': 'http://www.stormware.cz/schema/version_2/response.xsd',
    'rdc': 'http://www.stormware.cz/schema/version_2/documentresponse.xsd',
    'ftr': 'http://www.stormware.cz/schema/version_2/filter.xsd',
    'lst': 'http://www.stormware.cz/schema/version_2/list.xsd'
}

# Registrácia priestorov pre korektné prefixy v XML
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

# Inicializácia Session State pre Streamlit
if 'transformed_xml' not in st.session_state:
    st.session_state.transformed_xml = None
if 'errors' not in st.session_state:
    st.session_state.errors = []
if 'count' not in st.session_state:
    st.session_state.count = 0
if 'out_filename' not in st.session_state:
    st.session_state.out_filename = ""
if 'vf_goods_map' not in st.session_state:
    st.session_state.vf_goods_map = {}
if 'last_file_hash' not in st.session_state:
    st.session_state.last_file_hash = None

# ==========================================
# POMOCNÉ FUNKCIE PRE RADU VF
# ==========================================
def get_invoice_list(file_bytes):
    """Vytiahne zoznam faktúr a textu z prvej položky pre interaktívny výber VF."""
    try:
        tree = ET.parse(file_bytes)
        root = tree.getroot()
        invoices = []
        for item in root.findall('dat:dataPackItem', NS):
            inv = item.find('inv:invoice', NS)
            if inv is None: continue
            header = inv.find('inv:invoiceHeader', NS)
            detail = inv.find('inv:invoiceDetail', NS)
            if header is None: continue
            
            num_el = header.find('inv:number/typ:numberRequested', NS)
            num_val = num_el.text.strip() if num_el is not None else "0"
            
            text_val = "Bez popisu položky"
            if detail is not None:
                first_item = detail.find('inv:invoiceItem', NS)
                if first_item is not None:
                    it_text = first_item.find('inv:text', NS)
                    if it_text is not None and it_text.text:
                        text_val = it_text.text.strip()
            
            invoices.append({'id': num_val, 'text': text_val})
        return invoices
    except:
        return []

# ==========================================
# HLAVNÁ TRANSFORMAČNÁ FUNKCIA
# ==========================================
def transform_xml(file_bytes, rada, due_days, bank_ids, bank_acc, bank_code, payment_type, sym_const):
    tz_sk = ZoneInfo("Europe/Bratislava")
    now = datetime.now(tz_sk)
    month_map = {1:'JAN', 2:'FEB', 3:'MAR', 4:'APR', 5:'MAY', 6:'JUN', 
                 7:'JUL', 8:'AUG', 9:'SEP', 10:'OCT', 11:'NOV', 12:'DEC'}
    
    pack_id = f"{rada}_{now.day:02d}_{month_map[now.month]}_{now.year}_{now.hour:02d}_{now.minute:02d}"

    try:
        tree = ET.parse(file_bytes)
        root = tree.getroot()
    except ET.ParseError:
        return None, ["Chyba parsovania: Súbor nie je platný XML."], 0, pack_id, ""

    new_root = ET.Element(f'{{{NS["dat"]}}}dataPack', {
        'version': '2.0', 'id': pack_id, 'ico': MY_ICO, 'application': 'import', 'note': 'import'
    })

    invalid_invoices = []
    processed_count = 0
    first_inv_suffix = None
    last_inv_suffix = None

    for i, item in enumerate(root.findall('dat:dataPackItem', NS), 1):
        old_invoice = item.find('inv:invoice', NS)
        if old_invoice is None: continue
        old_header = old_invoice.find('inv:invoiceHeader', NS)
        if old_header is None: continue

        # =========================================================
        # 1. VETVA PRE VFB a VFD (TVOJ PÔVODNÝ KÓD - NEDOTKNUTÝ)
        # =========================================================
        if rada in ['VFB', 'VFD']:
            # 1. Číslo faktúry (formátovanie na 4 cifry)
            inv_number_elem = old_header.find('inv:number/typ:numberRequested', NS)
            current_suffix = ""
            inv_number = "Neznáme"
            if inv_number_elem is not None and inv_number_elem.text:
                orig_num = inv_number_elem.text.strip()
                match = re.match(r"^(.*?)(\d+)$", orig_num)
                if match:
                    prefix_val = match.group(1)
                    num_part = match.group(2)
                    current_suffix = num_part.zfill(4)
                    inv_number = f"{prefix_val}{current_suffix}" 
                    inv_number_elem.text = inv_number 
                    if first_inv_suffix is None: first_inv_suffix = current_suffix
                    last_inv_suffix = current_suffix
                else:
                    inv_number = orig_num

            # 2. Kontrola Partnera (IČO, Amazon link, Adresa)
            partner = old_header.find('.//typ:address', NS)
            if partner is not None:
                ico_e = partner.find('typ:ico', NS)
                if ico_e is not None and ico_e.text:
                    ico_text_lower = ico_e.text.strip().lower()
                    if "http" in ico_text_lower or "www." in ico_text_lower or "amazon" in ico_text_lower:
                        partner.remove(ico_e)
                        invalid_invoices.append(f"FA {inv_number}: Odstránené neplatné IČO (internetový odkaz)")
                        ico_e = None
                
                comp = partner.find('typ:company', NS)
                if comp is not None and comp.text and comp.text.strip():
                    if ico_e is None or not ico_e.text or not ico_e.text.strip():
                        invalid_invoices.append(f"FA {inv_number} (Firma: {comp.text.strip()})")

                missing_addr = []
                for addr_f in ['name', 'city', 'street', 'zip']:
                    e = partner.find(f'typ:{addr_f}', NS)
                    if e is None or not e.text or not e.text.strip():
                        missing_addr.append(addr_f)
                if missing_addr:
                    transl = {'name': 'Meno', 'city': 'Mesto', 'street': 'Ulica', 'zip': 'PSČ'}
                    miss_sk = [transl.get(x, x) for x in missing_addr]
                    invalid_invoices.append(f"FA {inv_number}: Neúplná adresa (chýba: {', '.join(miss_sk)})")

            # 3. Tvorba položky dataPackItem
            item_id = f"{pack_id} ({i:03d})"
            new_item = ET.SubElement(new_root, f'{{{NS["dat"]}}}dataPackItem', {'version': '2.0', 'id': item_id})
            new_invoice = ET.SubElement(new_item, f'{{{NS["inv"]}}}invoice', {'version': '2.0', 'xmlns:inv': NS['inv']})
            new_header = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceHeader', {'xmlns:typ': NS['typ']})
            
            # -- Poradie elementov v hlavičke --
            ET.SubElement(new_header, f'{{{NS["inv"]}}}invoiceType').text = 'issuedInvoice'
            new_header.append(old_header.find('inv:number', NS))
            new_header.append(old_header.find('inv:symVar', NS))
            
            date_val = old_header.find('inv:date', NS).text
            try:
                date_obj = datetime.strptime(date_val, "%Y-%m-%d")
                date_due_val = (date_obj + timedelta(days=due_days)).strftime("%Y-%m-%d")
            except:
                date_due_val = date_val
                
            ET.SubElement(new_header, f'{{{NS["inv"]}}}date').text = date_val
            ET.SubElement(new_header, f'{{{NS["inv"]}}}dateTax').text = date_val
            ET.SubElement(new_header, f'{{{NS["inv"]}}}dateAccounting').text = date_val
            ET.SubElement(new_header, f'{{{NS["inv"]}}}dateDue').text = date_due_val

            # Účtovanie a DPH
            acc = ET.SubElement(new_header, f'{{{NS["inv"]}}}accounting')
            cvat = ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationVAT')
            if rada == 'VFB':
                ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tovaru'
            else:
                ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tov.DE'
                
            ET.SubElement(cvat, f'{{{NS["typ"]}}}ids').text = 'UN'
            ET.SubElement(cvat, f'{{{NS["typ"]}}}classificationVATType').text = 'nonSubsume'

            ckv = ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationKVDPH')
            ET.SubElement(ckv, f'{{{NS["typ"]}}}ids').text = 'KN'

            text_v = 'Tržby z predaja tovaru' if rada == 'VFB' else 'Predaj tovaru - Nemecko'
            ET.SubElement(new_header, f'{{{NS["inv"]}}}text').text = text_v

            # Partner identity
            if partner is not None:
                has_ico_real = False
                for t in ['company', 'ico', 'dic', 'icDph']:
                    e = partner.find(f'typ:{t}', NS)
                    if e is not None:
                        if not e.text or not e.text.strip(): partner.remove(e)
                        elif t == 'ico': has_ico_real = True
                partner.set('linkToAddress', 'true' if has_ico_real else 'false')
                ET.SubElement(new_header, f'{{{NS["inv"]}}}partnerIdentity').append(partner)

            # Identita
            my_id = ET.SubElement(ET.SubElement(new_header, f'{{{NS["inv"]}}}myIdentity'), f'{{{NS["typ"]}}}address')
            ET.SubElement(my_id, f'{{{NS["typ"]}}}company').text = 'EPPO BRANDS s. r. o.'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}city').text = 'Zvolen'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}street').text = 'Tulská'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}number').text = '9386/6B'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}zip').text = '960 01'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}ico').text = '57039607'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}dic').text = '2122546481'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}icDph').text = 'SK2122546481'

            # Banka a platba
            pt_n = ET.SubElement(new_header, f'{{{NS["inv"]}}}paymentType')
            ET.SubElement(pt_n, f'{{{NS["typ"]}}}ids').text = payment_type
            ET.SubElement(pt_n, f'{{{NS["typ"]}}}paymentType').text = 'draft'
            bnk = ET.SubElement(new_header, f'{{{NS["inv"]}}}account')
            ET.SubElement(bnk, f'{{{NS["typ"]}}}ids').text = bank_ids
            ET.SubElement(bnk, f'{{{NS["typ"]}}}accountNo').text = bank_acc
            ET.SubElement(bnk, f'{{{NS["typ"]}}}bankCode').text = bank_code
            ET.SubElement(new_header, f'{{{NS["inv"]}}}symConst').text = sym_const

            # 4. Likvidácia a Sumáre
            old_sum = old_invoice.find('inv:invoiceSummary', NS)
            h_sum, f_sum = 0.0, 0.0
            is_f = False
            c_ids, c_rate = "EUR", 1.0

            if old_sum is not None:
                fc_e = old_sum.find('inv:foreignCurrency', NS)
                if fc_e is not None:
                    is_f = True
                    c_ids = fc_e.find('typ:currency/typ:ids', NS).text
                    c_rate = float(fc_e.find('typ:rate', NS).text)
                    f_sum = float(old_invoice.find('.//inv:foreignCurrency/typ:priceSum', NS).text)
                    h_sum = round(f_sum * c_rate, 2)
                else:
                    h_sum = float(old_invoice.find('.//inv:homeCurrency/typ:priceSum', NS).text)

            liq = ET.SubElement(new_header, f'{{{NS["inv"]}}}liquidation')
            ET.SubElement(liq, f'{{{NS["typ"]}}}amountHome').text = f"{h_sum:.2f}"
            if is_f: ET.SubElement(liq, f'{{{NS["typ"]}}}amountForeign').text = f"{f_sum:.2f}"

            ET.SubElement(new_header, f'{{{NS["inv"]}}}lock2').text = 'false'
            ET.SubElement(new_header, f'{{{NS["inv"]}}}markRecord').text = 'false'

            # Položky pre VFD (Rozpad podľa nového vzoru)
            if rada == 'VFD':
                det = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceDetail')
                val_to_split = f_sum if is_f else h_sum
                f_vat_part = round(val_to_split - (val_to_split / 1.19), 2)
                f_base_part = round(val_to_split - f_vat_part, 2)

                vfd_configs = [
                    ('Predaj tovaru - Nemecko', f_base_part, 'pred.tov.DE'),
                    ('Predaj tovaru - Nemecko DPH 19%', f_vat_part, 'DPH.tov.DE')
                ]

                for t, val, acc_id in vfd_configs:
                    it = ET.SubElement(det, f'{{{NS["inv"]}}}invoiceItem')
                    ET.SubElement(it, f'{{{NS["inv"]}}}text').text = t
                    ET.SubElement(it, f'{{{NS["inv"]}}}quantity').text = '1.0'
                    ET.SubElement(it, f'{{{NS["inv"]}}}coefficient').text = '1.0'
                    ET.SubElement(it, f'{{{NS["inv"]}}}payVAT').text = 'false'
                    ET.SubElement(it, f'{{{NS["inv"]}}}rateVAT').text = 'none'
                    ET.SubElement(it, f'{{{NS["inv"]}}}discountPercentage').text = '0.0'
                    
                    curr_node_name = 'foreignCurrency' if is_f else 'homeCurrency'
                    curr = ET.SubElement(it, f'{{{NS["inv"]}}}{curr_node_name}')
                    ET.SubElement(curr, f'{{{NS["typ"]}}}unitPrice').text = f"{val:.2f}"
                    ET.SubElement(curr, f'{{{NS["typ"]}}}price').text = f"{val:.2f}"
                    ET.SubElement(curr, f'{{{NS["typ"]}}}priceVAT').text = '0'
                    ET.SubElement(curr, f'{{{NS["typ"]}}}priceSum').text = f"{val:.2f}"
                    
                    ET.SubElement(ET.SubElement(it, f'{{{NS["inv"]}}}accounting'), f'{{{NS["typ"]}}}ids').text = acc_id
                    ET.SubElement(it, f'{{{NS["inv"]}}}PDP').text = 'false'

            # 6. Detailný sumár faktúry (podľa vzoru)
            summary_attrs = {
                'xmlns:rsp': NS['rsp'], 'xmlns:rdc': NS['rdc'], 'xmlns:typ': NS['typ'],
                'xmlns:ftr': NS['ftr'], 'xmlns:lst': NS['lst']
            }
            ns_sum = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceSummary', summary_attrs)
            ET.SubElement(ns_sum, f'{{{NS["inv"]}}}roundingDocument').text = 'none'
            ET.SubElement(ns_sum, f'{{{NS["inv"]}}}roundingVAT').text = 'none'
            
            hc = ET.SubElement(ns_sum, f'{{{NS["inv"]}}}homeCurrency')
            ET.SubElement(hc, f'{{{NS["typ"]}}}priceNone').text = f"{h_sum:.2f}"
            for tag in ['priceLow', 'priceLowVAT', 'priceLowSum', 'priceHigh', 'priceHighVAT', 'priceHighSum', 'price3', 'price3VAT', 'price3Sum']:
                ET.SubElement(hc, f'{{{NS["typ"]}}}{tag}').text = '0'
            ET.SubElement(ET.SubElement(hc, f'{{{NS["typ"]}}}round'), f'{{{NS["typ"]}}}priceRound').text = '0'

            if is_f:
                fc = ET.SubElement(ns_sum, f'{{{NS["inv"]}}}foreignCurrency')
                tc = ET.SubElement(fc, f'{{{NS["typ"]}}}currency')
                ET.SubElement(tc, f'{{{NS["typ"]}}}ids').text = c_ids
                ET.SubElement(fc, f'{{{NS["typ"]}}}rate').text = str(c_rate)
                ET.SubElement(fc, f'{{{NS["typ"]}}}amount').text = '1'
                ET.SubElement(fc, f'{{{NS["typ"]}}}priceNone').text = f"{f_sum:.2f}"
                for tag in ['priceLow', 'priceLowVAT', 'priceLowSum', 'priceHigh', 'priceHighVAT', 'priceHighSum', 'price3', 'price3VAT', 'price3Sum']:
                    ET.SubElement(fc, f'{{{NS["typ"]}}}{tag}').text = '0'
                ET.SubElement(fc, f'{{{NS["typ"]}}}priceSum').text = f"{f_sum:.2f}"
                ET.SubElement(ET.SubElement(fc, f'{{{NS["typ"]}}}round'), f'{{{NS["typ"]}}}priceRound').text = '0'
            
            processed_count += 1


        # =========================================================
        # 2. VETVA PRE VF (NOVÁ ODDELENÁ LOGIKA PRE BITFAKTURU)
        # =========================================================
        elif rada == 'VF':
            inv_number_elem = old_header.find('inv:number/typ:numberRequested', NS)
            orig_num = inv_number_elem.text.strip() if inv_number_elem is not None and inv_number_elem.text else "0"
            
            # Formátovanie na 3 cifry
            match = re.search(r"(\d+)$", orig_num)
            if match:
                num_part = match.group(1)
                suffix = num_part.zfill(3)
                inv_number = f"{orig_num[:match.start()]}{suffix}"
                if first_inv_suffix is None: first_inv_suffix = suffix
                last_inv_suffix = suffix
            else:
                inv_number = orig_num

            # Partner
            partner = old_header.find('.//typ:address', NS)
            country_code = "SK"
            if partner is not None:
                c_el = partner.find('typ:country/typ:ids', NS)
                if c_el is not None and c_el.text: country_code = c_el.text.strip().upper()
                
                ico_e = partner.find('typ:ico', NS)
                if ico_e is not None and ico_e.text:
                    if any(x in ico_e.text.lower() for x in ["http", "www.", "amazon"]):
                        partner.remove(ico_e)
                        invalid_invoices.append(f"FA {inv_number}: Odstránené neplatné IČO (link)")
                        ico_e = None
                
                comp = partner.find('typ:company', NS)
                if comp is not None and comp.text and comp.text.strip():
                    if ico_e is None or not ico_e.text or not ico_e.text.strip():
                        invalid_invoices.append(f"FA {inv_number} (Firma: {comp.text.strip()})")

                missing_addr = []
                for addr_f in ['name', 'city', 'street', 'zip']:
                    e = partner.find(f'typ:{addr_f}', NS)
                    if e is None or not e.text or not e.text.strip():
                        missing_addr.append(addr_f)
                if missing_addr:
                    transl = {'name': 'Meno', 'city': 'Mesto', 'street': 'Ulica', 'zip': 'PSČ'}
                    miss_sk = [transl.get(x, x) for x in missing_addr]
                    invalid_invoices.append(f"FA {inv_number}: Neúplná adresa (chýba {', '.join(miss_sk)})")

            item_id = f"{pack_id} ({i:03d})"
            new_item = ET.SubElement(new_root, f'{{{NS["dat"]}}}dataPackItem', {'version': '2.0', 'id': item_id})
            new_invoice = ET.SubElement(new_item, f'{{{NS["inv"]}}}invoice', {'version': '2.0', 'xmlns:inv': NS['inv']})
            new_header = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceHeader', {'xmlns:typ': NS['typ']})
            
            ET.SubElement(new_header, f'{{{NS["inv"]}}}invoiceType').text = 'issuedInvoice'
            new_num_node = ET.SubElement(new_header, f'{{{NS["inv"]}}}number')
            ET.SubElement(new_num_node, f'{{{NS["typ"]}}}numberRequested').text = inv_number
            
            old_sym = old_header.find('inv:symVar', NS)
            ET.SubElement(new_header, f'{{{NS["inv"]}}}symVar').text = old_sym.text if old_sym is not None and old_sym.text else inv_number
            
            date_val_el = old_header.find('inv:date', NS)
            date_val = date_val_el.text if date_val_el is not None and date_val_el.text else ""
            try:
                date_due_val = (datetime.strptime(date_val, "%Y-%m-%d") + timedelta(days=due_days)).strftime("%Y-%m-%d")
            except:
                date_due_val = date_val
                
            for d in ['date', 'dateTax', 'dateAccounting']:
                ET.SubElement(new_header, f'{{{NS["inv"]}}}{d}').text = date_val
            ET.SubElement(new_header, f'{{{NS["inv"]}}}dateDue').text = date_due_val

            # Účtovanie VF (Zaškrtávanie služba/tovar)
            acc = ET.SubElement(new_header, f'{{{NS["inv"]}}}accounting')
            cvat = ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationVAT')
            
            is_goods = st.session_state.vf_goods_map.get(orig_num, False)
            ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tovaru' if is_goods else 'pred.služ'
            ET.SubElement(cvat, f'{{{NS["typ"]}}}ids').text = 'UN' if country_code == 'SK' else 'UD'
            
            ET.SubElement(cvat, f'{{{NS["typ"]}}}classificationVATType').text = 'nonSubsume'
            ET.SubElement(ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationKVDPH'), f'{{{NS["typ"]}}}ids').text = 'KN'

            old_txt = old_header.find('inv:text', NS)
            ET.SubElement(new_header, f'{{{NS["inv"]}}}text').text = old_txt.text if old_txt is not None and old_txt.text else "Faktúra"

            if partner is not None:
                has_ico = False
                for t in ['company', 'ico', 'dic', 'icDph']:
                    e = partner.find(f'typ:{t}', NS)
                    if e is not None:
                        if not e.text or not e.text.strip(): partner.remove(e)
                        elif t == 'ico': has_ico = True
                partner.set('linkToAddress', 'true' if has_ico else 'false')
                ET.SubElement(new_header, f'{{{NS["inv"]}}}partnerIdentity').append(partner)

            # Moja identita
            my_id = ET.SubElement(ET.SubElement(new_header, f'{{{NS["inv"]}}}myIdentity'), f'{{{NS["typ"]}}}address')
            ET.SubElement(my_id, f'{{{NS["typ"]}}}company').text = 'EPPO BRANDS s. r. o.'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}city').text = 'Zvolen'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}street').text = 'Tulská'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}number').text = '9386/6B'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}zip').text = '960 01'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}ico').text = '57039607'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}dic').text = '2122546481'
            ET.SubElement(my_id, f'{{{NS["typ"]}}}icDph').text = 'SK2122546481'

            pt_n = ET.SubElement(new_header, f'{{{NS["inv"]}}}paymentType')
            ET.SubElement(pt_n, f'{{{NS["typ"]}}}ids').text = payment_type
            ET.SubElement(pt_n, f'{{{NS["typ"]}}}paymentType').text = 'draft'
            bnk = ET.SubElement(new_header, f'{{{NS["inv"]}}}account')
            ET.SubElement(bnk, f'{{{NS["typ"]}}}ids').text = bank_ids
            ET.SubElement(bnk, f'{{{NS["typ"]}}}accountNo').text = bank_acc
            ET.SubElement(bnk, f'{{{NS["typ"]}}}bankCode').text = bank_code
            ET.SubElement(new_header, f'{{{NS["inv"]}}}symConst').text = sym_const

            # Likvidácia (Bezpečné čítanie pre Bitfakturu)
            old_sum = old_invoice.find('inv:invoiceSummary', NS)
            h_sum, f_sum = 0.0, 0.0
            is_f = False
            c_ids, c_rate = "EUR", 1.0

            if old_sum is not None:
                fc_e = old_sum.find('inv:foreignCurrency', NS)
                if fc_e is not None:
                    is_f = True
                    c_ids_el = fc_e.find('typ:currency/typ:ids', NS)
                    c_ids = c_ids_el.text if c_ids_el is not None and c_ids_el.text else "EUR"
                    
                    c_rate_el = fc_e.find('typ:rate', NS)
                    try:
                        c_rate = float(c_rate_el.text) if c_rate_el is not None and c_rate_el.text else 1.0
                    except: c_rate = 1.0
                    
                    ps_val = fc_e.find('typ:priceSum', NS)
                    if ps_val is None: ps_val = fc_e.find('typ:priceNone', NS)
                    try:
                        f_sum = float(ps_val.text) if ps_val is not None and ps_val.text else 0.0
                    except: f_sum = 0.0
                    
                    h_sum = round(f_sum * c_rate, 2)
                else:
                    hc_e = old_sum.find('inv:homeCurrency', NS)
                    ps_val = hc_e.find('typ:priceSum', NS) if hc_e is not None else None
                    if ps_val is None and hc_e is not None: ps_val = hc_e.find('typ:priceNone', NS)
                    try:
                        h_sum = float(ps_val.text) if ps_val is not None and ps_val.text else 0.0
                    except: h_sum = 0.0

            liq = ET.SubElement(new_header, f'{{{NS["inv"]}}}liquidation')
            ET.SubElement(liq, f'{{{NS["typ"]}}}amountHome').text = f"{h_sum:.2f}"
            if is_f: ET.SubElement(liq, f'{{{NS["typ"]}}}amountForeign').text = f"{f_sum:.2f}"

            ET.SubElement(new_header, f'{{{NS["inv"]}}}lock2').text = 'false'
            ET.SubElement(new_header, f'{{{NS["inv"]}}}markRecord').text = 'false'

            # Položky VF
            old_detail = old_invoice.find('inv:invoiceDetail', NS)
            new_detail = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceDetail')
            if old_detail is not None:
                for old_it in old_detail.findall('inv:invoiceItem', NS):
                    new_it = ET.SubElement(new_detail, f'{{{NS["inv"]}}}invoiceItem')
                    for tn in ['text', 'quantity', 'unit', 'payVAT', 'rateVAT']:
                        v = old_it.find(f'inv:{tn}', NS)
                        if v is not None: ET.SubElement(new_it, f'{{{NS["inv"]}}}{tn}').text = v.text
                    
                    ctype = 'foreignCurrency' if is_f else 'homeCurrency'
                    oc = old_it.find(f'inv:{ctype}', NS)
                    if oc is not None:
                        nc = ET.SubElement(new_it, f'{{{NS["inv"]}}}{ctype}')
                        for pt in ['unitPrice', 'price', 'priceVAT', 'priceSum']:
                            pv = oc.find(f'typ:{pt}', NS)
                            if pv is not None: ET.SubElement(nc, f'{{{NS["typ"]}}}{pt}').text = pv.text
                    
                    ET.SubElement(ET.SubElement(new_it, f'{{{NS["inv"]}}}accounting'), f'{{{NS["typ"]}}}ids').text = 'pred.tovaru' if is_goods else 'pred.služ'

            # Sumár VF
            summary_attrs = {'xmlns:rsp': NS['rsp'], 'xmlns:rdc': NS['rdc'], 'xmlns:typ': NS['typ'], 'xmlns:ftr': NS['ftr'], 'xmlns:lst': NS['lst']}
            ns_sum = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceSummary', summary_attrs)
            ET.SubElement(ns_sum, f'{{{NS["inv"]}}}roundingDocument').text = 'none'
            ET.SubElement(ns_sum, f'{{{NS["inv"]}}}roundingVAT').text = 'none'
            
            hc = ET.SubElement(ns_sum, f'{{{NS["inv"]}}}homeCurrency')
            ET.SubElement(hc, f'{{{NS["typ"]}}}priceNone').text = f"{h_sum:.2f}"
            for tag in ['priceLow', 'priceLowVAT', 'priceLowSum', 'priceHigh', 'priceHighVAT', 'priceHighSum', 'price3', 'price3VAT', 'price3Sum']:
                ET.SubElement(hc, f'{{{NS["typ"]}}}{tag}').text = '0'
            ET.SubElement(ET.SubElement(hc, f'{{{NS["typ"]}}}round'), f'{{{NS["typ"]}}}priceRound').text = '0'

            if is_f:
                fc = ET.SubElement(ns_sum, f'{{{NS["inv"]}}}foreignCurrency')
                tc = ET.SubElement(fc, f'{{{NS["typ"]}}}currency')
                ET.SubElement(tc, f'{{{NS["typ"]}}}ids').text = c_ids
                ET.SubElement(fc, f'{{{NS["typ"]}}}rate').text = str(c_rate)
                ET.SubElement(fc, f'{{{NS["typ"]}}}amount').text = '1'
                ET.SubElement(fc, f'{{{NS["typ"]}}}priceNone').text = f"{f_sum:.2f}"
                for tag in ['priceLow', 'priceLowVAT', 'priceLowSum', 'priceHigh', 'priceHighVAT', 'priceHighSum', 'price3', 'price3VAT', 'price3Sum']:
                    ET.SubElement(fc, f'{{{NS["typ"]}}}{tag}').text = '0'
                ET.SubElement(fc, f'{{{NS["typ"]}}}priceSum').text = f"{f_sum:.2f}"
                ET.SubElement(ET.SubElement(fc, f'{{{NS["typ"]}}}round'), f'{{{NS["typ"]}}}priceRound').text = '0'
            
            processed_count += 1

    range_txt = f"{first_inv_suffix}-{last_inv_suffix}" if first_inv_suffix else ""
    ts = f"{now.day:02d}_{month_map[now.month]}_{now.year}_{now.hour:02d}_{now.minute:02d}"
    out_name = f"{rada}{range_txt}_{ts}.xml"

    out_bio = io.BytesIO()
    ET.ElementTree(new_root).write(out_bio, encoding='Windows-1250', xml_declaration=True)
    return out_bio.getvalue(), invalid_invoices, processed_count, pack_id, out_name

# ==========================================
# STREAMLIT UI
# ==========================================
with st.sidebar:
    if os.path.exists("eppobrands.png"):
        st.image("eppobrands.png", width=150)
    
    st.header("⚙️ Nastavenia")
    rada_sel = st.radio("Dokladová rada:", ('VFB', 'VFD', 'VF'))
    st.markdown("---")
    st.header("🏦 Bankové údaje")
    b_ids = st.text_input("Skratka banky", "TB")
    b_acc = st.text_input("Číslo účtu", "2949268117")
    b_code = st.text_input("Kód banky", "1100")
    p_type = st.text_input("Forma úhrady", "Príkazom")
    s_const = st.text_input("Konštantný symbol", "0308")
    d_days = st.number_input("Splatnosť (dni)", 7)

st.title("📦 Base.com -> Pohoda XML Transformátor")

u_file = st.file_uploader("Nahrajte XML", type=["xml"])

if u_file is not None:
    # Reset zaškrtávacích políčok ak sa zmení súbor
    current_hash = hash(u_file.name)
    if st.session_state.last_file_hash != current_hash:
        st.session_state.vf_goods_map = {}
        st.session_state.last_file_hash = current_hash

    content = u_file.getvalue()
    
    # Zobrazenie klasifikácie IBA pre radu VF
    if rada_sel == 'VF':
        st.subheader("📋 Klasifikácia faktúr rady VF")
        st.info("Označte faktúry s TOVAROM. Neoznačené budú importované s predkontáciou 'pred.služ'.")
        inv_list = get_invoice_list(io.BytesIO(content))
        
        for entry in inv_list:
            st.session_state.vf_goods_map[entry['id']] = st.checkbox(
                f"FA {entry['id']} | {entry['text']}", 
                value=st.session_state.vf_goods_map.get(entry['id'], False),
                key=f"check_{entry['id']}"
            )
        st.divider()

    if st.button("🚀 Spustiť transformáciu", type="primary"):
        with st.spinner('Spracovávam...'):
            xml_data, errors, count, pack_id, out_fn = transform_xml(
                io.BytesIO(content), rada_sel, d_days, b_ids, b_acc, b_code, p_type, s_const
            )
            st.session_state.transformed_xml, st.session_state.errors = xml_data, errors
            st.session_state.count, st.session_state.out_filename = count, out_fn

if st.session_state.transformed_xml is not None:
    st.divider()
    st.success(f"✅ Spracovaných {st.session_state.count} faktúr.")
    if st.session_state.errors:
        st.warning("⚠️ Skontrolujte tieto upozornenia:")
        for err in st.session_state.errors: st.write(f"- {err}")
    st.download_button(label="💾 Stiahnuť upravené XML", data=st.session_state.transformed_xml, file_name=st.session_state.out_filename, mime="application/xml")
