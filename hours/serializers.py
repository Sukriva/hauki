from django.utils.translation import gettext_lazy as _
from django_orghierarchy.models import Organization
from drf_writable_nested import WritableNestedModelSerializer
from enumfields.drf import EnumField, EnumSupportSerializerMixin
from modeltranslation import settings as mt_settings
from modeltranslation.translator import NotRegistered, translator
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from timezone_field.rest_framework import TimeZoneSerializerField

from users.serializers import UserSerializer

from .enums import State
from .fields import TimezoneRetainingDateTimeField
from .models import (
    DataSource,
    DatePeriod,
    PeriodOrigin,
    Resource,
    ResourceOrigin,
    Rule,
    TimeSpan,
    TimeSpanGroup,
)


class TranslationSerializerMixin:
    def to_representation(self, instance):
        result = super().to_representation(instance)

        try:
            translation_options = translator.get_options_for_model(instance.__class__)
        except NotRegistered:
            return result

        fields = self._readable_fields

        for field in fields:
            if field.field_name not in translation_options.fields.keys():
                continue

            new_value = {}
            for lang in mt_settings.AVAILABLE_LANGUAGES:
                key = f"{field.field_name}_{lang}"
                new_value[lang] = getattr(instance, key)

            result[field.field_name] = new_value

        return result

    def to_internal_value(self, data):
        try:
            translation_options = translator.get_options_for_model(self.Meta.model)
        except NotRegistered:
            return super().to_internal_value(data)

        translated_values = {}

        # Add the possibly existing already internalized values to the
        # translated_values variable. Otherwise we would lose the values
        # if to_internal_value is called twice (as is the case with nested serializers).
        for field_name in translation_options.fields.keys():
            for lang in mt_settings.AVAILABLE_LANGUAGES:
                translation_field_name = f"{field_name}_{lang}"
                if translation_field_name in data:
                    translated_values[translation_field_name] = data.get(
                        translation_field_name
                    )

        for field_name in translation_options.fields.keys():
            if field_name not in data.keys():
                continue

            if not isinstance(data.get(field_name), dict):
                continue

            field_values = data.get(field_name, {})

            for lang in mt_settings.AVAILABLE_LANGUAGES:
                if lang not in field_values:
                    continue

                translated_values[f"{field_name}_{lang}"] = field_values[lang]
                # Set the fields also in the initial_data, because the serializer
                # save uses the initial data and not the validated_data when saving
                data[f"{field_name}_{lang}"] = field_values[lang]

            del data[field_name]

        other_values = super().to_internal_value(data)
        other_values.update(**translated_values)

        return other_values


class OrganizationSerializer(serializers.ModelSerializer):
    data_source = serializers.PrimaryKeyRelatedField(read_only=True)
    classification = serializers.PrimaryKeyRelatedField(read_only=True)
    children = serializers.HyperlinkedRelatedField(
        many=True, read_only=True, view_name="organization-detail"
    )

    class Meta:
        model = Organization
        fields = [
            "id",
            "data_source",
            "origin_id",
            "name",
            "classification",
            "parent",
            "children",
            "created_time",
            "last_modified_time",
        ]


class DataSourceSerializer(TranslationSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = DataSource
        fields = ["id", "name"]


class ResourceOriginSerializer(serializers.ModelSerializer):
    data_source = DataSourceSerializer()

    class Meta:
        model = ResourceOrigin
        fields = ["data_source", "origin_id"]


class ResourceSerializer(
    TranslationSerializerMixin,
    EnumSupportSerializerMixin,
    WritableNestedModelSerializer,
):
    last_modified_by = UserSerializer(read_only=True)
    origins = ResourceOriginSerializer(many=True, required=False, allow_null=True)
    timezone = TimeZoneSerializerField(required=False)

    class Meta:
        model = Resource
        fields = [
            "id",
            "name",
            "description",
            "address",
            "resource_type",
            "children",
            "parents",
            "organization",
            "origins",
            "last_modified_by",
            "extra_data",
            "is_public",
            "timezone",
        ]

        read_only_fields = ["last_modified_by"]
        extra_kwargs = {"parents": {"required": False}}

    def validate(self, attrs):
        """Validate that the user is a member or admin of at least one of the
        immediate parent resources organizations"""
        result = super().validate(attrs)

        if not self.context.get("request"):
            return result

        user = self.context["request"].user

        if not user.is_superuser and result.get("parents"):
            users_organizations = user.get_all_organizations()
            if not any(
                [
                    parent.organization in users_organizations
                    for parent in result.get("parents")
                ]
            ):
                raise ValidationError(
                    detail=_(
                        "Cannot create or edit sub resources of a resource "
                        "in an organisation the user is not part of "
                    )
                )

        return result


class ResourceSimpleSerializer(
    TranslationSerializerMixin, EnumSupportSerializerMixin, serializers.ModelSerializer
):
    timezone = TimeZoneSerializerField()
    origins = ResourceOriginSerializer(many=True, required=False, allow_null=True)

    class Meta:
        model = Resource
        fields = [
            "id",
            "name",
            "timezone",
            "origins",
        ]


class TimeSpanCreateSerializer(
    TranslationSerializerMixin, EnumSupportSerializerMixin, serializers.ModelSerializer
):
    class Meta:
        model = TimeSpan
        fields = [
            "id",
            "group",
            "name",
            "description",
            "start_time",
            "end_time",
            "end_time_on_next_day",
            "full_day",
            "weekdays",
            "resource_state",
            "created",
            "modified",
        ]

    def validate(self, attrs):
        if "end_time_on_next_day" in attrs:
            return attrs

        if attrs.get("start_time") and attrs.get("end_time"):
            # Populate end_time_on_next_day field if it's not set
            attrs["end_time_on_next_day"] = attrs["end_time"] <= attrs["start_time"]

        return attrs


class TimeSpanSerializer(TimeSpanCreateSerializer):
    # Group should not be required when saving a nested object
    group = serializers.PrimaryKeyRelatedField(
        required=False, queryset=TimeSpanGroup.objects.all()
    )


class RuleCreateSerializer(
    TranslationSerializerMixin, EnumSupportSerializerMixin, serializers.ModelSerializer
):
    class Meta:
        model = Rule
        fields = [
            "id",
            "group",
            "name",
            "description",
            "context",
            "subject",
            "start",
            "frequency_ordinal",
            "frequency_modifier",
            "created",
            "modified",
        ]


class RuleSerializer(RuleCreateSerializer):
    # Group should not be required when saving a nested object
    group = serializers.PrimaryKeyRelatedField(
        required=False, queryset=TimeSpanGroup.objects.all()
    )


class TimeSpanGroupSerializer(
    EnumSupportSerializerMixin, WritableNestedModelSerializer
):
    period = serializers.PrimaryKeyRelatedField(
        required=False, queryset=DatePeriod.objects.all()
    )
    time_spans = TimeSpanSerializer(many=True, required=False, allow_null=True)
    rules = RuleSerializer(many=True, required=False, allow_null=True)

    class Meta:
        model = TimeSpanGroup
        fields = "__all__"


class PeriodOriginSerializer(serializers.ModelSerializer):
    data_source = DataSourceSerializer()

    class Meta:
        model = PeriodOrigin
        fields = ["data_source", "origin_id"]


class DatePeriodSerializer(
    TranslationSerializerMixin,
    EnumSupportSerializerMixin,
    WritableNestedModelSerializer,
):
    time_span_groups = TimeSpanGroupSerializer(
        many=True, required=False, allow_null=True
    )
    origins = PeriodOriginSerializer(many=True, required=False, allow_null=True)

    class Meta:
        model = DatePeriod
        fields = [
            "id",
            "resource",
            "name",
            "description",
            "start_date",
            "end_date",
            "resource_state",
            "override",
            "origins",
            "created",
            "modified",
            "time_span_groups",
        ]


class TimeElementSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField()
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    end_time_on_next_day = serializers.BooleanField()
    resource_state = EnumField(enum=State)
    full_day = serializers.BooleanField()
    periods = serializers.SerializerMethodField()

    def get_periods(self, obj):
        # Return only period ids for now
        # TODO: what else we would like to see in the API about the periods
        return [period.id for period in obj.periods]


class DailyOpeningHoursSerializer(serializers.Serializer):
    date = serializers.DateField()
    times = TimeElementSerializer(many=True)


class ResourceDailyOpeningHoursSerializer(serializers.Serializer):
    origin_id = serializers.CharField(required=False)
    resource = ResourceSimpleSerializer()
    opening_hours = DailyOpeningHoursSerializer(many=True)


class IsOpenNowSerializer(serializers.Serializer):
    is_open = serializers.BooleanField()
    resource_timezone = TimeZoneSerializerField(required=False)
    resource_time_now = serializers.DateTimeField()
    matching_opening_hours = TimeElementSerializer(many=True)

    other_timezone = TimeZoneSerializerField(required=False)
    other_timezone_time_now = TimezoneRetainingDateTimeField(required=False)
    matching_opening_hours_in_other_tz = TimeElementSerializer(
        many=True, required=False
    )

    resource = ResourceSerializer()
