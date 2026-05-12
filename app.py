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

# ==========================================
# HLAVNÁ TRANSFORMAČNÁ FUNKCIA
# ==========================================
def transform_xml(file_bytes, rada):
    # Generovanie unikátneho ID
    now = datetime.now()
    month_map = {1:'JAN', 2:'FEB', 3:'MAR', 4:'APR', 5:'MAY', 6:'JUN', 
                 7:'JUL', 8:'AUG', 9:'SEP', 10:'OCT', 11:'NOV', 12:'DEC'}
    pack_id = f"{rada}_{now.day:02d}_{month_map[now.month]}_{now.year}_{now.hour:02d}_{now.minute:02d}"

    try:
        tree = ET.parse(file_bytes)
        root = tree.getroot()
    except ET.ParseError:
        return None, ["Chyba parsovania: Nahratý súbor nie je platný XML dokument."], pack_id

    # Vytvorenie novej obálky
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
        
        # --- OPRAVA: Formátovanie čísla faktúry (napr. 2026VFB2 -> 2026VFB0002) ---
        inv_number_elem = old_header.find('inv:number/typ:numberRequested', NS)
        inv_number = "Neznáme"
        if inv_number_elem is not None and inv_number_elem.text:
            orig_num = inv_number_elem.text.strip()
            # Regulárny výraz oddelí textový začiatok od čísla na konci
            match = re.match(r"^(.*?)(\d+)$", orig_num)
            if match:
                prefix = match.group(1)
                num_part = match.group(2)
                inv_number = f"{prefix}{num_part.zfill(4)}" # Doplní nuly na 4 znaky
                inv_number_elem.text = inv_number # Aktualizujeme text priamo v zdroji
            else:
                inv_number = orig_num
        # -------------------------------------------------------------------------

        # --- KONTROLA FIRMY A IČO (Validátor) ---
        partner = old_header.find('.//typ:address', NS)
        if partner is not None:
            company = partner.find('typ:company', NS)
            ico = partner.find('typ:ico', NS)
            
            has_company = company is not None and company.text and company.text.strip()
            has_ico = ico is not None and ico.text and ico.text.strip()
            
            if has_company and not has_ico:
                invalid_invoices.append(f"FA {inv_number} (Firma: {company.text})")
        # ----------------------------------------

        # Tvorba novej položky
        new_item = ET.SubElement(new_root, f'{{{NS["dat"]}}}dataPackItem', {
            'version': '2.0', 'id': item.attrib.get('id', '')
        })
        new_invoice = ET.SubElement(new_item, f'{{{NS["inv"]}}}invoice', {'version': '2.0'})
        
        # --- HLAVIČKA ---
        new_header = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceHeader')
        for prefix in ['rsp', 'rdc', 'typ', 'ftr', 'lst']:
            new_header.set(f'xmlns:{prefix}', NS[prefix])

        ET.SubElement(new_header, f'{{{NS["inv"]}}}invoiceType').text = 'issuedInvoice'
        new_header.append(old_header.find('inv:number', NS))
        new_header.append(old_header.find('inv:symVar', NS))
        
        date_val = old_header.find('inv:date', NS).text
        for d_tag in ['date', 'dateTax', 'dateAccounting']:
            ET.SubElement(new_header, f'{{{NS["inv"]}}}{d_tag}').text = date_val

        # Účtovanie a DPH podľa zvolenej RADY
        acc = ET.SubElement(new_header, f'{{{NS["inv"]}}}accounting')
        class_vat = ET.SubElement(new_header, f'{{{NS["inv"]}}}classificationVAT')
        
        if rada == 'VFB':
            ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tovaru'
            ET.SubElement(class_vat, f'{{{NS["typ"]}}}ids').text = 'UN'
            ET.SubElement(new_header, f'{{{NS["inv"]}}}text').text = 'Tržby z predaja tovaru'
        else: # VFD
            ET.SubElement(acc, f'{{{NS["typ"]}}}ids').text = 'pred.tov.DE'
            ET.SubElement(class_vat, f'{{{NS["typ"]}}}ids').text = 'UD'
            ET.SubElement(new_header, f'{{{NS["inv"]}}}text').text = 'Predaj tovaru - Nemecko'
            
        ET.SubElement(class_vat, f'{{{NS["typ"]}}}classificationVATType').text = 'nonSubsume'

        # Adresa zákazníka
        old_address = old_header.find('.//typ:address', NS)
        if old_address is not None:
            partner_node = ET.SubElement(new_header, f'{{{NS["inv"]}}}partnerIdentity')
            partner_node.append(old_address)

        # Naša Identita
        my_identity = ET.SubElement(new_header, f'{{{NS["inv"]}}}myIdentity')
        addr = ET.SubElement(my_identity, f'{{{NS["typ"]}}}address')
        ET.SubElement(addr, f'{{{NS["typ"]}}}company').text = 'EPPO BRANDS s. r. o.'
        ET.SubElement(addr, f'{{{NS["typ"]}}}city').text = 'Zvolen'
        ET.SubElement(addr, f'{{{NS["typ"]}}}street').text = 'Tulská'
        ET.SubElement(addr, f'{{{NS["typ"]}}}number').text = '9386/6B'
        ET.SubElement(addr, f'{{{NS["typ"]}}}zip').text = '960 01'
        ET.SubElement(addr, f'{{{NS["typ"]}}}ico').text = '57039607'
        ET.SubElement(addr, f'{{{NS["typ"]}}}dic').text = '2122546481'
        ET.SubElement(addr, f'{{{NS["typ"]}}}icDph').text = 'SK2122546481'

        # --- POLOŽKY (IBA PRE VFD) ---
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

        # --- SUMÁR ---
        new_summary = ET.SubElement(new_invoice, f'{{{NS["inv"]}}}invoiceSummary')
        for prefix in ['rsp', 'rdc', 'typ', 'ftr', 'lst']:
            new_summary.set(f'xmlns:{prefix}', NS[prefix])

        ET.SubElement(new_summary, f'{{{NS["inv"]}}}roundingDocument').text = 'none'
        ET.SubElement(new_summary, f'{{{NS["inv"]}}}roundingVAT').text = 'none'
        
        old_summary = old_invoice.find('inv:invoiceSummary', NS)
        if old_summary is not None:
            home_curr = old_summary.find('inv:homeCurrency', NS)
            foreign_curr = old_summary.find('inv:foreignCurrency', NS)
            
            if foreign_curr is not None:
                rate = float(foreign_curr.find('typ:rate', NS).text)
                f_price_node = old_invoice.find('.//inv:foreignCurrency/typ:priceSum', NS)
                f_price = float(f_price_node.text) if f_price_node is not None else 0.0
                
                hc = ET.SubElement(new_summary, f'{{{NS["inv"]}}}homeCurrency')
                # --- OPRAVA: Prepočet teraz korektne násobí (Suma v EUR = Suma v Cudzej Mene * Kurz) ---
                ET.SubElement(hc, f'{{{NS["typ"]}}}priceNone').text = f"{f_price * rate:.2f}"
                
                fc = ET.SubElement(new_summary, f'{{{NS["inv"]}}}foreignCurrency')
                fc.append(foreign_curr.find('typ:currency', NS))
                fc.append(foreign_curr.find('typ:rate', NS))
                fc.append(foreign_curr.find('typ:amount', NS))
                ET.SubElement(fc, f'{{{NS["typ"]}}}priceSum').text = f"{f_price:.2f}"
            elif home_curr is not None:
                h_price_node = old_invoice.find('.//inv:homeCurrency/typ:priceSum', NS)
                h_price = h_price_node.text if h_price_node is not None else "0.00"
                hc = ET.SubElement(new_summary, f'{{{NS["inv"]}}}homeCurrency')
                ET.SubElement(hc, f'{{{NS["typ"]}}}priceNone').text = h_price
                
        processed_count += 1

    # Zápis do pamäte (BytesIO)
    output_bytes = io.BytesIO()
    new_tree = ET.ElementTree(new_root)
    # xml_declaration s Windows-1250 kódovaním
    new_tree.write(output_bytes, encoding='Windows-1250', xml_declaration=True)
    
    return output_bytes.getvalue(), invalid_invoices, processed_count

# ==========================================
# STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Pohoda XML Transform", page_icon="📝", layout="wide")

st.title("📦 Base.com -> Pohoda XML Transformátor")
st.markdown("Nástroj na automatickú úpravu XML exportov z Base.com pre potreby slovenskej Pohody (EPPO BRANDS s.r.o.).")

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Nastavenia importu")
    zvolena_rada = st.radio(
        "Vyberte dokladovú radu faktúr:",
        ('VFB', 'VFD'),
        captions=["Slovensko/Neplatca (bez detailov)", "Nemecko/Amazon FBA (rozpad DPH)"]
    )
    
    st.markdown("---")
    st.info("**Pravidlá aplikované skriptom:**\n"
            "* **Číslovanie:** Dopĺňanie na 4 cifry (2026VFB0002)\n"
            "* **Kódovanie:** Windows-1250\n"
            "* **Kurz:** EUR = Cena * Kurz zo súboru\n"
            "* **Validácia:** Kontrola IČO pri vyplnenej firme.")

# --- MAIN AREA ---
uploaded_file = st.file_uploader("Nahrajte zdrojový XML súbor z Base.com", type=["xml"])

if uploaded_file is not None:
    st.success(f"Súbor `{uploaded_file.name}` bol úspešne nahratý.")
    
    if st.button("🚀 Spustiť transformáciu", type="primary"):
        with st.spinner('Spracovávam faktúry...'):
            file_bytes = io.BytesIO(uploaded_file.getvalue())
            
            xml_data, errors, count = transform_xml(file_bytes, zvolena_rada)
            
            if xml_data is None:
                st.error("\n".join(errors))
            else:
                st.success(f"✅ Úspešne spracovaných {count} faktúr pre radu {zvolena_rada}.")
                
                # Výpis validátora
                if errors:
                    st.warning("⚠️ **UPOZORNENIE: Kontrola firiem odhalila podozrivé záznamy!**")
                    st.markdown("Tieto faktúry majú vyplnenú 'Firmu', ale nemajú zadané IČO. Pravdepodobne ide o Packstation alebo adresu napísanú v zlom poli. **Skontrolujte ich v Base.com:**")
                    for err in errors:
                        st.write(f"- {err}")
                else:
                    st.info("✓ Validácia firiem prebehla v poriadku. Žiadne chýbajúce IČO.")

                # Tlačidlo na stiahnutie
                now_str = datetime.now().strftime("%Y_%m_%d_%H%M")
                out_filename = f"Pohoda_Import_{zvolena_rada}_{now_str}.xml"
                
                st.markdown("---")
                st.download_button(
                    label="💾 Stiahnuť upravené XML pre Pohodu",
                    data=xml_data,
                    file_name=out_filename,
                    mime="application/xml"
                )
