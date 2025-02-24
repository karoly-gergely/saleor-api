import datetime
import logging
import os
from decimal import Decimal

import requests
import json
import time

# Zoho API Credentials (Replace with actual values)
ZOHO_CLIENT_ID = os.environ.get("ZOHO_CLIENT_ID", "")
ZOHO_CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET", "")
ZOHO_REFRESH_TOKEN = os.environ.get("ZOHO_REFRESH_TOKEN", "")
ZOHO_ORG_ID = "699140456"
TOKEN_FILE = "/tmp/zoho_access_token.json"


logger = logging.getLogger(__name__)


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def filter_object(obj, keys_to_keep):
    """
    Returns a new dictionary containing only the specified keys from the original object.

    :param obj: The original dictionary.
    :param keys_to_keep: A list of keys to retain in the new dictionary.
    :return: A new dictionary with only the specified keys.
    """
    return {key: obj[key] for key in keys_to_keep if key in obj}


def refresh_access_token():
    """Automatically refresh the Zoho Books access token using the refresh token."""
    url = "https://accounts.zoho.com/oauth/v2/token"
    payload = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }
    response = requests.post(url, data=payload).json()

    if "access_token" in response:
        token_data = {
            "access_token": response["access_token"],
            "expiry": time.time() + response["expires_in"] - 300
            # Refresh 5 min before expiry
        }
        with open(TOKEN_FILE, "w") as file:
            json.dump(token_data, file)
        return response["access_token"]

    raise Exception("Failed to refresh Zoho access token")


def get_access_token():
    """Retrieve the current access token, refreshing if necessary."""
    try:
        with open(TOKEN_FILE, "r") as file:
            token_data = json.load(file)
            if time.time() < token_data["expiry"]:
                return token_data["access_token"]
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return refresh_access_token()


def get_headers():
    """Generate headers with a fresh access token."""
    return {"Authorization": f"Zoho-oauthtoken {get_access_token()}"}


def get_or_create_vendor(vendor_name):
    """Find or create a vendor in Zoho Books."""
    headers = get_headers()

    search_url = f"https://www.zohoapis.com/books/v3/" \
                 f"contacts?organization_id={ZOHO_ORG_ID}&contact_type=vendor"
    response = requests.get(search_url, headers=headers).json()
    existing_vendor = next(
        (v for v in response.get("contacts", []) if v["contact_name"] == vendor_name),
        None
    )
    if existing_vendor:
        return existing_vendor["contact_id"]

    create_url = f"https://www.zohoapis.com/books/v3/" \
                 f"contacts?organization_id={ZOHO_ORG_ID}"
    payload = {
        "contact_name": vendor_name,
        "contact_type": "vendor"
    }
    response = requests.post(create_url, json=payload, headers=headers).json()

    return response["contact"]["contact_id"]


def get_or_create_category(category_name):
    """Find or create a category in Zoho Books."""
    headers = get_headers()

    search_url = f"https://www.zohoapis.com/books/v3/" \
                 f"settings/categories?organization_id={ZOHO_ORG_ID}"
    response = requests.get(search_url, headers=headers).json()

    existing_category = next(
        (
            c for c in response.get("categories", [])
            if c["category_name"] == category_name
        ),
        None
    )
    if existing_category:
        return existing_category["category_id"]

    create_url = f"https://www.zohoapis.com/books/v3/" \
                 f"settings/categories?organization_id={ZOHO_ORG_ID}"
    payload = {"category_name": category_name}
    response = requests.post(create_url, json=payload, headers=headers).json()
    return response["category"]["category_id"]


def get_custom_field_id(field_label, module="contact"):
    """Fetch the custom field ID by its label."""
    headers = get_headers()
    url = f"https://www.zohoapis.com/books/v3/settings/" \
          f"customfields?organization_id={ZOHO_ORG_ID}&module={module}"
    response = requests.get(url, headers=headers).json()

    for field in response.get("customfields", {}).get(module):
        if field["label"] == field_label:
            return field["customfield_id"]

    return create_custom_field(field_label, module)


def create_custom_field(field_label, module="contact"):
    """Create a custom field if it doesn't exist."""
    headers = get_headers()
    url = f"https://www.zohoapis.com/books/v3/settings/" \
          f"customfields?organization_id={ZOHO_ORG_ID}"
    payload = {
        "module": module,
        "custom_field_name": field_label,
        "data_type": "string",
        "is_active": True
    }
    response = requests.post(url, json=payload, headers=headers).json()

    return response.get("customfield", {}).get("customfield_id")


def get_or_create_customer(
    email,
    name,
    company_name,
    metadata,
    billing_address,
    shipping_address,
):
    """Find or create a customer in Zoho Books, ensuring custom fields exist."""
    headers = get_headers()
    ein_or_license = metadata.get("EIN / License Number / Reseller's Permit", "")
    field_id = get_custom_field_id(
        "EIN / License Number / Reseller's Permit",
    )

    search_url = f"https://www.zohoapis.com/books/v3/" \
                 f"contacts?organization_id={ZOHO_ORG_ID}&email={email}"
    response = requests.get(search_url, headers=headers).json()

    if response.get("contacts"):
        person_url = f"https://www.zohoapis.com/books/v3/" \
                     f"contacts/{response['contacts'][0]['contact_id']}/" \
                     f"contactpersons?organization_id={ZOHO_ORG_ID}"
        second_response = requests.get(person_url, headers=headers).json()
        return response["contacts"][0], second_response["contact_persons"][0]

    create_url = f"https://www.zohoapis.com/books/v3/" \
                 f"contacts?organization_id={ZOHO_ORG_ID}"
    payload = {
        "contact_name": name if name != email else company_name,
        "company_name": company_name,
        "contact_type": 'customer',
        # "currency_id": 460000000000097,
        "customer_sub_type": "business",
        "billing_address": {
            "attention": name,
            "country": "U.S.A.",
            "city": billing_address.city,
            "state": billing_address.country_area,
            "address": billing_address.street_address_1,
            "street2": billing_address.street_address_2,
            "state_code": billing_address.country_area,
            "zip": billing_address.postal_code,
        },
        "shipping_address": {
            "attention": name,
            "country": "U.S.A.",
            "city": shipping_address.city,
            "state": shipping_address.country_area,
            "address": shipping_address.street_address_1,
            "street2": shipping_address.street_address_2,
            "state_code": shipping_address.country_area,
            "zip": shipping_address.postal_code,
        },
        "custom_fields": [{"customfield_id": field_id, "value": ein_or_license}]
    }
    response = requests.post(create_url, json=payload, headers=headers).json()
    contact = response["contact"]

    create_person_url = f"https://www.zohoapis.com/books/v3/" \
                        f"contacts/contactpersons?organization_id={ZOHO_ORG_ID}"
    name_parts = name.split(' ')
    if len(name_parts) > 1:
        first_name = name_parts[0]
        last_name = " ".join(name_parts[1:])
    else:
        name.split('.')
        if len(name_parts) > 1:
            first_name = name_parts[0]
            last_name = " ".join(name_parts[1:])
        else:
            last_name = name
            first_name = ''
    payload = {
        "contact_id": contact['contact_id'],
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
    }
    second_response = requests\
        .post(create_person_url, json=payload, headers=headers).json()

    return contact, second_response["contact_person"]


def get_or_create_dropdown_custom_field(field_label, module, new_option):
    """
    Ensures a dropdown custom field exists in Zoho Books and adds a new option only
    if it's not already present.
    """
    headers = get_headers()
    custom_field_id = get_custom_field_id(field_label, module)

    if custom_field_id:
        # Fetch existing options
        url = f"https://www.zohoapis.com/books/v3/settings/fields/editpage?" \
              f"organization_id={ZOHO_ORG_ID}&entity={module}&" \
              f"field_id={custom_field_id}"
        response = requests.get(url, headers=headers).json()

        field = response.get("field", {})
        existing_options = field.get('values', [])
        existing_option_names = [item['name'] for item in existing_options]

        if new_option in existing_option_names:
            return {"message": f"'{new_option}' already exists in '{field_label}'."}

        # Add new option to existing list and update the field
        existing_options.append({
            "is_active": True,
            "name": new_option,
            "order": len(existing_options) + 1
        })
        put_url = f"https://www.zohoapis.com/books/v3/settings/" \
                  f"fields/{custom_field_id}/"
        field['values'] = existing_options
        put_keys = [
            "customfield_id",
            "is_mandatory",
            "is_basecurrency_amount",
            "data_type",
            "pii_type",
            "default_value",
            "entity",
            "values",
            "is_unique",
            "label",
            "selected_txn_entities",
            "help_text",
            "external_fields",
            "field_preferences",
            "show_on_pdf",
        ]
        clean_field = filter_object(field, keys_to_keep=put_keys)

        response = requests.put(put_url, json=clean_field, headers=headers).json()

        return response
    return None


def get_or_create_item(sku, name, price, cost, description, category, vendor,
                       attributes):
    """Find or create an item in Zoho Books, ensuring custom fields exist."""
    headers = get_headers()
    search_url = f"https://www.zohoapis.com/books/v3/" \
                 f"items?organization_id={ZOHO_ORG_ID}&search_text={sku}"
    response = requests.get(search_url, headers=headers).json()

    if response.get("items"):
        return response["items"][0]["item_id"]

    vendor_id = get_or_create_vendor(vendor) if vendor else None
    full_description = ''

    for label, value in attributes.items():
        full_description += f"{label}: {value}\n"

    full_description += (description or '')

    custom_fields = []
    for label, value in {
        "Category": category,
        "Item Number": sku,
        "Vendor": vendor,
    }.items():
        field_id = get_custom_field_id(label, "item")
        custom_fields.append({"customfield_id": field_id, "value": value})
        if label in ["Category", "Vendor"]:
            get_or_create_dropdown_custom_field(label, 'item', value)

    create_url = f"https://www.zohoapis.com/books/v3/items?organization_id={ZOHO_ORG_ID}"
    payload = {
        "name": name,
        "sku": sku,
        "rate": price,
        "purchase_rate": cost,
        "is_taxable": True,
        "unit": "Each",
        "description": full_description,
        "custom_fields": custom_fields,
    }

    if vendor_id:
        payload["vendor_id"] = vendor_id

    payload_json = json.dumps(payload, cls=DecimalEncoder)
    response = requests.post(create_url, data=payload_json, headers=headers).json()

    return response["item"]["item_id"]


def get_all_accepted_estimates():
    """Fetch all accepted estimates from Zoho Books."""
    url = f"https://www.zohoapis.com/books/v3/estimates?" \
          f"organization_id={ZOHO_ORG_ID}&status=accepted"
    headers = get_headers()
    response = requests.get(url, headers=headers).json()
    return response.get("estimates", [])


def update_retainer_invoice_payment_options(estimate_id):
    """
    Finds the retainer invoice linked to an estimate and updates its payment options.
    """
    headers = get_headers()

    # Step 1: Get Retainer Invoice(s) linked to the Estimate
    search_url = f"https://www.zohoapis.com/books/v3/retainerinvoices?" \
                 f"organization_id={ZOHO_ORG_ID}&estimate_id={estimate_id}"
    response = requests.get(search_url, headers=headers).json()

    retainer_invoices = response.get("retainerinvoices", [])

    if not retainer_invoices:
        return {"error": "No retainer invoices found for this estimate."}

    # Assuming we update the first (or only) retainer invoice linked to the estimate
    retainer_invoice_id = retainer_invoices[0]["retainerinvoice_id"]

    if retainer_invoices[0]["status"] == "drawn":
        # Step 2: Update the Retainer Invoice with Payment Options
        update_url = f"https://www.zohoapis.com/books/v3/retainerinvoices/" \
                     f"{retainer_invoice_id}?organization_id={ZOHO_ORG_ID}"

        put_keys = [
            "customer_id",
            "estimate_id",
            "retainerinvoice_number",
            "reference_number",
            "date",
            "contact_persons",
            "exchange_rate",
            "custom_fields",
            "notes",
            "terms",
            "line_items",
            "payment_options",
            "template_id",
            "billing_address_id",
            "documents",
        ]
        retainer_invoice = retainer_invoices[0]
        retainer_invoice["payment_options"] = {
            "payment_gateways": [{"gateway_name": "zoho_payments"}]
        }
        clean_invoice = filter_object(retainer_invoice, keys_to_keep=put_keys)

        update_response = requests.put(update_url, json=clean_invoice,
                                       headers=headers).json()

        return update_response


def create_estimate(
    customer_id, items, order, contact_id,
    customer_notes="""Freight is not included and will be added to the final invoice. Thank you for considering Terris Draheim for your project needs.""", terms_conditions="""By accepting this Estimate, you agree to our Terms and conditions, which can be found here:
https://outdoor.terrisdraheim.com/terms.

CARE AND MAINTENANCE BY BRAND for all of our products can be found on the bottom of our landing page: www.outdoor.terrisdraheim.com

WARRANTY BY BRAND for all of our products can be found on the bottom of our landing page: www.outdoor.terrisdraheim.com""",
    template_id='2066512000000316906', send_email=True, crm_potential_id=None
):
    """Create an estimate in Zoho Books with additional fields, ensuring custom
    fields exist."""
    headers = get_headers()
    create_url = f"https://www.zohoapis.com/books/v3/estimates?" \
                 f"organization_id={ZOHO_ORG_ID}&" \
                 f"send={'true' if send_email else 'false'}"

    line_items = [
        {
            "item_id": item["id"],
            "rate": item["price"],
            "quantity": item["quantity"],
            "tax_id": "",
        }
        for item in items
    ]

    custom_fields = []
    for label, value in {
        "Sidemark": "TBA",
        "Client PO": "TBA",
        "EST Lead Time": "TBA",
    }.items():
        field_id = get_custom_field_id(label, "estimate")
        custom_fields.append({"customfield_id": field_id, "value": value})

    payload = {
        "customer_id": customer_id,
        "contact_persons": [contact_id],
        "date": str(datetime.date.today()),
        "line_items": line_items,
        "accept_retainer": True,
        "retainer_percentage": 50,
        "payment_options": {
            "payment_gateways": [{"gateway_name": "zoho_payments"}],
        },
        # "notes": customer_notes,
        # "terms": terms_conditions,
        "salesperson_name": "Paul Patterson",
        "custom_fields": custom_fields
    }

    # if template_id:
    #     payload["template_id"] = template_id
    if crm_potential_id:
        payload["potential_id"] = crm_potential_id

    payload_json = json.dumps(payload, cls=DecimalEncoder)
    response = requests.post(create_url, data=payload_json, headers=headers).json()

    return response
