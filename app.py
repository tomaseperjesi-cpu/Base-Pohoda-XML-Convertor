import streamlit as st
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import io
import re

# ==========================================
# 1. NASTAVENIE STRÁNKY (MUSÍ BYŤ PRVÉ!)
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

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

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
            inv_number = "Neznáme"
            invalid_msgs.append(f"Kritická chyba: Faktúra v poradí {i} nemá číslo dokladu.")

        # 2. Kontrola prázdnych kritických polí v hlavičke
        sym_var_el = old_header.find('inv:symVar', NS)
        if sym_var_el is None or not sym_var_el.text or not sym_var_el.text.strip():
            invalid_msgs.append(f"Upozornenie FA {inv_number}: Chýba Variabilný symbol.")

        # 3. Štruktúra dataPackItem
        item_id = f"{pack_id} ({i:03d})"
        new_item = ET.SubElement(new_root, f'{{{NS["dat"]}}}dataPackItem', {'version': '2.0', 'id': item_id})
        new_invoice = ET.SubElement(new_item, f'{{{NS["inv"]}}}invoice', {'version': '2.0', 'xmlns:inv': NS['inv']})
        new_header = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceHeader', {'xmlns:typ': NS['typ']})
        
        ET.SubElement(new_header, f'{{{NS["inv"]}}}invoiceType').text = 'issuedInvoice'
        new_header.append(old_header.find('inv:number', NS))
        new_header.append(old_header.find('inv:symVar', NS))
        
        date_val = old_header.find('inv:date', NS).text
        try:
            date_obj = datetime.strptime(date_val, "%Y-%m-%d")
            date_due_val = (date_obj + timedelta(days=due_days)).strftime("%Y-%m-%d")
        except:
            date_due_val = date_val
            invalid_msgs.append(f"Upozornenie FA {inv_number}: Neplatný formát dátumu ({date_val}).")
            
        ET.SubElement(new_header, f'{{{NS["inv"]}}}date').text = date_val
        ET.SubElement(new_header, f'{{{NS["inv"]}}}dateTax').text = date_val
        ET.SubElement(new_header, f'{{{NS["inv"]}}}dateAccounting').text = date_val
        ET.SubElement(new_header, f'{{{NS["inv"]}}}dateDue').text = date_due_val

        # Účtovanie
        acc = ET.SubElement(new_header, f'{{{NS["inv"]}}}accounting')
        cvat = ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationVAT')
        
        if rada == 'VFB':
            ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tovaru'
            ET.SubElement(cvat, f'{{{NS["typ"]}}}ids').text = 'UN'
        else:
            ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tov.DE'
            ET.SubElement(cvat, f'{{{NS["typ"]}}}ids').text = 'UD'
            
        ET.SubElement(cvat, f'{{{NS["typ"]}}}classificationVATType').text = 'nonSubsume'

        ckv = ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationKVDPH')
        ET.SubElement(ckv, f'{{{NS["typ"]}}}ids').text = 'KN'

        text_v = 'Tržby z predaja tovaru' if rada == 'VFB' else 'Predaj tovaru - Nemecko'
        ET.SubElement(new_header, f'{{{NS["inv"]}}}text').text = text_v

        # 4. Partner
        partner = old_header.find('.//typ:address', NS)
        if partner is not None:
            has_ico_real = False
            missing_addr_fields = []
            
            # Kontrola bežnej adresy
            for t in ['name', 'city', 'street', 'zip', 'country']:
                e = partner.find(f'typ:{t}', NS)
                if e is None or not e.text or not e.text.strip():
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
                c_ids = fc_e.find('typ:currency/typ:ids', NS).text
                c_rate = float(fc_e.find('typ:rate', NS).text)
                
                f_sum_elem = old_invoice.find('.//inv:foreignCurrency/typ:priceSum', NS)
                f_sum = float(f_sum_elem.text) if f_sum_elem is not None else 0.0
                h_sum = round(f_sum * c_rate, 2)
            else:
                h_sum_el = old_invoice.find('.//inv:homeCurrency/typ:priceSum', NS)
                h_sum = float(h_sum_el.text) if h_sum_el is not None else 0.0

        liq = ET.SubElement(new_header, f'{{{NS["inv"]}}}liquidation')
        ET.SubElement(liq, f'{{{NS["typ"]}}}amountHome').text = f"{h_sum:.2f}"
        if is_f: 
            ET.SubElement(liq, f'{{{NS["typ"]}}}amountForeign').text = f"{f_sum:.2f}"

        ET.SubElement(new_header, f'{{{NS["inv"]}}}lock2').text = 'false'
        ET.SubElement(new_header, f'{{{NS["inv"]}}}markRecord').text = 'false'

        # Položky VFD (Zabránené dlhým riadkom)
        if rada == 'VFD':
            det = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceDetail')
            vat_p = round(f_sum - (f_sum / 1.19), 2)
            base_p = round(f_sum - vat_p, 2)
            
            vfd_items = [
                ('Tovar DE', base_p, 'pred.tov.DE'), 
                ('DPH DE', vat_p, 'DPH.tov.DE')
            ]
            
            for text_val, val, a_id in vfd_items:
                it = ET.SubElement(det, f'{{{NS["inv"]}}}invoiceItem')
                ET.SubElement(it, f'{{{NS["inv"]}}}text').text = text_val
                ET.SubElement(it, f'{{{NS["inv"]}}}quantity').text = '1'
                ET.SubElement(it, f'{{{NS["inv"]}}}rateVAT').text = 'none'
                
                if not is_f:
                    curr_tag = f'{{{NS["inv"]}}}homeCurrency'
                else:
                    curr_tag = f'{{{NS["inv"]}}}foreignCurrency'
                    
                curr = ET.SubElement(it, curr_tag)
                ET.SubElement(curr, f'{{{NS["typ"]}}}unitPrice').text = f"{val:.2f}"
                ET.SubElement(curr, f'{{{NS["typ"]}}}price').text = f"{val:.2f}"
                ET.SubElement(curr, f'{{{NS["typ"]}}}priceSum').text = f"{val:.2f}"
                
                acc_node = ET.SubElement(it, f'{{{NS["inv"]}}}accounting')
                ET.SubElement(acc_node, f'{{{NS["typ"]}}}ids').text = a_id

        # Sumár
        ns_sum = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceSummary', {'xmlns:typ': NS['typ']})
        ET.SubElement(ns_sum, f'{{{NS["inv"]}}}roundingDocument').text = 'none'
        ET.SubElement(ns_sum, f'{{{NS["inv"]}}}roundingVAT').text = 'none'
        
        hc = ET.SubElement(ns_sum, f'{{{NS["inv"]}}}homeCurrency')
        ET.SubElement(hc, f'{{{NS["typ"]}}}priceNone').text = f"{h_sum:.2f}"
        
        tags_zero = [
            'priceLow', 'priceLowVAT', 'priceLowSum', 
            'priceHigh', 'priceHighVAT', 'priceHighSum', 
            'price3', 'price3VAT', 'price3Sum'
        ]
        for tag in tags_zero:
            ET.SubElement(hc, f'{{{NS["typ"]}}}{tag}').text = '0'
            
        round_node = ET.Sub
