from django.urls import path

from apps.inbound_email.views import mailgun_inbound, sendgrid_inbound

app_name = "inbound_email"

urlpatterns = [
    path("sendgrid/", sendgrid_inbound, name="sendgrid-inbound"),
    path("mailgun/", mailgun_inbound, name="mailgun-inbound"),
]
