import streamlit as st
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import io
import re

# ==========================================
# 1. NASTAVENIE STRÁNKY (MUSÍ BYŤ ÚPLNE PRVÉ)
# ==========================================
st.set_page_config(page_title="Pohoda XML Transform", page_icon="📝", layout="wide")

# ==========================================
# KONFIGURÁCIA A MENNÉ PRIESTORY POHODY
# ==========================================
MY_ICO = "57039607"

NS = {
    'dat': 'http://www.stormware.cz/schema/version_2/data.xsd',
    'inv': 'http://www.stormware.cz/schema/version_2/invoice.xsd',
    'typ': 'http://www.stormware.cz/schema/version_2/type.xsd'
}

# Registrácia priestorov pre krajší XML výstup
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

# Bezpečná funkcia na generovanie XML tagov
def tag(prefix, name):
    return "{" + NS[prefix] + "}" + name

# Bezpečný čas pre Slovensko (bez rizika pádu knižnice zoneinfo)
def get_sk_time():
    return datetime.utcnow() + timedelta(hours=2)

# Inicializácia pamäte Streamlitu
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
    now = get_sk_time()
    month_map = {1:'JAN', 2:'FEB', 3:'MAR', 4:'APR', 5:'MAY', 6:'JUN', 
                 7:'JUL', 8:'AUG', 9:'SEP', 10:'OCT', 11:'NOV', 12:'DEC'}
    
    pack_id = f"{rada}_{now.day:02d}_{month_map[now.month]}_{now.year}_{now.hour:02d}_{now.minute:02d}"

    try:
        tree = ET.parse(file_bytes)
        root = tree.getroot()
    except ET.ParseError:
        return None, ["Chyba parsovania XML. Skontrolujte súbor."], 0, pack_id, ""

    new_root = ET.Element(tag('dat', 'dataPack'), {
        'version': '2.0', 
        'id': pack_id, 
        'ico': MY_ICO, 
        'application': 'import', 
        'note': 'import'
    })

    invalid_msgs = []
    processed_count = 0
    first_inv_suffix = None
    last_inv_suffix = None

    for i, item in enumerate(root.findall('dat:dataPackItem', NS), 1):
        old_invoice = item.find('inv:invoice', NS)
        if old_invoice is None: 
            continue
            
        old_header = old_invoice.find('inv:invoiceHeader', NS)
        if old_header is None: 
            continue
        
        # 1. Číslo faktúry
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
                
                if first_inv_suffix is None: 
                    first_inv_suffix = current_suffix
                last_inv_suffix = current_suffix
            else:
                inv_number = orig_num
        else:
            invalid_msgs.append(f"Kritická chyba: Faktúra v poradí {i} nemá číslo dokladu.")

        # 2. Kontrola prázdnych kritických polí v hlavičke
        sym_var_el = old_header.find('inv:symVar', NS)
        if sym_var_el is None or not sym_var_el.text or not sym_var_el.text.strip():
            invalid_msgs.append(f"Upozornenie FA {inv_number}: Chýba Variabilný symbol.")

        # 3. Tvorba položky dataPackItem
        item_id = f"{pack_id} ({i:03d})"
        new_item = ET.SubElement(new_root, tag('dat', 'dataPackItem'), {'version': '2.0', 'id': item_id})
        new_invoice = ET.SubElement(new_item, tag('inv', 'invoice'), {'version': '2.0', 'xmlns:inv': NS['inv']})
        new_header = ET.SubElement(new_invoice, tag('inv', 'invoiceHeader'), {'xmlns:typ': NS['typ']})
        
        ET.SubElement(new_header, tag('inv', 'invoiceType')).text = 'issuedInvoice'
        new_header.append(old_header.find('inv:number', NS))
        new_header.append(old_header.find('inv:symVar', NS))
        
        date_val_el = old_header.find('inv:date', NS)
        date_val = date_val_el.text if date_val_el is not None else ""
        
        try:
            date_obj = datetime.strptime(date_val, "%Y-%m-%d")
            date_due_val = (date_obj + timedelta(days=due_days)).strftime("%Y-%m-%d")
        except:
            date_due_val = date_val
            invalid_msgs.append(f"Upozornenie FA {inv_number}: Neplatný formát dátumu ({date_val}).")
            
        ET.SubElement(new_header, tag('inv', 'date')).text = date_val
        ET.SubElement(new_header, tag('inv', 'dateTax')).text = date_val
        ET.SubElement(new_header, tag('inv', 'dateAccounting')).text = date_val
        ET.SubElement(new_header, tag('inv', 'dateDue')).text = date_due_val

        # Účtovanie
        acc = ET.SubElement(new_header, tag('inv', 'accounting'))
        cvat = ET.SubElement(new_header, tag('inv', 'classificationVAT'))
        
        if rada == 'VFB':
            ET.SubElement(acc, tag('typ', 'ids')).text = 'pred.tovaru'
            ET.SubElement(cvat, tag('typ', 'ids')).text = 'UN'
        else:
            ET.SubElement(acc, tag('typ', 'ids')).text = 'pred.tov.DE'
            ET.SubElement(cvat, tag('typ', 'ids')).text = 'UD'
            
        ET.SubElement(cvat, tag('typ', 'classificationVATType')).text = 'nonSubsume'

        ckv = ET.SubElement(new_header, tag('inv', 'classificationKVDPH'))
        ET.SubElement(ckv, tag('typ', 'ids')).text = 'KN'

        text_v = 'Tržby z predaja tovaru' if rada == 'VFB' else 'Predaj tovaru - Nemecko'
        ET.SubElement(new_header, tag('inv', 'text')).text = text_v

        # 4. Partner
        partner = old_header.find('.//typ:address', NS)
        if partner is not None:
            has_ico_real = False
            missing_addr_fields = []
            
            # Kontrola bežnej adresy (Opravené pre Krajinu / typ:ids)
            for t in ['name', 'city', 'street', 'zip', 'country']:
                e = partner.find(f'typ:{t}', NS)
                if e is None:
                    missing_addr_fields.append(t)
                else:
                    if t == 'country':
                        # Krajina je zabalená v <typ:ids>
                        ids_e = e.find('typ:ids', NS)
                        if ids_e is None or not ids_e.text or not ids_e.text.strip():
                            missing_addr_fields.append(t)
                    else:
                        # Ostatné polia majú text priamo v sebe
                        if not e.text or not e.text.strip():
                            missing_addr_fields.append(t)

            # Očista firemných polí
            for t in ['company', 'ico', 'dic', 'icDph']:
                e = partner.find(f'typ:{t}', NS)
                if e is not None:
                    txt = e.text.strip() if e.text else ""
                    if not txt:
                        partner.remove(e)
                    elif t == 'ico':
                        has_ico_real = True
            
            # Upozornenia
            comp_e = partner.find('typ:company', NS)
            ico_e = partner.find('typ:ico', NS)
            if comp_e is not None and ico_e is None:
                invalid_msgs.append(f"FA {inv_number}: Firma '{comp_e.text}' nemá IČO.")

            if missing_addr_fields:
                transl = {'name': 'Meno', 'city': 'Mesto', 'street': 'Ulica', 'zip': 'PSČ', 'country': 'Krajina'}
                miss_sk = [transl.get(x, x) for x in missing_addr_fields]
                invalid_msgs.append(f"Upozornenie FA {inv_number}: Neúplná adresa (chýba: {', '.join(miss_sk)}).")

            partner.set('linkToAddress', 'true' if has_ico_real else 'false')
            ET.SubElement(new_header, tag('inv', 'partnerIdentity')).append(partner)

        # Identita
        my_id = ET.SubElement(ET.SubElement(new_header, tag('inv', 'myIdentity')), tag('typ', 'address'))
        ET.SubElement(my_id, tag('typ', 'company')).text = 'EPPO BRANDS s. r. o.'
        ET.SubElement(my_id, tag('typ', 'city')).text = 'Zvolen'
        ET.SubElement(my_id, tag('typ', 'street')).text = 'Tulská'
        ET.SubElement(my_id, tag('typ', 'number')).text = '9386/6B'
        ET.SubElement(my_id, tag('typ', 'zip')).text = '960 01'
        ET.SubElement(my_id, tag('typ', 'ico')).text = '57039607'
        ET.SubElement(my_id, tag('typ', 'dic')).text = '2122546481'
        ET.SubElement(my_id, tag('typ', 'icDph')).text = 'SK2122546481'

        # Banka a platba
        pt_n = ET.SubElement(new_header, tag('inv', 'paymentType'))
        ET.SubElement(pt_n, tag('typ', 'ids')).text = payment_type
        ET.SubElement(pt_n, tag('typ', 'paymentType')).text = 'draft'
        
        bnk = ET.SubElement(new_header, tag('inv', 'account'))
        ET.SubElement(bnk, tag('typ', 'ids')).text = bank_ids
        ET.SubElement(bnk, tag('typ', 'accountNo')).text = bank_acc
        ET.SubElement(bnk, tag('typ', 'bankCode')).text = bank_code
        
        ET.SubElement(new_header, tag('inv', 'symConst')).text = sym_const

        # Likvidácia
        old_sum = old_invoice.find('inv:invoiceSummary', NS)
        h_sum = 0.0
        f_sum = 0.0
        is_f = False
        c_ids = "EUR"
        c_rate = 1.0

        if old_sum is not None:
            fc_e = old_sum.find('inv:foreignCurrency', NS)
            if fc_e is not None:
                is_f = True
                c_ids_el = fc_e.find('typ:currency/typ:ids', NS)
                c_ids = c_ids_el.text if c_ids_el is not None else "EUR"
                
                c_rate_el = fc_e.find('typ:rate', NS)
                c_rate = float(c_rate_el.text) if c_rate_el is not None else 1.0
                
                f_sum_elem = old_invoice.find('.//inv:foreignCurrency/typ:priceSum', NS)
                f_sum = float(f_sum_elem.text) if f_sum_elem is not None else 0.0
                h_sum = round(f_sum * c_rate, 2)
            else:
                h_sum_el = old_invoice.find('.//inv:homeCurrency/typ:priceSum', NS)
                h_sum = float(h_sum_el.text) if h_sum_el is not None else 0.0

        liq = ET.SubElement(new_header, tag('inv', 'liquidation'))
        ET.SubElement(liq, tag('typ', 'amountHome')).text = f"{h_sum:.2f}"
        if is_f: 
            ET.SubElement(liq, tag('typ', 'amountForeign')).text = f"{f_sum:.2f}"

        ET.SubElement(new_header, tag('inv', 'lock2')).text = 'false'
        ET.SubElement(new_header, tag('inv', 'markRecord')).text = 'false'

        # Položky VFD
        if rada == 'VFD':
            det = ET.SubElement(new_invoice, tag('inv', 'invoiceDetail'))
            vat_p = round(f_sum - (f_sum / 1.19), 2)
            base_p = round(f_sum - vat_p, 2)
            
            vfd_items = [
                ('Tovar DE', base_p, 'pred.tov.DE'), 
                ('DPH DE', vat_p, 'DPH.tov.DE')
            ]
            
            for text_val, val, a_id in vfd_items:
                it = ET.SubElement(det, tag('inv', 'invoiceItem'))
                ET.SubElement(it, tag('inv', 'text')).text = text_val
                ET.SubElement(it, tag('inv', 'quantity')).text = '1'
                ET.SubElement(it, tag('inv', 'rateVAT')).text = 'none'
                
                if not is_f:
                    curr_tag = tag('inv', 'homeCurrency')
                else:
                    curr_tag = tag('inv', 'foreignCurrency')
                    
                curr = ET.SubElement(it, curr_tag)
                ET.SubElement(curr, tag('typ', 'unitPrice')).text
