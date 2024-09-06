from django.shortcuts import render, HttpResponse, redirect
from django.http import JsonResponse, Http404
from django.urls import reverse
from django.views import View
from django.contrib import messages
from datetime import datetime, timedelta
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
import stripe
import json
from itechnolabs.settings import STRIPE_SECRET_KEY
from django.db.models import Q
from home.utils import get_address_details
from requests.exceptions import HTTPError
from home.models import (
    UserModel,
    PropertyModel,
    LoanModel,
    TransactionModel,
    SubscriptionPlan,
    ValuationModel,
    Tenancy,
    Insurance,
    DepreciationModel,
)

# Set the Stripe API key
stripe.api_key = STRIPE_SECRET_KEY


def property_list_context(request) -> dict:
    """
    Generates a context dictionary containing the user's property list and subscription status.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        dict: A dictionary containing 'property_list' and 'have_subscription'.
            - 'property_list' (QuerySet): User's property list with 'name' and 'id' values.
            - 'have_subscription' (bool): User's subscription status (True/False).
    """
    property_list = None
    have_subscription = False

    if request.user.is_authenticated and not request.user.is_superuser:
        property_list = PropertyModel.objects.filter(property_of=request.user).values(
            "name", "id", "is_investment"
        )
        have_subscription = UserModel.objects.get(id=request.user.id).have_subscription

    return {
        "property_list": property_list,
        "property_len": property_list.count() if property_list else 0,
        "have_subscription": have_subscription,
    }


class PropertyAdd(LoginRequiredMixin, UserPassesTestMixin, View):
    """
    View for adding a new property.

    Attributes:
        template (str): The HTML template file for rendering the property addition page.
        login_url (str): The URL to redirect to for login.
    """

    def __init__(self):
        """Constructor method to initialize the PropertyAdd view."""
        self.template = "dashboard/add_property.html"
        self.login_url = reverse("home:login")

    def test_func(self) -> bool:
        """
        Test function to check if the user is not staff and has the required access.

        Returns:
            bool: True if the user has access, False otherwise.
        """
        return (
            not self.request.user.is_staff
            and UserModel.objects.get(id=self.request.user.id).access
        )

    def get(self, request) -> HttpResponse:
        """
        HTTP GET method for rendering the property addition page.

        Args:
            request (HttpRequest): The HTTP request object.

        Returns:
            HttpResponse: The HTTP response containing the rendered property addition page.
        """
        user = UserModel.objects.get(pk=request.user.pk)
        property_count = PropertyModel.objects.filter(property_of=user).count()

        if property_count >= 2 and not user.have_subscription:
            messages.warning(
                request, "To add more properties, please buy a subscription."
            )
            return redirect("/subscribe/")

        if user.subscription and user.subscription.no_of_properties <= property_count:
            upgraded_subscription = (
                SubscriptionPlan.objects.exclude(id=user.subscription.id)
                .filter(no_of_properties__gt=property_count)
                .first()
            )
            if upgraded_subscription:
                messages.error(
                    request,
                    f"To add more properties, please upgrade your subscription to our ${upgraded_subscription.price} plan.",
                )
                return redirect("/subscribe/")

        return render(request, self.template)

    def post(self, request) -> HttpResponse:
        """
        HTTP POST method for processing the property addition form.

        Args:
            request (HttpRequest): The HTTP request object.

        Returns:
            HttpResponse: The HTTP response, either rendering the page with form errors or redirecting to the same page on success.
        """
        property_name = request.POST.get("property_name")
        property_type = request.POST.get("property_type")
        valocity_address_id = request.POST.get("valocity_address_id")

        try:
            property_market_value = get_address_details(valocity_address_id)
        except HTTPError:
            messages.warning(request, "Property not found")
            return render(request, self.template, {"form": request.POST})

        user = UserModel.objects.get(pk=request.user.pk)
        property_list = PropertyModel.objects.filter(property_of=user)
        property_count = property_list.count()
        invested_property_count = property_list.filter(is_investment=True).count()

        if user.have_subscription and user.subscription:
            if (
                property_type == "investment"
                and int(user.subscription.no_of_properties) <= invested_property_count
            ):
                messages.error(
                    request,
                    f"You can only add {user.subscription.no_of_properties} investment properties with your current subscription.",
                )
                return render(request, self.template, {"form": request.POST})

        if not user.have_subscription:
            if (property_type == "investment" and invested_property_count == 1) or (
                property_type != "investment" and property_count >= 2
            ):
                messages.error(
                    request, "To add more properties, please buy a subscription."
                )
                return render(request, self.template, {"form": request.POST})

        if (
            user.subscription
            and int(user.subscription.no_of_properties) <= property_count
        ):
            upgraded_subscription = (
                SubscriptionPlan.objects.exclude(id=user.subscription.id)
                .filter(no_of_properties__gt=property_count)
                .first()
            )
            if upgraded_subscription:
                messages.error(
                    request,
                    f"To add more properties, please upgrade your subscription to our ${upgraded_subscription.price} plan.",
                )
                return render(request, self.template, {"form": request.POST})
            else:
                messages.error(
                    request,
                    "No more subscription plans available, please contact support.",
                )
                return render(request, self.template, {"form": request.POST})

        if PropertyModel.objects.filter(
            property_of=user, valocity_address_id=valocity_address_id
        ).exists():
            messages.error(request, "The same property already exists.")
            return render(request, self.template, {"form": request.POST})

        try:
            new_property = PropertyModel.objects.create(
                property_of=user,
                name=property_name,
                valocity_address_id=valocity_address_id,
                date_of_purchase=request.POST.get("date_of_purchase"),
                is_investment=(property_type == "investment"),
                stamp_duty=request.POST.get("stamp_duty"),
                other_acquisition_cost=request.POST.get("other_acquisition_cost"),
                purchase_price=request.POST.get("purchase_price"),
                rental_income=request.POST.get("rental_income", 0)
                if property_type == "investment"
                else 0,
                current_market_value=property_market_value,
                management_fee=request.POST.get("management_fee", 0),
            )
        except Exception as ex:
            messages.error(request, f"Error while saving property: {ex}")
            return render(request, self.template, {"form": request.POST})

        if property_type == "investment":
            # Add tenancy if all required data is available
            if all(
                element in request.POST
                for element in [
                    "rental_income",
                    "rental_renewal_date",
                    "management_fee",
                ]
            ):
                try:
                    Tenancy.objects.create(
                        tenancy_of=new_property,
                        rental_income=request.POST.get("rental_income"),
                        rental_renewal_date=request.POST.get("rental_renewal_date"),
                        agent_contact_name=request.POST.get("agent_contact_name", ""),
                        agent_contact_mobile=request.POST.get(
                            "agent_contact_mobile", ""
                        ),
                        agent_email=request.POST.get("agent_contact_email", ""),
                        management_fee=request.POST.get("management_fee"),
                    )
                except Exception as ex:
                    new_property.delete()  # Delete property on exception
                    messages.error(request, f"Error while saving tenancy: {ex}")
                    return render(request, self.template, {"form": request.POST})
            else:
                new_property.delete()  # Delete property on exception
                messages.warning(request, "Tenancy data is incorrect or missing.")
                return render(request, self.template, {"form": request.POST})

        # Handle insurance data
        annual_premium = request.POST.get("annual_premium")
        if annual_premium not in ["", None] and request.POST.get(
            "insurance_type"
        ) not in ["", None]:
            if all(
                element in request.POST
                for element in ["insurance_type", "policy_expiry_date"]
            ):
                try:
                    datetime.strptime(request.POST["policy_expiry_date"], "%Y-%m-%d")
                except ValueError:
                    pass
                else:
                    try:
                        Insurance.objects.create(
                            insurance_of=new_property,
                            insurance_type=request.POST.get("insurance_type"),
                            annual_premium=request.POST.get("annual_premium"),
                            policy_expiry_date=request.POST.get("policy_expiry_date"),
                            insurance_broker=request.POST.get("insurance_broker", ""),
                            contact_name=request.POST.get("broker_contact_name", ""),
                            contact_mobile=request.POST.get("contact_mobile", ""),
                            email=request.POST.get("broker_contact_email", ""),
                            set_reminder="set_reminder" in request.POST,
                        )
                    except Exception as ex:
                        new_property.delete()  # Delete property on exception
                        messages.error(request, f"Error while saving insurance: {ex}")
                        return render(request, self.template, {"form": request.POST})

        # Handle loan data
        if "financier_name" in request.POST:
            if all(
                element in request.POST
                for element in [
                    "loan_amount",
                    "initial_deposit",
                    "loan_term_yearly",
                    "loan_term_monthly",
                    "loan_type",
                ]
            ):
                interest_rate = float(request.POST.get("interest_rate", 6.5))
                try:
                    LoanModel.objects.create(
                        loan_of=new_property,
                        loan_name=request.POST.get("financier_name"),
                        loan_amount=request.POST.get("loan_amount"),
                        initial_deposit=request.POST.get("initial_deposit"),
                        interest_rate=interest_rate,
                        loan_term_yearly=request.POST.get("loan_term_yearly"),
                        loan_term_monthly=request.POST.get("loan_term_monthly"),
                        interest_only="Principal Interest"
                        not in request.POST.get("loan_type"),
                        loan_type=request.POST.get("loan_type"),
                    )
                except Exception as ex:
                    new_property.delete()  # Delete property on exception
                    messages.error(request, f"Error while saving loan: {ex}")
                    return render(request, self.template, {"form": request.POST})
            else:
                new_property.delete()  # Delete property on exception
                messages.warning(request, "Mortgage/Loan data is incorrect or missing.")
                return render(request, self.template, {"form": request.POST})

        # Create initial valuation
        ValuationModel.objects.create(
            valuation_of=new_property,
            initial=True,
            amount=property_market_value,
            date=request.POST.get("date_of_purchase"),
            property_value="itechnolabs",
        )

        messages.success(
            request,
            f'Property {request.POST.get("property_name", None)} added successfully.',
        )
        return redirect(request.path)


class PropertyView(LoginRequiredMixin, UserPassesTestMixin, View):
    """
    A view to display property details and valuation information.

    Attributes:
        template_name (str): The template file path for rendering the view.
        login_url (str): The URL to redirect users to if they are not authenticated.
    """

    def __init__(self):
        self.template = "dashboard/view_property.html"
        self.login_url = reverse("home:login")

    def test_func(self) -> bool:
        """Check if the user has access permissions."""
        return (
            not self.request.user.is_staff
            and UserModel.objects.get(id=self.request.user.id).access
        )

    def get(self, request, property_id: int) -> HttpResponse:
        """
        Handle GET requests to display property details and valuation information.

        Args:
            request (HttpRequest): The HTTP request object.
            property_id (int): The ID of the property to be viewed.

        Returns:
            HttpResponse: The HTTP response containing the rendered property details page.
        """
        try:
            property_instance = PropertyModel.objects.get(pk=property_id)
            initial_valuation = ValuationModel.objects.filter(
                valuation_of=property_instance.id, initial=True
            ).first()
            user = UserModel.objects.get(id=request.user.id)
            have_subscription = user.have_subscription
        except PropertyModel.DoesNotExist:
            raise Http404

        current_year = datetime.now().year
        current_year_str = str(current_year)[-2:]
        fiscal_years = [f"FY{int(current_year_str) + i}" for i in range(10)]

        context = {
            "have_subscription": have_subscription,
            "property": property_instance,
            "graph_x_labels": fiscal_years,
            "initial_valuation": initial_valuation,
        }
        return render(request, self.template, context)


class TransactionAdd(LoginRequiredMixin, UserPassesTestMixin, View):
    """A view for adding transactions related to properties."""

    def __init__(self):
        self.template = "dashboard/add_transaction.html"
        self.login_url = reverse("home:login")

    def test_func(self) -> bool:
        """Check if the user has access permissions."""
        return (
            not self.request.user.is_staff
            and UserModel.objects.get(id=self.request.user.id).access
        )

    def get(self, request) -> HttpResponse:
        """
        Handle GET requests to render the transaction form.

        Args:
            request (HttpRequest): The HTTP request object.

        Returns:
            HttpResponse: The HTTP response containing the rendered transaction form.
        """
        selected_property = request.GET.get("selected_property")
        if selected_property:
            return render(
                request, self.template, {"selected_property": int(selected_property)}
            )
        return render(request, self.template)

    def post(self, request) -> JsonResponse:
        """
        Handle POST requests to add transactions.

        Args:
            request (HttpRequest): The HTTP request object.

        Returns:
            JsonResponse: The JSON response indicating the success status of the transaction addition.
        """
        # Assuming the form data is posted as JSON in the request body
        data = json.loads(request.body)
        property_id = int(data.get("property_id"))

        try:
            property_instance = PropertyModel.objects.get(id=property_id)
        except PropertyModel.DoesNotExist:
            return JsonResponse({"success": False}, safe=True, status=404)

        transactions = []
        for transaction in data["transactions"]:
            date_str = transaction.get("date", "")
            custom_text = transaction.get("customText", "")
            type_of_entry = transaction.get("typeOfEntry", "")

            if type_of_entry == "Other":
                type_of_entry = f"Other - {custom_text}" if custom_text else "Other"

            formatted_date = (
                datetime.strptime(date_str, "%Y-%m-%d")
                if date_str and type_of_entry != "Other"
                else None
            )

            if formatted_date is not None:
                existing_entry = TransactionModel.objects.filter(
                    Q(transaction_of=property_instance)
                    & Q(date__gte=formatted_date)
                    & Q(date__lt=formatted_date + timedelta(days=1))
                    & Q(type_of_entry=type_of_entry)
                ).first()
            else:
                existing_entry = None

            txn_data = {
                "transaction_of": property_instance,
                "type_of_entry": type_of_entry,
                "date": formatted_date,
                "invoice_ref": transaction.get("invoice_ref"),
                "amount": transaction.get("amount", 0),
                "comment": transaction.get("comment", ""),
            }

            if existing_entry:
                existing_entry.amount = txn_data["amount"]
                existing_entry.comment = txn_data["comment"]
                existing_entry.save()
            else:
                txn_instance = TransactionModel(**txn_data)
                transactions.append(txn_instance)

        TransactionModel.objects.bulk_create(transactions)
        return JsonResponse({"success": True}, safe=True)


class DepreciationAdd(LoginRequiredMixin, UserPassesTestMixin, View):
    """A view for adding depreciation schedules."""

    def __init__(self):
        self.template = "dashboard/add_depreciation_schedule.html"
        self.login_url = reverse("home:login")

    def test_func(self) -> bool:
        """Check if the user has access permissions."""
        return (
            not self.request.user.is_staff
            and UserModel.objects.get(id=self.request.user.id).access
        )

    def get(self, request) -> HttpResponse:
        """
        Handle GET requests to render the depreciation schedule form.

        Args:
            request (HttpRequest): The HTTP request object.

        Returns:
            HttpResponse: The HTTP response containing the rendered depreciation schedule form.
        """
        selected_property = request.GET.get("selected_property")
        if selected_property:
            return render(
                request, self.template, {"selected_property": int(selected_property)}
            )
        return render(request, self.template)

    def post(self, request) -> JsonResponse:
        """
        Handle POST requests to add depreciation schedules.

        Args:
            request (HttpRequest): The HTTP request object.

        Returns:
            JsonResponse: The JSON response indicating the success status of the depreciation schedule addition.
        """
        data = json.loads(request.body)
        property_id = int(data.get("propertyId"))

        try:
            property_instance = PropertyModel.objects.get(id=property_id)
        except PropertyModel.DoesNotExist:
            return JsonResponse({"success": False}, status=404)

        schedule_array = json.loads(data.get("schedule_array"))
        for schedule in schedule_array:
            schedule_year = int(schedule["year"])
            entry_date = datetime(schedule_year, 1, 1)

            depreciation_obj, dep_created = DepreciationModel.objects.update_or_create(
                depreciation_of=property_instance,
                date=entry_date,
                type_of_entry=data.get("typeOfEntry"),
                defaults={
                    "amount": schedule["amount"],
                    "description": f'{data.get("description")}',
                },
            )

            transaction_obj, txn_created = TransactionModel.objects.update_or_create(
                transaction_of=property_instance,
                type_of_entry=f'Depreciation schedule payment for year {schedule_year - 1} - {schedule_year} {data.get("typeOfEntry")}',
                date=entry_date,
                invoice_ref="-",
                depreciationInstance=depreciation_obj,
                defaults={
                    "amount": schedule["amount"],
                    "comment": f'{data.get("description")}',
                },
            )

        return JsonResponse({"success": True})


class Settings(LoginRequiredMixin, UserPassesTestMixin, View):
    """A view for managing user settings."""

    def __init__(self):
        """Initializes the Settings instance."""
        self.template = "dashboard/settings.html"
        self.login_url = reverse("home:login")

    def test_func(self) -> bool:
        """Check if the user has access permissions."""
        return (
            not self.request.user.is_staff
            and UserModel.objects.get(id=self.request.user.id).access
        )

    def get(self, request) -> HttpResponse:
        """
        Handle GET requests to render the settings page.

        Args:
            request (HttpRequest): The HTTP request object.

        Returns:
            HttpResponse: The HTTP response containing the rendered settings page.
        """
        context = {
            "register_via": UserModel.objects.get(id=request.user.id).register_via,
            "subscription": UserModel.objects.get(id=request.user.id).subscription,
        }
        return render(request, self.template, context=context)
