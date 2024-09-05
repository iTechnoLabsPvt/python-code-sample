from itechno_labs.decorators import access_authorized_users_only
from itechno_labs.set_occupancy_percentage.set_occupancy_percentage import (
    OccupancyPercentage,
)
from itechno_labs.min_price_updation.min_stay_updation import MinPrice
from itechno_labs.min_stay_profile.min_stay_profile import MinStayProfile
from itechno_labs.models import (
    Minstayprofile,
    Pricingdata,
    Propertygroup,
    Propertylisting,
)
from itechno_labs.response import Responsehandler
from itechno_labs.tasks import *
from itechno_labs.serializers import *
from itechno_labs.sync_background_process.sync_background_process import *
from rest_framework.decorators import api_view
from rest_framework.response import Response
from itechno_labs.update_pricing.format_occupancy_data import FormatOccupancyData
from services.hostaway_service import hit_hostaway
import re
from django.conf import settings
from random import randint
import math
from rest_framework import status
from logger_setup import logger
import datetime
from dateutil.relativedelta import relativedelta

response_handler = Responsehandler()


@api_view(["GET", "POST"])
@access_authorized_users_only
def get_and_update_occupancy(request, group_id: int) -> Response:
    """
    Handles GET and POST requests for occupancy data.

    - GET: Returns records from the property group table for a specific group_id.
    - POST: Updates the group, inserts a new record in the pricing_data table if it doesn't exist,
      and syncs occupancy new prices.

    Args:
        request (object): The HTTP request object.
        group_id (int): The ID of the property group.

    Returns:
        Response: JSON response containing the result of the operation.
    """
    try:
        if request.method == "GET":
            group_data_obj = Propertygroup.objects.filter(id=group_id)
            if not group_data_obj.exists():
                response_dict = response_handler.msg_response(
                    "Invalid group id.", status.HTTP_422_UNPROCESSABLE_ENTITY
                )
                return Response(
                    response_dict, status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            group_data = GroupDataWithPricingDataSerializer(
                group_data_obj[0], many=False
            )
            response_dict = response_handler.success_response(
                group_data.data, status.HTTP_200_OK
            )
            return Response(response_dict, status=status.HTTP_200_OK)

        elif request.method == "POST":
            json_data = request.data
            if json_data.get("group_name") is not None:
                duplicate_group = Propertygroup.objects.filter(
                    group_name=json_data.get("group_name")
                ).exclude(id=group_id)
                if duplicate_group.exists():
                    response_dict = response_handler.msg_response(
                        "Duplicate group name.", status.HTTP_422_UNPROCESSABLE_ENTITY
                    )
                    return Response(
                        response_dict, status=status.HTTP_422_UNPROCESSABLE_ENTITY
                    )

            # Update minimum price
            min_price = MinPrice()
            if json_data.get("min_price") is not None:
                msg, returned_status = min_price.update_min_price_in_grp(
                    group_id, json_data.get("min_price")
                )
                if returned_status == 422:
                    response_dict = response_handler.msg_response(msg, returned_status)
                    return Response(
                        response_dict, status=status.HTTP_422_UNPROCESSABLE_ENTITY
                    )

            # Update minimum stay profile
            min_stay = MinStayProfile()
            if json_data.get("min_stay_profile_id"):
                msg, returned_status = min_stay.add_min_stay_profile_in_grp(
                    int(json_data.get("min_stay_profile_id")), group_id
                )
                if returned_status == 422:
                    response_dict = response_handler.msg_response(msg, returned_status)
                    return Response(
                        response_dict, status=status.HTTP_422_UNPROCESSABLE_ENTITY
                    )

            # Background update for minimum stay
            update_grp_min_stay_obj = BackgroundUpdateSingleGroupMinNights()
            update_grp_min_stay_obj.background_update_single_group_min_nights(group_id)

            group_data = Propertygroup.objects.filter(id=group_id)
            if not group_data.exists():
                response_dict = response_handler.msg_response(
                    "Invalid group id.", status.HTTP_422_UNPROCESSABLE_ENTITY
                )
                return Response(
                    response_dict, status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            if json_data.get("group_name") is not None:
                group_data[0].group_name = json_data["group_name"]
                group_data[0].save()

            # Handle seasonal data
            if "seasonal_data" in json_data:
                seasonal_data = json_data["seasonal_data"]
                if len(seasonal_data) > 1 and not any(
                    value in (None, "") for value in seasonal_data[0].values()
                ):
                    pricing_data = Pricingdata.objects.filter(
                        group_id=group_id, level="group", type="seasonal"
                    )
                    if not pricing_data.exists():
                        Pricingdata.objects.create(
                            data=json_data["seasonal_data"],
                            type="seasonal",
                            group_id=group_id,
                            level="group",
                        )
                    else:
                        pricing_data.update(data=json_data["seasonal_data"])

            # Handle occupancy data
            if json_data.get("pricing_data") is not None:
                duplicate_record = list(
                    Pricingdata.objects.filter(
                        group_id=group_id, level="group", type="occupancy"
                    )
                )
                if not duplicate_record:
                    create_obj = Pricingdata(
                        data=json_data["pricing_data"],
                        type="occupancy",
                        group_id=group_id,
                        level="group",
                    )
                    create_obj.save()
                else:
                    duplicate_record[0].data = json_data["pricing_data"]
                    duplicate_record[0].save()

            # Sync prices
            background_sync_bulk_price_obj = BackgroundSyncPrices()
            price_data_qs = Pricingdata.objects.filter(
                group__id=group_id, type="seasonal", level="group"
            ).exclude(data__exact=[])

            if price_data_qs.exists():
                background_sync_bulk_price_obj.update_bulk_prices()
            else:
                new_updated_pricing_data = list(
                    Pricingdata.objects.filter(
                        group_id=group_id, type="seasonal", level="group"
                    ).values()
                )
                if new_updated_pricing_data:
                    occupancy_data_obj = FormatOccupancyData()
                    group_price_data = occupancy_data_obj.get_json_data(
                        new_updated_pricing_data[0]
                    )
                    background_sync_bulk_price_obj.update_single_group_prices(
                        group_price_data
                    )

            # Update property listing
            if json_data.get("property_listing") is not None:
                group = Propertygroup.objects.get(id=group_id)
                group.property_listing_id = json_data.get("property_listing")
                group.save()
                listings = Propertylisting.objects.filter(group_id=group.id)
                for listing in listings:
                    listing.is_parent = listing.id == json_data.get("property_listing")
                    listing.save()

            # Return updated group data
            new_updated_group_data = Propertygroup.objects.get(id=group_id)
            result_data = GroupDataWithPricingDataSerializer(
                new_updated_group_data, many=False
            )
            response_dict = response_handler.success_response(
                result_data.data, status.HTTP_200_OK
            )
            return Response(response_dict, status=status.HTTP_200_OK)

    except Exception as e:
        logger.exception("Error in function get_and_update_occupancy")
        exception_dict = {"message": str(e), "status": 500}
        return Response(exception_dict, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@access_authorized_users_only
def sync_listing_prices(request) -> Response:
    """
    Syncs occupancy new prices for all occupancy-related records from the pricing_data table.

    Args:
        request (object): The HTTP request object.

    Returns:
        Response: JSON response indicating the result of the operation.
    """
    try:
        if request.method == "GET":
            background_sync_bulk_price_obj = BackgroundSyncPrices()
            background_sync_bulk_price_obj.update_bulk_prices()
            background_update_min_nights_obj = BackgroundUpdateListingsMinNights()
            background_update_min_nights_obj.background_update_listings_min_nights()
            response_dict = response_handler.msg_response(
                "Prices synced successfully.", status.HTTP_200_OK
            )
            return Response(response_dict, status=status.HTTP_200_OK)

    except Exception as e:
        logger.exception("Error in function sync_listing_prices")
        exception_dict = {"message": str(e), "status": 500}
        return Response(exception_dict, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@access_authorized_users_only
def property_group_listing(request) -> Response:
    """
    Fetches all property groups and listings, adding data to both tables if it doesn't already exist.

    Args:
        request (object): The HTTP request object.

    Returns:
        Response: JSON response containing the result of the operation.
    """
    try:
        if request.method == "GET":
            listing = hit_hostaway()
            if listing:
                background_listing_sync_obj = BackgroundSyncGroupsAndListings()
                msg = background_listing_sync_obj.background_sync_grps_and_listing(
                    listing
                )
                response_dict = response_handler.msg_response(msg, status.HTTP_200_OK)
                return Response(response_dict, status=status.HTTP_200_OK)
            else:
                response_dict = response_handler.msg_response(
                    "No listings found or incorrect Hostaway credentials.",
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
                return Response(
                    response_dict, status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

    except Exception as e:
        logger.exception("Error in function property_group_listing")
        exception_dict = {"message": str(e), "status": 500}
        return Response(exception_dict, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
def user_login(request) -> Response:
    """
    Logs in the user with validations.

    Args:
        request (object): The HTTP request object.

    Returns:
        Response: JSON response indicating the result of the login attempt.
    """
    try:
        if request.method == "POST":
            json_data = request.data
            request_email = json_data.get("email")
            request_password = json_data.get("password")

            # Validate email and password
            if not request_email:
                response_dict = response_handler.msg_response(
                    "Email is required.", status.HTTP_422_UNPROCESSABLE_ENTITY
                )
                return Response(
                    response_dict, status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            if not request_password:
                response_dict = response_handler.msg_response(
                    "Password is required.", status.HTTP_422_UNPROCESSABLE_ENTITY
                )
                return Response(
                    response_dict, status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            if len(request_password) < 6:
                response_dict = response_handler.msg_response(
                    "Password length must be at least 6 characters.",
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
                return Response(
                    response_dict, status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            # Validate email format
            email_regex = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b"
            if not re.fullmatch(email_regex, request_email):
                response_dict = response_handler.msg_response(
                    "Invalid email format.", status.HTTP_422_UNPROCESSABLE_ENTITY
                )
                return Response(
                    response_dict, status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            # Check user credentials
            if (
                request_email == settings.LOGIN_USER
                and request_password == settings.USER_PASSWORD
            ):
                rand_token = "128a5e3f-60d4-4fc7-92fb-2cd5c4c27e04"  # This should be replaced with a proper token generation
                request.session["access_token"] = rand_token
                response_dict = response_handler.msg_with_token(
                    "Logged in successfully.", rand_token, status.HTTP_200_OK
                )
                return Response(response_dict, status=status.HTTP_200_OK)
            else:
                response_dict = response_handler.msg_response(
                    "Invalid login details. Please try again.",
                    status.HTTP_401_UNAUTHORIZED,
                )
                return Response(response_dict, status=status.HTTP_401_UNAUTHORIZED)

    except Exception as e:
        logger.exception("Error in function user_login")
        exception_dict = {"message": str(e), "status": 500}
        return Response(exception_dict, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["PUT"])
@access_authorized_users_only
def clone_group(request, group_id: int) -> Response:
    """
    Clones a group based on the provided group_id and also clones the associated pricing data.

    Args:
        request (object): The HTTP request object.
        group_id (int): The ID of the group to clone.

    Returns:
        Response: JSON response indicating the result of the cloning operation.
    """
    try:
        if request.method == "PUT":
            group_data = Propertygroup.objects.filter(id=group_id).values()
            if not group_data.exists():
                response_dict = response_handler.msg_response(
                    "Invalid group id.", status.HTTP_422_UNPROCESSABLE_ENTITY
                )
                return Response(
                    response_dict, status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            new_group_name = (
                f"{group_data[0]['group_name']}-Copy-{randint(10000, 99999)}"
            )
            new_group_obj = Propertygroup(group_name=new_group_name)
            new_group_obj.save()

            pricing_data = Pricingdata.objects.filter(
                group_id=group_id, level="group"
            ).values()
            if pricing_data.exists():
                create_obj = Pricingdata(
                    data=pricing_data[0]["data"],
                    type=pricing_data[0]["type"],
                    group_id=new_group_obj.id,
                    level=pricing_data[0]["level"],
                )
                create_obj.save()

            new_updated_group_data = Propertygroup.objects.get(id=new_group_obj.id)
            result_data = GroupDataWithPricingDataSerializer(
                new_updated_group_data, many=False
            )
            response_dict = response_handler.success_response(
                result_data.data, status.HTTP_200_OK
            )
            return Response(response_dict, status=status.HTTP_200_OK)

    except Exception as e:
        logger.exception("Error in function clone_group")
        exception_dict = {"message": str(e), "status": 500}
        return Response(exception_dict, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["DELETE"])
@access_authorized_users_only
def delete_group(request, group_id: int) -> Response:
    """
    Deletes a group based on the provided group_id and also deletes the associated pricing data.

    Args:
        request (object): The HTTP request object.
        group_id (int): The ID of the group to delete.

    Returns:
        Response: JSON response indicating the result of the deletion operation.
    """
    try:
        if request.method == "DELETE":
            Propertygroup.objects.filter(id=group_id).delete()
            response_dict = response_handler.msg_response(
                "Group deleted successfully", status.HTTP_200_OK
            )
            return Response(response_dict, status=status.HTTP_200_OK)

    except Exception as e:
        logger.exception("Error in function delete_group")
        exception_dict = {"message": str(e), "status": 500}
        return Response(exception_dict, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@access_authorized_users_only
def create_new_group(request) -> Response:
    """
    Creates a new group and inserts its value into the database.

    Args:
        request (object): The HTTP request object.

    Returns:
        Response: JSON response indicating the result of the creation operation.
    """
    try:
        if request.method == "POST":
            json_data = request.data
            new_group_name = json_data.get("group_name")

            # Check if the group name already exists
            existing_group_names = Propertygroup.objects.values_list(
                "group_name", flat=True
            )
            if new_group_name in existing_group_names:
                response_dict = response_handler.msg_response(
                    "Group name already exists", status.HTTP_422_UNPROCESSABLE_ENTITY
                )
                return Response(
                    response_dict, status=status.HTTP_422_UNPROCESSABLE_ENTITY
                )

            property_grp = Propertygroup(
                group_name=new_group_name, is_self_created=True
            )
            property_grp.save()
            logger.info("Group saved in database successfully.")

            table_grp_row_obj = Propertygroup.objects.get(group_name=new_group_name)
            serializer = GroupSerializer(table_grp_row_obj, many=False)
            response_dict = response_handler.success_response(
                serializer.data, status.HTTP_200_OK
            )
            return Response(response_dict, status=status.HTTP_200_OK)

    except Exception as e:
        logger.exception("Error in function create_new_group")
        exception_dict = {"message": str(e), "status": 500}
        return Response(exception_dict, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@access_authorized_users_only
def get_listing_of_groups(request) -> Response:
    """
    Retrieves the listing (data) of all groups from the database.

    Args:
        request (object): The HTTP request object.

    Returns:
        Response: JSON response containing the group listings.
    """
    try:
        if request.method == "GET":
            table_grp_row_obj = Propertygroup.objects.all()
            serializer = GroupSerializer(table_grp_row_obj, many=True)
            response_dict = response_handler.success_response(
                serializer.data, status.HTTP_200_OK
            )
            return Response(response_dict, status=status.HTTP_200_OK)

    except Exception as e:
        logger.exception("Error in function get_listing_of_groups")
        exception_dict = {"message": str(e), "status": 500}
        return Response(exception_dict, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
