from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


@override_settings(DESKTOP_MODE=True)
class DesktopBootstrapTests(APITestCase):
    def setUp(self):
        self.url = reverse("desktop-bootstrap")

    def test_first_local_user_can_be_created_once(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["needs_setup"])

        response = self.client.post(
            self.url,
            {
                "username": "owner",
                "password": "Strong-Local-Passphrase-741!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = get_user_model().objects.get(username="owner")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("Strong-Local-Passphrase-741!"))

        response = self.client.post(
            self.url,
            {"username": "intruder", "password": "Another-Strong-Password-29!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_non_loopback_requests_are_rejected(self):
        response = self.client.get(self.url, REMOTE_ADDR="192.0.2.10")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(DESKTOP_MODE=False)
    def test_endpoint_is_not_available_in_server_mode(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
