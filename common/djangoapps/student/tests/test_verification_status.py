"""Tests for per-course verification status on the dashboard. """


import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import ddt
import six
from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from django.utils.timezone import now
from edx_toggles.toggles.testutils import override_waffle_flag
from pytz import UTC

from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.helpers import (
    VERIFY_STATUS_APPROVED,
    VERIFY_STATUS_MISSED_DEADLINE,
    VERIFY_STATUS_NEED_TO_REVERIFY,
    VERIFY_STATUS_NEED_TO_VERIFY,
    VERIFY_STATUS_RESUBMITTED,
    VERIFY_STATUS_SUBMITTED
)
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.util.testing import UrlResetMixin
from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification, VerificationDeadline
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order
from openedx.core.djangoapps.agreements.toggles import ENABLE_INTEGRITY_SIGNATURE


@patch.dict(settings.FEATURES, {'AUTOMATIC_VERIFY_STUDENT_IDENTITY_FOR_TESTING': True})
@override_settings(PLATFORM_NAME='edX')
@unittest.skipUnless(settings.ROOT_URLCONF == 'lms.urls', 'Test only valid in lms')
@ddt.ddt
class TestCourseVerificationStatus(UrlResetMixin, ModuleStoreTestCase):
    """Tests for per-course verification status on the dashboard. """

    PAST = 'past'
    FUTURE = 'future'
    DATES = {
        PAST: datetime.now(UTC) - timedelta(days=5),
        FUTURE: datetime.now(UTC) + timedelta(days=5),
        None: None,
    }

    URLCONF_MODULES = ['lms.djangoapps.verify_student.urls']

    def setUp(self):
        # Invoke UrlResetMixin
        super().setUp()

        self.user = UserFactory(password="edx")
        self.course = CourseFactory.create()
        success = self.client.login(username=self.user.username, password="edx")
        assert success, 'Did not log in successfully'
        self.dashboard_url = reverse('dashboard')

    def test_enrolled_as_non_verified(self):
        self._setup_mode_and_enrollment(None, "audit")

        # Expect that the course appears on the dashboard
        # without any verification messaging
        self._assert_course_verification_status(None)

    def test_no_verified_mode_available(self):
        # Enroll the student in a verified mode, but don't
        # create any verified course mode.
        # This won't happen unless someone deletes a course mode,
        # but if so, make sure we handle it gracefully.
        CourseEnrollmentFactory(
            course_id=self.course.id,
            user=self.user,
            mode="verified"
        )

        # Continue to show the student as needing to verify.
        # The student is enrolled as verified, so we might as well let them
        # complete verification.  We'd need to change their enrollment mode
        # anyway to ensure that the student is issued the correct kind of certificate.
        self._assert_course_verification_status(VERIFY_STATUS_NEED_TO_VERIFY)

    def test_need_to_verify_no_expiration(self):
        self._setup_mode_and_enrollment(None, "verified")

        # Since the student has not submitted a photo verification,
        # the student should see a "need to verify" message
        self._assert_course_verification_status(VERIFY_STATUS_NEED_TO_VERIFY)

        # Start the photo verification process, but do not submit
        # Since we haven't submitted the verification, we should still
        # see the "need to verify" message
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        self._assert_course_verification_status(VERIFY_STATUS_NEED_TO_VERIFY)

        # Upload images, but don't submit to the verification service
        # We should still need to verify
        attempt.mark_ready()
        self._assert_course_verification_status(VERIFY_STATUS_NEED_TO_VERIFY)

    def test_need_to_verify_expiration(self):
        self._setup_mode_and_enrollment(self.DATES[self.FUTURE], "verified")
        response = self.client.get(self.dashboard_url)
        self.assertContains(response, self.BANNER_ALT_MESSAGES[VERIFY_STATUS_NEED_TO_VERIFY])
        self.assertContains(response, "You only have 4 days left to verify for this course.")

    @ddt.data(None, FUTURE)
    def test_waiting_approval(self, expiration):
        self._setup_mode_and_enrollment(self.DATES[expiration], "verified")

        # The student has submitted a photo verification
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt.mark_ready()
        attempt.submit()

        # Now the student should see a "verification submitted" message
        self._assert_course_verification_status(VERIFY_STATUS_SUBMITTED)

    @ddt.data(None, FUTURE)
    def test_fully_verified(self, expiration):
        self._setup_mode_and_enrollment(self.DATES[expiration], "verified")

        # The student has an approved verification
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt.mark_ready()
        attempt.submit()
        attempt.approve()

        # Expect that the successfully verified message is shown
        self._assert_course_verification_status(VERIFY_STATUS_APPROVED)

        # Check that the "verification good until" date is displayed
        response = self.client.get(self.dashboard_url)
        self.assertContains(response, attempt.expiration_datetime.strftime("%m/%d/%Y"))

    @patch("lms.djangoapps.verify_student.services.is_verification_expiring_soon")
    def test_verify_resubmit_button_on_dashboard(self, mock_expiry):
        mock_expiry.return_value = True
        SoftwareSecurePhotoVerification.objects.create(
            user=self.user,
            status='approved',
            expiration_date=now() + timedelta(days=1)
        )
        response = self.client.get(self.dashboard_url)
        self.assertContains(response, "Resubmit Verification")

        mock_expiry.return_value = False
        response = self.client.get(self.dashboard_url)
        self.assertNotContains(response, "Resubmit Verification")

    def test_missed_verification_deadline(self):
        # Expiration date in the past
        self._setup_mode_and_enrollment(self.DATES[self.PAST], "verified")

        # The student does NOT have an approved verification
        # so the status should show that the student missed the deadline.
        self._assert_course_verification_status(VERIFY_STATUS_MISSED_DEADLINE)

    def test_missed_verification_deadline_verification_was_expired(self):
        # Expiration date in the past
        self._setup_mode_and_enrollment(self.DATES[self.PAST], "verified")

        # Create a verification, but the expiration date of the verification
        # occurred before the deadline.
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt.mark_ready()
        attempt.submit()
        attempt.approve()
        attempt.expiration_date = self.DATES[self.PAST] - timedelta(days=900)
        attempt.save()

        # The student didn't have an approved verification at the deadline,
        # so we should show that the student missed the deadline.
        self._assert_course_verification_status(VERIFY_STATUS_MISSED_DEADLINE)

    def test_missed_verification_deadline_but_later_verified(self):
        # Expiration date in the past
        self._setup_mode_and_enrollment(self.DATES[self.PAST], "verified")

        # Successfully verify, but after the deadline has already passed
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt.mark_ready()
        attempt.submit()
        attempt.approve()
        attempt.expiration_date = self.DATES[self.PAST] - timedelta(days=900)
        attempt.save()

        # The student didn't have an approved verification at the deadline,
        # so we should show that the student missed the deadline.
        self._assert_course_verification_status(VERIFY_STATUS_MISSED_DEADLINE)

    def test_verification_denied(self):
        # Expiration date in the future
        self._setup_mode_and_enrollment(self.DATES[self.FUTURE], "verified")

        # Create a verification with the specified status
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt.mark_ready()
        attempt.submit()
        attempt.deny("Not valid!")

        # Since this is not a status we handle, don't display any
        # messaging relating to verification
        self._assert_course_verification_status(None)

    def test_verification_error(self):
        # Expiration date in the future
        self._setup_mode_and_enrollment(self.DATES[self.FUTURE], "verified")

        # Create a verification with the specified status
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt.status = "must_retry"
        attempt.system_error("Error!")

        # Since this is not a status we handle, don't display any
        # messaging relating to verification
        self._assert_course_verification_status(None)

    @override_settings(VERIFY_STUDENT={"DAYS_GOOD_FOR": 5, "EXPIRING_SOON_WINDOW": 10})
    def test_verification_will_expire_by_deadline(self):
        # Expiration date in the future
        self._setup_mode_and_enrollment(self.DATES[self.FUTURE], "verified")

        # Create a verification attempt that:
        # 1) Is current (submitted in the last year)
        # 2) Will expire by the deadline for the course
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt.mark_ready()
        attempt.submit()
        attempt.approve()
        attempt.save()

        # Verify that learner can submit photos if verification is set to expire soon.
        self._assert_course_verification_status(VERIFY_STATUS_NEED_TO_REVERIFY)

    @override_settings(VERIFY_STUDENT={"DAYS_GOOD_FOR": 5, "EXPIRING_SOON_WINDOW": 10})
    def test_reverification_submitted_with_current_approved_verificaiton(self):
        # Expiration date in the future
        self._setup_mode_and_enrollment(self.DATES[self.FUTURE], "verified")

        # Create a verification attempt that is approved but expiring soon
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt.mark_ready()
        attempt.submit()
        attempt.approve()
        attempt.save()

        # Verify that learner can submit photos if verification is set to expire soon.
        self._assert_course_verification_status(VERIFY_STATUS_NEED_TO_REVERIFY)

        # Submit photos for reverification
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt.mark_ready()
        attempt.submit()

        # Expect that learner has submitted photos for reverfication and their
        # previous verification is set to expired soon.
        self._assert_course_verification_status(VERIFY_STATUS_RESUBMITTED)

    def test_verification_occurred_after_deadline(self):
        # Expiration date in the past
        self._setup_mode_and_enrollment(self.DATES[self.PAST], "verified")

        # The deadline has passed, and we've asked the student
        # to reverify (through the support team).
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt.mark_ready()
        attempt.submit()

        # Expect that the user's displayed enrollment mode is verified.
        self._assert_course_verification_status(VERIFY_STATUS_APPROVED)

    def test_with_two_verifications(self):
        # checking if a user has two verification and but most recent verification course deadline is expired

        self._setup_mode_and_enrollment(self.DATES[self.FUTURE], "verified")

        # The student has an approved verification
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt.mark_ready()
        attempt.submit()
        attempt.approve()
        # Making created at to previous date to differentiate with 2nd attempt.
        attempt.created_at = datetime.now(UTC) - timedelta(days=1)
        attempt.save()

        # Expect that the successfully verified message is shown
        self._assert_course_verification_status(VERIFY_STATUS_APPROVED)

        # Check that the "verification good until" date is displayed
        response = self.client.get(self.dashboard_url)
        self.assertContains(response, attempt.expiration_datetime.strftime("%m/%d/%Y"))

        # Adding another verification with different course.
        # Its created_at is greater than course deadline.
        course2 = CourseFactory.create()
        CourseModeFactory.create(
            course_id=course2.id,
            mode_slug="verified",
            expiration_datetime=self.DATES[self.PAST]
        )
        CourseEnrollmentFactory(
            course_id=course2.id,
            user=self.user,
            mode="verified"
        )

        # The student has an approved verification
        attempt2 = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt2.mark_ready()
        attempt2.submit()
        attempt2.approve()
        attempt2.save()

        # Mark the attemp2 as approved so its date will appear on dasboard.
        self._assert_course_verification_status(VERIFY_STATUS_APPROVED)
        response2 = self.client.get(self.dashboard_url)
        self.assertContains(response2, attempt2.expiration_datetime.strftime("%m/%d/%Y"))
        self.assertContains(response2, attempt2.expiration_datetime.strftime("%m/%d/%Y"), count=2)

    @override_waffle_flag(ENABLE_INTEGRITY_SIGNATURE, active=True)
    @ddt.data(
        None,
        'past',
        'future'
    )
    def test_verify_message_idv_disabled(self, deadline_key):
        if deadline_key:
            self._setup_mode_and_enrollment(self.DATES[deadline_key], "verified")
        else:
            self._setup_mode_and_enrollment(None, "verified")

        self._assert_course_verification_status(None)

        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        self._assert_course_verification_status(None)
        attempt.mark_ready()
        self._assert_course_verification_status(None)
        attempt.submit()
        self._assert_course_verification_status(None)
        attempt.approve()
        self._assert_course_verification_status(None)
        attempt.expiration_date = self.DATES[self.PAST] - timedelta(days=900)
        attempt.save()
        self._assert_course_verification_status(None)

    @ddt.data(True, False)
    def test_integrity_disables_sidebar(self, integrity_flag):
        self._setup_mode_and_enrollment(None, "verified")

        #no sidebar when no IDV yet
        response = self.client.get(self.dashboard_url)
        self.assertNotContains(response, "profile-sidebar")

        # The student has an approved verification
        attempt = SoftwareSecurePhotoVerification.objects.create(user=self.user)
        attempt.mark_ready()
        attempt.submit()
        attempt.approve()

        # sidebar only appears after IDV if integrity is not on
        with patch('common.djangoapps.student.views.dashboard.is_integrity_signature_enabled',
                   return_value=integrity_flag):
            response = self.client.get(self.dashboard_url)
            if integrity_flag:
                self.assertNotContains(response, "profile-sidebar")
            else:
                self.assertContains(response, "profile-sidebar")

    def _setup_mode_and_enrollment(self, deadline, enrollment_mode):
        """Create a course mode and enrollment.

        Arguments:
            deadline (datetime): The deadline for submitting your verification.
            enrollment_mode (str): The mode of the enrollment.

        """
        CourseModeFactory.create(
            course_id=self.course.id,
            mode_slug="verified",
            expiration_datetime=deadline
        )
        CourseEnrollmentFactory(
            course_id=self.course.id,
            user=self.user,
            mode=enrollment_mode
        )
        VerificationDeadline.set_deadline(self.course.id, deadline)

    BANNER_ALT_MESSAGES = {
        VERIFY_STATUS_NEED_TO_VERIFY: "ID verification pending",
        VERIFY_STATUS_SUBMITTED: "ID verification pending",
        VERIFY_STATUS_APPROVED: "ID Verified Ribbon/Badge",
    }

    NOTIFICATION_MESSAGES = {
        VERIFY_STATUS_NEED_TO_VERIFY: [
            "You still need to verify for this course.",
            "Verification not yet complete"
        ],
        VERIFY_STATUS_SUBMITTED: ["You have submitted your verification information."],
        VERIFY_STATUS_RESUBMITTED: ["You have submitted your reverification information."],
        VERIFY_STATUS_APPROVED: ["You have successfully verified your ID with edX"],
        VERIFY_STATUS_NEED_TO_REVERIFY: ["Your current verification will expire soon."]
    }

    MODE_CLASSES = {
        None: "audit",
        VERIFY_STATUS_NEED_TO_VERIFY: "verified",
        VERIFY_STATUS_SUBMITTED: "verified",
        VERIFY_STATUS_APPROVED: "verified",
        VERIFY_STATUS_MISSED_DEADLINE: "audit",
        VERIFY_STATUS_NEED_TO_REVERIFY: "audit",
        VERIFY_STATUS_RESUBMITTED: "audit"
    }

    def _assert_course_verification_status(self, status):
        """Check whether the specified verification status is shown on the dashboard.

        Arguments:
            status (str): One of the verification status constants.
                If None, check that *none* of the statuses are displayed.

        Raises:
            AssertionError

        """
        response = self.client.get(self.dashboard_url)

        # Sanity check: verify that the course is on the page
        self.assertContains(response, str(self.course.id))

        # Verify that the correct banner is rendered on the dashboard
        alt_text = self.BANNER_ALT_MESSAGES.get(status)
        if alt_text:
            self.assertContains(response, alt_text)

        # Verify that the correct banner color is rendered
        self.assertContains(
            response,
            f"<article class=\"course {self.MODE_CLASSES[status]}\""
        )

        # Verify that the correct copy is rendered on the dashboard
        if status is not None:
            if status in self.NOTIFICATION_MESSAGES:
                # Different states might have different messaging
                # so in some cases we check several possibilities
                # and fail if none of these are found.
                found_msg = False
                for message in self.NOTIFICATION_MESSAGES[status]:
                    if six.b(message) in response.content:
                        found_msg = True
                        break

                fail_msg = "Could not find any of these messages: {expected}".format(
                    expected=self.NOTIFICATION_MESSAGES[status]
                )
                assert found_msg, fail_msg
        else:
            # Combine all possible messages into a single list
            all_messages = []
            for msg_group in self.NOTIFICATION_MESSAGES.values():
                all_messages.extend(msg_group)

            # Verify that none of the messages are displayed
            for msg in all_messages:
                self.assertNotContains(response, msg)
