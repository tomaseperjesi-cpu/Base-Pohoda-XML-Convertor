import streamlit as st
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import io
import re

# ==========================================
# KONFIGURÁCIA A MENNÉ PRIESTORY POHODY
# ==========================================
MY_ICO = "57039607"
NS = {
    'dat': 'http://www.stormware.cz/schema/version_2/data.xsd',
    'inv': 'http://www.stormware.cz/schema/version_2/invoice.xsd',
    'typ': 'http://www.stormware.cz/schema/version_2/type.xsd'
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

# ==========================================
# HLAVNÁ TRANSFORMAČNÁ FUNKCIA
# ==========================================
def transform_xml(file_bytes, rada, due_days, bank_ids, bank_acc, bank_code, payment_type, sym_const):
    # Korektný slovenský čas
    tz_sk = ZoneInfo("Europe/Bratislava")
    now = datetime.now(tz_sk)
    
    month_map = {1:'JAN', 2:'FEB', 3:'MAR', 4:'APR', 5:'MAY', 6:'JUN', 
                 7:'JUL', 8:'AUG', 9:'SEP', 10:'OCT', 11:'NOV', 12:'DEC'}
    
    # Interné ID balíka
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

        # 2. Kontrola firmy bez IČO
        partner = old_header.find('.//typ:address', NS)
        if partner is not None:
            comp = partner.find('typ:company', NS)
            ico_e = partner.find('typ:ico', NS)
            if comp is not None and comp.text and comp.text.strip():
                if ico_e is None or not ico_e.text or not ico_e.text.strip():
                    invalid_invoices.append(f"FA {inv_number} (Firma: {comp.text})")

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
