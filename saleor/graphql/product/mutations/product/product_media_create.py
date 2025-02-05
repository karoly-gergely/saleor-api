import asyncio

import graphene

from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from .....core.http_client import HTTPClient
from .....core.utils.validators import get_oembed_data
from .....permission.enums import ProductPermissions
from .....product import ProductMediaTypes, models
from .....product.error_codes import ProductErrorCode
from .....thumbnail.utils import get_filename_from_url
from ....channel import ChannelContext
from ....core import ResolveInfo
from ....core.doc_category import DOC_CATEGORY_PRODUCTS
from ....core.mutations import BaseMutation
from ....core.types import BaseInputObjectType, ProductError, Upload
from ....core.validators.file import clean_image_file, is_image_url, validate_image_url
from ....plugins.dataloaders import get_plugin_manager_promise
from ...types import Product, ProductMedia, ProductVariant
from ...utils import ALT_CHAR_LIMIT, download_files


class ProductMediaCreateInput(BaseInputObjectType):
    alt = graphene.String(description="Alt text for a product media.")
    image = Upload(
        required=False, description="Represents an image file in a multipart request."
    )
    product = graphene.ID(
        required=True, description="ID of an product.", name="product"
    )
    media_url = graphene.String(
        required=False, description="Represents an URL to an external media."
    )
    media_urls = graphene.List(
        graphene.String,
        required=False,
        description="Represents an list of URLs to external media.",
    )
    variant_id = graphene.ID(required=False, description="ID of a product variant.")

    class Meta:
        doc_category = DOC_CATEGORY_PRODUCTS


class ProductMediaCreate(BaseMutation):
    product = graphene.Field(Product)
    product_variant = graphene.Field(ProductVariant)
    media = graphene.Field(ProductMedia)

    class Arguments:
        input = ProductMediaCreateInput(
            required=True, description="Fields required to create a product media."
        )

    class Meta:
        description = (
            "Create 1 or more media object(s) (image or video URL) "
            "associated with product and optionally a product variant. "
            "For image, this mutation must be sent as a `multipart` request. "
            "More detailed specs of the upload format can be found here: "
            "https://github.com/jaydenseric/graphql-multipart-request-spec"
        )
        doc_category = DOC_CATEGORY_PRODUCTS
        permissions = (ProductPermissions.MANAGE_PRODUCTS,)
        error_type_class = ProductError
        error_type_field = "product_errors"

    @classmethod
    def validate_input(cls, data):
        image = data.get("image")
        media_url = data.get("media_url")
        media_urls = data.get("media_urls")
        alt = data.get("alt")

        if not image and not media_url and not media_urls:
            raise ValidationError(
                {
                    "input": ValidationError(
                        "Image or external URL(s) is/are required.",
                        code=ProductErrorCode.REQUIRED.value,
                    )
                }
            )
        if image and (media_url or media_urls):
            raise ValidationError(
                {
                    "input": ValidationError(
                        "Either image or external URL is required.",
                        code=ProductErrorCode.DUPLICATED_INPUT_ITEM.value,
                    )
                }
            )

        if alt and len(alt) > ALT_CHAR_LIMIT:
            raise ValidationError(
                {
                    "input": ValidationError(
                        f"Alt field exceeds the character "
                        f"limit of {ALT_CHAR_LIMIT}.",
                        code=ProductErrorCode.INVALID.value,
                    )
                }
            )

    @classmethod
    def perform_mutation(  # type: ignore[override]
        cls, _root, info: ResolveInfo, /, *, input
    ):
        cls.validate_input(input)
        product = cls.get_node_or_error(
            info,
            input["product"],
            field="product",
            only_type=Product,
            qs=models.Product.objects.all(),
        )

        alt = input.get("alt", "")
        media_url = input.get("media_url")
        media_urls = input.get("media_urls")
        variant_id = input.get("variant_id")
        media = None
        variant = None
        if img_data := input.get("image"):
            input["image"] = info.context.FILES.get(img_data)
            image_data = clean_image_file(input, "image", ProductErrorCode)
            media = product.media.create(
                image=image_data, alt=alt, type=ProductMediaTypes.IMAGE
            )
        if media_urls:
            # Step 1: Download all files concurrently
            downloaded_files = asyncio.run(download_files(media_urls))

            with transaction.atomic():
                # Step 2: Prepare MediaFile objects
                media_objects = [
                    models.ProductMedia(
                        product=product,
                        image=ContentFile(
                            content,
                            name=get_filename_from_url(media_url),
                        ),
                        alt=alt,
                        type=ProductMediaTypes.IMAGE
                    )
                    for filename, content, media_url in downloaded_files if content
                ]

                # Step 3: Bulk insert into DB
                models.ProductMedia.objects.bulk_create(media_objects)

                # Step 4: (Optionally) assign to a product variant
                if variant_id:
                    variant = models.ProductVariant.objects.get(pk=variant_id)
                    variant_media_mobjects = [
                        models.VariantMedia(
                            variant=variant,
                            media=media_object
                        )
                        for media_object in media_objects
                    ]

                    models.VariantMedia.objects.bulk_create(variant_media_mobjects)

        elif media_url:
            # Remote URLs can point to the images or oembed data.
            # In case of images, file is downloaded. Otherwise we keep only
            # URL to remote media.
            if is_image_url(media_url):
                validate_image_url(
                    media_url, "media_url", ProductErrorCode.INVALID.value
                )
                filename = get_filename_from_url(media_url)
                image_data = HTTPClient.send_request(
                    "GET", media_url, stream=True, allow_redirects=False
                )
                image_file = File(image_data.raw, filename)
                media = product.media.create(
                    image=image_file,
                    alt=alt,
                    type=ProductMediaTypes.IMAGE,
                )
            else:
                oembed_data, media_type = get_oembed_data(media_url, "media_url")
                media = product.media.create(
                    external_url=oembed_data["url"],
                    alt=oembed_data.get("title", alt),
                    type=media_type,
                    oembed_data=oembed_data,
                )
        manager = get_plugin_manager_promise(info.context).get()
        cls.call_event(manager.product_updated, product)
        cls.call_event(manager.product_media_created, media)
        product = ChannelContext(node=product, channel_slug=None)
        return ProductMediaCreate(product=product, media=media, variant=variant)
