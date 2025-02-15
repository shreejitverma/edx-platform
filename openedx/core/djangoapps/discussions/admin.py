"""
Customize the django admin experience
"""
from django.contrib import admin
from django.contrib.admin import SimpleListFilter
from simple_history.admin import SimpleHistoryAdmin

from openedx.core.djangoapps.config_model_utils.admin import StackedConfigModelAdmin

from .forms import ProgramDiscussionsConfigurationForm
from .models import DiscussionsConfiguration, ProgramDiscussionsConfiguration
from .models import ProviderFilter


class DiscussionsConfigurationAdmin(SimpleHistoryAdmin):
    """
    Customize the admin interface for the discussions configuration
    """

    search_fields = (
        'context_key',
        'enabled',
        'provider_type',
    )
    list_filter = (
        'enabled',
        'provider_type',
    )


class ProgramDiscussionsConfigurationAdmin(SimpleHistoryAdmin):
    """
    Customize the admin interface for the program discussions configuration
    """
    form = ProgramDiscussionsConfigurationForm

    fieldsets = (
        (None, {
            'fields': ('program_uuid', 'enabled', 'lti_configuration', 'pii_share_username', 'pii_share_email',
                       'provider_type'),
        }),
    )

    search_fields = (
        'program_uuid',
        'enabled',
        'provider_type',
    )
    list_filter = (
        'enabled',
        'provider_type',
    )


class AllowListFilter(SimpleListFilter):
    """
    Customize the admin interface for the AllowList
    """

    title = 'Allow List'
    parameter_name = 'allow'

    def lookups(self, request, model_admin):
        queryset = model_admin.get_queryset(request)
        values = tuple(
            (
                ','.join(filters[self.parameter_name] or ['None']),
                ', '.join(filters[self.parameter_name] or ['None']),
            )
            for filters in queryset.values(self.parameter_name).distinct()
        )
        return values

    def queryset(self, request, queryset):
        value = self.value()
        if value:
            filter_kwargs = {}
            if ',' in value:
                for v in value.split(','):
                    filter_kwargs[self.parameter_name + '__contains'] = v
                    queryset = queryset.filter(**filter_kwargs)
            else:
                if value == 'None':
                    filter_kwargs[self.parameter_name + '__exact'] = ''
                else:
                    filter_kwargs[self.parameter_name + '__contains'] = value
                queryset = queryset.filter(**filter_kwargs)
        return queryset


class DenyListFilter(AllowListFilter):
    """
    Customize the admin interface for the DenyList
    """
    title = 'Deny List'
    parameter_name = 'deny'


class ProviderFilterAdmin(StackedConfigModelAdmin):
    """
    Customize the admin interface for the ProviderFilter
    """

    search_fields = (
        'allow',
        'deny',
    )
    list_filter = (
        'enabled',
        AllowListFilter,
        DenyListFilter,
    )


admin.site.register(DiscussionsConfiguration, DiscussionsConfigurationAdmin)
admin.site.register(ProgramDiscussionsConfiguration, ProgramDiscussionsConfigurationAdmin)
admin.site.register(ProviderFilter, ProviderFilterAdmin)
