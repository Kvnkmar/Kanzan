"""
django-filter FilterSets for the contacts app.

Provides structured filtering for Company and Contact list endpoints,
working alongside DRF's SearchFilter and OrderingFilter.
"""

import django_filters

from apps.contacts.models import Company, Contact


class CompanyFilter(django_filters.FilterSet):
    """
    FilterSet for Company list views.

    Supports filtering by:
    - industry: exact match
    - size: exact match (small, medium, large, enterprise)
    """

    industry = django_filters.CharFilter(lookup_expr="iexact")
    size = django_filters.ChoiceFilter(choices=Company.Size.choices)

    class Meta:
        model = Company
        fields = ["industry", "size"]


class ContactFilter(django_filters.FilterSet):
    """
    FilterSet for Contact list views.

    Supports filtering by:
    - company: UUID of the associated company
    - source: acquisition source (web, email, phone, referral, social, other)
    - is_active: boolean active status
    - created_after / created_before: date range on created_at
    """

    company = django_filters.UUIDFilter(field_name="company__id")
    source = django_filters.ChoiceFilter(choices=Contact.Source.choices)
    is_active = django_filters.BooleanFilter()
    created_after = django_filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
    )
    created_before = django_filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
    )

    class Meta:
        model = Contact
        fields = [
            "company",
            "source",
            "is_active",
            "created_after",
            "created_before",
        ]
