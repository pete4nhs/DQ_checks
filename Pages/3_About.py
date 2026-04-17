import streamlit as st

st.title('About')
st.write('This tool is designed to support trusts in running their own data quality checks for the following reports:')
st.write('- Indicative Activity Plan (IAP) reporting')
st.write('- Local Price reporting')
st.write('')
st.write('The reports specification can be found at:')
st.write('- https://www.england.nhs.uk/publication/iap-reporting-specification-technical-detail-specific-data-requirements/')
st.write('- https://www.england.nhs.uk/publication/local-prices-reporting-specification-technical-detail-specific-data-requirements/')
st.write('')
st.write('For any information on the process you can contact the **NHS England London Spec Comm BI team** at: england.dcmd.bilondon@nhs.net or melanietunmore@nhs.net')
st.write('To report any issue or provide feedback on the application, please contact peter.saiu@nhs.net')
st.write('')
st.warning("**Please note that uploading and processing DQ checks through this tool does not constitute data submission. " \
"This tool is solely intended to assess the formatting of your file.**")

