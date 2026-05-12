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

# Registrácia priestorov pre krajší a čistý XML výstup
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)
ET.register_namespace('rsp', 'http://www.stormware.cz/schema/version_2/response.xsd')
ET.register_namespace('rdc', 'http://www.stormware.cz/schema/version_2/documentresponse.xsd')
ET.register_namespace('ftr', 'http://www.stormware.cz/schema/version_2/filter.xsd')
ET.register_namespace('lst', 'http://www.stormware.cz/schema/version_2/list.xsd')

# Inicializácia trvalej pamäte (Session State)
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
    # Nastavenie slovenskej časovej zóny
    tz_sk = ZoneInfo("Europe/Bratislava")
    now = datetime.now(tz_sk)
    
    month_map = {1:'JAN', 2:'FEB', 3:'MAR', 4:'APR', 5:'MAY', 6:'JUN', 
                 7:'JUL', 8:'AUG', 9:'SEP', 10:'OCT', 11:'NOV', 12:'DEC'}
    
    pack_id = f"{rada}_{now.day:02d}_{month_map[now.month]}_{now.year}_{now.hour:02d}_{now.minute:02d}"

    try:
        tree = ET.parse(file_bytes)
        root = tree.getroot()
    except ET.ParseError:
        return None, ["Chyba parsovania: Nahratý súbor nie je platný XML dokument."], 0, pack_id

    # Obálka
    new_root = ET.Element(f'{{{NS["dat"]}}}dataPack', {
        'version': '2.0', 'id': pack_id, 'ico': MY_ICO, 
        'application': 'import', 'note': 'import'
    })

    invalid_invoices = []
    processed_count = 0

    for item in root.findall('dat:dataPackItem', NS):
        old_invoice = item.find('inv:invoice', NS)
        if old_invoice is None: continue
        old_header = old_invoice.find('inv:invoiceHeader', NS)
        if old_header is None: continue
        
        # 1. Formátovanie čísla faktúry (napr. 2026VFB2 -> 2026VFB0002)
        inv_number_elem = old_header.find('inv:number/typ:numberRequested', NS)
        inv_number = "Neznáme"
        if inv_number_elem is not None and inv_number_elem.text:
            orig_num = inv_number_elem.text.strip()
            match = re.match(r"^(.*?)(\d+)$", orig_num)
            if match:
                prefix = match.group(1)
                num_part = match.group(2)
                inv_number = f"{prefix}{num_part.zfill(4)}" 
                inv_number_elem.text = inv_number 
            else:
                inv_number = orig_num

        # 2. Kontrola firmy a prítomnosti IČO (Validátor)
        partner = old_header.find('.//typ:address', NS)
        if partner is not None:
            company = partner.find('typ:company', NS)
            ico = partner.find('typ:ico', NS)
            has_company = company is not None and company.text and company.text.strip()
            has_ico = ico is not None and ico.text and ico.text.strip()
            if has_company and not has_ico:
                invalid_invoices.append(f"FA {inv_number} (Firma: {company.text})")

        # Tvorba novej faktúry
        new_item = ET.SubElement(new_root, f'{{{NS["dat"]}}}dataPackItem', {
            'version': '2.0', 'id': item.attrib.get('id', '')
        })
        new_invoice = ET.SubElement(new_item, f'{{{NS["inv"]}}}invoice', {'version': '2.0'})
        new_header = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceHeader')
        
        # -- PORADIE ELEMENTOV PODĽA POHODA XSD --
        ET.SubElement(new_header, f'{{{NS["inv"]}}}invoiceType').text = 'issuedInvoice'
        new_header.append(old_header.find('inv:number', NS))
        new_header.append(old_header.find('inv:symVar', NS))
        
        # 3. Dátumy a výpočet splatnosti (+ počet dní)
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

        # 4. Účtovanie, DPH a KV DPH
        acc = ET.SubElement(new_header, f'{{{NS["inv"]}}}accounting')
        class_vat = ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationVAT')
        
        if rada == 'VFB':
            ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tovaru'
            ET.SubElement(class_vat, f'{{{NS["typ"]}}}ids').text = 'UN'
        else:
            ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tov.DE'
            ET.SubElement(class_vat, f'{{{NS["typ"]}}}ids').text = 'UD'
            
        ET.SubElement(class_vat, f'{{{NS["typ"]}}}classificationVATType').text = 'nonSubsume'

        class_kv = ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationKVDPH')
        ET.SubElement(class_kv, f'{{{NS["typ"]}}}ids').text = 'KN'

        if rada == 'VFB':
            ET.SubElement(new_header, f'{{{NS["inv"]}}}text').text = 'Tržby z predaja tovaru'
        else:
            ET.SubElement(new_header, f'{{{NS["inv"]}}}text').text = 'Predaj tovaru - Nemecko'

        # 5. Adresa odberateľa
        old_address = old_header.find('.//typ:address', NS)
        if old_address is not None:
            for empty_tag in ['ico', 'dic', 'icDph']:
                elem = old_address.find(f'typ:{empty_tag}', NS)
                if elem is not None and (not elem.text or not elem.text.strip()):
                    old_address.remove(elem)
            ET.SubElement(new_header, f'{{{NS["inv"]}}}partnerIdentity').append(old_address)

        # 6. Naša Identita
        my_id_addr = ET.SubElement(ET.SubElement(new_header, f'{{{NS["inv"]}}}myIdentity'), f'{{{NS["typ"]}}}address')
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}company').text = 'EPPO BRANDS s. r. o.'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}city').text = 'Zvolen'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}street').text = 'Tulská'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}number').text = '9386/6B'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}zip').text = '960 01'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}ico').text = '57039607'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}dic').text = '2122546481'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}icDph').text = 'SK2122546481'

        # 7. Údaje banky a typ platby
        pt = ET.SubElement(new_header, f'{{{NS["inv"]}}}paymentType')
        ET.SubElement(pt, f'{{{NS["typ"]}}}ids').text = payment_type
        ET.SubElement(pt, f'{{{NS["typ"]}}}paymentType').text = 'draft'

        acc_node = ET.SubElement(new_header, f'{{{NS["inv"]}}}account')
        ET.SubElement(acc_node, f'{{{NS["typ"]}}}ids').text = bank_ids
        ET.SubElement(acc_node, f'{{{NS["typ"]}}}accountNo').text = bank_acc
        ET.SubElement(acc_node, f'{{{NS["typ"]}}}bankCode').text = bank_code

        ET.SubElement(new_header, f'{{{NS["inv"]}}}symConst').text = sym_const

        # 8. Systémové zámky Pohody
        ET.SubElement(new_header, f'{{{NS["inv"]}}}markRecord').text = 'true'
        ET.SubElement(new_header, f'{{{NS["inv"]}}}lock2').text = 'true'

        # --- POLOŽKY (IBA PRE VFD) ---
        if rada == 'VFD':
            old_detail = old_invoice.find('inv:invoiceDetail', NS)
            if old_detail is not None:
                new_detail = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceDetail')
                
                # Tovar DE
                item1 = ET.SubElement(new_detail, f'{{{NS["inv"]}}}invoiceItem')
                ET.SubElement(item1, f'{{{NS["inv"]}}}text').text = 'Tovar DE'
                ET.SubElement(item1, f'{{{NS["inv"]}}}rateVAT').text = 'none'
                acc1 = ET.SubElement(item1, f'{{{NS["inv"]}}}accounting')
                ET.SubElement(acc1, f'{{{NS["typ"]}}}ids').text = 'pred.tov.DE'
                
                # DPH DE
                item2 = ET.SubElement(new_detail, f'{{{NS["inv"]}}}invoiceItem')
                ET.SubElement(item2, f'{{{NS["inv"]}}}text').text = 'DPH DE'
                ET.SubElement(item2, f'{{{NS["inv"]}}}rateVAT').text = 'none'
                acc2 = ET.SubElement(item2, f'{{{NS["inv"]}}}accounting')
                ET.SubElement(acc2, f'{{{NS["typ"]}}}ids').text = 'DPH.tov.DE'

        # --- SUMÁR A PREPOČET MENY ---
        new_summary = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceSummary')
        ET.SubElement(new_summary, f'{{{NS["inv"]}}}roundingDocument').text = 'none'
        
        old_summary = old_invoice.find('inv:invoiceSummary', NS)
        if old_summary is not None:
            foreign_curr = old_summary.find('inv:foreignCurrency', NS)
            if foreign_curr is not None:
                rate = float(foreign_curr.find('typ:rate', NS).text)
                f_price = float(old_invoice.find('.//inv:foreignCurrency/typ:priceSum', NS).text)
                
                hc = ET.SubElement(new_summary, f'{{{NS["inv"]}}}homeCurrency')
                ET.SubElement(hc, f'{{{NS["typ"]}}}priceNone').text = f"{f_price * rate:.2f}"
                
                fc = ET.SubElement(new_summary, f'{{{NS["inv"]}}}foreignCurrency')
                fc.append(foreign_curr.find('typ:currency', NS))
                fc.append(foreign_curr.find('typ:rate', NS))
                fc.append(foreign_curr.find('typ:amount', NS))
                ET.SubElement(fc, f'{{{NS["typ"]}}}priceSum').text = f"{f_price:.2f}"
            else:
                h_price = old_invoice.find('.//inv:homeCurrency/typ:priceSum', NS).text
                hc = ET.SubElement(new_summary, f'{{{NS["inv"]}}}homeCurrency')
                ET.SubElement(hc, f'{{{NS["typ"]}}}priceNone').text = h_price
                
        processed_count += 1

    output_bytes = io.BytesIO()
    ET.ElementTree(new_root).write(output_bytes, encoding='Windows-1250', xml_declaration=True)
    return output_bytes.getvalue(), invalid_invoices, processed_count, pack_id

# ==========================================
# STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Pohoda XML Transform", page_icon="📝", layout="wide")
st.title("📦 Base.com -> Pohoda XML Transformátor")

with st.sidebar:
    st.header("⚙️ Základné nastavenia")
    zvolena_rada = st.radio("Dokladová rada:", ('VFB', 'VFD'))
    
    st.markdown("---")
    st.header("🏦 Údaje pre úhradu")
    bank_ids = st.text_input("Skratka banky (napr. TB)", value="TB")
    bank_acc = st.text_input("Číslo účtu", value="2949268117")
    bank_code = st.text_input("Kód banky", value="1100")
    payment_type = st.text_input("Forma úhrady", value="Príkazom")
    sym_const = st.text_input("Konštantný symbol", value="0308")
    due_days = st.number_input("Splatnosť (počet dní od vystavenia)", value=7, min_value=0)

uploaded_file = st.file_uploader("Nahrajte zdrojový XML súbor z Base.com", type=["xml"])

if uploaded_file is not None:
    if st.button("🚀 Spustiť transformáciu", type="primary"):
        with st.spinner('Spracovávam...'):
            xml_data, errors, count, pack_id = transform_xml(
                io.BytesIO(uploaded_file.getvalue()), 
                zvolena_rada, due_days, bank_ids, bank_acc, bank_code, payment_type, sym_const
            )
            
            st.session_state.transformed_xml = xml_data
            st.session_state.errors = errors
            st.session_state.count = count
            st.session_state.out_filename = f"{pack_id}.xml"

# Zobrazenie výsledkov (perzistentné)
if st.session_state.transformed_xml is not None:
    st.divider()
    st.success(f"✅ Úspešne spracovaných {st.session_state.count} faktúr.")

    if st.session_state.errors:
        st.warning("⚠️ **UPOZORNENIE: Kontrola firiem odhalila podozrivé záznamy!**")
        st.write("Tieto faktúry majú vyplnenú firmu, ale nemajú IČO (Adresa / Packstation):")
        for err in st.session_state.errors:
            st.write(f"- {err}")
    else:
        st.info("✓ Validácia firiem prebehla v poriadku.")

    st.download_button(
        label="💾 Stiahnuť upravené XML pre Pohodu",
        data=st.session_state.transformed_xml,
        file_name=st.session_state.out_filename if 'out_filename' in st.session_state else "export.xml",
        mime="application/xml"
    )
