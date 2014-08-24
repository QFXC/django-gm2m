from django.db.models.fields.related import RelatedField, RelatedObject, \
    add_lazy_relation, RECURSIVE_RELATIONSHIP_CONSTANT
from django.db.models import Q
from django.db.utils import DEFAULT_DB_ALIAS
from django.utils import six
from .models import create_gm2m_intermediary_model, \
                    CT_ATTNAME, PK_ATTNAME, SRC_ATTNAME
from .descriptors import GM2MRelatedDescriptor, ReverseGM2MRelatedDescriptor
from .relations import GM2MRel
from .helpers import get_content_type


class GM2MRelatedObject(RelatedObject):

    def __init__(self, parent_model, model, field, rel):
        super(GM2MRelatedObject, self).__init__(parent_model, model, field)
        self.rel = rel

    def bulk_related_objects(self, objs, using=DEFAULT_DB_ALIAS):
        """
        Return all objects related to objs
        """

        q = Q()
        for obj in objs:
            # Convert each obj to (content_type, primary_key)
            q = q | Q(**{
                CT_ATTNAME: get_content_type(obj),
                PK_ATTNAME: obj.pk
            })

        return self.field.through._base_manager.db_manager(using).filter(q)


class GM2MField(RelatedField):
    """
    Provides a generic relation to several generic objects through a
    generic model storing content-type/object-id information

    Reverse relations can be established with models provided as arguments
    """

    def __init__(self, *related_models, **params):
        self.rels = []
        for m in related_models:
            self._add_relation(m)

        self.verbose_name = params.pop('verbose_name', None)
        self.through = params.pop('through', None)
        self.db_table = params.pop('db_table', None)

        self._related_name = params.pop('related_name', None)
        self._related_query_name = params.pop('related_query_name', None)

        if self.through is not None:
            assert self.db_table is None, "Cannot specify a db_table if an ' \
            'intermediary model is used."

    def _add_relation(self, model):
        to = model
        try:
            assert not to._meta.abstract, \
            "%s cannot define a relation with abstract class %s" \
            % (self.__class__.__name__, to._meta.object_name)
        except AttributeError:
            # to._meta doesn't exist, so it must be a string
            assert isinstance(to, six.string_types), \
            '%s(%r) is invalid. First parameter to ManyToManyField must ' \
            'be either a model, a model name, or the string %r' \
            % (self.__class__.__name__, to,
               RECURSIVE_RELATIONSHIP_CONSTANT)

        rel = GM2MRel(self, to)
        self.rels.append(rel)

        return rel

    def add_relation(self, model):
        rel = self._add_relation(model)
        self._contribute_to_class(rel)

    def get_reverse_path_info(self):
        linkfield = self.through._meta.get_field_by_name(SRC_ATTNAME)[0]
        return linkfield.get_reverse_path_info()

    def db_type(self, connection):
        """
        By default related field will not have a column as it relates
        columns to another table
        """
        return None

    def contribute_to_class(self, cls, name):
        """
        Appends accessor to a class and create intermediary model

        :param cls: the class to contribute to
        :param name: the name of the accessor
        """

        self.name = name
        self.model = cls
        self.opts = cls._meta

        self.through = create_gm2m_intermediary_model(self, cls)
        cls._meta.add_virtual_field(self)

        # Connect the descriptor for this field
        setattr(cls, name, ReverseGM2MRelatedDescriptor(self))

        # set related name
        if not self.model._meta.abstract and self._related_name:
            related_name = self._related_name % {
                'class': self.model.__name__.lower(),
                'app_label': self.model._meta.app_label.lower()
            }
            self._related_name = related_name

        # Set up related classes if relations are defined
        for rel in self.rels:
            self._contribute_to_class(rel)

    def _contribute_to_class(self, rel):
        rel.through = self.through

        other = rel.to
        if isinstance(other, six.string_types) or other._meta.pk is None:
            def resolve_related_class(field, model, cls, rel):
                rel.to = model
                field.do_related_class(model, cls, rel)
            add_lazy_relation(self.model, self, other, resolve_related_class)
        else:
            self.do_related_class(other, rel)

    def do_related_class(self, other, rel):
        self.related = GM2MRelatedObject(other, self.model, self, rel)
        if not self.model._meta.abstract:
            self.contribute_to_related_class(other, self.related, rel)

    def contribute_to_related_class(self, cls, related, rel):
        """
        Appends accessors to related classes

        :param cls: the class to contribute to
        :param related: the related field
        :param rel: the relation object concerning cls and self.model
        """

        assert rel.to == cls, 'Bad relation object'

        # Internal M2Ms (i.e., those with a related name ending with '+')
        # and swapped models don't get a related descriptor.
        if not rel.is_hidden() and not related.model._meta.swapped:
            cls._meta.add_virtual_field(related)
            setattr(cls, self._related_name or (self.opts.model_name + '_set'),
                    GM2MRelatedDescriptor(related, rel))

    def is_hidden(self):
        "Should the related object be hidden?"
        return self._related_name and self._related_name[-1] == '+'

    def related_query_name(self):
        return self._related_query_name or self._related_name \
            or self.opts.model_name
