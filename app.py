import streamlit as st
import xml.etree.ElementTree as ET
from datetime import datetime
import io
import re

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

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

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
def transform_xml(file_bytes, rada):
    # Generovanie ID podľa vzoru: VFB_01_JAN_2026_18_38
    now = datetime.now()
    month_map = {1:'JAN', 2:'FEB', 3:'MAR', 4:'APR', 5:'MAY', 6:'JUN', 
                 7:'JUL', 8:'AUG', 9:'SEP', 10:'OCT', 11:'NOV', 12:'DEC'}
    
    # Štruktúra ID s podčiarkovníkmi medzi všetkými prvkami
    pack_id = f"{rada}_{now.day:02d}_{month_map[now.month]}_{now.year}_{now.hour:02d}_{now.minute:02d}"

    try:
        tree = ET.parse(file_bytes)
        root = tree.getroot()
    except ET.ParseError:
        return None, ["Chyba parsovania: Nahratý súbor nie je platný XML dokument."], 0, pack_id

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
        
        # 1. Formátovanie čísla faktúry na 4 cifry (napr. 2026VFB2 -> 2026VFB0002)
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

        # 2. Kontrola firmy a IČO (Validátor)
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
        for p in ['rsp', 'rdc', 'typ', 'ftr', 'lst']: new_header.set(f'xmlns:{p}', NS[p])

        ET.SubElement(new_header, f'{{{NS["inv"]}}}invoiceType').text = 'issuedInvoice'
        new_header.append(old_header.find('inv:number', NS))
        new_header.append(old_header.find('inv:symVar', NS))
        
        # 3. Dátumy (vrátane dateDue)
        date_val = old_header.find('inv:date', NS).text
        for d_tag in ['date', 'dateTax', 'dateDue', 'dateAccounting']:
            ET.SubElement(new_header, f'{{{NS["inv"]}}}{d_tag}').text = date_val

        # Účtovanie
        acc = ET.SubElement(new_header, f'{{{NS["inv"]}}}accounting')
        class_vat = ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationVAT')
        
        if rada == 'VFB':
            ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tovaru'
            ET.SubElement(class_vat, f'{{{NS["typ"]}}}ids').text = 'UN'
            ET.SubElement(new_header, f'{{{NS["inv"]}}}text').text = 'Tržby z predaja tovaru'
        else:
            ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tov.DE'
            ET.SubElement(class_vat, f'{{{NS["typ"]}}}ids').text = 'UD'
            ET.SubElement(new_header, f'{{{NS["inv"]}}}text').text = 'Predaj tovaru - Nemecko'
            
        ET.SubElement(class_vat, f'{{{NS["typ"]}}}classificationVATType').text = 'nonSubsume'

        # Partner a MyIdentity
        old_address = old_header.find('.//typ:address', NS)
        if old_address is not None:
            ET.SubElement(new_header, f'{{{NS["inv"]}}}partnerIdentity').append(old_address)

        my_id_addr = ET.SubElement(ET.SubElement(new_header, f'{{{NS["inv"]}}}myIdentity'), f'{{{NS["typ"]}}}address')
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}company').text = 'EPPO BRANDS s. r. o.'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}city').text = 'Zvolen'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}street').text = 'Tulská'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}number').text = '9386/6B'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}zip').text = '960 01'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}ico').text = '57039607'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}dic').text = '2122546481'
        ET.SubElement(my_id_addr, f'{{{NS["typ"]}}}icDph').text = 'SK2122546481'

        # Položky (iba pre VFD)
        if rada == 'VFD':
            old_detail = old_invoice.find('inv:invoiceDetail', NS)
            if old_detail is not None:
                new_detail = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceDetail')
                
                # Riadok 1: Tovar DE (Základ)
                item1 = ET.SubElement(new_detail, f'{{{NS["inv"]}}}invoiceItem')
                ET.SubElement(item1, f'{{{NS["inv"]}}}text').text = 'Tovar DE'
                ET.SubElement(item1, f'{{{NS["inv"]}}}rateVAT').text = 'none'
                acc1 = ET.SubElement(item1, f'{{{NS["inv"]}}}accounting')
                ET.SubElement(acc1, f'{{{NS["typ"]}}}ids').text = 'pred.tov.DE'
                
                # Riadok 2: DPH DE
                item2 = ET.SubElement(new_detail, f'{{{NS["inv"]}}}invoiceItem')
                ET.SubElement(item2, f'{{{NS["inv"]}}}text').text = 'DPH DE'
                ET.SubElement(item2, f'{{{NS["inv"]}}}rateVAT').text = 'none'
                acc2 = ET.SubElement(item2, f'{{{NS["inv"]}}}accounting')
                ET.SubElement(acc2, f'{{{NS["typ"]}}}ids').text = 'DPH.tov.DE'

        # Sumár a prepočet (Násobenie: Cena * Kurz)
        new_summary = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceSummary')
        for p in ['rsp', 'rdc', 'typ', 'ftr', 'lst']: new_summary.set(f'xmlns:{p}', NS[p])
        ET.SubElement(new_summary, f'{{{NS["inv"]}}}roundingDocument').text = 'none'
        
        old_summary = old_invoice.find('inv:invoiceSummary', NS)
        if old_summary is not None:
            foreign_curr = old_summary.find('inv:foreignCurrency', NS)
            if foreign_curr is not None:
                rate = float(foreign_curr.find('typ:rate', NS).text)
                f_price = float(old_invoice.find('.//inv:foreignCurrency/typ:priceSum', NS).text)
                ET.SubElement(ET.SubElement(new_summary, f'{{{NS["inv"]}}}homeCurrency'), f'{{{NS["typ"]}}}priceNone').text = f"{f_price * rate:.2f}"
                fc = ET.SubElement(new_summary, f'{{{NS["inv"]}}}foreignCurrency')
                fc.append(foreign_curr.find('typ:currency', NS))
                fc.append(foreign_curr.find('typ:rate', NS))
                fc.append(foreign_curr.find('typ:amount', NS))
                ET.SubElement(fc, f'{{{NS["typ"]}}}priceSum').text = f"{f_price:.2f}"
            else:
                h_price = old_invoice.find('.//inv:homeCurrency/typ:priceSum', NS).text
                ET.SubElement(ET.SubElement(new_summary, f'{{{NS["inv"]}}}homeCurrency'), f'{{{NS["typ"]}}}priceNone').text = h_price
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
    st.header("⚙️ Nastavenia")
    zvolena_rada = st.radio("Dokladová rada:", ('VFB', 'VFD'))

uploaded_file = st.file_uploader("Nahrajte zdrojový XML súbor", type=["xml"])

if uploaded_file is not None:
    if st.button("🚀 Spustiť transformáciu", type="primary"):
        with st.spinner('Spracovávam...'):
            xml_data, errors, count, pack_id = transform_xml(io.BytesIO(uploaded_file.getvalue()), zvolena_rada)
            
            # Uloženie do session_state
            st.session_state.transformed_xml = xml_data
            st.session_state.errors = errors
            st.session_state.count = count
            st.session_state.out_filename = f"{pack_id}.xml"

# Zobrazenie výsledkov (perzistentné vďaka session_state)
if st.session_state.transformed_xml is not None:
    st.divider()
    st.success(f"✅ Úspešne spracovaných {st.session_state.count} faktúr.")

    if st.session_state.errors:
        st.warning("⚠️ **UPOZORNENIE: Kontrola firiem odhalila podozrivé záznamy!**")
        st.write("Tieto faktúry majú vyplnenú firmu, ale nemajú IČO (Packstation/Adresa):")
        for err in st.session_state.errors:
            st.write(f"- {err}")
    else:
        st.info("✓ Validácia firiem v poriadku.")

    # Opravený blok pre sťahovanie
    st.download_button(
        label="💾 Stiahnuť upravené XML pre Pohodu",
        data=st.session_state.transformed_xml,
        file_name=st.session_state.out_filename if 'out_filename' in st.session_state else "export.xml",
        mime="application/xml"
    )
