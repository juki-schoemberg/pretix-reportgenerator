# Owner from wave 1 on: persistence-dev (see ORCHESTRIERUNG.md section 5)
"""Deliberately plain forms for storing report definitions.

This is the "no fancy UI" layer from SPEC.md P4: a name, an identifier and the
definition as a JSON textarea. The graphical editor lives in ``views/editor.py``
and belongs to ``frontend-dev``; both write through the same model and therefore
through the same validation.

Validation split, once more, because it is the thing that must not blur:
:func:`contracts.validate_definition` checks **structure** only. Whether a field
key exists is a registry question and is asked in the editor, on import and when
a report runs -- never when it is saved (docs/adr/0001-contracts.md section 4).
"""

from typing import Any, Optional

import json
from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.forms.fields import InvalidJSONInput
from django.utils.translation import gettext_lazy as _

from pretix_custom_reports import contracts
from pretix_custom_reports.models import ReportDefinition

__all__ = [
    "ModelErrorRemapMixin",
    "PrettyJSONFormField",
    "ReportDefinitionForm",
    "default_definition",
]


def default_definition(base: Any = contracts.Base.ORDER) -> dict:
    """A small but *valid* starting definition for the "new report" form.

    Built from the frozen dataclasses instead of a hand-written literal, so it
    cannot drift away from the schema. Deliberately not
    :func:`contracts.empty_definition`: that one has no columns and therefore
    does not validate, which would greet every new report with an error.
    """
    document = contracts.ReportDefinition(
        base=contracts.Base.coerce(base),
        columns=(
            contracts.Column(field="order.code"),
            contracts.Column(field="order.datetime"),
        ),
    )
    return document.as_dict()


class PrettyJSONFormField(forms.JSONField):
    """``forms.JSONField`` that renders indented JSON.

    The default implementation dumps everything onto one line, which makes a
    definition with twenty columns unreadable and therefore uneditable.
    """

    def prepare_value(self, value):
        if isinstance(value, InvalidJSONInput):
            # Keep the user's own text so they can fix their typo.
            return value
        if value is None:
            return ""
        return json.dumps(value, indent=2, ensure_ascii=False, cls=self.encoder)


class ModelErrorRemapMixin:
    """Keep model-level errors from crashing forms that hide the field.

    :meth:`ReportDefinition.clean` reports problems keyed on ``definition``,
    ``base`` or ``identifier``. Django's ``BaseModelForm._post_clean`` hands
    those to ``add_error``, which raises ``ValueError`` for a key the form does
    not render -- turning a validation error into a 500. Any form on this model
    that omits one of those fields (the graphical editor, an API form) should
    mix this in; it moves such errors to the top of the form instead.
    """

    def add_error(self, field, error):
        if field is None and hasattr(error, "error_dict"):
            remapped: dict = {}
            for key, messages in error.error_dict.items():
                target = (
                    key
                    if key == NON_FIELD_ERRORS or key in self.fields
                    else NON_FIELD_ERRORS
                )
                remapped.setdefault(target, []).extend(messages)
            error = ValidationError(remapped)
        return super().add_error(field, error)


class ReportDefinitionForm(ModelErrorRemapMixin, forms.ModelForm):
    """Create/change form for a report, event level or organizer template.

    The owner is passed in, never posted: which event or organizer a report
    belongs to follows from the URL and must not be forgeable through a hidden
    input.
    """

    class Meta:
        model = ReportDefinition
        fields = ("name", "description", "identifier", "base", "definition")
        field_classes = {"definition": PrettyJSONFormField}
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "definition": forms.Textarea(attrs={"rows": 20, "spellcheck": "false"}),
        }

    def __init__(self, *args: Any, event=None, organizer=None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if event is not None:
            self.instance.event = event
            self.instance.organizer = None
        elif organizer is not None:
            self.instance.organizer = organizer
            self.instance.event = None
        if not self.instance.pk and not self.initial.get("definition"):
            self.fields["definition"].initial = default_definition()
        self.fields["identifier"].required = False
        # ``models.JSONField(default=dict)`` has a *callable* default, so
        # Django's ``Field.formfield`` switches on ``show_hidden_initial``
        # (django/db/models/fields/__init__.py:1108-1111). ``bootstrap_field``
        # does not render that extra hidden input, so nothing ever posts it
        # back and ``changed_data`` would list ``definition`` on every save --
        # which would make the ``changed_fields`` entry in our audit log
        # worthless. Compare against the value loaded from the database
        # instead.
        self.fields["definition"].show_hidden_initial = False

    def clean_definition(self) -> Optional[dict]:
        """Structural validation, reported on the field the user is looking at.

        The model validates again in ``save()``; this exists so the message
        lands on the textarea (with the JSON path of every problem) instead of
        at the top of the page.
        """
        value = self.cleaned_data.get("definition")
        try:
            document = contracts.validate_definition(value)
        except contracts.DefinitionValidationError as e:
            raise ValidationError(
                [
                    f"{issue.path}: {issue.message}" if issue.path else issue.message
                    for issue in e.issues
                ]
            )
        # Store the canonical form: exactly one representation per definition.
        return document.as_dict()

    def clean(self) -> dict:
        cleaned = super().clean()
        definition = cleaned.get("definition")
        base = cleaned.get("base")
        if definition and base and definition.get("base") != base:
            self.add_error(
                "base",
                ValidationError(
                    _(
                        "The selected base does not match the base inside the "
                        "definition (%(definition_base)s)."
                    )
                    % {"definition_base": definition.get("base")},
                ),
            )
        return cleaned
