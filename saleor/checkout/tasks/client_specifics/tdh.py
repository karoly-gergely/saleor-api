import traceback
from collections import defaultdict

from ....celeryconf import app
from ....core.db.connection import allow_writer


@app.task
@allow_writer()
def post_confirm_order(order_id):
    from saleor.order.models import Order
    from .utils.zoho_client import get_or_create_customer, get_or_create_item, \
        create_estimate

    def get_line_item_attributes_and_brand(line_item):
        """
        Extracts all attributes from the product and variant associated with a
        Saleor line item.

        :param line_item: A Saleor line item object.
        :return: Tuple: (Dictionary of attribute names and values, Brand).
        """

        attributes_map = {}

        product = line_item.variant.product
        variant = line_item.variant
        brand = None

        # Extract product attributes
        attribute_products = product.product_type.attributeproduct.all()
        assigned_values = product.attributevalues.all()

        values_map = defaultdict(list)
        for av in assigned_values:
            values_map[av.value.attribute.slug].append(av.value)

        for attribute_product in attribute_products:
            attribute = attribute_product.attribute
            attr_slug = attribute.slug

            attr_values = []
            for attr_value in values_map.get(attr_slug, []):
                value = attr_value.name
                attr_values.append(value)

            attributes_map[attr_slug] = attr_values if len(attr_values) < 1 else \
                attr_values[0]

        # Extract variant attributes
        for attr in variant.attributes.all():
            attr_name = attr.assignment.attribute.slug
            attr_value = [value.name for value in attr.values.all()]
            if attr_name == 'brand':
                brand = attr_value if len(attr_value) > 1 else \
                    attr_value[0]
                continue
            attributes_map[attr_name] = attr_value if len(attr_value) < 1 else \
                attr_value[0]

        return attributes_map, brand

    """Triggered when an order is confirmed in Saleor."""
    try:
        order = Order.objects.get(id=order_id)
        user = order.user
        customer_email = user.email
        customer_name = f"{user.first_name} {user.last_name}" if user.first_name \
            else customer_email.split("@")[0]
        company_name = user.default_shipping_address.company_name
        customer_metadata = user.metadata

        # Fetch customer details from Zoho (Ensuring we have the response object)
        customer_response, person_response = get_or_create_customer(
            customer_email, customer_name, company_name, customer_metadata,
            user.default_billing_address, user.default_shipping_address
        )
        customer_id = customer_response["contact_id"]
        contact_id = person_response["contact_person_id"]

        items = []
        for line in order.lines.all():
            sku = line.product_sku
            name = line.product_name
            price = line.unit_price_gross_amount
            channel_listing = line.variant.channel_listings.get(channel=order.channel)
            cost = channel_listing.cost_price.amount if \
                channel_listing.cost_price else 0.00
            # description = line.variant.product.description or \
            #     "No description available"
            description = ''
            category = line.variant.product.category.name \
                or "Default Category"
            attributes, vendor = get_line_item_attributes_and_brand(line)

            item_id = get_or_create_item(
                sku, name, price, cost, description, category, vendor, attributes
            )
            items.append({"id": item_id, "price": price, "quantity": line.quantity})

        # Create the estimate
        response = create_estimate(
            customer_id, items, order, contact_id
        )
        return {"status": "success", "estimate_response": response}

    except Exception as e:
        return {
            "status": "error", "message": str(e), "traceback": traceback.format_exc()
        }


@app.task
@allow_writer()
def check_estimate_accepted_and_add_zoho_payments():
    from saleor.checkout.tasks.client_specifics.utils.zoho_client import \
        get_all_accepted_estimates
    estimates = get_all_accepted_estimates()

    for estimate in estimates:
        estimate_id = estimate["estimate_id"]
        from saleor.checkout.tasks.client_specifics.utils.zoho_client import \
            update_retainer_invoice_payment_options
        update_retainer_invoice_payment_options(estimate_id)

    if estimates:
        return "Retainer invoice check completed."
