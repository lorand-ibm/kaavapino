import datetime
import itertools

from actstream import action
from django.conf import settings
from django.contrib.gis.db import models
from django.contrib.postgres.fields import JSONField, ArrayField
from django.core.serializers.json import DjangoJSONEncoder, json
from django.db import transaction
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from private_storage.fields import PrivateFileField
from PIL import Image

from projects.actions import verbs
from projects.models.utils import KaavapinoPrivateStorage, arithmetic_eval
from .attribute import Attribute, FieldSetAttribute
from .deadline import Deadline
from .projectcomment import FieldComment


class BaseAttributeMatrixStructure(models.Model):
    column_names = ArrayField(models.CharField(max_length=255))
    row_names = ArrayField(models.CharField(max_length=255))

    class Meta:
        abstract = True


class BaseAttributeMatrixCell(models.Model):
    row = models.IntegerField()
    column = models.IntegerField()

    class Meta:
        abstract = True


class ProjectType(models.Model):
    """Types of projects that the system supports e.g. asemakaava/city plan."""

    name = models.CharField(max_length=255, verbose_name=_("name"))
    metadata = JSONField(
        verbose_name=_("metadata"),
        default=dict,
        blank=True,
        null=True,
        encoder=DjangoJSONEncoder,
    )

    class Meta:
        verbose_name = _("project type")
        verbose_name_plural = _("project types")
        ordering = ("name",)

    def __str__(self):
        return self.name


class ProjectSubtype(models.Model):
    project_type = models.ForeignKey(
        ProjectType,
        verbose_name=_("project type"),
        on_delete=models.CASCADE,
        related_name="subtypes",
    )

    name = models.CharField(max_length=255, verbose_name=_("name"))
    metadata = JSONField(
        verbose_name=_("metadata"),
        default=dict,
        blank=True,
        null=True,
        encoder=DjangoJSONEncoder,
    )
    index = models.PositiveIntegerField(verbose_name=_("index"), default=0)

    class Meta:
        verbose_name = _("project subtype")
        verbose_name_plural = _("project subtypes")
        ordering = ("index",)

    def __str__(self):
        return self.name


class Project(models.Model):
    """Represents a single project in the system."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("user"),
        related_name="projects",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(
        verbose_name=_("created at"), auto_now_add=True, editable=False
    )
    modified_at = models.DateTimeField(
        verbose_name=_("modified at"), auto_now=True, editable=False
    )
    name = models.CharField(
        verbose_name=_("name"),
        max_length=255,
        unique=True,
    )
    identifier = models.CharField(
        max_length=50,
        verbose_name=_("identifier"),
        db_index=True,
        blank=True,
        null=True,
    )
    pino_number = models.CharField(
        max_length=7,
        verbose_name=_("pino number"),
        unique=True,
        blank=True,
        null=True,
    )
    subtype = models.ForeignKey(
        ProjectSubtype,
        verbose_name=_("subtype"),
        related_name="projects",
        on_delete=models.PROTECT,
    )
    attribute_data = JSONField(
        verbose_name=_("attribute data"),
        default=dict,
        blank=True,
        null=True,
        encoder=DjangoJSONEncoder,
    )
    deadlines = models.ManyToManyField(
        "ProjectDeadline",
        verbose_name=_("deadlines"),
        related_name="projects",
    )
    phase = models.ForeignKey(
        "ProjectPhase",
        verbose_name=_("phase"),
        null=True,
        related_name="projects",
        on_delete=models.PROTECT,
    )
    create_principles = models.BooleanField(
        verbose_name=_("create principles"),
        default=False,
    )
    create_draft = models.BooleanField(
        verbose_name=_("create draft"),
        default=False,
    )
    public = models.BooleanField(default=True)
    archived = models.BooleanField(default=False)
    onhold = models.BooleanField(default=False)
    owner_edit_override = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("project")
        verbose_name_plural = _("projects")
        ordering = ("name",)

    def __str__(self):
        return self.name

    def get_attribute_data(self):
        """Returns deserialized attribute data for the project."""
        ret = {}

        for attribute in Attribute.objects.all().prefetch_related("value_choices"):
            deserialized_value = None

            if attribute.value_type == Attribute.TYPE_GEOMETRY:
                geometry = ProjectAttributeMultipolygonGeometry.objects.filter(
                    attribute=attribute, project=self
                ).first()
                if not geometry:
                    continue
                deserialized_value = geometry.geometry
            elif attribute.value_type in [Attribute.TYPE_IMAGE, Attribute.TYPE_FILE]:
                try:
                    deserialized_value = ProjectAttributeFile.objects.filter(
                        attribute=attribute, project=self
                    ).order_by("-created_at").first().file
                except AttributeError:
                    deserialized_value = None
            elif attribute.identifier in self.attribute_data:
                deserialized_value = attribute.deserialize_value(
                    self.attribute_data[attribute.identifier]
                )

            ret[attribute.identifier] = deserialized_value
        return ret

    def set_attribute_data(self, data):
        self.attribute_data = {}
        self.update_attribute_data(data)

    def update_attribute_data(self, data):
        if not isinstance(self.attribute_data, dict):
            self.attribute_data = {}

        if not data:
            return False

        phase_section_attrs = Attribute.objects.filter(
            phase_sections__phase__project_subtype__projects=self
        )
        floor_area_section_attrs = Attribute.objects.filter(
            floor_area_sections__project_subtype__projects=self
        )
        deadline_attrs = Attribute.objects.filter(
            deadline__in=Deadline.objects.filter(subtype=self.subtype)
        )
        project_attributes = (
            Attribute.objects.all()
            .distinct()
            .prefetch_related("value_choices")
        )

        generated_attributes = project_attributes.filter(generated=True)

        attributes = {a.identifier: a for a in project_attributes}

        for identifier, value in data.items():
            attribute = attributes.get(identifier)

            if not attribute:
                continue

            if attribute.value_type == Attribute.TYPE_GEOMETRY:
                geometry_query_params = {"attribute": attribute, "project": self}
                if not value:
                    ProjectAttributeMultipolygonGeometry.objects.filter(
                        **geometry_query_params
                    ).delete()
                else:
                    ProjectAttributeMultipolygonGeometry.objects.update_or_create(
                        **geometry_query_params, defaults={"geometry": value}
                    )
            elif attribute.value_type in [Attribute.TYPE_IMAGE, Attribute.TYPE_FILE]:
                if not value:
                    self.attribute_data.pop(identifier, None)
            elif attribute.value_type == Attribute.TYPE_FIELDSET:
                serialized_value = attribute.serialize_value(value)
                if not serialized_value:
                    self.attribute_data.pop(identifier, None)
                else:
                    self.attribute_data[identifier] = serialized_value
            else:
                serialized_value = attribute.serialize_value(value)

                if serialized_value is not None:
                    self.attribute_data[identifier] = serialized_value
                else:
                    self.attribute_data.pop(identifier, None)

        return True

    # TODO disabled for now; frontend generates and sends values but we need to develop this later
    def update_generated_values(self, generated_attributes, attribute_data):
        for attribute in generated_attributes:
            calculation_operators = attribute.calculation_operators
            attribute_values = [
                attribute_data.get(identifier, 0) or 0
                for identifier in attribute.calculation_attribute_identifiers
            ]

            calculation_string = "".join(
                [
                    str(value) + (operator or "")
                    for value, operator in itertools.zip_longest(
                        attribute_values, calculation_operators
                    )
                ]
            )

            try:
                calculated_value = arithmetic_eval(calculation_string)
            except (ValueError, KeyError, ZeroDivisionError):
                # Value errors are thrown for
                calculated_value = 0

            attribute_data[attribute.identifier] = calculated_value

    def _check_condition(self, deadline):
        if not deadline.condition_attributes.count():
            return True

        for attr in deadline.condition_attributes.all():
            if bool(self.attribute_data.get(attr.identifier, None)):
                return True

        return False

    def _get_applicable_deadlines(self, subtype=None):
        excluded_phases = []

        # TODO hard-coded, maybe change later
        if not self.create_principles:
            excluded_phases.append("Periaatteet")

        if not self.create_draft:
            excluded_phases.append("Luonnos")

        return [
            deadline
            for deadline in list(
                Deadline.objects \
                    .filter(subtype=subtype or self.subtype) \
                    .exclude(phase__name__in=excluded_phases)
            )
            if self._check_condition(deadline)
        ]

    def _set_calculated_deadline(self, deadline, date, initial, user, preview):
        try:
            project_deadline = self.deadlines.get(deadline=deadline)
        except ProjectDeadline.DoesNotExist:
            return

        if project_deadline and date:
            if preview:
                return date

            project_deadline.date = date
            project_deadline.save()

            if deadline.attribute:
                with transaction.atomic():
                    old_value = json.loads(json.dumps(
                        self.attribute_data.get(deadline.attribute.identifier),
                        default=str,
                    ))
                    new_value = json.loads(json.dumps(date, default=str))

                    self.update_attribute_data( \
                        {deadline.attribute.identifier: date})
                    self.save()
                    if old_value != new_value:
                        action.send(
                            user or self.user,
                            verb=verbs.UPDATED_ATTRIBUTE,
                            action_object=deadline.attribute,
                            target=self,
                            attribute_identifier=deadline.attribute.identifier,
                            old_value=old_value,
                            new_value=new_value,
                        )

            return date

    def _set_calculated_deadlines(self, deadlines, user, ignore=[], initial=False, preview=False):
        results = {}

        for deadline in deadlines:
            if initial:
                calculate_deadline = deadline.calculate_initial
                dependencies = [
                    dl for dl in deadline.initial_depends_on
                    if dl not in ignore
                ]
            else:
                calculate_deadline = deadline.calculate_updated
                dependencies = [
                    dl for dl in deadline.update_depends_on
                    if dl not in ignore
                ]

            if dependencies:
                ignore += dependencies
                self._set_calculated_deadlines(
                    dependencies,
                    user,
                    ignore=ignore,
                    initial=initial,
                )

            results[deadline] = self._set_calculated_deadline(
                deadline,
                calculate_deadline(self),
                initial,
                user,
                preview,
            )

        return results

    # Generate or update schedule for project
    def update_deadlines(self, values=None, user=None):
        deadlines = self._get_applicable_deadlines()

        # Delete no longer relevant deadlines and create missing
        self.deadlines.exclude(deadline__in=deadlines).delete()
        generated_deadlines = []
        project_deadlines = list(self.deadlines.all())

        for deadline in deadlines:
            project_deadline, created = ProjectDeadline.objects.get_or_create(
                project=self,
                deadline=deadline,
                defaults={
                    "generated": True,
                }
            )
            if created:
                generated_deadlines.append(project_deadline)
                project_deadlines.append(project_deadline)

        self.deadlines.set(project_deadlines)

        # Update attribute-based deadlines
        for dl in self.deadlines.all():
            if not dl.deadline.attribute:
                continue

            value = self.attribute_data.get(dl.deadline.attribute.identifier)

            if value:
                dl.date = value
                dl.save()

        # Calculate automatic values for newly added deadlines
        self._set_calculated_deadlines(
            [
                dl.deadline for dl in generated_deadlines
                if dl.deadline.initial_calculations.count() \
                    or dl.deadline.default_to_created_at
            ],
            user,
            initial=True,
        )

        # Update automatic deadlines
        self._set_calculated_deadlines(
            [
                dl.deadline for dl in project_deadlines
                if dl.deadline.update_calculations.count() \
                    or dl.deadline.default_to_created_at \
                    or dl.deadline.attribute
            ],
            user,
            initial=False,
        )

    # Calculate a preview schedule without saving anything
    def get_preview_deadlines(self, updated_attributes, subtype):
        # Filter out deadlines that would be deleted
        project_dls = {
            dl.deadline: dl.date
            for dl in self.deadlines.all()
            if dl.deadline.subtype == subtype
        }

        # List deadlines that would be created
        new_dls = {
            dl: None
            for dl in self._get_applicable_deadlines(subtype=subtype)
        }

        project_dls = {**new_dls, **project_dls}

        # Update attribute-based deadlines
        for dl in project_dls.keys():
            if not dl.attribute:
                continue

            value = self.attribute_data.get(dl.attribute.identifier)

            if value:
                project_dls[dl] = value

        # Generate newly added deadlines
        project_dls = {**project_dls, **self._set_calculated_deadlines(
            [
                dl for dl in new_dls.keys()
                if dl.initial_calculations.count() \
                    or dl.default_to_created_at
            ],
            None,
            initial=True,
            preview=True,
        )}

        # Update all deadlines
        project_dls = {**project_dls, **self._set_calculated_deadlines(
            [
                dl for dl in project_dls
                if dl.update_calculations.count() \
                    or dl.default_to_created_at \
                    or dl.attribute
            ],
            None,
            initial=False,
            preview=True,
        )}

        return project_dls

    @property
    def type(self):
        return self.subtype.project_type

    def save(self, *args, **kwargs):
        super(Project, self).save(*args, **kwargs)
        if not self.pino_number:
            self.pino_number = str(self.pk).zfill(7)
            self.save()


class ProjectFloorAreaSection(models.Model):
    """Defines a floor area data section."""

    project_subtype = models.ForeignKey(
        ProjectSubtype,
        verbose_name=_("project subtype"),
        on_delete=models.CASCADE,
        related_name="floor_area_sections",
    )
    name = models.CharField(max_length=255, verbose_name=_("name"))
    index = models.PositiveIntegerField(verbose_name=_("index"), default=0)
    attributes = models.ManyToManyField(
        Attribute,
        verbose_name=_("attributes"),
        related_name="floor_area_sections",
        through="ProjectFloorAreaSectionAttribute",
    )

    class Meta:
        verbose_name = _("project floor area section")
        verbose_name_plural = _("project floor area sections")
        ordering = ("index",)

    def __str__(self):
        return f"{self.name} ({self.project_subtype.name})"

    def get_attribute_identifiers(self):
        return [a.identifier for a in self.attributes.all()]


class ProjectFloorAreaSectionAttribute(models.Model):
    """Links an attribute into a project floor area section."""

    attribute = models.ForeignKey(
        Attribute, verbose_name=_("attribute"), on_delete=models.CASCADE
    )
    section = models.ForeignKey(
        ProjectFloorAreaSection,
        verbose_name=_("floor area section"),
        on_delete=models.CASCADE,
    )
    index = models.PositiveIntegerField(verbose_name=_("index"), default=0)

    relies_on = models.ForeignKey(
        "self",
        verbose_name=_("relies on"),
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = _("project floor area section attribute")
        verbose_name_plural = _("project floor area section attributes")
        ordering = ("index",)

    def __str__(self):
        return f"{self.attribute} {self.section} {self.index}"


class ProjectFloorAreaSectionAttributeMatrixStructure(BaseAttributeMatrixStructure):
    section = models.ForeignKey(
        ProjectFloorAreaSection, verbose_name=_("phase section"), on_delete=models.CASCADE
    )

    def __str__(self):
        return f"{self.section} ({len(self.row_names)}x{len(self.column_names)})"


class ProjectFloorAreaSectionAttributeMatrixCell(BaseAttributeMatrixCell):
    attribute = models.ForeignKey(
        ProjectFloorAreaSectionAttribute, on_delete=models.CASCADE
    )
    structure = models.ForeignKey(
        ProjectFloorAreaSectionAttributeMatrixStructure, on_delete=models.CASCADE
    )

    def __str__(self):
        return f"{self.structure} {self.attribute} ({self.row}, {self.column})"


class ProjectPhase(models.Model):
    """Describes a phase of a certain project subtype."""

    project_subtype = models.ForeignKey(
        ProjectSubtype,
        verbose_name=_("project subtype"),
        on_delete=models.CASCADE,
        related_name="phases",
    )
    name = models.CharField(max_length=255, verbose_name=_("name"))
    color = models.CharField(max_length=64, verbose_name=_("color"), blank=True)
    color_code = models.CharField(
        max_length=10, verbose_name=_("color code"), blank=True
    )
    list_prefix = models.CharField(
        max_length=2, verbose_name=_("list prefix"), blank=True, null=True
    )
    index = models.PositiveIntegerField(verbose_name=_("index"), default=0)

    metadata = JSONField(
        verbose_name=_("metadata"),
        default=dict,
        blank=True,
        null=True,
        encoder=DjangoJSONEncoder,
    )

    class Meta:
        verbose_name = _("project phase")
        verbose_name_plural = _("project phases")
        ordering = ("index",)

    def __str__(self):
        return f"{self.name} ({self.project_subtype.name})"

    @property
    def project_type(self):
        return self.project_subtype.project_type


class ProjectPhaseLog(models.Model):
    """Records project phase changes."""

    project = models.ForeignKey(
        "Project",
        verbose_name=_("project"),
        related_name="phase_logs",
        on_delete=models.CASCADE,
    )
    phase = models.ForeignKey(
        ProjectPhase,
        verbose_name=_("phase"),
        related_name="phase_logs",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("user"),
        related_name="phase_logs",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(
        verbose_name=_("created at"), auto_now_add=True, editable=False
    )

    class Meta:
        verbose_name = _("project phase log entry")
        verbose_name_plural = _("project phase log entries")
        ordering = ("created_at",)

    def __str__(self):
        return f"{self.project.name} {self.phase.name} {self.created_at}"


class ProjectPhaseSection(models.Model):
    """Defines a section within a project phase."""

    phase = models.ForeignKey(
        ProjectPhase,
        verbose_name=_("phase"),
        related_name="sections",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255, verbose_name=_("name"))
    index = models.PositiveIntegerField(verbose_name=_("index"), default=0)
    attributes = models.ManyToManyField(
        Attribute,
        verbose_name=_("attributes"),
        related_name="phase_sections",
        through="ProjectPhaseSectionAttribute",
    )

    class Meta:
        verbose_name = _("project phase section")
        verbose_name_plural = _("project phase sections")
        ordering = ("index",)

    def __str__(self):
        return f"{self.name} ({self.phase.name}, {self.phase.project_subtype.name})"

    def get_attribute_identifiers(self):
        return [a.identifier for a in self.attributes.all()]


class ProjectPhaseSectionAttribute(models.Model):
    """Links an attribute into a project phase section."""

    attribute = models.ForeignKey(
        Attribute, verbose_name=_("attribute"), on_delete=models.CASCADE
    )
    section = models.ForeignKey(
        ProjectPhaseSection, verbose_name=_("phase section"), on_delete=models.CASCADE
    )
    index = models.PositiveIntegerField(verbose_name=_("index"), default=0)

    relies_on = models.ForeignKey(
        "self",
        verbose_name=_("relies on"),
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = _("project phase section attribute")
        verbose_name_plural = _("project phase section attributes")
        ordering = ("index",)

    def __str__(self):
        return f"{self.attribute} {self.section} {self.section.phase} {self.index}"


class ProjectPhaseFieldSetAttributeIndex(models.Model):
    index = models.PositiveIntegerField(verbose_name=_("index"), default=0)
    phase = models.ForeignKey(
        ProjectPhase, on_delete=models.CASCADE
    )
    attribute = models.ForeignKey(
        FieldSetAttribute, on_delete=models.CASCADE
    )
    unique_together = ("attribute", "phase")

    def __str__(self):
        return f"{self.attribute} {self.phase} {self.index}"


class ProjectAttributeFile(models.Model):
    """Project attribute value that is an file."""

    attribute = models.ForeignKey(
        Attribute,
        verbose_name=_("attribute"),
        related_name="files",
        on_delete=models.CASCADE,
    )
    project = models.ForeignKey(
        Project,
        verbose_name=_("project"),
        related_name="files",
        on_delete=models.CASCADE,
    )
    description = models.TextField(verbose_name=_("description"), null=True, blank=True)
    created_at = models.DateTimeField(
        verbose_name=_("created at"),auto_now_add=True, editable=False
    )
    archived_at = models.DateTimeField(
        verbose_name=_("archived at"),
        null=True,
        blank=True,
    )
    fieldset_path_str = models.TextField(
        verbose_name=_("fieldset path string"),
        null=True,
        blank=True,
    )

    @property
    def fieldset_path(self):
        return [
            {"parent": loc.parent_fieldset, "index": loc.child_index}
            for loc in self.fieldset_path_locations.all()
        ]

    def get_upload_subfolder(self):
        project_id = str(self.project.pk)
        if not project_id:
            raise ValueError("No project id could be found, can't save file!")
        return ["projects", project_id, self.attribute.identifier]

    file = PrivateFileField(
        "File",
        storage=KaavapinoPrivateStorage(
            base_url=reverse_lazy("serve_private_project_file", kwargs={"path": ""}),
            url_postfix="projects",
        ),
        upload_subfolder=get_upload_subfolder,
        max_length=255,
    )

    def save(self, *args, **kwargs):
        super(ProjectAttributeFile, self).save(*args, **kwargs)
        # portrait a4 paper @ 200dpi
        paper_size_in_pixels = (1654, 2339)
        try:
            # resize to 200dpi print size
            image = Image.open(self.file.path)
            image.thumbnail(paper_size_in_pixels, Image.ANTIALIAS)
            image.save(self.file.path, quality=100, optimize=True)
        except IOError:
            # not an image
            pass

    class Meta:
        verbose_name = _("project attribute file")
        verbose_name_plural = _("project attribute files")

    def __str__(self):
        return f"{self.project} {self.attribute}"


class PhaseAttributeMatrixStructure(BaseAttributeMatrixStructure):
    section = models.ForeignKey(
        ProjectPhaseSection, verbose_name=_("phase section"), on_delete=models.CASCADE
    )

    def __str__(self):
        return f"{self.section} ({len(self.row_names)}x{len(self.column_names)})"


class PhaseAttributeMatrixCell(BaseAttributeMatrixCell):
    attribute = models.ForeignKey(
        ProjectPhaseSectionAttribute, on_delete=models.CASCADE
    )
    structure = models.ForeignKey(
        PhaseAttributeMatrixStructure, on_delete=models.CASCADE
    )

    def __str__(self):
        return f"{self.structure} {self.attribute} ({self.row}, {self.column})"


class ProjectAttributeMultipolygonGeometry(models.Model):
    geometry = models.MultiPolygonField(null=True, blank=True)
    attribute = models.ForeignKey(
        Attribute,
        verbose_name=_("attribute"),
        related_name="geometries",
        on_delete=models.CASCADE,
    )
    project = models.ForeignKey(
        Project,
        verbose_name=_("project"),
        related_name="geometries",
        on_delete=models.CASCADE,
    )


class ProjectDeadline(models.Model):
    deadline = models.ForeignKey(
        Deadline,
        verbose_name=_("deadline"),
        related_name="project_deadlines",
        on_delete=models.CASCADE,
    )
    project = models.ForeignKey(
        Project,
        verbose_name=_("project"),
        related_name="project_deadlines",
        on_delete=models.CASCADE,
    )
    date = models.DateField(
        verbose_name=_("deadline date"),
        null=True,
        blank=True,
    )
    generated = models.BooleanField(
        verbose_name=_("generated"),
        default=False,
    )

    @property
    def confirmed(self):
        try:
            identifier = self.deadline.confirmation_attribute.identifier
        except AttributeError:
            return None

        value = self.project.attribute_data.get(identifier)

        if value is not None:
            return bool(value)

        return None

    class Meta:
        unique_together = ("deadline", "project")
        ordering = ("deadline__index",)

    def __str__(self):
        return f"{self.deadline.abbreviation} {self.date}"


class ProjectPhaseDeadlineSectionAttribute(models.Model):
    """Links an attribute into a project phase deadline section."""

    attribute = models.ForeignKey(
        Attribute,
        verbose_name=_("attribute"),
        on_delete=models.CASCADE,
    )
    section = models.ForeignKey(
        "ProjectPhaseDeadlineSection",
        verbose_name=_("deadline phase section"),
        on_delete=models.CASCADE,
    )
    owner_field = models.BooleanField(
        default=False,
        verbose_name=_("show for project owner"),
    )
    admin_field = models.BooleanField(
        default=False,
        verbose_name=_("show for administrator"),
    )
    index = models.PositiveIntegerField(verbose_name=_("index"), default=0)

    class Meta:
        verbose_name = _("project phase deadline section item")
        verbose_name_plural = _("project phase deadline section items")
        ordering = ("index",)

    def __str__(self):
        return f"{self.attribute} {self.section} {self.index}"


class ProjectPhaseDeadlineSection(models.Model):
    """Defines a deadline section for a project phase."""

    phase = models.ForeignKey(
        ProjectPhase,
        verbose_name=_("phase"),
        related_name="deadline_sections",
        on_delete=models.CASCADE,
    )
    index = models.PositiveIntegerField(verbose_name=_("index"), default=0)
    attributes = models.ManyToManyField(
        Attribute,
        verbose_name=_("attributes"),
        related_name="phase_deadline_sections",
        through="ProjectPhaseDeadlineSectionAttribute",
    )

    @property
    def name(self):
        return f"{self.phase.list_prefix}. {self.phase.name}"

    class Meta:
        verbose_name = _("project phase deadline section")
        verbose_name_plural = _("project phase deadline sections")
        ordering = ("index",)

    def __str__(self):
        return f"{self.phase.name}, {self.phase.project_subtype.name}"


class FieldsetPathLocation(models.Model):
    """Defines a single node in a fieldset path to a field"""
    child_index = models.PositiveIntegerField(verbose_name=_("child index"))
    parent_fieldset = models.ForeignKey(
        Attribute,
        verbose_name=_("parent fieldset"),
        on_delete=models.CASCADE,
    )
    index = models.PositiveIntegerField(verbose_name=_("index"))

    class Meta:
        abstract = True
        ordering = ("index",)


class ProjectAttributeFileFieldsetPathLocation(FieldsetPathLocation):
    target = models.ForeignKey(
        ProjectAttributeFile,
        verbose_name=_("target file"),
        related_name="fieldset_path_locations",
        on_delete=models.CASCADE,
    )

    class Meta(FieldsetPathLocation.Meta):
        unique_together = ("index", "target")


class FieldCommentFieldsetPathLocation(FieldsetPathLocation):
    target = models.ForeignKey(
        FieldComment,
        verbose_name=_("target comment"),
        related_name="fieldset_path_locations",
        on_delete=models.CASCADE,
    )

    class Meta(FieldsetPathLocation.Meta):
        unique_together = ("index", "target")
