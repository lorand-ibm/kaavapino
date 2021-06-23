from django.contrib.auth.models import Group
from helusers.models import AbstractUser
from django.db import models
from django.utils.translation import ugettext_lazy as _


# TODO: hard-coded for the MVP, migrate into a separate model when needed
# Available privilege levels in ascending order
PRIVILEGE_LEVELS = (
    (None, 'Ei roolia'),
    ('browse', 'Selaaja'),
    ('edit', 'Asiantuntija'),
    ('create', 'Vastuuhenkilö'),
    ('admin', 'Pääkäyttäjä'),
)

def privilege_as_int(privilege):
    levels = [privilege[0] for privilege in PRIVILEGE_LEVELS]
    try:
        return levels.index(privilege)
    except ValueError:
        return -1

def privilege_as_label(name):
    return {
        name: None,
        **{p[0]: p[1] for p in PRIVILEGE_LEVELS}
    }[name]


class User(AbstractUser):
    additional_groups = models.ManyToManyField(
        Group,
        verbose_name=_("additional groups"),
        related_name="additional_users",
        blank=True,
    )

    def is_in_group(self, group):
        if isinstance(group, Group):
            return self.groups.filter(group=group).exists()
        return self.groups.filter(name=group).exists()

    def is_in_any_of_groups(self, groups):
        if not groups:
            return False

        if isinstance(groups[0], Group):
            return self.groups.filter(group__in=groups).exists()
        return self.groups.filter(name__in=groups).exists()

    @property
    def all_groups(self):
        return self.groups.all().union(self.additional_groups.all())

    @property
    def privilege(self):
        privileges = tuple(p for (p, _) in PRIVILEGE_LEVELS)
        user_privilege = None

        for group in self.groups.all().union(self.additional_groups.all()):
            try:
                privilege_name = group.groupprivilege.privilege_level
                group_privilege = privileges.index(privilege_name)

                if group_privilege > privileges.index(user_privilege):
                    user_privilege = privileges[group_privilege]

            except AttributeError:
                pass
            except TypeError:
                pass

        return user_privilege

    def has_privilege(self, target_privilege):
        privileges = tuple(p for (p, _) in PRIVILEGE_LEVELS)
        user_privilege = privileges.index(self.privilege)

        try:
            return privileges.index(target_privilege) <= user_privilege
        except ValueError:
            return False


class GroupPrivilege(models.Model):
    group = models.OneToOneField( \
        Group, primary_key=True, on_delete=models.CASCADE)
    privilege_level = models.CharField( \
        default=None, null=True, max_length=6, choices=PRIVILEGE_LEVELS)

    @property
    def as_int(self):
        return privilege_as_int(self.privilege_level)

    def __str__(self):
        return '%s: %s' % (
            self.group.name,
            dict(PRIVILEGE_LEVELS)[self.privilege_level],
        )
