import base64
import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.html import escape
from django.utils.safestring import mark_safe

from utilities.utils import get_subquery
from utilities.views import (
    BulkDeleteView, BulkEditView, BulkImportView, ObjectView, ObjectDeleteView, ObjectEditView, ObjectListView,
)
from . import filters, forms, tables
from .models import SecretRole, Secret, SessionKey, UserKey


def get_session_key(request):
    """
    Extract and decode the session key sent with a request. Returns None if no session key was provided.
    """
    session_key = request.COOKIES.get('session_key', None)
    if session_key is not None:
        return base64.b64decode(session_key)
    return session_key


#
# Secret roles
#

class SecretRoleListView(ObjectListView):
    queryset = SecretRole.objects.annotate(
        secret_count=get_subquery(Secret, 'role')
    )
    table = tables.SecretRoleTable


class SecretRoleEditView(ObjectEditView):
    queryset = SecretRole.objects.all()
    model_form = forms.SecretRoleForm


class SecretRoleDeleteView(ObjectDeleteView):
    queryset = SecretRole.objects.all()


class SecretRoleBulkImportView(BulkImportView):
    queryset = SecretRole.objects.all()
    model_form = forms.SecretRoleCSVForm
    table = tables.SecretRoleTable


class SecretRoleBulkDeleteView(BulkDeleteView):
    queryset = SecretRole.objects.annotate(
        secret_count=get_subquery(Secret, 'role')
    )
    table = tables.SecretRoleTable


#
# Secrets
#

class SecretListView(ObjectListView):
    queryset = Secret.objects.prefetch_related('role', 'device')
    filterset = filters.SecretFilterSet
    filterset_form = forms.SecretFilterForm
    table = tables.SecretTable
    action_buttons = ('import', 'export')


class SecretView(ObjectView):
    queryset = Secret.objects.all()

    def get(self, request, pk):

        secret = get_object_or_404(self.queryset, pk=pk)

        return render(request, 'secrets/secret.html', {
            'secret': secret,
        })


class SecretEditView(ObjectEditView):
    queryset = Secret.objects.all()
    model_form = forms.SecretForm
    template_name = 'secrets/secret_edit.html'

    def dispatch(self, request, *args, **kwargs):

        # Check that the user has a valid UserKey
        try:
            uk = UserKey.objects.get(user=request.user)
        except UserKey.DoesNotExist:
            messages.warning(request, "This operation requires an active user key, but you don't have one.")
            return redirect('user:userkey')
        if not uk.is_active():
            messages.warning(request, "This operation is not available. Your user key has not been activated.")
            return redirect('user:userkey')

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        logger = logging.getLogger('netbox.views.ObjectEditView')
        session_key = get_session_key(request)
        secret = self.get_object(kwargs)
        form = self.model_form(request.POST, instance=secret)

        if form.is_valid():
            logger.debug("Form validation was successful")

            # We must have a session key in order to create a secret or update the plaintext of an existing secret
            if (form.cleaned_data['plaintext'] or secret.pk is None) and session_key is None:
                logger.debug("Unable to proceed: No session key was provided with the request")
                form.add_error(None, "No session key was provided with the request. Unable to encrypt secret data.")

            else:
                master_key = None
                try:
                    sk = SessionKey.objects.get(userkey__user=request.user)
                    master_key = sk.get_master_key(session_key)
                except SessionKey.DoesNotExist:
                    logger.debug("Unable to proceed: User has no session key assigned")
                    form.add_error(None, "No session key found for this user.")

                if master_key is not None:
                    logger.debug("Successfully resolved master key for encryption")
                    secret = form.save(commit=False)
                    if form.cleaned_data['plaintext']:
                        secret.plaintext = str(form.cleaned_data['plaintext'])
                    secret.encrypt(master_key)
                    secret.save()
                    form.save_m2m()

                    msg = '{} secret'.format('Created' if not form.instance.pk else 'Modified')
                    logger.info(f"{msg} {secret} (PK: {secret.pk})")
                    msg = '{} <a href="{}">{}</a>'.format(msg, secret.get_absolute_url(), escape(secret))
                    messages.success(request, mark_safe(msg))

                    return redirect(self.get_return_url(request, secret))

        else:
            logger.debug("Form validation failed")

        return render(request, self.template_name, {
            'obj': secret,
            'obj_type': self.queryset.model._meta.verbose_name,
            'form': form,
            'return_url': self.get_return_url(request, secret),
        })


class SecretDeleteView(ObjectDeleteView):
    queryset = Secret.objects.all()


class SecretBulkImportView(BulkImportView):
    queryset = Secret.objects.all()
    model_form = forms.SecretCSVForm
    table = tables.SecretTable
    template_name = 'secrets/secret_import.html'
    widget_attrs = {'class': 'requires-session-key'}

    master_key = None

    def _save_obj(self, obj_form, request):
        """
        Encrypt each object before saving it to the database.
        """
        obj = obj_form.save(commit=False)
        obj.encrypt(self.master_key)
        obj.save()
        return obj

    def post(self, request):

        # Grab the session key from cookies.
        session_key = request.COOKIES.get('session_key')
        if session_key:

            # Attempt to derive the master key using the provided session key.
            try:
                sk = SessionKey.objects.get(userkey__user=request.user)
                self.master_key = sk.get_master_key(base64.b64decode(session_key))
            except SessionKey.DoesNotExist:
                messages.error(request, "No session key found for this user.")

            if self.master_key is not None:
                return super().post(request)
            else:
                messages.error(request, "Invalid private key! Unable to encrypt secret data.")

        else:
            messages.error(request, "No session key was provided with the request. Unable to encrypt secret data.")

        return render(request, self.template_name, {
            'form': self._import_form(request.POST),
            'fields': self.model_form().fields,
            'obj_type': self.model_form._meta.model._meta.verbose_name,
            'return_url': self.get_return_url(request),
        })


class SecretBulkEditView(BulkEditView):
    queryset = Secret.objects.prefetch_related('role', 'device')
    filterset = filters.SecretFilterSet
    table = tables.SecretTable
    form = forms.SecretBulkEditForm


class SecretBulkDeleteView(BulkDeleteView):
    queryset = Secret.objects.prefetch_related('role', 'device')
    filterset = filters.SecretFilterSet
    table = tables.SecretTable
