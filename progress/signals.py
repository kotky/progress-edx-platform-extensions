"""
Signal handlers supporting various progress use cases
"""
import sys
import logging

from django.dispatch import receiver
from django.db.models.signals import post_save, pre_save
from django.db.models import F
from django.core.exceptions import ObjectDoesNotExist
from django.conf import settings
from opaque_keys import InvalidKeyError
from opaque_keys.edx.locator import BlockUsageLocator
from student.roles import get_aggregate_exclusion_user_ids
from util.signals import course_deleted

from course_metadata.utils import is_progress_detached_vertical

from progress.models import StudentProgress, StudentProgressHistory, CourseModuleCompletion

from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

log = logging.getLogger(__name__)


def is_valid_progress_module(content_id):
    """
    Returns boolean indicating if given module is valid for marking progress
    A valid module should be child of `vertical` and its category should be
    one of the PROGRESS_DETACHED_CATEGORIES
    """
    try:
        detached_categories = getattr(settings, 'PROGRESS_DETACHED_CATEGORIES', [])
        usage_id = BlockUsageLocator.from_string(content_id)
        module = modulestore().get_item(usage_id)
        if module and module.parent and module.parent.category == "vertical" and \
                module.category not in detached_categories and not is_progress_detached_vertical(module.parent):
            return True
        else:
            return False
    except (InvalidKeyError, ItemNotFoundError) as exception:
        log.debug("Error getting module for content_id:%s %s", content_id, exception.message)
        return False
    except Exception as exception:  # pylint: disable=broad-except
        # broad except to avoid wrong calculation of progress in case of unknown exception
        log.exception("Error getting module for content_id:%s %s", content_id, exception.message)
        return False


@receiver(post_save, sender=CourseModuleCompletion, dispatch_uid='lms.progress.post_save_cms')
def handle_cmc_post_save_signal(sender, instance, created, **kwargs):  # pylint: disable=unused-argument
    """
    Broadcast the progress change event
    """
    content_id = unicode(instance.content_id)
    if is_valid_progress_module(content_id):
        try:
            progress = StudentProgress.objects.get(user=instance.user, course_id=instance.course_id)
            progress.completions = F('completions') + 1
            progress.save()
        except ObjectDoesNotExist:
            progress = StudentProgress(user=instance.user, course_id=instance.course_id, completions=1)
            progress.save()
        except Exception:  # pylint: disable=broad-except
            exc_type, exc_value, __ = sys.exc_info()
            logging.error("Exception type: %s with value: %s", exc_type, exc_value)


@receiver(post_save, sender=StudentProgress)
def save_history(sender, instance, **kwargs):  # pylint: disable=no-self-argument, unused-argument
    """
    Event hook for creating progress entry copies
    """
    # since instance.completions return F() ExpressionNode we have to pull completions from db
    progress = StudentProgress.objects.get(pk=instance.id)
    history_entry = StudentProgressHistory(
        user=instance.user,
        course_id=instance.course_id,
        completions=progress.completions
    )
    history_entry.save()


@receiver(course_deleted)
def on_course_deleted(sender, **kwargs):  # pylint: disable=W0613
    """
    Listens for a 'course_deleted' signal and when observed
    removes model entries for the specified course
    """
    course_key = kwargs['course_key']
    CourseModuleCompletion.objects.filter(course_id=unicode(course_key)).delete()
    StudentProgress.objects.filter(course_id=course_key).delete()
    StudentProgressHistory.objects.filter(course_id=course_key).delete()
