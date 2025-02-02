from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.contrib.auth.models import Group

from helusers.admin import admin
from helusers.models import ADGroupMapping

from .models import User, GroupPrivilege


admin.site.unregister(Group)

@admin.register(User)
class UserAdmin(UserAdmin):
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "additional_groups":
            user = User.objects.get(pk=request.resolver_match.kwargs['object_id'])
            kwargs["queryset"] = (
                user.additional_groups.all() | \
                Group.objects.exclude(user=user)
            ).distinct().order_by("name")
        return super(UserAdmin, self).formfield_for_manytomany(db_field, request, **kwargs)

    fieldsets = UserAdmin.fieldsets
    for fieldset in fieldsets:
        if "groups" in fieldset[1]["fields"]:
            fieldlist = list(fieldset[1]["fields"])
            fieldlist += ["additional_groups"]
            fieldlist.remove("user_permissions")
            fieldset[1]["fields"] = tuple(fieldlist),
            break

    filter_horizontal = UserAdmin.filter_horizontal + ("additional_groups",)
    readonly_fields = UserAdmin.readonly_fields + (
        "groups",
        "date_joined",
        "last_login",
        "first_name",
        "last_name",
        "is_staff",
        "is_superuser",
    )


class GroupPrivilegeInline(admin.TabularInline):
    model = GroupPrivilege
    can_delete = False

class ADGroupMappingInline(admin.TabularInline):
    model = ADGroupMapping
    extra = 0

@admin.register(Group)
class GroupAdmin(GroupAdmin):
    exclude = ('permissions',)
    inlines = [
        ADGroupMappingInline,
        GroupPrivilegeInline,
    ]
